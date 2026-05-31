"""Aruba (HPE) AOS-Switch / AOS-CX device implementation."""

from __future__ import annotations

import logging
import re
from typing import Any

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.registry import register_device
from devices.ssh_device import SSHDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for AOS-Switch CLI output
# ---------------------------------------------------------------------------

# show version
_RE_ARUBA_MODEL = re.compile(
    r"(?:Aruba|HPE?)\s+([\w\-]+(?:\s+[\w\-]+)?)\s+Switch", re.IGNORECASE
)
_RE_ARUBA_MODEL_ALT = re.compile(
    r"(?:System Description|Image)\s*:\s*(.+)", re.IGNORECASE
)
_RE_ARUBA_FIRMWARE = re.compile(
    r"(?:Software revision|Firmware Version|ROM Version|Boot ROM Version)\s*:\s*(\S+)",
    re.IGNORECASE,
)
_RE_ARUBA_SERIAL = re.compile(
    r"(?:Serial Number|Serial-Num)\s*:\s*(\S+)", re.IGNORECASE
)
_RE_ARUBA_HOSTNAME = re.compile(
    r"(?:System Name|Hostname)\s*:\s*(\S+)", re.IGNORECASE
)
_RE_ARUBA_UPTIME = re.compile(
    r"(?:Up Time|System Up Time)\s*:\s*(.+)", re.IGNORECASE
)

# show interfaces brief
_RE_ARUBA_INTF_BRIEF = re.compile(
    r"^\s*(\S+)\s+"          # port/interface name
    r"(\S+)?\s*"             # type (may be absent)
    r"(?:Yes|No)\s+"         # intrusion alert
    r"(Up|Down)\s+"          # enabled (admin)
    r"(Up|Down)\s+"          # status (link)
    r"(\S+)\s+"              # mode (trunk/access/etc.)
    r"(\S+)",                # speed
    re.MULTILINE | re.IGNORECASE,
)

# Alternate brief format (AOS-CX)
_RE_ARUBA_INTF_BRIEF_CX = re.compile(
    r"^\s*(\d+/\d+(?:/\d+)?|\S+)\s+"   # interface name
    r"(up|down)\s+"                     # admin state
    r"(up|down)\s+"                     # link state
    r"(\S+)\s*"                         # speed/duplex
    r"(\S*)",                           # description
    re.MULTILINE | re.IGNORECASE,
)

# show interfaces <port>
_RE_ARUBA_INTF_SPEED = re.compile(r"Speed\s*:\s*(\S+)", re.IGNORECASE)
_RE_ARUBA_INTF_MTU = re.compile(r"MTU\s*:\s*(\d+)", re.IGNORECASE)
_RE_ARUBA_INTF_MAC = re.compile(
    r"(?:MAC Address|Port MAC)\s*:\s*([0-9a-fA-F\-:]+)", re.IGNORECASE
)
_RE_ARUBA_INTF_DESC = re.compile(r"Name\s*:\s*(.+)", re.IGNORECASE)
_RE_ARUBA_INTF_RX_BYTES = re.compile(r"Bytes Rx\s*:\s*(\d+)", re.IGNORECASE)
_RE_ARUBA_INTF_TX_BYTES = re.compile(r"Bytes Tx\s*:\s*(\d+)", re.IGNORECASE)
_RE_ARUBA_INTF_RX_ERRORS = re.compile(
    r"(?:Rx Err|Total Rx Errors|Input Errors)\s*:\s*(\d+)", re.IGNORECASE
)
_RE_ARUBA_INTF_TX_ERRORS = re.compile(
    r"(?:Tx Err|Total Tx Errors|Output Errors)\s*:\s*(\d+)", re.IGNORECASE
)
_RE_ARUBA_INTF_IP = re.compile(
    r"(?:IP Addr|Internet Address)\s*:\s*(\d+\.\d+\.\d+\.\d+)\s*/?\s*(\S*)",
    re.IGNORECASE,
)

