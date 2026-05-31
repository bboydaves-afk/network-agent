"""Palo Alto Networks PAN-OS device implementation."""

from __future__ import annotations

import logging
import re
from typing import Any

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.registry import register_device
from devices.ssh_device import SSHDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for PAN-OS CLI output
# ---------------------------------------------------------------------------

# show system info
_RE_PA_HOSTNAME = re.compile(r"^hostname:\s+(.+)", re.MULTILINE)
_RE_PA_MODEL = re.compile(r"^model:\s+(\S+)", re.MULTILINE)
_RE_PA_SERIAL = re.compile(r"^serial:\s+(\S+)", re.MULTILINE)
_RE_PA_VERSION = re.compile(r"^sw-version:\s+(\S+)", re.MULTILINE)
_RE_PA_APP_VERSION = re.compile(r"^app-version:\s+(\S+)", re.MULTILINE)
_RE_PA_THREAT_VERSION = re.compile(r"^threat-version:\s+(\S+)", re.MULTILINE)
_RE_PA_UPTIME = re.compile(r"^uptime:\s+(.+)", re.MULTILINE)
_RE_PA_FAMILY = re.compile(r"^family:\s+(\S+)", re.MULTILINE)
_RE_PA_IP = re.compile(r"^ip-address:\s+(\S+)", re.MULTILINE)

# show interface all
_RE_PA_INTF_HEADER = re.compile(
    r"^(ethernet\d+/\d+(?:\.\d+)?|ae\d+(?:\.\d+)?|vlan\.\d+|loopback\.\d+|tunnel\.\d+)\s+",
    re.MULTILINE,
)
_RE_PA_INTF_LINE = re.compile(
    r"^(\S+)\s+"           # name
    r"(\d+)\s+"            # id
    r"(\S+)\s+"            # speed/type
    r"(\S+)\s+"            # duplex
    r"(up|down)\s+"        # state
    r"(\S+)\s*"            # mac
    r"(\S*)",              # ip/mask
    re.MULTILINE | re.IGNORECASE,
)

# show interface all (verbose block format)
_RE_PA_INTF_BLOCK = re.compile(r"^-{10,}", re.MULTILINE)
_RE_PA_INTF_NAME_LINE = re.compile(r"^Name:\s+(\S+)", re.MULTILINE)
_RE_PA_INTF_IP_BLOCK = re.compile(r"^IP:\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", re.MULTILINE)
_RE_PA_INTF_STATE = re.compile(r"^State:\s+(\S+)", re.MULTILINE | re.IGNORECASE)
_RE_PA_INTF_SPEED_BLOCK = re.compile(r"^Speed:\s+(\S+)", re.MULTILINE)
_RE_PA_INTF_MAC_BLOCK = re.compile(r"^MAC:\s+([0-9a-fA-F:]+)", re.MULTILINE)
_RE_PA_INTF_MTU_BLOCK = re.compile(r"^MTU:\s+(\d+)", re.MULTILINE)
_RE_PA_INTF_RX_BYTES = re.compile(r"bytes received:\s+(\d+)", re.IGNORECASE)
_RE_PA_INTF_TX_BYTES = re.compile(r"bytes transmitted:\s+(\d+)", re.IGNORECASE)
_RE_PA_INTF_RX_ERRORS = re.compile(r"receive errors:\s+(\d+)", re.IGNORECASE)
_RE_PA_INTF_TX_ERRORS = re.compile(r"transmit errors:\s+(\d+)", re.IGNORECASE)

# show system resources
_RE_PA_CPU = re.compile(
    r"CPU\s+mgmt:\s+([\d\.]+)%.*?CPU\s+dp:\s+([\d\.]+)%", re.IGNORECASE | re.DOTALL
)
_RE_PA_CPU_LOAD_AVG = re.compile(
    r"load average:\s+([\d\.]+)", re.IGNORECASE
)
_RE_PA_CPU_TOP = re.compile(
    r"Cpu\(s\):\s*([\d\.]+)%\s*us,\s*([\d\.]+)%\s*sy", re.IGNORECASE
)
_RE_PA_MEM = re.compile(
    r"Mem:\s+(\d+)\s+total,\s+(\d+)\s+used,\s+(\d+)\s+free", re.IGNORECASE
)
_RE_PA_MEM_ALT = re.compile(
    r"KiB Mem\s*:\s*(\d+)\s+total,\s*(\d+)\s+free,\s*(\d+)\s+used", re.IGNORECASE
)
_RE_PA_SWAP = re.compile(
    r"Swap:\s+(\d+)\s+total,\s+(\d+)\s+used", re.IGNORECASE
)

