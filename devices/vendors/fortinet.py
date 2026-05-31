"""Fortinet FortiGate device implementation."""

from __future__ import annotations

import logging
import re
from typing import Any

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.registry import register_device
from devices.ssh_device import SSHDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for FortiOS CLI output
# ---------------------------------------------------------------------------

# get system status
_RE_FGT_HOSTNAME = re.compile(r"^Hostname:\s+(.+)", re.MULTILINE)
_RE_FGT_SERIAL = re.compile(r"^Serial-Number:\s+(\S+)", re.MULTILINE)
_RE_FGT_VERSION = re.compile(r"^Version:\s+(.+)", re.MULTILINE)
_RE_FGT_MODEL = re.compile(
    r"(?:^Platform.*?:\s+(.+)|Version:\s+(\S+)\s+v)", re.MULTILINE
)
_RE_FGT_UPTIME = re.compile(
    r"(?:System time|Uptime):\s*(.+)", re.IGNORECASE | re.MULTILINE
)
_RE_FGT_FIRMWARE = re.compile(r"Firmware\s+Version:\s+(.+)", re.IGNORECASE)

# get system interface
_RE_FGT_INTF = re.compile(
    r"^==\s*\[\s*(\S+)\s*\]", re.MULTILINE
)
_RE_FGT_INTF_IP = re.compile(r"ip:\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE)
_RE_FGT_INTF_STATUS = re.compile(r"status:\s+(\S+)", re.IGNORECASE)
_RE_FGT_INTF_SPEED = re.compile(r"speed:\s+(\S+)", re.IGNORECASE)
_RE_FGT_INTF_MTU = re.compile(r"mtu-override:\s+(?:enable|disable).*?mtu:\s+(\d+)", re.IGNORECASE | re.DOTALL)
_RE_FGT_INTF_MTU_ALT = re.compile(r"mtu:\s+(\d+)", re.IGNORECASE)
_RE_FGT_INTF_MAC = re.compile(r"mac(?:addr)?:\s+([0-9a-fA-F:]+)", re.IGNORECASE)
_RE_FGT_INTF_DESC = re.compile(r"description:\s+(.+)", re.IGNORECASE)
_RE_FGT_INTF_LINK = re.compile(r"link:\s+(\S+)", re.IGNORECASE)
_RE_FGT_INTF_RX_BYTES = re.compile(r"rx_bytes:\s+(\d+)", re.IGNORECASE)
_RE_FGT_INTF_TX_BYTES = re.compile(r"tx_bytes:\s+(\d+)", re.IGNORECASE)
_RE_FGT_INTF_RX_ERRORS = re.compile(r"rx_errors:\s+(\d+)", re.IGNORECASE)
_RE_FGT_INTF_TX_ERRORS = re.compile(r"tx_errors:\s+(\d+)", re.IGNORECASE)

# get system performance status
_RE_FGT_CPU = re.compile(r"CPU\s+states:\s+(\d+)%\s+user\s+(\d+)%\s+system", re.IGNORECASE)
_RE_FGT_CPU_ALT = re.compile(r"CPU:\s+(\d+)%", re.IGNORECASE)
_RE_FGT_MEM_TOTAL = re.compile(r"Total RAM:\s+(\d+)\s+(\w+)", re.IGNORECASE)
_RE_FGT_MEM_USED = re.compile(r"Used:\s+(\d+)\s+(\w+)", re.IGNORECASE)
_RE_FGT_MEM_PCT = re.compile(r"Memory:\s+(\d+)%\s+used", re.IGNORECASE)
_RE_FGT_DISK = re.compile(r"Hard Disk:\s+(\d+)%\s+used", re.IGNORECASE)
_RE_FGT_UPTIME_PERF = re.compile(r"Uptime:\s+(.+)", re.IGNORECASE)

# Firewall policies
_RE_FGT_POLICY_ID = re.compile(r"^config firewall policy.*?edit\s+(\d+)", re.MULTILINE)
_RE_FGT_SESSIONS = re.compile(r"Total sessions:\s+(\d+)", re.IGNORECASE)

# Ping
_RE_FGT_PING = re.compile(
    r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received", re.IGNORECASE
)
_RE_FGT_PING_RTT = re.compile(
    r"(?:rtt|round-trip)\s+min/avg/max(?:/mdev)?\s+=\s+([\d\.]+)/([\d\.]+)/([\d\.]+)",
    re.IGNORECASE,
)


def _parse_forti_mem_value(value: str, unit: str) -> int:
    """Convert FortiOS memory value to bytes."""
    val = int(value)
    unit_lower = unit.lower()
    if unit_lower in ("kb", "kbytes"):
        return val * 1024
    elif unit_lower in ("mb", "mbytes"):
        return val * 1024 * 1024
    elif unit_lower in ("gb", "gbytes"):
        return val * 1024 * 1024 * 1024
    return val


def _parse_forti_uptime(uptime_str: str) -> int:
    """Parse FortiOS uptime string to seconds."""
    total = 0
    for m in re.finditer(r"(\d+)\s+(day|hour|minute|second)s?", uptime_str, re.IGNORECASE):
        val = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "day":
            total += val * 86400
        elif unit == "hour":
            total += val * 3600
        elif unit == "minute":
            total += val * 60
        elif unit == "second":
            total += val
    return total


@register_device("fortinet")
class FortinetDevice(SSHDevice):
    """Fortinet FortiGate device driver (FortiOS)."""

    netmiko_type = "fortinet"

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        if config_type in ("running", "full"):
            return await self.send_command("show full-configuration", timeout=120)
        elif config_type == "system":
            return await self.send_command("show system global", timeout=30)
        else:
            return await self.send_command("show full-configuration", timeout=120)

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        output = await self.send_command("get system status")

        hostname_m = _RE_FGT_HOSTNAME.search(output)
        serial_m = _RE_FGT_SERIAL.search(output)
        version_m = _RE_FGT_VERSION.search(output)
        model_m = _RE_FGT_MODEL.search(output)

        hostname = hostname_m.group(1).strip() if hostname_m else self.host
        serial = serial_m.group(1) if serial_m else ""
        version = version_m.group(1).strip() if version_m else ""
        model = ""
        if model_m:
            model = (model_m.group(1) or model_m.group(2) or "").strip()

        # Derive model from serial if not found
        if not model and serial:
            # FortiGate serials often start with FG (FortiGate), FW (FortiWiFi)
            if serial.startswith("FG"):
                model = "FortiGate"
            elif serial.startswith("FW"):
                model = "FortiWiFi"

        # Uptime from performance status
        uptime_str = ""
        uptime_seconds = 0
        try:
            perf_output = await self.send_command("get system performance status")
            up_m = _RE_FGT_UPTIME_PERF.search(perf_output)
            if up_m:
                uptime_str = up_m.group(1).strip()
                uptime_seconds = _parse_forti_uptime(uptime_str)
        except Exception:
            pass

        # Interface count
        iface_count = 0
        try:
            intf_output = await self.send_command("get system interface")
            iface_count = len(_RE_FGT_INTF.findall(intf_output))
        except Exception:
            pass

        return DeviceFacts(
            hostname=hostname,
            vendor="Fortinet",
            model=model,
            serial_number=serial,
            os_version=version,
            uptime=uptime_str,
            uptime_seconds=uptime_seconds,
            interface_count=iface_count,
        )

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    async def get_interfaces(self) -> list[InterfaceInfo]:
        output = await self.send_command("get system interface", timeout=30)

        # Also get physical link/stats
        phys_output = ""
        try:
            phys_output = await self.send_command("get system interface physical", timeout=30)
        except Exception:
            pass

        blocks = self._split_intf_blocks(output)
        phys_blocks = self._split_intf_blocks(phys_output) if phys_output else {}

        interfaces: list[InterfaceInfo] = []
        for name, block in blocks.items():
            ip_m = _RE_FGT_INTF_IP.search(block)
            ip_address = ip_m.group(1) if ip_m else ""
            subnet_mask = ip_m.group(2) if ip_m else ""

            status_m = _RE_FGT_INTF_STATUS.search(block)
            status_val = status_m.group(1).lower() if status_m else "unknown"

            speed_m = _RE_FGT_INTF_SPEED.search(block)
            speed = speed_m.group(1) if speed_m else ""

            mtu_m = _RE_FGT_INTF_MTU.search(block) or _RE_FGT_INTF_MTU_ALT.search(block)
            mtu = int(mtu_m.group(1)) if mtu_m else 1500

            mac_m = _RE_FGT_INTF_MAC.search(block)
            mac = mac_m.group(1).lower() if mac_m else ""

            desc_m = _RE_FGT_INTF_DESC.search(block)
            description = desc_m.group(1).strip() if desc_m else ""

            # Counters from physical output
            pblock = phys_blocks.get(name, block)
            rx_bytes_m = _RE_FGT_INTF_RX_BYTES.search(pblock)
            tx_bytes_m = _RE_FGT_INTF_TX_BYTES.search(pblock)
            rx_err_m = _RE_FGT_INTF_RX_ERRORS.search(pblock)
            tx_err_m = _RE_FGT_INTF_TX_ERRORS.search(pblock)

            in_octets = int(rx_bytes_m.group(1)) if rx_bytes_m else 0
            out_octets = int(tx_bytes_m.group(1)) if tx_bytes_m else 0
            in_errors = int(rx_err_m.group(1)) if rx_err_m else 0
            out_errors = int(tx_err_m.group(1)) if tx_err_m else 0

            # Link status
            link_m = _RE_FGT_INTF_LINK.search(pblock)
            link_status = link_m.group(1).lower() if link_m else ""

            if status_val == "down":
                status = "administratively down"
                proto = "down"
            elif link_status == "up" or status_val == "up":
                status = "up"
                proto = "up" if link_status == "up" else "down"
            else:
                status = "down"
                proto = "down"

            interfaces.append(
                InterfaceInfo(
                    name=name,
                    status=status,
                    protocol_status=proto,
                    ip_address=ip_address,
                    subnet_mask=subnet_mask,
                    speed=speed,
                    mtu=mtu,
                    mac_address=mac,
                    description=description,
                    in_octets=in_octets,
                    out_octets=out_octets,
                    in_errors=in_errors,
                    out_errors=out_errors,
                )
            )

        return interfaces

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def get_health(self) -> DeviceHealth:
        output = await self.send_command("get system performance status")

        # CPU
        cpu_percent = 0.0
        cpu_m = _RE_FGT_CPU.search(output)
        if cpu_m:
            cpu_percent = float(cpu_m.group(1)) + float(cpu_m.group(2))
        else:
            cpu_alt_m = _RE_FGT_CPU_ALT.search(output)
            if cpu_alt_m:
                cpu_percent = float(cpu_alt_m.group(1))

        # Memory
        mem_total = 0
        mem_used = 0
        mem_percent = 0.0

        mem_total_m = _RE_FGT_MEM_TOTAL.search(output)
        mem_used_m = _RE_FGT_MEM_USED.search(output)
        mem_pct_m = _RE_FGT_MEM_PCT.search(output)

        if mem_total_m:
            mem_total = _parse_forti_mem_value(mem_total_m.group(1), mem_total_m.group(2))
        if mem_used_m:
            mem_used = _parse_forti_mem_value(mem_used_m.group(1), mem_used_m.group(2))

        if mem_pct_m:
            mem_percent = float(mem_pct_m.group(1))
        elif mem_total:
            mem_percent = (mem_used / mem_total * 100.0)

        # Disk
        disk_percent = 0.0
        disk_m = _RE_FGT_DISK.search(output)
        if disk_m:
            disk_percent = float(disk_m.group(1))

        return DeviceHealth(
            cpu_percent=round(cpu_percent, 2),
            memory_used_bytes=mem_used,
            memory_total_bytes=mem_total,
            memory_percent=round(mem_percent, 2),
            disk_percent=round(disk_percent, 2),
        )

    # ------------------------------------------------------------------
    # Firewall-specific methods
    # ------------------------------------------------------------------

    async def get_policies(self) -> list[dict[str, Any]]:
        """Retrieve firewall policies (summary)."""
        output = await self.send_command("get firewall policy", timeout=60)
        policies: list[dict[str, Any]] = []

        # Parse policy blocks separated by "== [ N ]" headers or "edit N"
        current_policy: dict[str, Any] | None = None
        for line in output.splitlines():
            # Look for policy ID
            id_m = re.match(r"^\s*(?:==\s*\[\s*|edit\s+)(\d+)", line)
            if id_m:
                if current_policy:
                    policies.append(current_policy)
                current_policy = {"id": int(id_m.group(1))}
                continue

            if current_policy is None:
                continue

            # Key-value lines
            kv_m = re.match(r"^\s*([\w\-]+):\s+(.+)", line)
            if kv_m:
                key = kv_m.group(1).strip().lower().replace("-", "_")
                val = kv_m.group(2).strip()
                current_policy[key] = val

        if current_policy:
            policies.append(current_policy)

        return policies

    async def get_sessions_count(self) -> int:
        """Return the number of active sessions."""
        output = await self.send_command("get system session status")
        m = _RE_FGT_SESSIONS.search(output)
        if m:
            return int(m.group(1))
        # Alternate: just a number on its own line
        for line in output.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)
        return 0

    # ------------------------------------------------------------------
    # Ping / Traceroute
    # ------------------------------------------------------------------

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        output = await self.send_command(
            f"execute ping-options repeat-count {count}\nexecute ping {target}",
            timeout=30 + count * 2,
        )
        result: dict[str, Any] = {"raw_output": output, "success": False}

        m = _RE_FGT_PING.search(output)
        if m:
            sent = int(m.group(1))
            recv = int(m.group(2))
            result["sent"] = sent
            result["received"] = recv
            result["success_rate"] = int((recv / sent * 100) if sent else 0)
            result["success"] = recv > 0

        rtt_m = _RE_FGT_PING_RTT.search(output)
        if rtt_m:
            result["rtt_min"] = float(rtt_m.group(1))
            result["rtt_avg"] = float(rtt_m.group(2))
            result["rtt_max"] = float(rtt_m.group(3))

        return result

    async def traceroute(self, target: str) -> dict[str, Any]:
        output = await self.send_command(
            f"execute traceroute {target}", timeout=120
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_intf_blocks(output: str) -> dict[str, str]:
        """Split FortiOS interface output into per-interface blocks."""
        blocks: dict[str, str] = {}
        current_name: str | None = None
        current_lines: list[str] = []

        for line in output.splitlines():
            m = _RE_FGT_INTF.match(line)
            if m:
                if current_name is not None:
                    blocks[current_name] = "\n".join(current_lines)
                current_name = m.group(1)
                current_lines = [line]
            elif current_name is not None:
                current_lines.append(line)

        if current_name is not None:
            blocks[current_name] = "\n".join(current_lines)
        return blocks
