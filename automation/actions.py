"""Action registry -- maps action type strings to fully-implemented handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

import httpx

from core.database import Database
from core.credentials import CredentialManager
from devices.registry import get_device_class
from alerts.engine import AlertEngine
from operations.config_manager import ConfigManager
from operations.monitor import MonitoringEngine
from operations.troubleshoot import Troubleshooter
from operations.discovery import NetworkDiscovery

from .audit import AuditLogger
from .exceptions import ActionError

logger = logging.getLogger(__name__)


class ActionRegistry:
    """Resolves action type strings to concrete async handler methods and
    executes them with full error handling.

    Each handler receives a ``params`` dict (template-resolved) and a
    ``context`` dict carrying execution-scoped state such as ``device_id``,
    ``alert_id``, and accumulated output variables.
    """

    def __init__(
        self,
        db: Database,
        config_manager: ConfigManager,
        monitor: MonitoringEngine,
        troubleshooter: Troubleshooter,
        discovery: NetworkDiscovery,
        credential_manager: CredentialManager,
        alert_engine: AlertEngine,
        audit_logger: AuditLogger,
    ) -> None:
        self._db = db
        self._config_manager = config_manager
        self._monitor = monitor
        self._troubleshooter = troubleshooter
        self._discovery = discovery
        self._credential_manager = credential_manager
        self._alert_engine = alert_engine
        self._audit_logger = audit_logger

        # Map action type strings to handler coroutines.
        self._handlers: dict[
            str, Callable[[dict[str, Any], dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]
        ] = {
            "run_command": self._action_run_command,
            "poll_device": self._action_poll_device,
            "backup_config": self._action_backup_config,
            "backup_all": self._action_backup_all,
            "deploy_config": self._action_deploy_config,
            "rollback_config": self._action_rollback_config,
            "check_interface_errors": self._action_check_interface_errors,
            "check_device_health": self._action_check_device_health,
            "ping_test": self._action_ping_test,
            "check_port": self._action_check_port,
            "scan_subnet": self._action_scan_subnet,
            "notify": self._action_notify,
            "log": self._action_log,
            "wait": self._action_wait,
            "condition": self._action_condition,
            "escalate": self._action_escalate,
            "resolve_alert": self._action_resolve_alert,
            "http_request": self._action_http_request,
        }

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def execute(
        self,
        action_type: str,
        params: dict[str, Any],
        context: dict[str, Any],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Look up and execute the handler for *action_type*.

        If *dry_run* is ``True`` the action is not actually executed; instead a
        summary of what would happen is returned.

        Returns
        -------
        dict
            The result from the handler, or ``{"error": ...}`` on failure.
        """
        handler = self._handlers.get(action_type)
        if handler is None:
            return {"error": f"Unknown action type: {action_type!r}"}

        if dry_run:
            logger.info("[DRY-RUN] Would execute %s with params=%s", action_type, params)
            return {
                "dry_run": True,
                "action": action_type,
                "params": params,
            }

        try:
            result = await handler(params, context)
            return result
        except Exception as exc:
            logger.exception("Action %s failed: %s", action_type, exc)
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # 1. run_command
    # ------------------------------------------------------------------

    async def _action_run_command(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Connect to a device, execute a CLI command, and return output."""
        device_id = params.get("device_id") or context.get("device_id")
        command = params.get("command", "")
        if not device_id:
            return {"error": "device_id is required for run_command"}
        if not command:
            return {"error": "command is required for run_command"}

        device_record = await self._db.get_device(device_id)
        if device_record is None:
            return {"error": f"Device {device_id!r} not found"}

        cred_id = device_record.get("credential_id")
        creds: dict[str, Any] = {}
        if cred_id:
            try:
                creds = await self._credential_manager.retrieve(cred_id)
            except Exception as exc:
                return {"error": f"Failed to retrieve credentials: {exc}"}

        try:
            device_cls = get_device_class(device_record["device_type"])
        except KeyError as exc:
            return {"error": str(exc)}

        device = device_cls(
            host=device_record.get("ip_address", device_record.get("host", "")),
            username=creds.get("username", ""),
            password=creds.get("password", ""),
            port=device_record.get("port", 22),
            device_type=device_record["device_type"],
            enable_secret=creds.get("enable_secret", ""),
            ssh_key_path=creds.get("ssh_key_path", ""),
            timeout=device_record.get("timeout", 30),
        )

        try:
            await device.connect()
            output = await device.send_command(command)
        except Exception as exc:
            return {"error": f"Command execution failed: {exc}"}
        finally:
            try:
                await device.disconnect()
            except Exception:
                pass

        return {"output": output, "device_id": device_id}

    # ------------------------------------------------------------------
    # 2. poll_device
    # ------------------------------------------------------------------

    async def _action_poll_device(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Poll a device for health metrics via the monitoring engine."""
        device_id = params.get("device_id") or context.get("device_id")
        if not device_id:
            return {"error": "device_id is required for poll_device"}

        try:
            metrics = await self._monitor.poll_device(device_id)
            return metrics
        except Exception as exc:
            return {"error": f"poll_device failed: {exc}"}

    # ------------------------------------------------------------------
    # 3. backup_config
    # ------------------------------------------------------------------

    async def _action_backup_config(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Back up a device configuration."""
        device_id = params.get("device_id") or context.get("device_id")
        if not device_id:
            return {"error": "device_id is required for backup_config"}

        try:
            result = await self._config_manager.backup_config(device_id)
            return {
                "backup_id": result.get("id", ""),
                "hash": result.get("config_hash", ""),
                "device_id": device_id,
            }
        except Exception as exc:
            return {"error": f"backup_config failed: {exc}"}

    # ------------------------------------------------------------------
    # 4. backup_all
    # ------------------------------------------------------------------

    async def _action_backup_all(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Back up configurations for all devices (optionally filtered by tag)."""
        tag = params.get("tag")

        try:
            results = await self._config_manager.backup_all(tag=tag)
            backed_up = len([r for r in results if r is not None])
            return {
                "backed_up": backed_up,
                "failed": 0,
                "tag": tag,
            }
        except Exception as exc:
            return {"error": f"backup_all failed: {exc}"}

    # ------------------------------------------------------------------
    # 5. deploy_config
    # ------------------------------------------------------------------

    async def _action_deploy_config(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Deploy configuration commands to a device."""
        device_id = params.get("device_id") or context.get("device_id")
        commands = params.get("commands", [])
        dry_run = params.get("dry_run", False)

        if not device_id:
            return {"error": "device_id is required for deploy_config"}
        if not commands:
            return {"error": "commands list is required for deploy_config"}

        try:
            result = await self._config_manager.deploy_config(
                device_id, commands, dry_run=dry_run
            )
            return result
        except Exception as exc:
            return {"error": f"deploy_config failed: {exc}"}

    # ------------------------------------------------------------------
    # 6. rollback_config
    # ------------------------------------------------------------------

    async def _action_rollback_config(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Roll back a device to a previous configuration backup."""
        device_id = params.get("device_id") or context.get("device_id")
        backup_id = params.get("backup_id")

        if not device_id:
            return {"error": "device_id is required for rollback_config"}
        if not backup_id:
            return {"error": "backup_id is required for rollback_config"}

        try:
            result = await self._config_manager.rollback_config(device_id, backup_id)
            return result
        except Exception as exc:
            return {"error": f"rollback_config failed: {exc}"}

    # ------------------------------------------------------------------
    # 7. check_interface_errors
    # ------------------------------------------------------------------

    async def _action_check_interface_errors(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Check for interface errors on a device."""
        device_id = params.get("device_id") or context.get("device_id")
        if not device_id:
            return {"error": "device_id is required for check_interface_errors"}

        try:
            errors = await self._troubleshooter.check_interface_errors(device_id)
            return {"errors": errors, "device_id": device_id, "error_count": len(errors)}
        except Exception as exc:
            return {"error": f"check_interface_errors failed: {exc}"}

    # ------------------------------------------------------------------
    # 8. check_device_health
    # ------------------------------------------------------------------

    async def _action_check_device_health(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Run a comprehensive health check on a device."""
        device_id = params.get("device_id") or context.get("device_id")
        if not device_id:
            return {"error": "device_id is required for check_device_health"}

        try:
            health = await self._troubleshooter.check_device_health(device_id)
            return health
        except Exception as exc:
            return {"error": f"check_device_health failed: {exc}"}

    # ------------------------------------------------------------------
    # 9. ping_test
    # ------------------------------------------------------------------

    async def _action_ping_test(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a ping test to a target."""
        target = params.get("target", "")
        count = int(params.get("count", 4))
        source_device_id = params.get("source_device_id") or context.get("device_id")

        if not target:
            return {"error": "target is required for ping_test"}

        try:
            result = await self._troubleshooter.ping_test(
                target, count=count, source_device_id=source_device_id
            )
            return result
        except Exception as exc:
            return {"error": f"ping_test failed: {exc}"}

    # ------------------------------------------------------------------
    # 9b. check_port
    # ------------------------------------------------------------------

    async def _action_check_port(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Check TCP port connectivity to a target."""
        target = params.get("target", "")
        port = int(params.get("port", 22))
        timeout = int(params.get("timeout", 5))

        if not target:
            return {"error": "target is required for check_port"}

        try:
            result = await self._troubleshooter.port_check(
                target, port=port, timeout=timeout
            )
            return result
        except Exception as exc:
            return {"error": f"check_port failed: {exc}"}

    # ------------------------------------------------------------------
    # 10. scan_subnet
    # ------------------------------------------------------------------

    async def _action_scan_subnet(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Scan a subnet for SNMP-responsive devices."""
        subnet = params.get("subnet", "")
        community = params.get("community", "public")

        if not subnet:
            return {"error": "subnet is required for scan_subnet"}

        try:
            discovered = await self._discovery.scan_subnet(
                subnet, community=community
            )
            return {
                "discovered": discovered,
                "count": len(discovered),
                "subnet": subnet,
            }
        except Exception as exc:
            return {"error": f"scan_subnet failed: {exc}"}

    # ------------------------------------------------------------------
    # 11. notify
    # ------------------------------------------------------------------

    async def _action_notify(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Send a notification through a named alert channel."""
        channel_name = params.get("channel", "")
        message = params.get("message", "")
        severity = params.get("severity", "info")

        if not message:
            return {"error": "message is required for notify"}

        # Find the matching channel from the alert engine's registered channels.
        target_channel = None
        for ch in self._alert_engine._channels:
            ch_type = type(ch).__name__.lower()
            if channel_name and channel_name.lower() in ch_type:
                target_channel = ch
                break

        if target_channel is None and self._alert_engine._channels:
            # Fall back to the first registered channel.
            target_channel = self._alert_engine._channels[0]

        if target_channel is None:
            return {"error": f"No notification channel found (requested: {channel_name!r})"}

        try:
            if hasattr(target_channel, "send"):
                await target_channel.send(message, severity=severity)
            elif hasattr(target_channel, "send_alert"):
                alert_payload = {
                    "message": message,
                    "severity": severity,
                    "device_id": context.get("device_id", ""),
                }
                await target_channel.send_alert(alert_payload, event="notification")
            return {
                "notified": True,
                "channel": type(target_channel).__name__,
                "severity": severity,
            }
        except Exception as exc:
            return {"error": f"notify failed: {exc}"}

    # ------------------------------------------------------------------
    # 12. log
    # ------------------------------------------------------------------

    async def _action_log(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Write an entry to the audit log."""
        level = params.get("level", "info")
        message = params.get("message", "")

        if not message:
            return {"error": "message is required for log action"}

        # Emit to Python logger at the requested level.
        log_level = getattr(logging, level.upper(), logging.INFO)
        logger.log(log_level, "[runbook] %s", message)

        entry_id = await self._audit_logger.log(
            actor="automation-engine",
            action_type="runbook_log",
            description=message,
            runbook_name=context.get("runbook_name"),
            execution_id=context.get("execution_id"),
            device_id=context.get("device_id"),
            details={"level": level},
            result="success",
        )
        return {"logged": True, "level": level, "audit_entry_id": entry_id}

    # ------------------------------------------------------------------
    # 13. wait
    # ------------------------------------------------------------------

    async def _action_wait(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Pause execution for a specified number of seconds."""
        seconds = int(params.get("seconds", 0))
        if seconds <= 0:
            return {"error": "seconds must be a positive integer for wait"}

        logger.info("[runbook] Waiting %d seconds...", seconds)
        await asyncio.sleep(seconds)
        return {"waited": seconds}

    # ------------------------------------------------------------------
    # 14. condition
    # ------------------------------------------------------------------

    async def _action_condition(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Evaluate a conditional check and return a branch jump target.

        Supported check types:
        - ``metric_threshold``: compare a numeric value against a threshold
        - ``list_not_empty``: check whether a value is a non-empty list
        - ``string_contains``: check whether a string contains a substring
        """
        check = params.get("check", "")
        on_true = params.get("on_true")
        on_false = params.get("on_false")
        result = False

        if check == "metric_threshold":
            metric_value = self._resolve_numeric(
                params.get("metric_value", params.get("value")),
                context,
            )
            threshold = float(params.get("threshold", 0))
            condition = params.get("condition", "gt")

            comparators = {
                "gt": lambda a, b: a > b,
                "gte": lambda a, b: a >= b,
                "lt": lambda a, b: a < b,
                "lte": lambda a, b: a <= b,
                "eq": lambda a, b: a == b,
                "ne": lambda a, b: a != b,
            }
            cmp_fn = comparators.get(condition)
            if cmp_fn is None:
                return {"error": f"Unknown condition operator: {condition!r}"}

            result = cmp_fn(metric_value, threshold)

        elif check == "list_not_empty":
            value = params.get("value", [])
            # If value is a string key, try to resolve from context.
            if isinstance(value, str) and value in context:
                value = context[value]
            result = isinstance(value, list) and len(value) > 0

        elif check == "string_contains":
            string = str(params.get("string", ""))
            substring = str(params.get("substring", ""))
            result = substring in string

        else:
            return {"error": f"Unknown condition check type: {check!r}"}

        jump_to = on_true if result else on_false
        return {"result": result, "jump_to": jump_to}

    # ------------------------------------------------------------------
    # 15. escalate
    # ------------------------------------------------------------------

    async def _action_escalate(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Send escalation notifications to all specified channels."""
        level = int(params.get("level", 1))
        message = params.get("message", "Escalation triggered by automation engine")
        channels = params.get("channels", [])
        severity = params.get("severity", "critical")

        notified_channels: list[str] = []

        # If channel names are specified, try to match them against registered channels.
        if channels:
            for ch_name in channels:
                for ch in self._alert_engine._channels:
                    ch_type = type(ch).__name__.lower()
                    if ch_name.lower() in ch_type:
                        try:
                            if hasattr(ch, "send"):
                                await ch.send(
                                    f"[ESCALATION L{level}] {message}",
                                    severity=severity,
                                )
                            notified_channels.append(type(ch).__name__)
                        except Exception as exc:
                            logger.error(
                                "Escalation notification to %s failed: %s",
                                type(ch).__name__,
                                exc,
                            )
                        break
        else:
            # No specific channels -- send to all registered channels.
            for ch in self._alert_engine._channels:
                try:
                    if hasattr(ch, "send"):
                        await ch.send(
                            f"[ESCALATION L{level}] {message}",
                            severity=severity,
                        )
                    notified_channels.append(type(ch).__name__)
                except Exception as exc:
                    logger.error(
                        "Escalation notification to %s failed: %s",
                        type(ch).__name__,
                        exc,
                    )

        # Audit the escalation.
        await self._audit_logger.log(
            actor="automation-engine",
            action_type="escalation",
            description=f"Escalation level {level}: {message}",
            runbook_name=context.get("runbook_name"),
            execution_id=context.get("execution_id"),
            device_id=context.get("device_id"),
            details={
                "level": level,
                "channels": notified_channels,
                "severity": severity,
            },
            result="success" if notified_channels else "failure",
        )

        return {
            "escalated": True,
            "level": level,
            "channels_notified": notified_channels,
        }

    # ------------------------------------------------------------------
    # 16. resolve_alert
    # ------------------------------------------------------------------

    async def _action_resolve_alert(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve an alert by its ID."""
        alert_id = params.get("alert_id") or context.get("alert_id")
        if not alert_id:
            return {"error": "alert_id is required for resolve_alert"}

        try:
            await self._alert_engine.resolve_alert(alert_id)
            return {"resolved": True, "alert_id": alert_id}
        except Exception as exc:
            return {"error": f"resolve_alert failed: {exc}"}

    # ------------------------------------------------------------------
    # 17. http_request
    # ------------------------------------------------------------------

    async def _action_http_request(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Make an HTTP request using httpx."""
        method = params.get("method", "GET").upper()
        url = params.get("url", "")
        headers = params.get("headers", {})
        body = params.get("body")
        timeout = int(params.get("timeout", 30))

        if not url:
            return {"error": "url is required for http_request"}

        if method not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            return {"error": f"Unsupported HTTP method: {method!r}"}

        try:
            async with httpx.AsyncClient(timeout=float(timeout)) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=body if isinstance(body, (dict, list)) else None,
                    content=body if isinstance(body, str) else None,
                )
                return {
                    "status_code": response.status_code,
                    "body": response.text[:10000],  # Cap response body size
                    "headers": dict(response.headers),
                }
        except httpx.TimeoutException as exc:
            return {"error": f"HTTP request timed out after {timeout}s: {exc}"}
        except httpx.HTTPError as exc:
            return {"error": f"HTTP request failed: {exc}"}
        except Exception as exc:
            return {"error": f"http_request unexpected error: {exc}"}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_numeric(value: Any, context: dict[str, Any]) -> float:
        """Coerce *value* to a float, resolving context keys if it is a string
        that looks like a dotted path."""
        if isinstance(value, (int, float)):
            return float(value)

        if isinstance(value, str):
            # Try to resolve as a dotted context path.
            parts = value.split(".")
            current: Any = context
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    break
            if isinstance(current, (int, float)):
                return float(current)

            # Try direct float conversion.
            try:
                return float(value)
            except (ValueError, TypeError):
                pass

        return 0.0