# show system (CPU / memory)
_RE_ARUBA_CPU = re.compile(
    r"CPU\s+Util\s*\(%\)\s*:\s*(\d+)", re.IGNORECASE
)
_RE_ARUBA_CPU_ALT = re.compile(
    r"(?:CPU utilization|CPU Usage)\s*:\s*(\d+)%?", re.IGNORECASE
)
_RE_ARUBA_MEM_TOTAL = re.compile(
    r"(?:Memory\s*-\s*Total|Total Memory|Mem Total)\s*:\s*([\d,]+)\s*(\w+)?",
    re.IGNORECASE,
)
_RE_ARUBA_MEM_FREE = re.compile(
    r"(?:Memory\s*-\s*Free|Free Memory|Mem Free)\s*:\s*([\d,]+)\s*(\w+)?",
    re.IGNORECASE,
)
_RE_ARUBA_MEM_USED = re.compile(
    r"(?:Memory\s*-\s*Used|Used Memory)\s*:\s*([\d,]+)", re.IGNORECASE
)

# Wireless (controller-specific)
_RE_ARUBA_AP_COUNT = re.compile(
    r"Total APs\s*:\s*(\d+)", re.IGNORECASE
)
_RE_ARUBA_AP_UP = re.compile(
    r"(?:Up|Active)\s+APs?\s*:\s*(\d+)", re.IGNORECASE
)
_RE_ARUBA_CLIENT_COUNT = re.compile(
    r"(?:Total|Associated)\s+(?:Clients|Users|Stations)\s*:\s*(\d+)",
    re.IGNORECASE,
)

# Ping
_RE_ARUBA_PING = re.compile(
    r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received", re.IGNORECASE
)
_RE_ARUBA_PING_RTT = re.compile(
    r"(?:rtt|round-trip)\s+min/avg/max\s+=\s+([\d\.]+)/([\d\.]+)/([\d\.]+)",
    re.IGNORECASE,
)


def _normalize_aruba_mac(mac: str) -> str:
    """Normalize Aruba MAC formats to colon-separated lowercase."""
    mac = mac.replace("-", "").replace(":", "").replace(".", "").lower()
    if len(mac) == 12:
        return ":".join(mac[i : i + 2] for i in range(0, 12, 2))
    return mac


def _parse_aruba_uptime(uptime_str: str) -> int:
    """Parse Aruba uptime strings to seconds."""
    total = 0
    for m in re.finditer(r"(\d+)\s*(day|hour|min|minute|sec|second)s?", uptime_str, re.IGNORECASE):
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


def _parse_mem_value(raw: str, unit: str | None = None) -> int:
    """Parse memory value (optionally with unit) to bytes."""
    raw = raw.replace(",", "")
    val = int(raw)
    if unit:
        unit = unit.upper()
        if unit in ("KB", "KIB"):
            return val * 1024
        elif unit in ("MB", "MIB"):
            return val * 1024 * 1024
        elif unit in ("GB", "GIB"):
            return val * 1024 * 1024 * 1024
    return val


