"""Cisco IOS device implementation."""

from __future__ import annotations

import logging
import re
from typing import Any

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.registry import register_device
from devices.ssh_device import SSHDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for Cisco IOS CLI output
# ---------------------------------------------------------------------------

# show version
_RE_HOSTNAME = re.compile(r"^(\S+)\s+uptime\s+is", re.MULTILINE)
_RE_VERSION = re.compile(
    r"(?:Cisco IOS.*?Version|IOS.*?Version|Version)\s+([\S]+)", re.IGNORECASE
)
_RE_MODEL = re.compile(
    r"(?:cisco\s+([\w\-\/]+)\s+(?:\(|processor)|Cisco\s+([\w\-]+)\s+\()", re.IGNORECASE
)
_RE_SERIAL = re.compile(
    r"(?:Processor board ID|System serial number)\s+(\S+)", re.IGNORECASE
)
_RE_UPTIME = re.compile(r"uptime is\s+(.+)", re.IGNORECASE)

# show ip interface brief
_RE_IP_BRIEF = re.compile(
    r"^(\S+)\s+"           # interface name
    r"(\S+)\s+"            # ip address (or "unassigned")
    r"(?:YES|NO|NVRAM|TFTP|manual|DHCP|unset)\s+"  # method
    r"\S+\s+"              # OK?
    r"(\S+)\s+"            # admin status
    r"(\S+)",              # protocol status
    re.MULTILINE,
)

# show interfaces <name>
_RE_INTF_STATUS = re.compile(
    r"^(\S+)\s+is\s+([\w\s]+),\s+line protocol is\s+(\w+)", re.MULTILINE
)
_RE_INTF_SPEED = re.compile(r"BW\s+(\d+)\s+Kbit", re.IGNORECASE)
_RE_INTF_MTU = re.compile(r"MTU\s+(\d+)\s+bytes", re.IGNORECASE)
_RE_INTF_MAC = re.compile(
    r"(?:Hardware is|address is)\s+.*?(?:address is\s+)?([0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})",
    re.IGNORECASE,
)
_RE_INTF_DESC = re.compile(r"Description:\s+(.+)", re.IGNORECASE)
_RE_INTF_IP = re.compile(
    r"Internet address is\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", re.IGNORECASE
)
_RE_INTF_IN_OCTETS = re.compile(r"(\d+)\s+packets input.*?(\d+)\s+bytes", re.DOTALL)
_RE_INTF_OUT_OCTETS = re.compile(r"(\d+)\s+packets output.*?(\d+)\s+bytes", re.DOTALL)
_RE_INTF_IN_ERRORS = re.compile(r"(\d+)\s+input errors", re.IGNORECASE)
_RE_INTF_OUT_ERRORS = re.compile(r"(\d+)\s+output errors", re.IGNORECASE)

# show processes cpu
_RE_CPU_FIVE_SEC = re.compile(
    r"CPU utilization for five seconds:\s+(\d+)%/(\d+)%", re.IGNORECASE
)
_RE_CPU_ONE_MIN = re.compile(
    r"one minute:\s+(\d+)%", re.IGNORECASE
)
_RE_CPU_FIVE_MIN = re.compile(
    r"five minutes:\s+(\d+)%", re.IGNORECASE
)

# show memory statistics
_RE_MEM_TOTAL = re.compile(
    r"Processor\s+\S+\s+(\d+)\s+(\d+)\s+(\d+)", re.IGNORECASE
)

# ping
_RE_PING_SUCCESS = re.compile(
    r"Success rate is\s+(\d+)\s+percent\s+\((\d+)/(\d+)\)", re.IGNORECASE
)
_RE_PING_RTT = re.compile(
    r"round-trip min/avg/max\s+=\s+([\d\.]+)/([\d\.]+)/([\d\.]+)", re.IGNORECASE
)

# traceroute
_RE_TRACE_HOP = re.compile(
    r"^\s*(\d+)\s+([\d\.]+|\*)\s+", re.MULTILINE
)


def _cisco_mac_to_colon(mac: str) -> str:
    """Convert Cisco ``aaaa.bbbb.cccc`` MAC to ``aa:bb:cc:dd:ee:ff``."""
    mac = mac.replace(".", "")
    if len(mac) == 12:
        return ":".join(mac[i : i + 2] for i in range(0, 12, 2)).lower()
    return mac


