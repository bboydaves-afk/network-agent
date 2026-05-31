"""MikroTik RouterOS device implementation."""

from __future__ import annotations

import logging
import re
from typing import Any

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.registry import register_device
from devices.ssh_device import SSHDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for RouterOS CLI output
# ---------------------------------------------------------------------------

# /system resource print
_RE_MT_UPTIME = re.compile(r"uptime:\s+(.+)", re.IGNORECASE)
_RE_MT_VERSION = re.compile(r"version:\s+(\S+)", re.IGNORECASE)
_RE_MT_CPU = re.compile(r"cpu:\s+(.+)", re.IGNORECASE)
_RE_MT_CPU_COUNT = re.compile(r"cpu-count:\s+(\d+)", re.IGNORECASE)
_RE_MT_CPU_LOAD = re.compile(r"cpu-load:\s+(\d+)%?", re.IGNORECASE)
_RE_MT_FREE_MEM = re.compile(r"free-memory:\s+([\d\.\w]+)", re.IGNORECASE)
_RE_MT_TOTAL_MEM = re.compile(r"total-memory:\s+([\d\.\w]+)", re.IGNORECASE)
_RE_MT_FREE_DISK = re.compile(r"free-hdd-space:\s+([\d\.\w]+)", re.IGNORECASE)
_RE_MT_TOTAL_DISK = re.compile(r"total-hdd-space:\s+([\d\.\w]+)", re.IGNORECASE)
_RE_MT_ARCH = re.compile(r"architecture-name:\s+(\S+)", re.IGNORECASE)
_RE_MT_BOARD = re.compile(r"board-name:\s+(\S+)", re.IGNORECASE)
_RE_MT_PLATFORM = re.compile(r"platform:\s+(\S+)", re.IGNORECASE)

# /system routerboard print
_RE_MT_RB_MODEL = re.compile(r"model:\s+(.+)", re.IGNORECASE)
_RE_MT_RB_SERIAL = re.compile(r"serial-number:\s+(\S+)", re.IGNORECASE)
_RE_MT_RB_FIRMWARE = re.compile(r"current-firmware:\s+(\S+)", re.IGNORECASE)

# /system identity print
_RE_MT_IDENTITY = re.compile(r"name:\s+(.+)", re.IGNORECASE)

# /interface print
# Flags and columns vary, but typical output looks like:
# Flags: D - dynamic, X - disabled, R - running, S - slave
#  #     NAME                TYPE       ACTUAL-MTU L2MTU  MAX-L2MTU MAC-ADDRESS
#  0  R  ether1              ether            1500  1598       4074 AA:BB:CC:DD:EE:FF
_RE_MT_INTF_LINE = re.compile(
    r"^\s*\d+\s+"              # index number
    r"([DXRS,\s]*?)\s+"        # flags
    r"(\S+)\s+"                # name
    r"(\S+)\s+"                # type
    r"(\d+)\s+"                # actual-mtu
    r"(?:\d+\s+)?"             # l2mtu (optional)
    r"(?:\d+\s+)?"             # max-l2mtu (optional)
    r"([0-9A-Fa-f:]+)",        # mac-address
    re.MULTILINE,
)

# Simpler format (some RouterOS versions)
_RE_MT_INTF_SIMPLE = re.compile(
    r"^\s*(\d+)\s+([DXRS ]*?)\s*;?\s*(\S+)\s+(\S+)\s+(\d+)",
    re.MULTILINE,
)