@register_device("aruba")
class ArubaDevice(SSHDevice):
    """Aruba AOS-Switch (ProCurve heritage) / AOS-CX device driver.

    Also works for Aruba wireless controllers with SSH access.
    """

    netmiko_type = "aruba_osswitch"

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        if config_type in ("running",):
            return await self.send_command("show running-config", timeout=60)
        elif config_type == "startup":
            return await self.send_command("show startup-config", timeout=60)
        else:
            return await self.send_command("show running-config", timeout=60)

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        output = await self.send_command("show version")

        hostname_m = _RE_ARUBA_HOSTNAME.search(output)
        model_m = _RE_ARUBA_MODEL.search(output) or _RE_ARUBA_MODEL_ALT.search(output)
        firmware_m = _RE_ARUBA_FIRMWARE.search(output)
        serial_m = _RE_ARUBA_SERIAL.search(output)
        uptime_m = _RE_ARUBA_UPTIME.search(output)

        hostname = hostname_m.group(1) if hostname_m else self.host
        model = model_m.group(1).strip() if model_m else ""
        firmware = firmware_m.group(1) if firmware_m else ""
        serial = serial_m.group(1) if serial_m else ""
        uptime_str = uptime_m.group(1).strip() if uptime_m else ""
        uptime_seconds = _parse_aruba_uptime(uptime_str)

        # If hostname not found in show version, try show system
        if hostname == self.host:
            try:
                sys_output = await self.send_command("show system")
                h_m = _RE_ARUBA_HOSTNAME.search(sys_output)
                if h_m:
                    hostname = h_m.group(1)
            except Exception:
                pass

        return DeviceFacts(
            hostname=hostname,
            vendor="Aruba/HPE",
            model=model,
            serial_number=serial,
            os_version=firmware,
            uptime=uptime_str,
            uptime_seconds=uptime_seconds,
        )

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    async def get_interfaces(self) -> list[InterfaceInfo]:
        brief_output = await self.send_command("show interfaces brief", timeout=30)

        # Try to get per-interface details
        detail_output = ""
        try:
            detail_output = await self.send_command("show interfaces", timeout=60)
        except Exception:
            pass

        # IP address info
        ip_output = ""
        try:
            ip_output = await self.send_command("show ip", timeout=15)
        except Exception:
            pass

        # Parse brief listing
        interfaces: list[InterfaceInfo] = []

        # Try standard AOS-Switch format
        matches = _RE_ARUBA_INTF_BRIEF.findall(brief_output)
        if not matches:
            # Try AOS-CX format
            matches_cx = _RE_ARUBA_INTF_BRIEF_CX.findall(brief_output)
            for name, admin, link, speed, desc in matches_cx:
                if admin.lower() != "up":
                    status = "administratively down"
                elif link.lower() == "up":
                    status = "up"
                else:
                    status = "down"

                interfaces.append(
                    InterfaceInfo(
                        name=name,
                        status=status,
                        protocol_status=link.lower(),
                        speed=speed,
                        description=desc.strip(),
                    )
                )
        else:
            for name, itype, admin, link, mode, speed in matches:
                if admin.lower() != "up":
                    status = "administratively down"
                elif link.lower() == "up":
                    status = "up"
                else:
                    status = "down"

                interfaces.append(
                    InterfaceInfo(
                        name=name,
                        status=status,
                        protocol_status=link.lower(),
                        speed=speed,
                    )
                )

        # Enrich with detail output if available
        if detail_output and interfaces:
            detail_blocks = self._split_intf_blocks(detail_output)
            for iface in interfaces:
                block = detail_blocks.get(iface.name, "")
                if not block:
                    continue

                mac_m = _RE_ARUBA_INTF_MAC.search(block)
                if mac_m:
                    iface.mac_address = _normalize_aruba_mac(mac_m.group(1))

                mtu_m = _RE_ARUBA_INTF_MTU.search(block)
                if mtu_m:
                    iface.mtu = int(mtu_m.group(1))

                desc_m = _RE_ARUBA_INTF_DESC.search(block)
                if desc_m:
                    iface.description = desc_m.group(1).strip()

                rx_m = _RE_ARUBA_INTF_RX_BYTES.search(block)
                if rx_m:
                    iface.in_octets = int(rx_m.group(1))

                tx_m = _RE_ARUBA_INTF_TX_BYTES.search(block)
                if tx_m:
                    iface.out_octets = int(tx_m.group(1))

                rx_err_m = _RE_ARUBA_INTF_RX_ERRORS.search(block)
                if rx_err_m:
                    iface.in_errors = int(rx_err_m.group(1))

                tx_err_m = _RE_ARUBA_INTF_TX_ERRORS.search(block)
                if tx_err_m:
                    iface.out_errors = int(tx_err_m.group(1))

                ip_m = _RE_ARUBA_INTF_IP.search(block)
                if ip_m:
                    iface.ip_address = ip_m.group(1)
                    iface.subnet_mask = ip_m.group(2) if ip_m.group(2) else ""

        return interfaces

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def get_health(self) -> DeviceHealth:
        output = await self.send_command("show system")

        # CPU
        cpu_percent = 0.0
        cpu_m = _RE_ARUBA_CPU.search(output) or _RE_ARUBA_CPU_ALT.search(output)
        if cpu_m:
            cpu_percent = float(cpu_m.group(1))

        # Memory
        mem_total = 0
        mem_used = 0
        mem_free = 0

        total_m = _RE_ARUBA_MEM_TOTAL.search(output)
        if total_m:
            mem_total = _parse_mem_value(total_m.group(1), total_m.group(2))

        free_m = _RE_ARUBA_MEM_FREE.search(output)
        if free_m:
            mem_free = _parse_mem_value(free_m.group(1), free_m.group(2))

        used_m = _RE_ARUBA_MEM_USED.search(output)
        if used_m:
            mem_used = _parse_mem_value(used_m.group(1), None)
        elif mem_total and mem_free:
            mem_used = mem_total - mem_free

        mem_pct = (mem_used / mem_total * 100.0) if mem_total else 0.0

        return DeviceHealth(
            cpu_percent=round(cpu_percent, 2),
            memory_used_bytes=mem_used,
            memory_total_bytes=mem_total,
            memory_percent=round(mem_pct, 2),
        )

    # ------------------------------------------------------------------
    # Wireless-specific methods
    # ------------------------------------------------------------------

    async def get_ap_status(self) -> dict[str, Any]:
        """Retrieve Access Point status (controller mode).

        Returns a dict with AP counts and a list of AP entries.
        """
        output = await self.send_command("show ap active", timeout=30)
        result: dict[str, Any] = {"raw_output": output}

        total_m = _RE_ARUBA_AP_COUNT.search(output)
        up_m = _RE_ARUBA_AP_UP.search(output)
        result["total_aps"] = int(total_m.group(1)) if total_m else 0
        result["active_aps"] = int(up_m.group(1)) if up_m else 0

        # Parse AP table lines
        aps: list[dict[str, str]] = []
        in_table = False
        header_cols: list[str] = []
        for line in output.splitlines():
            # Detect header row (contains "Name" and "IP Address" or similar)
            if re.search(r"Name\s+.*IP\s*Address", line, re.IGNORECASE):
                header_cols = [c.strip() for c in re.split(r"\s{2,}", line.strip())]
                in_table = True
                continue
            if in_table and line.strip() and not line.startswith("-"):
                cols = [c.strip() for c in re.split(r"\s{2,}", line.strip())]
                if cols and len(cols) >= 2:
                    ap_entry: dict[str, str] = {}
                    for i, val in enumerate(cols):
                        key = header_cols[i].lower().replace(" ", "_") if i < len(header_cols) else f"col{i}"
                        ap_entry[key] = val
                    aps.append(ap_entry)

        result["aps"] = aps
        return result

    async def get_client_count(self) -> int:
        """Return the number of associated wireless clients."""
        output = await self.send_command("show ap association count", timeout=15)
        m = _RE_ARUBA_CLIENT_COUNT.search(output)
        if m:
            return int(m.group(1))

        # Fallback: try "show clients" or "show user-table"
        try:
            user_output = await self.send_command("show user-table count", timeout=15)
            m2 = _RE_ARUBA_CLIENT_COUNT.search(user_output)
            if m2:
                return int(m2.group(1))
        except Exception:
            pass

        return 0

    # ------------------------------------------------------------------
    # Ping / Traceroute
    # ------------------------------------------------------------------

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        output = await self.send_command(
            f"ping {target} repetitions {count}", timeout=30 + count * 2
        )
        result: dict[str, Any] = {"raw_output": output, "success": False}

        m = _RE_ARUBA_PING.search(output)
        if m:
            sent = int(m.group(1))
            recv = int(m.group(2))
            result["sent"] = sent
            result["received"] = recv
            result["success_rate"] = int((recv / sent * 100) if sent else 0)
            result["success"] = recv > 0

        rtt_m = _RE_ARUBA_PING_RTT.search(output)
        if rtt_m:
            result["rtt_min"] = float(rtt_m.group(1))
            result["rtt_avg"] = float(rtt_m.group(2))
            result["rtt_max"] = float(rtt_m.group(3))

        return result

    async def traceroute(self, target: str) -> dict[str, Any]:
        output = await self.send_command(
            f"traceroute {target}", timeout=120
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
        """Split ``show interfaces`` output into per-port blocks."""
        blocks: dict[str, str] = {}
        current_name: str | None = None
        current_lines: list[str] = []

        for line in output.splitlines():
            # Port header line (e.g., " Status and Counters - Port Counters for port 1")
            port_m = re.match(
                r".*(?:port|interface)\s+(\S+)", line, re.IGNORECASE
            )
            if port_m and ("Status" in line or "Counters" in line or re.match(r"^\s*\S+\s+is\s+", line)):
                if current_name is not None:
                    blocks[current_name] = "\n".join(current_lines)
                current_name = port_m.group(1)
                current_lines = [line]
            elif current_name is not None:
                current_lines.append(line)

        if current_name is not None:
            blocks[current_name] = "\n".join(current_lines)
        return blocks