def _prefix_to_mask(prefix_len: int) -> str:
    """Convert CIDR prefix length to dotted-decimal subnet mask."""
    if prefix_len < 0 or prefix_len > 32:
        return ""
    bits = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
    return ".".join(str((bits >> (8 * i)) & 0xFF) for i in range(3, -1, -1))


@register_device("cisco_ios")
class CiscoIOSDevice(SSHDevice):
    """Cisco IOS device driver (classic IOS, e.g. 2900, 3900 series)."""

    netmiko_type = "cisco_ios"

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        cmd_map = {
            "running": "show running-config",
            "startup": "show startup-config",
        }
        cmd = cmd_map.get(config_type, f"show {config_type}-config")
        return await self.send_command(cmd, timeout=60)

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        output = await self.send_command("show version")

        hostname_m = _RE_HOSTNAME.search(output)
        version_m = _RE_VERSION.search(output)
        model_m = _RE_MODEL.search(output)
        serial_m = _RE_SERIAL.search(output)
        uptime_m = _RE_UPTIME.search(output)

        hostname = hostname_m.group(1) if hostname_m else self.host
        version = version_m.group(1).rstrip(",") if version_m else ""
        model = ""
        if model_m:
            model = model_m.group(1) or model_m.group(2) or ""
        serial = serial_m.group(1) if serial_m else ""
        uptime_str = uptime_m.group(1).strip() if uptime_m else ""

        # Parse uptime into seconds
        uptime_seconds = self._parse_uptime(uptime_str)

        # Count interfaces from brief output
        try:
            brief = await self.send_command("show ip interface brief")
            iface_count = len(_RE_IP_BRIEF.findall(brief))
        except Exception:
            iface_count = 0

        return DeviceFacts(
            hostname=hostname,
            vendor="Cisco",
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
        brief_output = await self.send_command("show ip interface brief")
        detail_output = await self.send_command("show interfaces", timeout=60)

        # Parse brief for interface list
        brief_matches = _RE_IP_BRIEF.findall(brief_output)
        # Parse detail for counters, organized by interface blocks
        detail_blocks = self._split_interface_blocks(detail_output)

        interfaces: list[InterfaceInfo] = []
        for name, ip, admin, proto in brief_matches:
            ip_addr = ip if ip.lower() != "unassigned" else ""

            # Find the corresponding detail block
            block = detail_blocks.get(name, "")

            speed = ""
            mtu = 0
            mac = ""
            description = ""
            subnet_mask = ""
            in_octets = 0
            out_octets = 0
            in_errors = 0
            out_errors = 0

            if block:
                sp_m = _RE_INTF_SPEED.search(block)
                if sp_m:
                    kbps = int(sp_m.group(1))
                    if kbps >= 1_000_000:
                        speed = f"{kbps // 1_000_000}Gbps"
                    elif kbps >= 1000:
                        speed = f"{kbps // 1000}Mbps"
                    else:
                        speed = f"{kbps}Kbps"

                mtu_m = _RE_INTF_MTU.search(block)
                if mtu_m:
                    mtu = int(mtu_m.group(1))

                mac_m = _RE_INTF_MAC.search(block)
                if mac_m:
                    mac = _cisco_mac_to_colon(mac_m.group(1))

                desc_m = _RE_INTF_DESC.search(block)
                if desc_m:
                    description = desc_m.group(1).strip()

                ip_m = _RE_INTF_IP.search(block)
                if ip_m:
                    if not ip_addr:
                        ip_addr = ip_m.group(1)
                    subnet_mask = _prefix_to_mask(int(ip_m.group(2)))

                # Input bytes -- look for "N packets input, N bytes"
                in_bytes_m = re.search(
                    r"(\d+)\s+packets? input,\s+(\d+)\s+bytes", block
                )
                if in_bytes_m:
                    in_octets = int(in_bytes_m.group(2))

                out_bytes_m = re.search(
                    r"(\d+)\s+packets? output,\s+(\d+)\s+bytes", block
                )
                if out_bytes_m:
                    out_octets = int(out_bytes_m.group(2))

                in_err_m = _RE_INTF_IN_ERRORS.search(block)
                if in_err_m:
                    in_errors = int(in_err_m.group(1))

                out_err_m = _RE_INTF_OUT_ERRORS.search(block)
                if out_err_m:
                    out_errors = int(out_err_m.group(1))

            # Determine status
            admin_lower = admin.lower()
            proto_lower = proto.lower()
            if "administratively" in admin_lower:
                status = "administratively down"
            elif admin_lower == "up":
                status = "up"
            else:
                status = "down"

            interfaces.append(
                InterfaceInfo(
                    name=name,
                    status=status,
                    protocol_status=proto_lower,
                    ip_address=ip_addr,
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
        cpu_output = await self.send_command("show processes cpu")
        mem_output = await self.send_command("show memory statistics")

        # CPU
        cpu_5s = 0.0
        cpu_1m = 0.0
        cpu_5m = 0.0
        m = _RE_CPU_FIVE_SEC.search(cpu_output)
        if m:
            cpu_5s = float(m.group(1))
        m1 = _RE_CPU_ONE_MIN.search(cpu_output)
        if m1:
            cpu_1m = float(m1.group(1))
        m5 = _RE_CPU_FIVE_MIN.search(cpu_output)
        if m5:
            cpu_5m = float(m5.group(1))

        # Memory
        mem_total = 0
        mem_used = 0
        m_mem = _RE_MEM_TOTAL.search(mem_output)
        if m_mem:
            mem_total = int(m_mem.group(1))
            mem_used = int(m_mem.group(2))

        # Alternative parsing: look for "Total" line
        if not mem_total:
            for line in mem_output.splitlines():
                if "processor" in line.lower():
                    parts = line.split()
                    nums = [p for p in parts if p.isdigit()]
                    if len(nums) >= 3:
                        mem_total = int(nums[0])
                        mem_used = int(nums[1])
                        break

        mem_pct = (mem_used / mem_total * 100.0) if mem_total else 0.0

        return DeviceHealth(
            cpu_percent=cpu_5s,
            memory_used_bytes=mem_used,
            memory_total_bytes=mem_total,
            memory_percent=round(mem_pct, 2),
            extra={
                "cpu_1min": cpu_1m,
                "cpu_5min": cpu_5m,
            },
        )

    # ------------------------------------------------------------------
    # Ping / Traceroute
    # ------------------------------------------------------------------

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        output = await self.send_command(
            f"ping {target} repeat {count}", timeout=30 + count * 2
        )
        result: dict[str, Any] = {"raw_output": output, "success": False}

        m = _RE_PING_SUCCESS.search(output)
        if m:
            result["success_rate"] = int(m.group(1))
            result["sent"] = int(m.group(3))
            result["received"] = int(m.group(2))
            result["success"] = int(m.group(1)) > 0

        rtt_m = _RE_PING_RTT.search(output)
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
        for m in _RE_TRACE_HOP.finditer(output):
            hop_num = int(m.group(1))
            addr = m.group(2)
            hops.append({"hop": hop_num, "address": addr if addr != "*" else None})

        return {"raw_output": output, "hops": hops}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_interface_blocks(output: str) -> dict[str, str]:
        """Split ``show interfaces`` output into per-interface blocks."""
        blocks: dict[str, str] = {}
        current_name: str | None = None
        current_lines: list[str] = []

        for line in output.splitlines():
            m = _RE_INTF_STATUS.match(line)
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

    @staticmethod
    def _parse_uptime(uptime_str: str) -> int:
        """Convert Cisco uptime string to seconds."""
        total = 0
        for m in re.finditer(r"(\d+)\s+(year|week|day|hour|minute|second)s?", uptime_str, re.IGNORECASE):
            val = int(m.group(1))
            unit = m.group(2).lower()
            if unit == "year":
                total += val * 365 * 86400
            elif unit == "week":
                total += val * 7 * 86400
            elif unit == "day":
                total += val * 86400
            elif unit == "hour":
                total += val * 3600
            elif unit == "minute":
                total += val * 60
            elif unit == "second":
                total += val
        return total
