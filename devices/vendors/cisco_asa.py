"""Cisco ASA (Adaptive Security Appliance) device implementation."""

from __future__ import annotations

import logging
import re
from typing import Any

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.registry import register_device
from devices.ssh_device import SSHDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for Cisco ASA CLI output
# ---------------------------------------------------------------------------

# show version
_RE_ASA_HOSTNAME = re.compile(
    r"^(\S+)\s+up\s+", re.MULTILINE
)
_RE_ASA_HOSTNAME_ALT = re.compile(
    r"Hostname:\s*(\S+)", re.IGNORECASE
)
_RE_ASA_VERSION = re.compile(
    r"(?:Cisco Adaptive Security Appliance|ASA)\s+Software Version\s+(\S+)",
    re.IGNORECASE,
)
_RE_ASA_VERSION_ALT = re.compile(
    r"Software Version\s+(\S+)", re.IGNORECASE
)
_RE_ASA_MODEL = re.compile(
    r"Hardware:\s+(\S+)", re.IGNORECASE
)
_RE_ASA_MODEL_ALT = re.compile(
    r"cisco\s+(ASA\S+)", re.IGNORECASE
)
_RE_ASA_SERIAL = re.compile(
    r"Serial Number:\s+(\S+)", re.IGNORECASE
)
_RE_ASA_UPTIME = re.compile(
    r"up\s+(.+?)$", re.MULTILINE | re.IGNORECASE
)
_RE_ASA_UPTIME_ALT = re.compile(
    r"uptime is\s+(.+)", re.IGNORECASE
)

# show interface
_RE_ASA_INTF_STATUS = re.compile(
    r'^Interface\s+(\S+)\s+"([^"]*)",\s+is\s+([\w\s]+),\s+line protocol is\s+(\w+)',
    re.MULTILINE,
)
_RE_ASA_INTF_STATUS_ALT = re.compile(
    r"^(\S+)\s+is\s+([\w\s]+),\s+line protocol is\s+(\w+)",
    re.MULTILINE,
)
_RE_ASA_INTF_IP = re.compile(
    r"IP address\s+(\d+\.\d+\.\d+\.\d+),\s+subnet mask\s+(\d+\.\d+\.\d+\.\d+)",
    re.IGNORECASE,
)
_RE_ASA_INTF_SPEED = re.compile(r"BW\s+(\d+)\s+Kbit", re.IGNORECASE)
_RE_ASA_INTF_MTU = re.compile(r"MTU\s+(\d+)", re.IGNORECASE)
_RE_ASA_INTF_MAC = re.compile(
    r"MAC address\s+([0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})",
    re.IGNORECASE,
)
_RE_ASA_INTF_IN = re.compile(
    r"(\d+)\s+packets input,\s+(\d+)\s+bytes", re.IGNORECASE
)
_RE_ASA_INTF_OUT = re.compile(
    r"(\d+)\s+packets output,\s+(\d+)\s+bytes", re.IGNORECASE
)

# show cpu usage
_RE_ASA_CPU = re.compile(
    r"CPU utilization for 5 seconds\s*=\s*(\d+)%", re.IGNORECASE
)
_RE_ASA_CPU_1M = re.compile(
    r"1 minute:\s*(\d+)%", re.IGNORECASE
)
_RE_ASA_CPU_5M = re.compile(
    r"5 minutes:\s*(\d+)%", re.IGNORECASE
)

# show memory
_RE_ASA_MEM = re.compile(
    r"Used memory:\s+(\d+)\s+Free memory:\s+(\d+)", re.IGNORECASE
)
_RE_ASA_MEM_ALT = re.compile(
    r"Total memory:\s+(\d+).*?Used memory:\s+(\d+)", re.IGNORECASE | re.DOTALL
)

# ping
_RE_ASA_PING = re.compile(
    r"Success rate is\s+(\d+)\s+percent\s+\((\d+)/(\d+)\)", re.IGNORECASE
)
_RE_ASA_PING_RTT = re.compile(
    r"round-trip min/avg/max\s+=\s+([\d\.]+)/([\d\.]+)/([\d\.]+)",
    re.IGNORECASE,
)

# traceroute
_RE_ASA_TRACE_HOP = re.compile(
    r"^\s*(\d+)\s+([\d\.]+|\*)\s+", re.MULTILINE
)


def _asa_mac_to_colon(mac: str) -> str:
    """Convert Cisco ``aaaa.bbbb.cccc`` to ``aa:bb:cc:dd:ee:ff``."""
    mac = mac.replace(".", "")
    if len(mac) == 12:
        return ":".join(mac[i : i + 2] for i in range(0, 12, 2)).lower()
    return mac


def _parse_asa_uptime(uptime_str: str) -> int:
    """Convert ASA uptime string to seconds."""
    total = 0
    for m in re.finditer(
        r"(\d+)\s+(year|week|day|hour|min|minute|sec|second)s?",
        uptime_str,
        re.IGNORECASE,
    ):
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
        elif unit in ("min", "minute"):
            total += val * 60
        elif unit in ("sec", "second"):
            total += val
    return total