# /interface print detail
_RE_MT_INTF_DETAIL_NAME = re.compile(r"name=\"([^\"]+)\"", re.IGNORECASE)
_RE_MT_INTF_DETAIL_TYPE = re.compile(r"type=\"([^\"]+)\"", re.IGNORECASE)
_RE_MT_INTF_DETAIL_MTU = re.compile(r"(?:actual-)?mtu=(\d+)", re.IGNORECASE)
_RE_MT_INTF_DETAIL_MAC = re.compile(r"mac-address=([0-9A-Fa-f:]+)", re.IGNORECASE)
_RE_MT_INTF_DETAIL_RUNNING = re.compile(r"running=(yes|no|true|false)", re.IGNORECASE)
_RE_MT_INTF_DETAIL_DISABLED = re.compile(r"disabled=(yes|no|true|false)", re.IGNORECASE)
_RE_MT_INTF_DETAIL_RX = re.compile(r"rx-byte=(\d+)", re.IGNORECASE)
_RE_MT_INTF_DETAIL_TX = re.compile(r"tx-byte=(\d+)", re.IGNORECASE)
_RE_MT_INTF_DETAIL_RX_ERR = re.compile(r"rx-error=(\d+)", re.IGNORECASE)
_RE_MT_INTF_DETAIL_TX_ERR = re.compile(r"tx-error=(\d+)", re.IGNORECASE)
_RE_MT_INTF_DETAIL_SPEED = re.compile(r"speed=(\S+)", re.IGNORECASE)
_RE_MT_INTF_DETAIL_COMMENT = re.compile(r"comment=\"([^\"]+)\"", re.IGNORECASE)

# /ip address print
_RE_MT_IPADDR = re.compile(
    r"^\s*\d+\s+[A-Z]*\s*(\d+\.\d+\.\d+\.\d+/\d+)\s+[\d\.]+\s+(\S+)",
    re.MULTILINE,
)

# Ping
_RE_MT_PING_SENT = re.compile(r"sent=(\d+)", re.IGNORECASE)
_RE_MT_PING_RECV = re.compile(r"received=(\d+)", re.IGNORECASE)
_RE_MT_PING_AVG = re.compile(r"avg-rtt=(\d+)ms", re.IGNORECASE)
_RE_MT_PING_MIN = re.compile(r"min-rtt=(\d+)ms", re.IGNORECASE)
_RE_MT_PING_MAX = re.compile(r"max-rtt=(\d+)ms", re.IGNORECASE)


def _parse_mt_size(value: str) -> int:
    """Parse RouterOS size strings like '1024.0MiB' or '256KiB' to bytes."""
    value = value.strip()
    m = re.match(r"([\d\.]+)\s*(KiB|MiB|GiB|TiB|KB|MB|GB|TB|B)?", value, re.IGNORECASE)
    if not m:
        try:
            return int(value)
        except ValueError:
            return 0
    num = float(m.group(1))
    unit = (m.group(2) or "B").upper()
    multipliers = {
        "B": 1,
        "KB": 1024, "KIB": 1024,
        "MB": 1024**2, "MIB": 1024**2,
        "GB": 1024**3, "GIB": 1024**3,
        "TB": 1024**4, "TIB": 1024**4,
    }
    return int(num * multipliers.get(unit, 1))


def _parse_mt_uptime(uptime_str: str) -> int:
    """Parse RouterOS uptime like '3w2d5h30m12s' to seconds."""
    total = 0
    for m in re.finditer(r"(\d+)([wdhms])", uptime_str, re.IGNORECASE):
        val = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "w":
            total += val * 7 * 86400
        elif unit == "d":
            total += val * 86400
        elif unit == "h":
            total += val * 3600
        elif unit == "m":
            total += val * 60
        elif unit == "s":
            total += val
    return total


