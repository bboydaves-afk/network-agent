"""AI Network Agent powered by Claude.

This module implements the core agentic loop: the user sends a message, Claude
decides which tool(s) to call, the agent executes them against the real
network-operations back-end, feeds the results back to Claude, and returns a
final answer.  Destructive operations are gated behind an explicit
confirmation step.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from anthropic import AsyncAnthropic

from .tools import NETWORK_TOOLS
from .safety import (
    SafetyLevel,
    check_dangerous_command,
    get_safety_level,
    requires_confirmation,
)
from automation.audit import AuditLogger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert network engineer AI assistant. You manage and monitor \
network infrastructure including routers, switches, firewalls, wireless \
access points, and other network devices.

Your capabilities:
- View and manage device inventory
- Monitor device health (CPU, memory, interfaces)
- Backup and deploy configurations
- Troubleshoot connectivity issues (ping, traceroute, port checks)
- Discover new devices on the network
- Manage alerts and alert rules
- Execute CLI commands on network devices
- Manage firewall policies, NAT rules, zones, and address/service objects on Fortinet, Palo Alto, pfSense, and Sophos UTM devices
- Manage VLANs: create, delete, sync from devices, assign interfaces to VLANs across all supported vendors
- Manage routing: view routing tables, create/delete static routes, configure OSPF (process ID, router ID, networks, neighbors)
- Manage ACLs: create/delete access lists, add/remove entries (ACEs), bind/unbind to interfaces
- Change management: create change requests with rollback plans, approve/reject, execute with pre/post checks, auto-rollback on failure, view change history
- Serial console: connect to devices via serial port for out-of-band management, password recovery (break signal), initial device setup, and accessing devices when the network is down

Safety rules:
1. ALWAYS explain what you are about to do before taking action.
2. For DESTRUCTIVE operations (config deployment, arbitrary commands) you \
MUST explain the impact and ask for explicit confirmation.
3. Never run commands that could cause network outages without triple \
confirmation.
4. When in doubt, start with read-only operations to gather information.
5. Always backup config before making changes.
6. Provide clear summaries of findings.

When presenting data, format it clearly with tables or structured output. \
Be concise but thorough."""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class NetworkAIAgent:
    """Orchestrator that bridges Claude tool-use with network operations.

    Parameters
    ----------
    api_key : str
        Anthropic API key.
    model : str
        Claude model to use (default ``claude-sonnet-4-5-20250929``).
    max_tokens : int
        Maximum tokens per Claude response.
    db : Database
        Async SQLite database instance (``core.database.Database``).
    config_manager : ConfigManager
        Configuration backup / deploy / rollback engine.
    monitor : MonitoringEngine
        Device polling and metrics engine.
    discovery : NetworkDiscovery
        Subnet scanning and auto-discovery engine.
    troubleshooter : Troubleshooter
        Ping, traceroute, port-check helpers.
    credential_manager : CredentialManager
        Credential storage and retrieval.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 4096,
        db: Any = None,
        config_manager: Any = None,
        monitor: Any = None,
        discovery: Any = None,
        troubleshooter: Any = None,
        credential_manager: Any = None,
    ) -> None:
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.db = db
        self.config_manager = config_manager
        self.monitor = monitor
        self.discovery = discovery
        self.troubleshooter = troubleshooter
        self.credential_manager = credential_manager
        self.site_manager = None
        self.serial_manager = None

        # Audit trail for all tool executions
        self._audit = AuditLogger(db) if db else None

        # Conversation state
        self.conversation_history: list[dict[str, Any]] = []
        self._pending_confirmation: Optional[dict[str, Any]] = None
        self._pending_confirmation_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(self, user_message: str) -> dict[str, Any]:
        """Process a user message and return the agent's response.

        Returns
        -------
        dict
            ``message``            -- The assistant's textual response.
            ``tool_calls``         -- List of tools that were invoked.
            ``confirmation_required`` -- True when the agent is waiting for
                                        the user to approve a destructive op.
            ``confirmation_details``  -- Details of what needs confirmation.
        """

        # ---- Handle pending confirmation flow ----------------------------
        if self._pending_confirmation is not None:
            affirmative = user_message.lower().strip() in (
                "yes", "y", "confirm", "proceed", "do it", "ok", "sure",
            )
            if affirmative:
                pending = self._pending_confirmation
                pending_id = self._pending_confirmation_id
                self._pending_confirmation = None
                self._pending_confirmation_id = None

                tool_result = await self._execute_tool(
                    pending["tool_name"],
                    pending["tool_input"],
                )
                return await self._continue_with_tool_result(
                    pending_id, tool_result
                )
            else:
                self._pending_confirmation = None
                self._pending_confirmation_id = None
                cancel_msg = "Operation cancelled."
                self.conversation_history.append(
                    {"role": "user", "content": user_message}
                )
                self.conversation_history.append(
                    {"role": "assistant", "content": cancel_msg}
                )
                return {
                    "message": cancel_msg,
                    "tool_calls": [],
                    "confirmation_required": False,
                    "confirmation_details": None,
                }

        # ---- Normal message flow -----------------------------------------
        self.conversation_history.append(
            {"role": "user", "content": user_message}
        )

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            tools=NETWORK_TOOLS,
            messages=self.conversation_history,
        )

        return await self._process_response(response)

    def reset_conversation(self) -> None:
        """Clear conversation history and any pending confirmation."""
        self.conversation_history.clear()
        self._pending_confirmation = None
        self._pending_confirmation_id = None

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    async def _process_response(self, response) -> dict[str, Any]:
        """Walk through Claude's response blocks, executing tools as needed.

        If a DESTRUCTIVE tool is requested the method stores the pending
        action and returns immediately so the caller can ask the user for
        confirmation.
        """
        result: dict[str, Any] = {
            "message": "",
            "tool_calls": [],
            "confirmation_required": False,
            "confirmation_details": None,
        }

        assistant_content = response.content
        self.conversation_history.append(
            {"role": "assistant", "content": assistant_content}
        )

        for block in assistant_content:
            if block.type == "text":
                result["message"] += block.text

            elif block.type == "tool_use":
                tool_name: str = block.name
                tool_input: dict = block.input
                tool_id: str = block.id

                logger.info(
                    "Claude requested tool=%s input=%s",
                    tool_name,
                    json.dumps(tool_input, default=str)[:200],
                )

                # -- Destructive? Ask for confirmation ---------------------
                if requires_confirmation(tool_name):
                    warning_parts: list[str] = []

                    if tool_name == "run_command":
                        is_dangerous, reason = check_dangerous_command(
                            tool_input.get("command", "")
                        )
                        if is_dangerous:
                            warning_parts.append(
                                f"\n\nWARNING: {reason}"
                            )

                    if tool_name == "deploy_config":
                        cmds = tool_input.get("commands", [])
                        for cmd in cmds:
                            is_dangerous, reason = check_dangerous_command(cmd)
                            if is_dangerous:
                                warning_parts.append(
                                    f"\n\nWARNING: {reason}"
                                )
                                break

                    self._pending_confirmation = {
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "tool_id": tool_id,
                    }
                    self._pending_confirmation_id = tool_id

                    result["confirmation_required"] = True
                    result["confirmation_details"] = {
                        "tool": tool_name,
                        "input": tool_input,
                        "safety_level": "DESTRUCTIVE",
                    }

                    extra = "".join(warning_parts)
                    result["message"] += (
                        f"{extra}\n\n"
                        "This operation requires your confirmation. "
                        "Type 'yes' to proceed or 'no' to cancel."
                    )
                    return result

                # -- Safe / Write: execute immediately ---------------------
                tool_result = await self._execute_tool(tool_name, tool_input)
                result["tool_calls"].append(
                    {
                        "tool": tool_name,
                        "input": tool_input,
                        "result": tool_result,
                    }
                )

                # Feed the result back so Claude can interpret it
                self.conversation_history.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": json.dumps(
                                    tool_result, default=str
                                ),
                            }
                        ],
                    }
                )

                # Ask Claude for follow-up (may trigger more tool calls)
                followup = await self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=SYSTEM_PROMPT,
                    tools=NETWORK_TOOLS,
                    messages=self.conversation_history,
                )
                followup_result = await self._process_response(followup)
                result["message"] = followup_result["message"]
                result["tool_calls"].extend(followup_result["tool_calls"])
                if followup_result["confirmation_required"]:
                    result["confirmation_required"] = True
                    result["confirmation_details"] = followup_result[
                        "confirmation_details"
                    ]
                return result

        return result

    async def _continue_with_tool_result(
        self, tool_id: str, tool_result: dict[str, Any]
    ) -> dict[str, Any]:
        """Resume the conversation after the user confirmed a destructive op.

        The tool has already been executed; we feed the result back to Claude
        and let it produce a final summary.
        """
        self.conversation_history.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": json.dumps(tool_result, default=str),
                    }
                ],
            }
        )

        followup = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            tools=NETWORK_TOOLS,
            messages=self.conversation_history,
        )

        result = await self._process_response(followup)
        result["tool_calls"].insert(
            0,
            {
                "tool": "confirmed_operation",
                "input": {},
                "result": tool_result,
            },
        )
        return result

    # ------------------------------------------------------------------
    # Tool dispatcher
    # ------------------------------------------------------------------

    async def _execute_tool(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        """Route a tool call to the appropriate handler method.

        Every execution is recorded in the audit log with the tool name,
        input parameters, result, and the safety level of the operation.
        """
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            logger.error("No handler for tool %s", tool_name)
            return {"error": f"Unknown tool: {tool_name}"}

        safety = get_safety_level(tool_name).value
        device_id = tool_input.get("device_id")

        try:
            result = await handler(tool_input)
            has_error = "error" in result
            # Log to audit trail
            if self._audit:
                await self._audit.log(
                    actor="ai-agent",
                    action_type=f"ai_tool_{safety}",
                    description=f"AI agent executed tool '{tool_name}'",
                    device_id=device_id,
                    details={
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "safety_level": safety,
                    },
                    result="failure" if has_error else "success",
                    after_state={"error": result["error"]} if has_error else None,
                )
            return result
        except Exception as exc:
            logger.exception("Tool execution error (%s): %s", tool_name, exc)
            if self._audit:
                try:
                    await self._audit.log(
                        actor="ai-agent",
                        action_type=f"ai_tool_{safety}",
                        description=f"AI agent tool '{tool_name}' raised an exception",
                        device_id=device_id,
                        details={
                            "tool_name": tool_name,
                            "tool_input": tool_input,
                            "safety_level": safety,
                            "exception": str(exc),
                        },
                        result="failure",
                    )
                except Exception:
                    logger.warning("Failed to write audit log for tool error")
            return {"error": str(exc)}

    # ==================================================================
    # Tool handlers -- each calls the appropriate operations back-end
    # ==================================================================

    # ---- list_devices ------------------------------------------------

    async def _tool_list_devices(self, params: dict) -> dict[str, Any]:
        """Return all devices, with optional filtering."""
        devices = await self.db.list_devices()

        tag = params.get("tag")
        status_filter = params.get("status")
        device_type = params.get("device_type")

        if tag:
            devices = [
                d for d in devices if tag in (d.get("tags") or [])
            ]
        if status_filter:
            devices = [
                d for d in devices if d.get("status") == status_filter
            ]
        if device_type:
            devices = [
                d for d in devices if d.get("device_type") == device_type
            ]

        # Build a concise summary for each device
        summary = []
        for d in devices:
            summary.append(
                {
                    "id": d.get("id", ""),
                    "hostname": d.get("hostname", ""),
                    "ip_address": d.get("ip_address", d.get("host", "")),
                    "device_type": d.get("device_type", ""),
                    "vendor": d.get("vendor", ""),
                    "status": d.get("status", "unknown"),
                    "tags": d.get("tags", []),
                }
            )

        return {"devices": summary, "total": len(summary)}

    # ---- get_device_status -------------------------------------------

    async def _tool_get_device_status(self, params: dict) -> dict[str, Any]:
        """Get detailed status for a single device.

        Tries to look up by ID first; if not found, searches by hostname.
        Then polls the device for live health data.
        """
        device_id = params["device_id"]

        # Try direct ID lookup
        device = await self.db.get_device(device_id)

        # Fallback: search by hostname
        if device is None:
            all_devices = await self.db.list_devices()
            for d in all_devices:
                if d.get("hostname", "").lower() == device_id.lower():
                    device = d
                    device_id = d["id"]
                    break

        if device is None:
            return {"error": f"Device '{params['device_id']}' not found"}

        # Gather live metrics if monitoring engine is available
        health_data: dict[str, Any] = {}
        if self.monitor:
            try:
                health_data = await self.monitor.poll_device(device_id)
            except Exception as exc:
                health_data = {"poll_error": str(exc)}

        return {
            "device": {
                "id": device.get("id", ""),
                "hostname": device.get("hostname", ""),
                "ip_address": device.get("ip_address", device.get("host", "")),
                "device_type": device.get("device_type", ""),
                "vendor": device.get("vendor", ""),
                "model": device.get("model", ""),
                "os_version": device.get("os_version", ""),
                "status": device.get("status", "unknown"),
                "tags": device.get("tags", []),
                "last_seen": device.get("last_seen", ""),
            },
            "health": health_data,
        }

    # ---- ping_device -------------------------------------------------

    async def _tool_ping_device(self, params: dict) -> dict[str, Any]:
        """Ping a target, either locally or from a remote device."""
        target = params["target"]
        count = params.get("count", 4)
        source_device_id = params.get("source_device_id")

        if source_device_id and self.troubleshooter:
            result = await self.troubleshooter.ping_test(
                target,
                count=count,
                source_device_id=source_device_id,
            )
            return {
                "target": target,
                "source": source_device_id,
                "result": result,
            }

        if self.troubleshooter:
            result = await self.troubleshooter.ping_test(
                target, count=count
            )
            return {
                "target": target,
                "source": "local",
                "result": result,
            }

        return {"error": "Troubleshooter not available"}

    # ---- traceroute --------------------------------------------------

    async def _tool_traceroute(self, params: dict) -> dict[str, Any]:
        """Run traceroute to a target."""
        target = params["target"]
        source_device_id = params.get("source_device_id")

        if self.troubleshooter is None:
            return {"error": "Troubleshooter not available"}

        if source_device_id:
            result = await self.troubleshooter.traceroute_test(
                target, source_device_id=source_device_id
            )
        else:
            result = await self.troubleshooter.traceroute_test(target)

        return {
            "target": target,
            "source": source_device_id or "local",
            "result": result,
        }

    # ---- show_interfaces ---------------------------------------------

    async def _tool_show_interfaces(self, params: dict) -> dict[str, Any]:
        """Retrieve interface details for a device."""
        device_id = params["device_id"]

        if self.troubleshooter:
            try:
                result = await self.troubleshooter.check_interface_errors(
                    device_id
                )
                return {
                    "device_id": device_id,
                    "interfaces": result,
                }
            except Exception as exc:
                logger.warning(
                    "Troubleshooter.check_interface_errors failed for %s: %s",
                    device_id,
                    exc,
                )

        # Fallback: use monitor to poll device (which collects interfaces)
        if self.monitor:
            poll = await self.monitor.poll_device(device_id)
            interfaces = poll.get("interfaces", [])
            return {
                "device_id": device_id,
                "interfaces": interfaces,
            }

        return {"error": "No troubleshooter or monitor available"}

    # ---- get_config --------------------------------------------------

    async def _tool_get_config(self, params: dict) -> dict[str, Any]:
        """Retrieve the running or startup configuration."""
        device_id = params["device_id"]
        config_type = params.get("config_type", "running")

        if self.config_manager is None:
            return {"error": "ConfigManager not available"}

        # Get config history -- if we have a recent backup, return that
        # Otherwise trigger a fresh backup
        history = await self.config_manager.get_config_history(
            device_id, limit=1
        )
        if history:
            latest = history[0]
            return {
                "device_id": device_id,
                "config_type": config_type,
                "config_text": latest.get("config_text", ""),
                "backup_id": latest.get("id", ""),
                "backed_up_at": latest.get("created_at", ""),
                "config_hash": latest.get("config_hash", ""),
            }

        # No backup exists yet -- take one now
        try:
            backup = await self.config_manager.backup_config(device_id)
            return {
                "device_id": device_id,
                "config_type": config_type,
                "config_text": backup.get("config_text", ""),
                "backup_id": backup.get("id", ""),
                "backed_up_at": backup.get("created_at", ""),
                "config_hash": backup.get("config_hash", ""),
            }
        except Exception as exc:
            return {"error": f"Failed to retrieve config: {exc}"}

    # ---- diff_configs ------------------------------------------------

    async def _tool_diff_configs(self, params: dict) -> dict[str, Any]:
        """Diff two configuration backups."""
        if self.config_manager is None:
            return {"error": "ConfigManager not available"}

        backup_id_1 = params["backup_id_1"]
        backup_id_2 = params["backup_id_2"]

        try:
            diff_text = await self.config_manager.diff_configs(
                backup_id_1, backup_id_2
            )
            if not diff_text:
                return {
                    "backup_id_1": backup_id_1,
                    "backup_id_2": backup_id_2,
                    "diff": "",
                    "summary": "No differences found -- configurations are identical.",
                }
            return {
                "backup_id_1": backup_id_1,
                "backup_id_2": backup_id_2,
                "diff": diff_text,
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ---- backup_config -----------------------------------------------

    async def _tool_backup_config(self, params: dict) -> dict[str, Any]:
        """Create a backup of a device's config."""
        if self.config_manager is None:
            return {"error": "ConfigManager not available"}

        device_id = params["device_id"]
        try:
            backup = await self.config_manager.backup_config(device_id)
            return {
                "device_id": device_id,
                "backup_id": backup.get("id", ""),
                "config_hash": backup.get("config_hash", ""),
                "file_path": backup.get("file_path", ""),
                "created_at": backup.get("created_at", ""),
                "status": "success",
            }
        except Exception as exc:
            return {"error": f"Backup failed: {exc}"}

    # ---- check_port --------------------------------------------------

    async def _tool_check_port(self, params: dict) -> dict[str, Any]:
        """Check TCP port reachability."""
        if self.troubleshooter is None:
            return {"error": "Troubleshooter not available"}

        target = params["target"]
        port = params["port"]
        timeout = params.get("timeout", 5)

        try:
            result = await self.troubleshooter.port_check(
                target, port, timeout=timeout
            )
            return {
                "target": target,
                "port": port,
                "result": result,
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ---- get_metrics -------------------------------------------------

    async def _tool_get_metrics(self, params: dict) -> dict[str, Any]:
        """Retrieve stored metrics for a device."""
        if self.monitor is None:
            return {"error": "MonitoringEngine not available"}

        device_id = params["device_id"]
        metric_name = params.get("metric_name")
        hours = params.get("hours", 24)

        try:
            metrics = await self.monitor.get_device_metrics(
                device_id,
                metric_name=metric_name,
                hours=hours,
            )

            # Build a compact summary
            if not metrics:
                return {
                    "device_id": device_id,
                    "metric_name": metric_name,
                    "hours": hours,
                    "data_points": 0,
                    "metrics": [],
                    "summary": "No metrics found for the specified time range.",
                }

            # Compute min/max/avg if numeric
            values = [
                m.get("metric_value", 0.0)
                for m in metrics
                if m.get("metric_value") is not None
            ]
            summary_stats: dict[str, Any] = {}
            if values:
                summary_stats = {
                    "min": round(min(values), 2),
                    "max": round(max(values), 2),
                    "avg": round(sum(values) / len(values), 2),
                    "latest": round(values[-1], 2),
                }

            return {
                "device_id": device_id,
                "metric_name": metric_name or "all",
                "hours": hours,
                "data_points": len(metrics),
                "statistics": summary_stats,
                "metrics": metrics[-50:],  # Cap to last 50 data points
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ---- get_alerts --------------------------------------------------

    async def _tool_get_alerts(self, params: dict) -> dict[str, Any]:
        """Retrieve alerts from the database."""
        status_filter = params.get("status", "active")

        try:
            alerts = await self.db.get_alerts(status=status_filter)
            return {
                "status_filter": status_filter,
                "total": len(alerts),
                "alerts": alerts,
            }
        except Exception as exc:
            return {"error": str(exc)}

    # ---- deploy_config (DESTRUCTIVE) ---------------------------------

    async def _tool_deploy_config(self, params: dict) -> dict[str, Any]:
        """Deploy configuration commands to a device."""
        if self.config_manager is None:
            return {"error": "ConfigManager not available"}

        device_id = params["device_id"]
        commands = params["commands"]
        dry_run = params.get("dry_run", False)

        try:
            result = await self.config_manager.deploy_config(
                device_id, commands, dry_run=dry_run
            )
            return {
                "device_id": device_id,
                "deploy_id": result.get("id", ""),
                "status": result.get("status", ""),
                "result": result.get("result", ""),
                "commands_sent": commands,
                "dry_run": dry_run,
                "created_at": result.get("created_at", ""),
            }
        except Exception as exc:
            return {"error": f"Deployment failed: {exc}"}

    # ---- discover_network (WRITE) ------------------------------------

    async def _tool_discover_network(self, params: dict) -> dict[str, Any]:
        """Scan a subnet for devices via SNMP."""
        if self.discovery is None:
            return {"error": "NetworkDiscovery not available"}

        subnet = params["subnet"]
        community = params.get("community", "public")

        try:
            results = await self.discovery.scan_subnet(
                subnet, community=community
            )
            return {
                "subnet": subnet,
                "community": community,
                "discovered": len(results),
                "devices": results,
            }
        except Exception as exc:
            return {"error": f"Discovery failed: {exc}"}

    # ---- run_command (DESTRUCTIVE) -----------------------------------

    async def _tool_run_command(self, params: dict) -> dict[str, Any]:
        """Execute an arbitrary CLI command on a device.

        This connects to the device, runs the command, and returns the
        output.  The caller is responsible for safety gating (handled by
        the confirmation flow in ``_process_response``).
        """
        device_id = params["device_id"]
        command = params["command"]

        # Resolve the device record
        device_record = await self.db.get_device(device_id)
        if device_record is None:
            return {"error": f"Device '{device_id}' not found"}

        # Try troubleshooter first (it handles connection lifecycle)
        if self.troubleshooter:
            try:
                result = await self.troubleshooter.check_device_health(
                    device_id
                )
                # For arbitrary commands we need to connect directly
            except Exception:
                pass

        # Connect via credential manager + device registry
        if self.credential_manager is None:
            return {"error": "CredentialManager not available"}

        try:
            from core.credentials import CredentialManager
            from devices.registry import get_device_class

            creds = await self.credential_manager.get_credentials(
                device_record.get("credential_id", "")
            )
            device_cls = get_device_class(device_record["device_type"])
            device = device_cls(
                host=device_record.get("host", device_record.get("ip_address", "")),
                username=creds.get("username", ""),
                password=creds.get("password", ""),
                port=device_record.get("port", 22),
                device_type=device_record["device_type"],
                enable_secret=creds.get("enable_secret", ""),
                ssh_key_path=creds.get("ssh_key_path", ""),
                timeout=device_record.get("timeout", 30),
            )
            await device.connect()
            try:
                output = await device.send_command(command, timeout=60)
            finally:
                await device.disconnect()

            return {
                "device_id": device_id,
                "command": command,
                "output": output,
                "status": "success",
            }
        except Exception as exc:
            return {
                "device_id": device_id,
                "command": command,
                "error": str(exc),
                "status": "failed",
            }

    # ---- list_sites (SAFE) -------------------------------------------

    async def _tool_list_sites(self, params: dict) -> dict[str, Any]:
        """List all sites."""
        if self.site_manager is None:
            from operations.sites import SiteManager
            self.site_manager = SiteManager(self.db)
        sites = await self.site_manager.list_sites(region=params.get("region"))
        return {"sites": sites, "total": len(sites)}

    # ---- get_site_summary (SAFE) -------------------------------------

    async def _tool_get_site_summary(self, params: dict) -> dict[str, Any]:
        """Get site summary with device counts."""
        if self.site_manager is None:
            from operations.sites import SiteManager
            self.site_manager = SiteManager(self.db)
        return await self.site_manager.get_site_summary(params["site_id"])

    # ---- create_site (WRITE) -----------------------------------------

    async def _tool_create_site(self, params: dict) -> dict[str, Any]:
        """Create a new site."""
        if self.site_manager is None:
            from operations.sites import SiteManager
            self.site_manager = SiteManager(self.db)
        return await self.site_manager.create_site(
            name=params["name"],
            location=params.get("location"),
            region=params.get("region"),
            description=params.get("description"),
            contact=params.get("contact"),
        )

    # ---- discover_topology (WRITE) -----------------------------------

    async def _tool_discover_topology(self, params: dict) -> dict[str, Any]:
        from operations.topology import TopologyMapper
        mapper = TopologyMapper(self.db)
        return await mapper.discover_topology(device_id=params.get("device_id"))

    # ---- get_topology (SAFE) -----------------------------------------

    async def _tool_get_topology(self, params: dict) -> dict[str, Any]:
        from operations.topology import TopologyMapper
        mapper = TopologyMapper(self.db)
        return await mapper.get_topology_graph()

    # ---- get_device_neighbors (SAFE) ---------------------------------

    async def _tool_get_device_neighbors(self, params: dict) -> dict[str, Any]:
        from operations.topology import TopologyMapper
        mapper = TopologyMapper(self.db)
        neighbors = await mapper.get_device_neighbors(params["device_id"])
        return {"neighbors": neighbors, "total": len(neighbors)}

    # ---- check_firmware_status (SAFE) --------------------------------

    async def _tool_check_firmware_status(self, params: dict) -> dict[str, Any]:
        from operations.firmware import FirmwareManager
        mgr = FirmwareManager(self.db)
        return await mgr.check_compliance()

    # ---- list_eol_devices (SAFE) -------------------------------------

    async def _tool_list_eol_devices(self, params: dict) -> dict[str, Any]:
        from operations.firmware import FirmwareManager
        mgr = FirmwareManager(self.db)
        devices = await mgr.get_eol_devices()
        return {"devices": devices, "total": len(devices)}

    # ---- list_firmware_catalog (SAFE) --------------------------------

    async def _tool_list_firmware_catalog(self, params: dict) -> dict[str, Any]:
        from operations.firmware import FirmwareManager
        mgr = FirmwareManager(self.db)
        catalog = await mgr.list_catalog()
        return {"catalog": catalog, "total": len(catalog)}

    # ---- list_subnets (SAFE) -----------------------------------------

    async def _tool_list_subnets(self, params: dict) -> dict[str, Any]:
        from operations.ipam import IPAMManager
        mgr = IPAMManager(self.db)
        subnets = await mgr.list_subnets(site_id=params.get("site_id"))
        return {"subnets": subnets, "total": len(subnets)}

    # ---- get_subnet_utilization (SAFE) -------------------------------

    async def _tool_get_subnet_utilization(self, params: dict) -> dict[str, Any]:
        from operations.ipam import IPAMManager
        mgr = IPAMManager(self.db)
        return await mgr.get_utilization(params["subnet_id"])

    # ---- find_free_ips (SAFE) ----------------------------------------

    async def _tool_find_free_ips(self, params: dict) -> dict[str, Any]:
        from operations.ipam import IPAMManager
        mgr = IPAMManager(self.db)
        ips = await mgr.find_free_ips(params["subnet_id"], count=params.get("count", 5))
        return {"free_ips": ips, "total": len(ips)}

    # ---- scan_arp_table (WRITE) --------------------------------------

    async def _tool_scan_arp_table(self, params: dict) -> dict[str, Any]:
        from operations.ipam import IPAMManager
        mgr = IPAMManager(self.db)
        return await mgr.scan_subnet(params["subnet_id"])

    # ---- get_traffic_trends (SAFE) -----------------------------------

    async def _tool_get_traffic_trends(self, params: dict) -> dict[str, Any]:
        from operations.traffic import TrafficAnalyzer
        analyzer = TrafficAnalyzer(self.db)
        return await analyzer.get_traffic_trends(params["device_id"], hours=params.get("hours", 24))

    # ---- get_top_interfaces (SAFE) -----------------------------------

    async def _tool_get_top_interfaces(self, params: dict) -> dict[str, Any]:
        from operations.traffic import TrafficAnalyzer
        analyzer = TrafficAnalyzer(self.db)
        return {"top_interfaces": await analyzer.get_top_talkers(count=params.get("count", 10))}

    # ---- search_syslog (SAFE) ----------------------------------------

    async def _tool_search_syslog(self, params: dict) -> dict[str, Any]:
        from operations.syslog import SyslogReceiver
        receiver = SyslogReceiver(self.db)
        messages = await receiver.search_messages(
            device_id=params.get("device_id"),
            min_severity=params.get("severity"),
            query=params.get("query"),
            limit=params.get("limit", 50),
        )
        return {"messages": messages, "total": len(messages)}

    # ---- get_syslog_stats (SAFE) -------------------------------------

    async def _tool_get_syslog_stats(self, params: dict) -> dict[str, Any]:
        from operations.syslog import SyslogReceiver
        receiver = SyslogReceiver(self.db)
        return await receiver.get_stats()

    # ---- run_compliance_check (WRITE) --------------------------------

    async def _tool_run_compliance_check(self, params: dict) -> dict[str, Any]:
        from operations.compliance import ComplianceEngine
        engine = ComplianceEngine(self.db)
        return await engine.run_check(params["device_id"], params.get("ruleset", "cis-network"))

    # ---- get_compliance_report (SAFE) --------------------------------

    async def _tool_get_compliance_report(self, params: dict) -> dict[str, Any]:
        from operations.compliance import ComplianceEngine
        engine = ComplianceEngine(self.db)
        report = await engine.get_latest_report(params["device_id"])
        if not report:
            return {"error": "No compliance report found for this device"}
        return report

    # ---- list_compliance_rules (SAFE) --------------------------------

    async def _tool_list_compliance_rules(self, params: dict) -> dict[str, Any]:
        from operations.compliance import ComplianceEngine
        engine = ComplianceEngine(self.db)
        rules = engine.list_rules(params.get("ruleset", "cis-network"))
        return {"rules": rules, "total": len(rules)}

    # ---- create_change_request (WRITE) -------------------------------

    async def _tool_create_change_request(self, params: dict) -> dict[str, Any]:
        from operations.change_management import ChangeManager
        mgr = ChangeManager(self.db)
        return await mgr.create_request(
            device_id=params["device_id"],
            title=params["title"],
            config_commands=params["config_commands"],
            requested_by=params.get("requested_by", "ai-agent"),
            priority=params.get("priority", "normal"),
            notes=params.get("notes"),
        )

    # ---- list_change_requests (SAFE) ---------------------------------

    async def _tool_list_change_requests(self, params: dict) -> dict[str, Any]:
        from operations.change_management import ChangeManager
        mgr = ChangeManager(self.db)
        changes = await mgr.list_requests(status=params.get("status"), device_id=params.get("device_id"))
        return {"changes": changes, "total": len(changes)}

    # ---- approve_change (DESTRUCTIVE) --------------------------------

    async def _tool_approve_change(self, params: dict) -> dict[str, Any]:
        approver = params.get("approved_by", "")
        # Prevent the AI agent from approving its own change requests.
        # Changes must be approved by an identified human operator.
        if not approver or approver.lower() in ("ai-agent", "ai_agent", "agent", "bot", "system"):
            return {
                "error": "Change requests cannot be approved by the AI agent. "
                "A human operator must approve changes. Please ask a team member "
                "to approve this change via the web dashboard or CLI.",
                "change_id": params.get("change_id"),
                "status": "rejected",
            }
        from operations.change_management import ChangeManager
        mgr = ChangeManager(self.db)
        return await mgr.approve_request(params["change_id"], approved_by=approver)

    # ---- reject_change (WRITE) ---------------------------------------

    async def _tool_reject_change(self, params: dict) -> dict[str, Any]:
        from operations.change_management import ChangeManager
        mgr = ChangeManager(self.db)
        return await mgr.reject_request(params["change_id"], reason=params.get("reason"))

    # ---- execute_change (DESTRUCTIVE) --------------------------------

    async def _tool_execute_change(self, params: dict) -> dict[str, Any]:
        from operations.change_management import ChangeManager
        mgr = ChangeManager(self.db, getattr(self, "config_manager", None))
        return await mgr.execute_change(params["change_id"])

    # ---- rollback_change (DESTRUCTIVE) -------------------------------

    async def _tool_rollback_change(self, params: dict) -> dict[str, Any]:
        from operations.change_management import ChangeManager
        mgr = ChangeManager(self.db, getattr(self, "config_manager", None))
        return await mgr.rollback_change(params["change_id"], executed_by=params.get("executed_by", "ai-agent"))

    # ---- get_change_history (SAFE) -----------------------------------

    async def _tool_get_change_history(self, params: dict) -> dict[str, Any]:
        from operations.change_management import ChangeManager
        mgr = ChangeManager(self.db)
        history = await mgr.get_change_history(params["device_id"], limit=params.get("limit", 50))
        return {"device_id": params["device_id"], "changes": history, "total": len(history)}

    # ---- list_pending_approvals (SAFE) -------------------------------

    async def _tool_list_pending_approvals(self, params: dict) -> dict[str, Any]:
        from operations.change_management import ChangeManager
        mgr = ChangeManager(self.db)
        pending = await mgr.list_pending()
        return {"pending_changes": pending, "total": len(pending)}

    # ---- rotate_credential (DESTRUCTIVE) -----------------------------

    async def _tool_rotate_credential(self, params: dict) -> dict[str, Any]:
        from operations.credential_rotation import CredentialRotator
        rotator = CredentialRotator(self.db)
        return await rotator.rotate_credential(params["credential_id"], initiated_by=params.get("initiated_by", "ai-agent"))

    # ---- schedule_rotation (DESTRUCTIVE) -----------------------------

    async def _tool_schedule_rotation(self, params: dict) -> dict[str, Any]:
        from operations.credential_rotation import CredentialRotator
        rotator = CredentialRotator(self.db)
        return await rotator.schedule_rotation(params["credential_id"], params["cron_expression"])

    # ---- verify_credentials (DESTRUCTIVE) ----------------------------

    async def _tool_verify_credentials(self, params: dict) -> dict[str, Any]:
        from operations.credential_rotation import CredentialRotator
        rotator = CredentialRotator(self.db)
        return await rotator.verify_all_devices(params["credential_id"])

    # ---- get_firewall_rules (SAFE) -----------------------------------

    async def _tool_get_firewall_rules(self, params: dict) -> dict[str, Any]:
        from operations.firewall import FirewallManager
        mgr = FirewallManager(self.db)
        rules = await mgr.get_rules(params["device_id"])
        return {"rules": rules, "count": len(rules)}

    # ---- get_nat_rules (SAFE) ----------------------------------------

    async def _tool_get_nat_rules(self, params: dict) -> dict[str, Any]:
        from operations.firewall import FirewallManager
        mgr = FirewallManager(self.db)
        rules = await mgr.get_nat_rules(params["device_id"])
        return {"nat_rules": rules, "count": len(rules)}

    # ---- get_firewall_zones (SAFE) -----------------------------------

    async def _tool_get_firewall_zones(self, params: dict) -> dict[str, Any]:
        from operations.firewall import FirewallManager
        mgr = FirewallManager(self.db)
        zones = await mgr.get_zones(params["device_id"])
        return {"zones": zones, "count": len(zones)}

    # ---- get_firewall_objects (SAFE) ---------------------------------

    async def _tool_get_firewall_objects(self, params: dict) -> dict[str, Any]:
        from operations.firewall import FirewallManager
        mgr = FirewallManager(self.db)
        objects = await mgr.get_objects(params["device_id"])
        return {"objects": objects, "count": len(objects)}

    # ---- get_firewall_summary (SAFE) ---------------------------------

    async def _tool_get_firewall_summary(self, params: dict) -> dict[str, Any]:
        from operations.firewall import FirewallManager
        mgr = FirewallManager(self.db)
        return await mgr.get_summary(params["device_id"])

    # ---- sync_firewall_rules (WRITE) ---------------------------------

    async def _tool_sync_firewall_rules(self, params: dict) -> dict[str, Any]:
        from operations.firewall import FirewallManager
        mgr = FirewallManager(self.db, self.config_manager)
        return await mgr.sync_rules(params["device_id"])

    # ---- create_address_object (WRITE) -------------------------------

    async def _tool_create_address_object(self, params: dict) -> dict[str, Any]:
        from operations.firewall import FirewallManager
        mgr = FirewallManager(self.db, self.config_manager)
        obj_data = {
            "name": params["name"],
            "object_type": params["object_type"],
        }
        if params.get("value"):
            obj_data["value"] = params["value"]
        if params.get("members"):
            obj_data["members"] = params["members"]
        if params.get("description"):
            obj_data["description"] = params["description"]
        return await mgr.create_object(params["device_id"], obj_data)

    # ---- create_service_object (WRITE) -------------------------------

    async def _tool_create_service_object(self, params: dict) -> dict[str, Any]:
        from operations.firewall import FirewallManager
        mgr = FirewallManager(self.db, self.config_manager)
        obj_data = {
            "name": params["name"],
            "object_type": params["object_type"],
        }
        if params.get("value"):
            obj_data["value"] = params["value"]
        if params.get("members"):
            obj_data["members"] = params["members"]
        if params.get("description"):
            obj_data["description"] = params["description"]
        return await mgr.create_object(params["device_id"], obj_data)

    # ---- create_firewall_rule (DESTRUCTIVE) --------------------------

    async def _tool_create_firewall_rule(self, params: dict) -> dict[str, Any]:
        from operations.firewall import FirewallManager
        mgr = FirewallManager(self.db, self.config_manager)
        rule_data = {
            "name": params["name"],
            "action": params.get("action", "deny"),
            "log_enabled": params.get("log_enabled", False),
        }
        for field in ("source_zone", "dest_zone", "comment"):
            if params.get(field):
                rule_data[field] = params[field]
        for field in ("source_addresses", "dest_addresses", "services"):
            if params.get(field):
                rule_data[field] = params[field]
        return await mgr.create_rule(params["device_id"], rule_data)

    # ---- delete_firewall_rule (DESTRUCTIVE) --------------------------

    async def _tool_delete_firewall_rule(self, params: dict) -> dict[str, Any]:
        from operations.firewall import FirewallManager
        mgr = FirewallManager(self.db, self.config_manager)
        return await mgr.delete_rule(params["device_id"], params["rule_id"])

    # ---- modify_firewall_rule (DESTRUCTIVE) --------------------------

    async def _tool_modify_firewall_rule(self, params: dict) -> dict[str, Any]:
        from operations.firewall import FirewallManager
        mgr = FirewallManager(self.db, self.config_manager)
        return await mgr.modify_rule(
            params["device_id"], params["rule_id"], params.get("changes", {})
        )

    # ---- create_nat_rule (DESTRUCTIVE) -------------------------------

    async def _tool_create_nat_rule(self, params: dict) -> dict[str, Any]:
        from operations.firewall import FirewallManager
        mgr = FirewallManager(self.db, self.config_manager)
        rule_data = {"name": params["name"], "nat_type": params["nat_type"]}
        for field in (
            "source_zone", "dest_zone", "original_source", "original_dest",
            "original_service", "translated_source", "translated_dest",
            "translated_service",
        ):
            if params.get(field):
                rule_data[field] = params[field]
        return await mgr.create_nat_rule(params["device_id"], rule_data)

    # ------------------------------------------------------------------
    # ACL Management Handlers
    # ------------------------------------------------------------------

    async def _tool_list_acls(self, params: dict) -> dict[str, Any]:
        from operations.acl import ACLManager
        mgr = ACLManager(self.db)
        acls = await mgr.list_acls(params["device_id"])
        return {"acls": acls, "count": len(acls)}

    async def _tool_sync_acls(self, params: dict) -> dict[str, Any]:
        from operations.acl import ACLManager
        mgr = ACLManager(self.db, self.config_manager)
        return await mgr.sync_acls(params["device_id"])

    async def _tool_create_acl(self, params: dict) -> dict[str, Any]:
        from operations.acl import ACLManager
        mgr = ACLManager(self.db, self.config_manager)
        return await mgr.create_acl(
            params["device_id"], params["name"],
            params.get("acl_type", "extended"), params.get("description"),
        )

    async def _tool_delete_acl(self, params: dict) -> dict[str, Any]:
        from operations.acl import ACLManager
        mgr = ACLManager(self.db, self.config_manager)
        return await mgr.delete_acl(params["device_id"], params["acl_name"])

    async def _tool_add_acl_entry(self, params: dict) -> dict[str, Any]:
        from operations.acl import ACLManager
        mgr = ACLManager(self.db, self.config_manager)
        return await mgr.add_entry(
            device_id=params["device_id"],
            acl_name=params["acl_name"],
            sequence=params["sequence"],
            action=params["action"],
            protocol=params.get("protocol", "ip"),
            source=params.get("source", "any"),
            destination=params.get("destination", "any"),
            source_wildcard=params.get("source_wildcard"),
            dest_wildcard=params.get("dest_wildcard"),
            dest_port=params.get("dest_port"),
            log_enabled=params.get("log_enabled", False),
        )

    async def _tool_remove_acl_entry(self, params: dict) -> dict[str, Any]:
        from operations.acl import ACLManager
        mgr = ACLManager(self.db, self.config_manager)
        return await mgr.remove_entry(params["device_id"], params["acl_name"], params["sequence"])

    async def _tool_bind_acl(self, params: dict) -> dict[str, Any]:
        from operations.acl import ACLManager
        mgr = ACLManager(self.db, self.config_manager)
        return await mgr.bind_acl(
            params["device_id"], params["acl_name"],
            params["interface"], params.get("direction", "in"),
        )

    async def _tool_unbind_acl(self, params: dict) -> dict[str, Any]:
        from operations.acl import ACLManager
        mgr = ACLManager(self.db, self.config_manager)
        return await mgr.unbind_acl(
            params["device_id"], params["acl_name"],
            params["interface"], params.get("direction", "in"),
        )

    # ------------------------------------------------------------------
    # Routing Management Handlers
    # ------------------------------------------------------------------

    # ---- get_routing_table (SAFE) ------------------------------------

    async def _tool_get_routing_table(self, params: dict) -> dict[str, Any]:
        from operations.routing import RoutingManager
        mgr = RoutingManager(self.db)
        routes = await mgr.get_routing_table(params["device_id"], params.get("protocol"))
        return {"routes": routes, "count": len(routes)}

    # ---- sync_routes (WRITE) -----------------------------------------

    async def _tool_sync_routes(self, params: dict) -> dict[str, Any]:
        from operations.routing import RoutingManager
        mgr = RoutingManager(self.db, self.config_manager)
        return await mgr.sync_routes(params["device_id"])

    # ---- create_static_route (DESTRUCTIVE) ---------------------------

    async def _tool_create_static_route(self, params: dict) -> dict[str, Any]:
        from operations.routing import RoutingManager
        mgr = RoutingManager(self.db, self.config_manager)
        return await mgr.create_static_route(
            device_id=params["device_id"],
            destination=params["destination"],
            prefix_length=params["prefix_length"],
            next_hop=params["next_hop"],
            metric=params.get("metric", 0),
            vrf=params.get("vrf"),
        )

    # ---- delete_static_route (DESTRUCTIVE) ---------------------------

    async def _tool_delete_static_route(self, params: dict) -> dict[str, Any]:
        from operations.routing import RoutingManager
        mgr = RoutingManager(self.db, self.config_manager)
        return await mgr.delete_static_route(
            params["device_id"], params["destination"],
            params["prefix_length"], params["next_hop"],
        )

    # ---- get_ospf_status (SAFE) --------------------------------------

    async def _tool_get_ospf_status(self, params: dict) -> dict[str, Any]:
        from operations.routing import RoutingManager
        mgr = RoutingManager(self.db)
        return await mgr.get_ospf_status(params["device_id"])

    # ---- sync_ospf (WRITE) -------------------------------------------

    async def _tool_sync_ospf(self, params: dict) -> dict[str, Any]:
        from operations.routing import RoutingManager
        mgr = RoutingManager(self.db, self.config_manager)
        return await mgr.sync_ospf(params["device_id"])

    # ---- configure_ospf (DESTRUCTIVE) --------------------------------

    async def _tool_configure_ospf(self, params: dict) -> dict[str, Any]:
        from operations.routing import RoutingManager
        mgr = RoutingManager(self.db, self.config_manager)
        return await mgr.configure_ospf(
            device_id=params["device_id"],
            process_id=params.get("process_id", 1),
            router_id=params.get("router_id"),
            networks=params.get("networks"),
        )

    # ---- add_ospf_network (DESTRUCTIVE) ------------------------------

    async def _tool_add_ospf_network(self, params: dict) -> dict[str, Any]:
        from operations.routing import RoutingManager
        mgr = RoutingManager(self.db, self.config_manager)
        return await mgr.add_ospf_network(
            device_id=params["device_id"],
            network=params["network"],
            wildcard=params["wildcard"],
            area=params.get("area", "0"),
        )

    # ------------------------------------------------------------------
    # VLAN Management Handlers
    # ------------------------------------------------------------------

    # ---- list_vlans (SAFE) -------------------------------------------

    async def _tool_list_vlans(self, params: dict) -> dict[str, Any]:
        from operations.vlan import VlanManager
        mgr = VlanManager(self.db)
        vlans = await mgr.list_vlans(params["device_id"])
        return {"vlans": vlans, "count": len(vlans)}

    # ---- get_vlan_summary (SAFE) -------------------------------------

    async def _tool_get_vlan_summary(self, params: dict) -> dict[str, Any]:
        from operations.vlan import VlanManager
        mgr = VlanManager(self.db)
        return await mgr.get_vlan_summary(params["device_id"])

    # ---- sync_vlans (WRITE) ------------------------------------------

    async def _tool_sync_vlans(self, params: dict) -> dict[str, Any]:
        from operations.vlan import VlanManager
        mgr = VlanManager(self.db, self.config_manager)
        return await mgr.sync_vlans(params["device_id"])

    # ---- create_vlan (DESTRUCTIVE) -----------------------------------

    async def _tool_create_vlan(self, params: dict) -> dict[str, Any]:
        from operations.vlan import VlanManager
        mgr = VlanManager(self.db, self.config_manager)
        return await mgr.create_vlan(
            device_id=params["device_id"],
            vlan_id=params["vlan_id"],
            name=params.get("name"),
            description=params.get("description"),
        )

    # ---- delete_vlan (DESTRUCTIVE) -----------------------------------

    async def _tool_delete_vlan(self, params: dict) -> dict[str, Any]:
        from operations.vlan import VlanManager
        mgr = VlanManager(self.db, self.config_manager)
        return await mgr.delete_vlan(params["device_id"], params["vlan_id"])

    # ---- assign_vlan_interface (DESTRUCTIVE) -------------------------

    async def _tool_assign_vlan_interface(self, params: dict) -> dict[str, Any]:
        from operations.vlan import VlanManager
        mgr = VlanManager(self.db, self.config_manager)
        return await mgr.assign_interface(
            device_id=params["device_id"],
            vlan_id=params["vlan_id"],
            interface=params["interface"],
            mode=params.get("mode", "access"),
        )

    # ---- Serial Console tools ----------------------------------------

    def _get_serial_manager(self):
        if self.serial_manager is None:
            from operations.serial_console import SerialConsoleManager
            self.serial_manager = SerialConsoleManager(
                self.db, self.credential_manager
            )
        return self.serial_manager

    async def _tool_list_serial_ports(self, params: dict) -> dict[str, Any]:
        mgr = self._get_serial_manager()
        ports = await mgr.list_serial_ports()
        return {"ports": ports, "total": len(ports)}

    async def _tool_serial_connect(self, params: dict) -> dict[str, Any]:
        mgr = self._get_serial_manager()
        return await mgr.connect_serial(params["device_id"])

    async def _tool_serial_send_command(self, params: dict) -> dict[str, Any]:
        mgr = self._get_serial_manager()
        return await mgr.send_command(
            params["device_id"],
            params["command"],
            timeout=params.get("timeout", 30),
        )

    async def _tool_serial_send_config(self, params: dict) -> dict[str, Any]:
        mgr = self._get_serial_manager()
        return await mgr.send_config(
            params["device_id"], params["commands"]
        )

    async def _tool_serial_send_break(self, params: dict) -> dict[str, Any]:
        mgr = self._get_serial_manager()
        return await mgr.send_break(
            params["device_id"],
            duration=params.get("duration", 0.5),
        )

    async def _tool_serial_get_facts(self, params: dict) -> dict[str, Any]:
        mgr = self._get_serial_manager()
        return await mgr.get_facts(params["device_id"])

    async def _tool_serial_disconnect(self, params: dict) -> dict[str, Any]:
        mgr = self._get_serial_manager()
        return await mgr.disconnect_serial(params["device_id"])