@register_device("cisco_asa")
class CiscoASADevice(SSHDevice):
    """Cisco ASA firewall device driver."""

    netmiko_type = "cisco_asa"

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        cmd_map = {
            "running": "show running-config",
            "startup": "show startup-config",
        }
        cmd = cmd_map.get(config_type, "show running-config")
        return await self.send_command(cmd, timeout=60)

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        output = await self.send_command("show version")

        hostname_m = (
            _RE_ASA_HOSTNAME.search(output)
            or _RE_ASA_HOSTNAME_ALT.search(output)
        )
        version_m = (
            _RE_ASA_VERSION.search(output)
            or _RE_ASA_VERSION_ALT.search(output)
        )
        model_m = (
            _RE_ASA_MODEL.search(output)
            or _RE_ASA_MODEL_ALT.search(output)
        )
        serial_m = _RE_ASA_SERIAL.search(output)
        uptime_m = (
            _RE_ASA_UPTIME.search(output)
            or _RE_ASA_UPTIME_ALT.search(output)
        )

        hostname = hostname_m.group(1) if hostname_m else self.host
        version = version_m.group(1).rstrip(",") if version_m else ""
        model = model_m.group(1) if model_m else ""
        serial = serial_m.group(1) if serial_m else ""
        uptime_str = uptime_m.group(1).strip() if uptime_m else ""
        uptime_seconds = _parse_asa_uptime(uptime_str)

        return DeviceFacts(
            hostname=hostname,
            vendor="Cisco",
            model=model,
            serial_number=serial,
            os_version=version,
            uptime=uptime_str,
            uptime_seconds=uptime_seconds,
        )

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    async def get_interfaces(self) -> list[InterfaceInfo]:
        output = await self.send_command("show interface", timeout=60)
        blocks = self._split_interface_blocks(output)

        interfaces: list[InterfaceInfo] = []
        for name, block in blocks.items():
            nameif = ""
            status = "down"
            proto = "down"
            ip_addr = ""
            subnet_mask = ""
            speed = ""
            mtu = 0
            mac = ""
            in_octets = 0
            out_octets = 0

            # Parse status
            m = _RE_ASA_INTF_STATUS.search(block)
            if m:
                nameif = m.group(2)
                admin_str = m.group(3).strip().lower()
                proto = m.group(4).lower()
                if "administratively" in admin_str:
                    status = "administratively down"
                elif "up" in admin_str:
                    status = "up"
            else:
                m_alt = _RE_ASA_INTF_STATUS_ALT.search(block)
                if m_alt:
                    admin_str = m_alt.group(2).strip().lower()
                    proto = m_alt.group(3).lower()
                    if "administratively" in admin_str:
                        status = "administratively down"
                    elif "up" in admin_str:
                        status = "up"

            # IP
            ip_m = _RE_ASA_INTF_IP.search(block)
            if ip_m:
                ip_addr = ip_m.group(1)
                subnet_mask = ip_m.group(2)

            # Speed
            sp_m = _RE_ASA_INTF_SPEED.search(block)
            if sp_m:
                kbps = int(sp_m.group(1))
                if kbps >= 1_000_000:
                    speed = f"{kbps // 1_000_000}Gbps"
                elif kbps >= 1000:
                    speed = f"{kbps // 1000}Mbps"
                else:
                    speed = f"{kbps}Kbps"

            # MTU
            mtu_m = _RE_ASA_INTF_MTU.search(block)
            if mtu_m:
                mtu = int(mtu_m.group(1))

            # MAC
            mac_m = _RE_ASA_INTF_MAC.search(block)
            if mac_m:
                mac = _asa_mac_to_colon(mac_m.group(1))

            # Counters
            in_m = _RE_ASA_INTF_IN.search(block)
            if in_m:
                in_octets = int(in_m.group(2))
            out_m = _RE_ASA_INTF_OUT.search(block)
            if out_m:
                out_octets = int(out_m.group(2))

            description = nameif if nameif else ""

            interfaces.append(
                InterfaceInfo(
                    name=name,
                    status=status,
                    protocol_status=proto,
                    ip_address=ip_addr,
                    subnet_mask=subnet_mask,
                    speed=speed,
                    mtu=mtu,
                    mac_address=mac,
                    description=description,
                    in_octets=in_octets,
                    out_octets=out_octets,
                )
            )

        return interfaces

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def get_health(self) -> DeviceHealth:
        cpu_output = await self.send_command("show cpu usage")
        mem_output = await self.send_command("show memory")

        # CPU
        cpu_5s = 0.0
        cpu_1m = 0.0
        cpu_5m = 0.0
        m = _RE_ASA_CPU.search(cpu_output)
        if m:
            cpu_5s = float(m.group(1))
        m1 = _RE_ASA_CPU_1M.search(cpu_output)
        if m1:
            cpu_1m = float(m1.group(1))
        m5 = _RE_ASA_CPU_5M.search(cpu_output)
        if m5:
            cpu_5m = float(m5.group(1))

        # Memory
        mem_total = 0
        mem_used = 0
        m_mem = _RE_ASA_MEM.search(mem_output)
        if m_mem:
            mem_used = int(m_mem.group(1))
            mem_free = int(m_mem.group(2))
            mem_total = mem_used + mem_free
        else:
            m_alt = _RE_ASA_MEM_ALT.search(mem_output)
            if m_alt:
                mem_total = int(m_alt.group(1))
                mem_used = int(m_alt.group(2))

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

        m = _RE_ASA_PING.search(output)
        if m:
            result["success_rate"] = int(m.group(1))
            result["sent"] = int(m.group(3))
            result["received"] = int(m.group(2))
            result["success"] = int(m.group(1)) > 0

        rtt_m = _RE_ASA_PING_RTT.search(output)
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
        for m in _RE_ASA_TRACE_HOP.finditer(output):
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
    def _split_interface_blocks(output: str) -> dict[str, str]:
        """Split ``show interface`` output into per-interface blocks."""
        blocks: dict[str, str] = {}
        current_name: str | None = None
        current_lines: list[str] = []

        for line in output.splitlines():
            # ASA format: Interface GigabitEthernet0/0 "inside", is up
            m = _RE_ASA_INTF_STATUS.match(line)
            if not m:
                m = _RE_ASA_INTF_STATUS_ALT.match(line)
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
        """Convert ASA uptime string to seconds."""
        return _parse_asa_uptime(uptime_str)