# Security rules
_RE_PA_RULE = re.compile(
    r"^\s*(\d+)\s+(\S+)\s+([\w,]+)\s+([\w,]+)\s+([\w,]+)\s+([\w,]+)\s+(\S+)\s+(\S+)",
    re.MULTILINE,
)

# Session info
_RE_PA_SESSION_COUNT = re.compile(
    r"(?:number of sessions|active sessions|num-active):\s+(\d+)", re.IGNORECASE
)

# Ping
_RE_PA_PING = re.compile(
    r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received", re.IGNORECASE
)
_RE_PA_PING_RTT = re.compile(
    r"(?:rtt|round-trip)\s+min/avg/max(?:/mdev)?\s+=\s+([\d\.]+)/([\d\.]+)/([\d\.]+)",
    re.IGNORECASE,
)


def _parse_pa_uptime(uptime_str: str) -> int:
    """Parse PAN-OS uptime string to seconds."""
    total = 0
    for m in re.finditer(r"(\d+)\s+(day|hour|min|minute|sec|second)s?", uptime_str, re.IGNORECASE):
        val = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "day":
            total += val * 86400
        elif unit == "hour":
            total += val * 3600
        elif unit in ("min", "minute"):
            total += val * 60
        elif unit in ("sec", "second"):
            total += val
    return total


def _prefix_to_mask(prefix_len: int) -> str:
    """Convert CIDR prefix length to dotted-decimal subnet mask."""
    if prefix_len < 0 or prefix_len > 32:
        return ""
    bits = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
    return ".".join(str((bits >> (8 * i)) & 0xFF) for i in range(3, -1, -1))