@register_device("mikrotik")
class MikroTikDevice(SSHDevice):
    """MikroTik RouterOS device driver."""

    netmiko_type = "mikrotik_routeros"

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        if config_type in ("running", "export"):
            return await self.send_command("/export", timeout=120)
        elif config_type == "verbose":
            return await self.send_command("/export verbose", timeout=120)
        elif config_type == "compact":
            return await self.send_command("/export compact", timeout=60)
        else:
            return await self.send_command("/export", timeout=120)

    async def send_config(self, commands: list[str]) -> str:
        """Execute RouterOS commands directly (no config mode concept).

        Unlike IOS/JunOS, RouterOS applies commands immediately; there is
        no commit step.
        """
        self._require_connection()
        results: list[str] = []
        for cmd in commands:
            output = await self.send_command(cmd)
            results.append(output)
        return "\n".join(results)

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        resource_output = await self.send_command("/system resource print")

        # Try routerboard info (not all MikroTik devices are RouterBOARDs)
        rb_output = ""
        try:
            rb_output = await self.send_command("/system routerboard print")
        except Exception:
            pass

        # Identity (hostname)
        identity_output = ""
        try:
            identity_output = await self.send_command("/system identity print")
        except Exception:
            pass

        # Hostname
        hostname = self.host
        id_m = _RE_MT_IDENTITY.search(identity_output)
        if id_m:
            hostname = id_m.group(1).strip()

        # Version
        version_m = _RE_MT_VERSION.search(resource_output)
        version = version_m.group(1) if version_m else ""

        # Model
        model = ""
        if rb_output:
            model_m = _RE_MT_RB_MODEL.search(rb_output)
            if model_m:
                model = model_m.group(1).strip()
        if not model:
            board_m = _RE_MT_BOARD.search(resource_output)
            if board_m:
                model = board_m.group(1)

        # Serial
        serial = ""
        if rb_output:
            serial_m = _RE_MT_RB_SERIAL.search(rb_output)
            if serial_m:
                serial = serial_m.group(1)

        # Uptime
        uptime_m = _RE_MT_UPTIME.search(resource_output)
        uptime_str = uptime_m.group(1).strip() if uptime_m else ""
        uptime_seconds = _parse_mt_uptime(uptime_str)

        # Extra info
        extra: dict[str, Any] = {}
        arch_m = _RE_MT_ARCH.search(resource_output)
        if arch_m:
            extra["architecture"] = arch_m.group(1)
        cpu_m = _RE_MT_CPU.search(resource_output)
        if cpu_m:
            extra["cpu"] = cpu_m.group(1).strip()
        cpu_count_m = _RE_MT_CPU_COUNT.search(resource_output)
        if cpu_count_m:
            extra["cpu_count"] = int(cpu_count_m.group(1))
        if rb_output:
            fw_m = _RE_MT_RB_FIRMWARE.search(rb_output)
            if fw_m:
                extra["firmware"] = fw_m.group(1)

        return DeviceFacts(
            hostname=hostname,
            vendor="MikroTik",
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
        # Get detailed interface info
        detail_output = await self.send_command(
            "/interface print detail without-paging", timeout=30
        )
        # Get IP addresses
        ip_output = await self.send_command(
            "/ip address print without-paging", timeout=15
        )

        # Build IP map: interface_name -> (ip, prefix)
        ip_map: dict[str, tuple[str, str]] = {}
        for m in _RE_MT_IPADDR.finditer(ip_output):
            addr_cidr = m.group(1)  # e.g. 192.168.1.1/24
            intf_name = m.group(2)
            if "/" in addr_cidr:
                ip, prefix = addr_cidr.split("/", 1)
                ip_map[intf_name] = (ip, prefix)

        # Parse detail output -- each interface is a numbered block
        # RouterOS detail format: space-separated key=value pairs
        interfaces: list[InterfaceInfo] = []

        # Split into records by line-initial index numbers
        records = re.split(r"\n\s*(?=\d+\s)", detail_output)
        for record in records:
            record = record.strip()
            if not record:
                continue

            name_m = _RE_MT_INTF_DETAIL_NAME.search(record)
            if not name_m:
                continue
            name = name_m.group(1)

            type_m = _RE_MT_INTF_DETAIL_TYPE.search(record)
            intf_type = type_m.group(1) if type_m else ""

            mtu_m = _RE_MT_INTF_DETAIL_MTU.search(record)
            mtu = int(mtu_m.group(1)) if mtu_m else 1500

            mac_m = _RE_MT_INTF_DETAIL_MAC.search(record)
            mac = mac_m.group(1).lower() if mac_m else ""

            running_m = _RE_MT_INTF_DETAIL_RUNNING.search(record)
            running = running_m.group(1).lower() in ("yes", "true") if running_m else False

            disabled_m = _RE_MT_INTF_DETAIL_DISABLED.search(record)
            disabled = disabled_m.group(1).lower() in ("yes", "true") if disabled_m else False

            speed_m = _RE_MT_INTF_DETAIL_SPEED.search(record)
            speed = speed_m.group(1) if speed_m else ""

            comment_m = _RE_MT_INTF_DETAIL_COMMENT.search(record)
            description = comment_m.group(1) if comment_m else ""

            rx_m = _RE_MT_INTF_DETAIL_RX.search(record)
            tx_m = _RE_MT_INTF_DETAIL_TX.search(record)
            rx_err_m = _RE_MT_INTF_DETAIL_RX_ERR.search(record)
            tx_err_m = _RE_MT_INTF_DETAIL_TX_ERR.search(record)

            in_octets = int(rx_m.group(1)) if rx_m else 0
            out_octets = int(tx_m.group(1)) if tx_m else 0
            in_errors = int(rx_err_m.group(1)) if rx_err_m else 0
            out_errors = int(tx_err_m.group(1)) if tx_err_m else 0

            # Status
            if disabled:
                status = "administratively down"
                proto = "down"
            elif running:
                status = "up"
                proto = "up"
            else:
                status = "down"
                proto = "down"

            # IP address
            ip_address = ""
            subnet_mask = ""
            if name in ip_map:
                ip_address, subnet_mask = ip_map[name]

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
                    extra={"type": intf_type} if intf_type else {},
                )
            )

        return interfaces

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def get_health(self) -> DeviceHealth:
        output = await self.send_command("/system resource print")

        # CPU load
        cpu_load_m = _RE_MT_CPU_LOAD.search(output)
        cpu_percent = float(cpu_load_m.group(1)) if cpu_load_m else 0.0

        # Memory
        total_mem_m = _RE_MT_TOTAL_MEM.search(output)
        free_mem_m = _RE_MT_FREE_MEM.search(output)

        mem_total = _parse_mt_size(total_mem_m.group(1)) if total_mem_m else 0
        mem_free = _parse_mt_size(free_mem_m.group(1)) if free_mem_m else 0
        mem_used = mem_total - mem_free
        mem_pct = (mem_used / mem_total * 100.0) if mem_total else 0.0

        # Disk
        total_disk_m = _RE_MT_TOTAL_DISK.search(output)
        free_disk_m = _RE_MT_FREE_DISK.search(output)

        disk_total = _parse_mt_size(total_disk_m.group(1)) if total_disk_m else 0
        disk_free = _parse_mt_size(free_disk_m.group(1)) if free_disk_m else 0
        disk_used = disk_total - disk_free
        disk_pct = (disk_used / disk_total * 100.0) if disk_total else 0.0

        return DeviceHealth(
            cpu_percent=round(cpu_percent, 2),
            memory_used_bytes=mem_used,
            memory_total_bytes=mem_total,
            memory_percent=round(mem_pct, 2),
            disk_used_bytes=disk_used,
            disk_total_bytes=disk_total,
            disk_percent=round(disk_pct, 2),
        )

    # ------------------------------------------------------------------
    # Ping / Traceroute
    # ------------------------------------------------------------------

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        output = await self.send_command(
            f"/ping address={target} count={count}", timeout=30 + count * 2
        )
        result: dict[str, Any] = {"raw_output": output, "success": False}

        sent_m = _RE_MT_PING_SENT.search(output)
        recv_m = _RE_MT_PING_RECV.search(output)
        if sent_m and recv_m:
            sent = int(sent_m.group(1))
            recv = int(recv_m.group(1))
            result["sent"] = sent
            result["received"] = recv
            result["success_rate"] = int((recv / sent * 100) if sent else 0)
            result["success"] = recv > 0

        min_m = _RE_MT_PING_MIN.search(output)
        avg_m = _RE_MT_PING_AVG.search(output)
        max_m = _RE_MT_PING_MAX.search(output)
        if min_m:
            result["rtt_min"] = float(min_m.group(1))
        if avg_m:
            result["rtt_avg"] = float(avg_m.group(1))
        if max_m:
            result["rtt_max"] = float(max_m.group(1))

        return result

    async def traceroute(self, target: str) -> dict[str, Any]:
        output = await self.send_command(
            f"/tool traceroute address={target} count=1", timeout=120
        )
        hops: list[dict[str, Any]] = []
        for line in output.splitlines():
            m = re.match(r"^\s*(\d+)\s+([\d\.]+|\*)", line)
            if m:
                hop_num = int(m.group(1))
                addr = m.group(2)
                hops.append({
                    "hop": hop_num,
                    "address": addr if addr != "*" else None,
                })
        return {"raw_output": output, "hops": hops}
