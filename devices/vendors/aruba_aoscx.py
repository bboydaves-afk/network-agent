"""Aruba AOS-CX device implementation (6000/6100/6200/6300/6400 series).

AOS-CX is a modern, Linux-based network OS distinct from legacy AOS-Switch
(ProCurve).  It uses numeric interface naming (1/1/1), different ``show
version`` output, and has its own CLI conventions.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.registry import register_device
from devices.ssh_device import SSHDevice

# Reuse helpers from legacy Aruba driver where applicable
from devices.vendors.aruba import (
    _normalize_aruba_mac,
    _parse_aruba_uptime,
    _parse_mem_value,
    _RE_ARUBA_INTF_BRIEF_CX,
    _RE_ARUBA_INTF_MAC,
    _RE_ARUBA_INTF_MTU,
    _RE_ARUBA_INTF_DESC,
    _RE_ARUBA_INTF_RX_BYTES,
    _RE_ARUBA_INTF_TX_BYTES,
    _RE_ARUBA_INTF_RX_ERRORS,
    _RE_ARUBA_INTF_TX_ERRORS,
    _RE_ARUBA_INTF_IP,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AOS-CX specific regex patterns
# ---------------------------------------------------------------------------

# show version
_RE_CX_HOSTNAME = re.compile(
    r"Hostname\s*:\s*(\S+)", re.IGNORECASE
)
_RE_CX_VERSION = re.compile(
    r"(?:AOS-CX|Software)\s+Version\s*:\s*(\S+)", re.IGNORECASE
)
_RE_CX_VERSION_ALT = re.compile(
    r"Version\s*:\s*(\S+)", re.IGNORECASE
)
_RE_CX_MODEL = re.compile(
    r"(?:Product Name|Platform)\s*:\s*(.+)", re.IGNORECASE
)
_RE_CX_SERIAL = re.compile(
    r"Serial[-_ ]?Number\s*:\s*(\S+)", re.IGNORECASE
)
_RE_CX_UPTIME = re.compile(
    r"(?:Up Time|System Up Time|Uptime)\s*:\s*(.+)", re.IGNORECASE
)

# show system resource-utilization
_RE_CX_CPU = re.compile(
    r"CPU\s+Util(?:ization)?\s*(?:\(%\))?\s*:\s*(\d+)", re.IGNORECASE
)
_RE_CX_CPU_ALT = re.compile(
    r"(?:CPU utilization|CPU Usage)\s*:\s*(\d+)%?", re.IGNORECASE
)
_RE_CX_MEM_TOTAL = re.compile(
    r"(?:Memory\s*-?\s*Total|Total Memory|Mem Total)\s*:\s*([\d,]+)\s*(\w+)?",
    re.IGNORECASE,
)
_RE_CX_MEM_FREE = re.compile(
    r"(?:Memory\s*-?\s*Free|Free Memory|Mem Free)\s*:\s*([\d,]+)\s*(\w+)?",
    re.IGNORECASE,
)
_RE_CX_MEM_USED = re.compile(
    r"(?:Memory\s*-?\s*Used|Used Memory)\s*:\s*([\d,]+)", re.IGNORECASE
)

# ping
_RE_CX_PING = re.compile(
    r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received",
    re.IGNORECASE,
)
_RE_CX_PING_RTT = re.compile(
    r"(?:rtt|round-trip)\s+min/avg/max\s+=\s+([\d\.]+)/([\d\.]+)/([\d\.]+)",
    re.IGNORECASE,
)


@register_device("aruba_aoscx")
class ArubaAOSCXDevice(SSHDevice):
    """Aruba AOS-CX device driver (6000/6100/6200/6300/6400 series)."""

    netmiko_type = "aruba_aoscx"

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        if config_type == "startup":
            return await self.send_command("show startup-config", timeout=60)
        return await self.send_command("show running-config", timeout=60)

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        output = await self.send_command("show version")

        hostname_m = _RE_CX_HOSTNAME.search(output)
        version_m = (
            _RE_CX_VERSION.search(output)
            or _RE_CX_VERSION_ALT.search(output)
        )
        model_m = _RE_CX_MODEL.search(output)
        serial_m = _RE_CX_SERIAL.search(output)
        uptime_m = _RE_CX_UPTIME.search(output)

        hostname = hostname_m.group(1) if hostname_m else self.host
        version = version_m.group(1).rstrip(",") if version_m else ""
        model = model_m.group(1).strip() if model_m else ""
        serial = serial_m.group(1) if serial_m else ""
        uptime_str = uptime_m.group(1).strip() if uptime_m else ""
        uptime_seconds = _parse_aruba_uptime(uptime_str)

        # If hostname not in show version, try show system
        if hostname == self.host:
            try:
                sys_out = await self.send_command("show system")
                h_m = _RE_CX_HOSTNAME.search(sys_out)
                if h_m:
                    hostname = h_m.group(1)
            except Exception:
                pass

        return DeviceFacts(
            hostname=hostname,
            vendor="Aruba/HPE",
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
        brief_output = await self.send_command(
            "show interface brief", timeout=30
        )

        detail_output = ""
        try:
            detail_output = await self.send_command(
                "show interface", timeout=60
            )
        except Exception:
            pass

        # Parse AOS-CX brief format (numeric notation 1/1/1)
        interfaces: list[InterfaceInfo] = []
        matches = _RE_ARUBA_INTF_BRIEF_CX.findall(brief_output)
        for name, admin, link, speed, desc in matches:
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

        # Enrich with detail output
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
        output = await self.send_command("show system resource-utilization")

        # CPU
        cpu_percent = 0.0
        cpu_m = _RE_CX_CPU.search(output) or _RE_CX_CPU_ALT.search(output)
        if cpu_m:
            cpu_percent = float(cpu_m.group(1))

        # Memory
        mem_total = 0
        mem_used = 0
        mem_free = 0

        total_m = _RE_CX_MEM_TOTAL.search(output)
        if total_m:
            mem_total = _parse_mem_value(total_m.group(1), total_m.group(2))

        free_m = _RE_CX_MEM_FREE.search(output)
        if free_m:
            mem_free = _parse_mem_value(free_m.group(1), free_m.group(2))

        used_m = _RE_CX_MEM_USED.search(output)
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
    # Ping / Traceroute
    # ------------------------------------------------------------------

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        output = await self.send_command(
            f"ping {target} repetitions {count}", timeout=30 + count * 2
        )
        result: dict[str, Any] = {"raw_output": output, "success": False}

        m = _RE_CX_PING.search(output)
        if m:
            sent = int(m.group(1))
            recv = int(m.group(2))
            result["sent"] = sent
            result["received"] = recv
            result["success_rate"] = int((recv / sent * 100) if sent else 0)
            result["success"] = recv > 0

        rtt_m = _RE_CX_PING_RTT.search(output)
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
        """Split ``show interface`` output into per-interface blocks."""
        blocks: dict[str, str] = {}
        current_name: str | None = None
        current_lines: list[str] = []

        for line in output.splitlines():
            # AOS-CX: "Interface 1/1/1 is up" or "Interface vlan10 is up"
            m = re.match(
                r"^\s*Interface\s+(\S+)\s+is\s+", line, re.IGNORECASE
            )
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