@register_device("paloalto")
class PaloAltoDevice(SSHDevice):
    """Palo Alto Networks PAN-OS device driver (PA-series, VM-Series)."""

    netmiko_type = "paloalto_panos"

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        if config_type in ("running", "active"):
            return await self.send_command("show config running", timeout=120)
        elif config_type == "candidate":
            return await self.send_command("show config candidate", timeout=120)
        elif config_type == "pushed":
            return await self.send_command("show config pushed-shared-policy", timeout=60)
        else:
            return await self.send_command("show config running", timeout=120)

    async def send_config(self, commands: list[str]) -> str:
        """Send config commands. PAN-OS uses ``set`` commands followed
        by ``commit``.
        """
        self._require_connection()
        try:
            # Enter configure mode and send commands
            output: str = await self._run_sync(
                self._net_connect.send_config_set,
                commands,
                exit_config_mode=False,
            )
            # Commit
            commit_output = await self._run_sync(
                self._net_connect.send_command,
                "commit",
                read_timeout=120,
            )
            output += "\n" + commit_output

            # Exit config mode
            try:
                await self._run_sync(self._net_connect.exit_config_mode)
            except Exception:
                pass

            return output
        except Exception as exc:
            from core.exceptions import DeviceCommandError
            raise DeviceCommandError(
                device_id=self.host,
                message=f"PAN-OS config push failed: {exc}",
                command="; ".join(commands),
            ) from exc

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        output = await self.send_command("show system info")

        hostname_m = _RE_PA_HOSTNAME.search(output)
        model_m = _RE_PA_MODEL.search(output)
        serial_m = _RE_PA_SERIAL.search(output)
        version_m = _RE_PA_VERSION.search(output)
        uptime_m = _RE_PA_UPTIME.search(output)
        app_m = _RE_PA_APP_VERSION.search(output)
        threat_m = _RE_PA_THREAT_VERSION.search(output)
        family_m = _RE_PA_FAMILY.search(output)

        hostname = hostname_m.group(1).strip() if hostname_m else self.host
        model = model_m.group(1) if model_m else ""
        serial = serial_m.group(1) if serial_m else ""
        version = version_m.group(1) if version_m else ""
        uptime_str = uptime_m.group(1).strip() if uptime_m else ""
        uptime_seconds = _parse_pa_uptime(uptime_str)

        extra: dict[str, Any] = {}
        if app_m:
            extra["app_version"] = app_m.group(1)
        if threat_m:
            extra["threat_version"] = threat_m.group(1)
        if family_m:
            extra["family"] = family_m.group(1)

        return DeviceFacts(
            hostname=hostname,
            vendor="Palo Alto Networks",
            model=model,
            serial_number=serial,
            os_version=version,
            uptime=uptime_str,
            uptime_seconds=uptime_seconds,
            extra=extra,
        )

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    async def get_interfaces(self) -> list[InterfaceInfo]:
        output = await self.send_command("show interface all", timeout=30)

        # Try table format first, then fall back to block format
        interfaces = self._parse_intf_table(output)
        if not interfaces:
            interfaces = self._parse_intf_blocks(output)
        return interfaces

    def _parse_intf_table(self, output: str) -> list[InterfaceInfo]:
        """Parse tabular ``show interface all`` output."""
        interfaces: list[InterfaceInfo] = []
        for m in _RE_PA_INTF_LINE.finditer(output):
            name = m.group(1)
            speed = m.group(3)
            state = m.group(5).lower()
            mac = m.group(6).lower() if m.group(6) != "N/A" else ""
            ip_mask = m.group(7)

            ip_address = ""
            subnet_mask = ""
            if ip_mask and "/" in ip_mask:
                parts = ip_mask.split("/")
                ip_address = parts[0]
                try:
                    subnet_mask = _prefix_to_mask(int(parts[1]))
                except ValueError:
                    subnet_mask = parts[1]
            elif ip_mask and ip_mask != "N/A":
                ip_address = ip_mask

            status = "up" if state == "up" else "down"

            interfaces.append(
                InterfaceInfo(
                    name=name,
                    status=status,
                    protocol_status=state,
                    ip_address=ip_address,
                    subnet_mask=subnet_mask,
                    speed=speed,
                    mac_address=mac,
                )
            )
        return interfaces

    def _parse_intf_blocks(self, output: str) -> list[InterfaceInfo]:
        """Parse block-formatted ``show interface all`` output."""
        # Split by separator lines
        blocks = re.split(r"-{10,}", output)
        interfaces: list[InterfaceInfo] = []

        for block in blocks:
            name_m = _RE_PA_INTF_NAME_LINE.search(block)
            if not name_m:
                continue

            name = name_m.group(1)
            ip_m = _RE_PA_INTF_IP_BLOCK.search(block)
            ip_address = ip_m.group(1) if ip_m else ""
            subnet_mask = _prefix_to_mask(int(ip_m.group(2))) if ip_m else ""

            state_m = _RE_PA_INTF_STATE.search(block)
            state = state_m.group(1).lower() if state_m else "unknown"

            speed_m = _RE_PA_INTF_SPEED_BLOCK.search(block)
            speed = speed_m.group(1) if speed_m else ""

            mac_m = _RE_PA_INTF_MAC_BLOCK.search(block)
            mac = mac_m.group(1).lower() if mac_m else ""

            mtu_m = _RE_PA_INTF_MTU_BLOCK.search(block)
            mtu = int(mtu_m.group(1)) if mtu_m else 0

            rx_m = _RE_PA_INTF_RX_BYTES.search(block)
            tx_m = _RE_PA_INTF_TX_BYTES.search(block)
            rx_err_m = _RE_PA_INTF_RX_ERRORS.search(block)
            tx_err_m = _RE_PA_INTF_TX_ERRORS.search(block)

            status = "up" if state == "up" else "down"

            interfaces.append(
                InterfaceInfo(
                    name=name,
                    status=status,
                    protocol_status=state,
                    ip_address=ip_address,
                    subnet_mask=subnet_mask,
                    speed=speed,
                    mtu=mtu,
                    mac_address=mac,
                    in_octets=int(rx_m.group(1)) if rx_m else 0,
                    out_octets=int(tx_m.group(1)) if tx_m else 0,
                    in_errors=int(rx_err_m.group(1)) if rx_err_m else 0,
                    out_errors=int(tx_err_m.group(1)) if tx_err_m else 0,
                )
            )

        return interfaces

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def get_health(self) -> DeviceHealth:
        output = await self.send_command("show system resources")

        # CPU
        cpu_percent = 0.0
        cpu_top_m = _RE_PA_CPU_TOP.search(output)
        if cpu_top_m:
            cpu_percent = float(cpu_top_m.group(1)) + float(cpu_top_m.group(2))
        else:
            cpu_m = _RE_PA_CPU.search(output)
            if cpu_m:
                cpu_percent = (float(cpu_m.group(1)) + float(cpu_m.group(2))) / 2.0
            else:
                load_m = _RE_PA_CPU_LOAD_AVG.search(output)
                if load_m:
                    # Load average -> rough percentage (assuming single core)
                    cpu_percent = min(float(load_m.group(1)) * 100.0, 100.0)

        # Memory (in KB from top-like output)
        mem_total = 0
        mem_used = 0
        mem_m = _RE_PA_MEM.search(output)
        if mem_m:
            mem_total = int(mem_m.group(1)) * 1024  # KB -> bytes
            mem_used = int(mem_m.group(2)) * 1024
        else:
            mem_alt_m = _RE_PA_MEM_ALT.search(output)
            if mem_alt_m:
                mem_total = int(mem_alt_m.group(1)) * 1024
                mem_used = int(mem_alt_m.group(3)) * 1024

        mem_pct = (mem_used / mem_total * 100.0) if mem_total else 0.0

        # Swap
        extra: dict[str, Any] = {}
        swap_m = _RE_PA_SWAP.search(output)
        if swap_m:
            extra["swap_total_bytes"] = int(swap_m.group(1)) * 1024
            extra["swap_used_bytes"] = int(swap_m.group(2)) * 1024

        return DeviceHealth(
            cpu_percent=round(cpu_percent, 2),
            memory_used_bytes=mem_used,
            memory_total_bytes=mem_total,
            memory_percent=round(mem_pct, 2),
            extra=extra,
        )

    # ------------------------------------------------------------------
    # Firewall-specific
    # ------------------------------------------------------------------

    async def get_security_rules(self) -> list[dict[str, Any]]:
        """Retrieve security policy rules (rulebase)."""
        output = await self.send_command("show running security-policy", timeout=60)
        rules: list[dict[str, Any]] = []
        current_rule: dict[str, Any] | None = None

        for line in output.splitlines():
            # Rule name line (e.g., "Rule 'allow-web' {")
            rule_m = re.match(r"^\s*(?:Rule\s+)?'?(\S+?)'?\s*\{", line)
            if rule_m:
                if current_rule:
                    rules.append(current_rule)
                current_rule = {"name": rule_m.group(1)}
                continue

            if current_rule is None:
                continue

            # Key-value pairs
            kv_m = re.match(r"^\s+([\w\-]+):\s+(.+);?", line)
            if kv_m:
                key = kv_m.group(1).strip().lower().replace("-", "_")
                val = kv_m.group(2).strip().rstrip(";")
                if "," in val:
                    current_rule[key] = [v.strip() for v in val.split(",")]
                else:
                    current_rule[key] = val

            # End of rule block
            if line.strip() == "}":
                if current_rule:
                    rules.append(current_rule)
                current_rule = None

        if current_rule:
            rules.append(current_rule)

        return rules

    async def get_session_info(self) -> dict[str, Any]:
        """Retrieve session table summary."""
        output = await self.send_command("show session info")
        info: dict[str, Any] = {"raw_output": output}

        count_m = _RE_PA_SESSION_COUNT.search(output)
        if count_m:
            info["active_sessions"] = int(count_m.group(1))

        # Parse additional session metrics
        for pattern, key in [
            (r"num-max:\s+(\d+)", "max_sessions"),
            (r"num-tcp:\s+(\d+)", "tcp_sessions"),
            (r"num-udp:\s+(\d+)", "udp_sessions"),
            (r"num-icmp:\s+(\d+)", "icmp_sessions"),
            (r"cps:\s+(\d+)", "connections_per_second"),
            (r"kbps:\s+(\d+)", "kbps"),
        ]:
            m = re.search(pattern, output, re.IGNORECASE)
            if m:
                info[key] = int(m.group(1))

        return info

    # ------------------------------------------------------------------
    # Ping / Traceroute
    # ------------------------------------------------------------------

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        output = await self.send_command(
            f"ping count {count} host {target}", timeout=30 + count * 2
        )
        result: dict[str, Any] = {"raw_output": output, "success": False}

        m = _RE_PA_PING.search(output)
        if m:
            sent = int(m.group(1))
            recv = int(m.group(2))
            result["sent"] = sent
            result["received"] = recv
            result["success_rate"] = int((recv / sent * 100) if sent else 0)
            result["success"] = recv > 0

        rtt_m = _RE_PA_PING_RTT.search(output)
        if rtt_m:
            result["rtt_min"] = float(rtt_m.group(1))
            result["rtt_avg"] = float(rtt_m.group(2))
            result["rtt_max"] = float(rtt_m.group(3))

        return result

    async def traceroute(self, target: str) -> dict[str, Any]:
        output = await self.send_command(
            f"traceroute host {target}", timeout=120
        )
        hops: list[dict[str, Any]] = []
        for line in output.splitlines():
            m = re.match(r"^\s*(\d+)\s+([\d\.\*]+)", line)
            if m:
                hop_num = int(m.group(1))
                addr = m.group(2)
                hops.append({
                    "hop": hop_num,
                    "address": addr if addr != "*" else None,
                })
        return {"raw_output": output, "hops": hops}
