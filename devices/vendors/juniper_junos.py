"""Juniper JunOS device implementation."""

from __future__ import annotations

import logging
import re
from typing import Any

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.registry import register_device
from devices.ssh_device import SSHDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for JunOS CLI output
# ---------------------------------------------------------------------------

# show version
_RE_JUNOS_HOSTNAME = re.compile(r"^Hostname:\s+(\S+)", re.MULTILINE)
_RE_JUNOS_MODEL = re.compile(r"^Model:\s+(\S+)", re.MULTILINE)
_RE_JUNOS_VERSION = re.compile(
    r"(?:Junos:\s+|JUNOS (?:Base )?OS.*?\[)([\w\.\-]+)", re.IGNORECASE
)
_RE_JUNOS_VERSION_ALT = re.compile(
    r"JUNOS\s+[\w\s]*\[([\w\.\-]+)\]", re.IGNORECASE
)

# show chassis hardware
_RE_CHASSIS_SERIAL = re.compile(
    r"^Chassis\s+\S+\s+\S+\s+(\S+)", re.MULTILINE
)
_RE_CHASSIS_SERIAL_ALT = re.compile(
    r"Chassis\s+.*?(\S{10,})\s*$", re.MULTILINE
)

# show system uptime
_RE_JUNOS_UPTIME = re.compile(
    r"System booted:\s+.+\((.+?)\s+ago\)", re.IGNORECASE
)
_RE_JUNOS_UPTIME_ALT = re.compile(
    r"up\s+(.+?)(?:,\s+\d+\s+user|\s*$)", re.IGNORECASE
)

# show interfaces terse
# Name                Admin Link Proto    Local                 Remote
_RE_TERSE_LINE = re.compile(
    r"^(\S+)\s+"          # interface name
    r"(up|down)\s+"       # admin status
    r"(up|down)\s+"       # link status
    r"(\S*)\s*"           # protocol (inet, inet6, etc.)
    r"(\S*)",             # local address (ip/prefix)
    re.MULTILINE | re.IGNORECASE,
)

# show interfaces detail <name>
_RE_INTF_DETAIL_BLOCK = re.compile(
    r"^Physical interface:\s+(\S+)", re.MULTILINE
)
_RE_INTF_SPEED_JUNOS = re.compile(r"Speed:\s+(\S+)", re.IGNORECASE)
_RE_INTF_MTU_JUNOS = re.compile(r"MTU:\s+(\d+)", re.IGNORECASE)
_RE_INTF_MAC_JUNOS = re.compile(
    r"(?:Current address|Hardware address):\s+([0-9a-fA-F:]+)", re.IGNORECASE
)
_RE_INTF_DESC_JUNOS = re.compile(r"Description:\s+(.+)", re.IGNORECASE)
_RE_INTF_INPUT_BYTES = re.compile(r"Input\s+bytes\s*:\s*(\d+)", re.IGNORECASE)
_RE_INTF_OUTPUT_BYTES = re.compile(r"Output\s+bytes\s*:\s*(\d+)", re.IGNORECASE)
_RE_INTF_INPUT_ERRORS = re.compile(r"Input\s+errors\s*:\s*(\d+)", re.IGNORECASE)
_RE_INTF_OUTPUT_ERRORS = re.compile(r"Output\s+errors\s*:\s*(\d+)", re.IGNORECASE)

# show chassis routing-engine
_RE_RE_CPU_IDLE = re.compile(
    r"CPU utilization.*?Idle\s+(\d+)\s+percent", re.IGNORECASE | re.DOTALL
)
_RE_RE_CPU_USER = re.compile(r"User\s+(\d+)\s+percent", re.IGNORECASE)
_RE_RE_CPU_SYSTEM = re.compile(r"(?:Background|System)\s+(\d+)\s+percent", re.IGNORECASE)
_RE_RE_MEM_TOTAL = re.compile(r"Memory utilization\s+(\d+)\s+percent", re.IGNORECASE)
_RE_RE_MEM_BUFFER = re.compile(
    r"(\d+)\s+MB\s+used\s+\((\d+)\s+MB\s+installed\)", re.IGNORECASE
)
_RE_RE_MEM_ALT = re.compile(
    r"Memory.*?(\d+)\s+MB\s+total.*?(\d+)\s+MB\s+used", re.IGNORECASE | re.DOTALL
)
_RE_RE_TEMP = re.compile(
    r"Temperature\s+([\d\.]+)\s+degrees", re.IGNORECASE
)
_RE_RE_UPTIME = re.compile(
    r"Uptime\s+(.+)", re.IGNORECASE
)

# Ping
_RE_JUNOS_PING = re.compile(
    r"(\d+)\s+packets transmitted,\s+(\d+)\s+packets received", re.IGNORECASE
)
_RE_JUNOS_PING_RTT = re.compile(
    r"round-trip min/avg/max/stddev\s+=\s+([\d\.]+)/([\d\.]+)/([\d\.]+)/([\d\.]+)",
    re.IGNORECASE,
)


def _junos_uptime_to_seconds(uptime_str: str) -> int:
    """Parse JunOS uptime strings like '23 days, 4:32:10' or
    '1 week, 2 days, 05:10:22' into seconds.
    """
    total = 0
    # Named durations
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
    # HH:MM:SS format
    hms = re.search(r"(\d+):(\d+):(\d+)", uptime_str)
    if hms:
        total += int(hms.group(1)) * 3600 + int(hms.group(2)) * 60 + int(hms.group(3))
    return total


@register_device("juniper_junos")
class JuniperJunOSDevice(SSHDevice):
    """Juniper JunOS device driver (EX, MX, QFX, SRX, vSRX, etc.)."""

    netmiko_type = "juniper_junos"

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        """Retrieve JunOS configuration.

        ``config_type`` options:
        - ``"running"`` / ``"set"``  -> ``show configuration | display set``
        - ``"hierarchical"``         -> ``show configuration``
        - ``"candidate"``            -> ``show | compare``
        """
        if config_type in ("running", "set"):
            return await self.send_command(
                "show configuration | display set", timeout=60
            )
        elif config_type == "hierarchical":
            return await self.send_command("show configuration", timeout=60)
        elif config_type == "candidate":
            return await self.send_command("show | compare", timeout=30)
        else:
            return await self.send_command("show configuration", timeout=60)

    async def send_config(self, commands: list[str]) -> str:
        """Push configuration using JunOS commit model.

        Commands are entered in ``configure`` mode. A ``commit`` is
        automatically appended.  If the commit fails, a ``rollback 0``
        is issued.
        """
        self._require_connection()

        full_commands = list(commands)
        # Netmiko handles 'configure' mode entry, but we add commit explicitly
        try:
            output: str = await self._run_sync(
                self._net_connect.send_config_set,
                full_commands,
                exit_config_mode=False,
            )
            # Commit
            commit_output = await self._run_sync(
                self._net_connect.send_command,
                "commit",
                read_timeout=60,
            )
            output += "\n" + commit_output

            if "commit complete" not in commit_output.lower() and "error" in commit_output.lower():
                # Rollback
                rollback_output = await self._run_sync(
                    self._net_connect.send_command,
                    "rollback 0",
                    read_timeout=30,
                )
                output += "\n[ROLLBACK] " + rollback_output
                logger.warning("JunOS commit failed on %s, rolled back", self.host)

            # Exit configure mode
            try:
                await self._run_sync(self._net_connect.exit_config_mode)
            except Exception:
                pass

            return output
        except Exception as exc:
            # Attempt rollback on any error
            try:
                await self._run_sync(
                    self._net_connect.send_command, "rollback 0"
                )
                await self._run_sync(self._net_connect.exit_config_mode)
            except Exception:
                pass
            from core.exceptions import DeviceCommandError
            raise DeviceCommandError(
                device_id=self.host,
                message=f"JunOS config push failed: {exc}",
                command="; ".join(commands),
            ) from exc

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        version_output = await self.send_command("show version")
        hw_output = ""
        try:
            hw_output = await self.send_command("show chassis hardware")
        except Exception:
            pass
        uptime_output = ""
        try:
            uptime_output = await self.send_command("show system uptime")
        except Exception:
            pass

        hostname_m = _RE_JUNOS_HOSTNAME.search(version_output)
        model_m = _RE_JUNOS_MODEL.search(version_output)
        version_m = (
            _RE_JUNOS_VERSION.search(version_output)
            or _RE_JUNOS_VERSION_ALT.search(version_output)
        )

        hostname = hostname_m.group(1) if hostname_m else self.host
        model = model_m.group(1) if model_m else ""
        version = version_m.group(1) if version_m else ""

        # Serial from chassis hardware
        serial = ""
        if hw_output:
            serial_m = _RE_CHASSIS_SERIAL.search(hw_output)
            if not serial_m:
                serial_m = _RE_CHASSIS_SERIAL_ALT.search(hw_output)
            if serial_m:
                serial = serial_m.group(1)

        # Uptime
        uptime_str = ""
        uptime_seconds = 0
        if uptime_output:
            up_m = _RE_JUNOS_UPTIME.search(uptime_output)
            if up_m:
                uptime_str = up_m.group(1).strip()
            else:
                up_m2 = _RE_JUNOS_UPTIME_ALT.search(uptime_output)
                if up_m2:
                    uptime_str = up_m2.group(1).strip()
            uptime_seconds = _junos_uptime_to_seconds(uptime_str)

        # Interface count
        try:
            terse_output = await self.send_command("show interfaces terse")
            iface_count = len(_RE_TERSE_LINE.findall(terse_output))
        except Exception:
            iface_count = 0

        return DeviceFacts(
            hostname=hostname,
            vendor="Juniper",
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
        terse_output = await self.send_command("show interfaces terse")
        detail_output = await self.send_command("show interfaces detail", timeout=60)

        detail_blocks = self._split_detail_blocks(detail_output)

        interfaces: list[InterfaceInfo] = []
        for m in _RE_TERSE_LINE.finditer(terse_output):
            name = m.group(1)
            admin = m.group(2).lower()
            link = m.group(3).lower()
            proto = m.group(4)
            local_addr = m.group(5)

            ip_address = ""
            subnet_mask = ""
            if local_addr and "/" in local_addr:
                parts = local_addr.split("/")
                ip_address = parts[0]
                try:
                    prefix = int(parts[1])
                    subnet_mask = str(prefix)
                except ValueError:
                    subnet_mask = parts[1]

            # Physical interface name (strip unit, e.g. ge-0/0/0.0 -> ge-0/0/0)
            phys_name = name.split(".")[0]
            block = detail_blocks.get(phys_name, "")

            speed = ""
            mtu = 0
            mac = ""
            description = ""
            in_octets = 0
            out_octets = 0
            in_errors = 0
            out_errors = 0

            if block:
                sp_m = _RE_INTF_SPEED_JUNOS.search(block)
                if sp_m:
                    speed = sp_m.group(1)

                mtu_m = _RE_INTF_MTU_JUNOS.search(block)
                if mtu_m:
                    mtu = int(mtu_m.group(1))

                mac_m = _RE_INTF_MAC_JUNOS.search(block)
                if mac_m:
                    mac = mac_m.group(1).lower()

                desc_m = _RE_INTF_DESC_JUNOS.search(block)
                if desc_m:
                    description = desc_m.group(1).strip()

                ib_m = _RE_INTF_INPUT_BYTES.search(block)
                if ib_m:
                    in_octets = int(ib_m.group(1))

                ob_m = _RE_INTF_OUTPUT_BYTES.search(block)
                if ob_m:
                    out_octets = int(ob_m.group(1))

                ie_m = _RE_INTF_INPUT_ERRORS.search(block)
                if ie_m:
                    in_errors = int(ie_m.group(1))

                oe_m = _RE_INTF_OUTPUT_ERRORS.search(block)
                if oe_m:
                    out_errors = int(oe_m.group(1))

            if admin != "up":
                status = "administratively down"
            elif link == "up":
                status = "up"
            else:
                status = "down"

            interfaces.append(
                InterfaceInfo(
                    name=name,
                    status=status,
                    protocol_status=link,
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
        re_output = await self.send_command("show chassis routing-engine")

        # CPU
        cpu_idle_m = _RE_RE_CPU_IDLE.search(re_output)
        cpu_user_m = _RE_RE_CPU_USER.search(re_output)
        cpu_sys_m = _RE_RE_CPU_SYSTEM.search(re_output)

        if cpu_idle_m:
            cpu_percent = 100.0 - float(cpu_idle_m.group(1))
        elif cpu_user_m and cpu_sys_m:
            cpu_percent = float(cpu_user_m.group(1)) + float(cpu_sys_m.group(1))
        elif cpu_user_m:
            cpu_percent = float(cpu_user_m.group(1))
        else:
            cpu_percent = 0.0

        # Memory
        mem_total_bytes = 0
        mem_used_bytes = 0
        mem_percent = 0.0

        mem_pct_m = _RE_RE_MEM_TOTAL.search(re_output)
        mem_buf_m = _RE_RE_MEM_BUFFER.search(re_output)
        mem_alt_m = _RE_RE_MEM_ALT.search(re_output)

        if mem_buf_m:
            mem_used_bytes = int(mem_buf_m.group(1)) * 1024 * 1024
            mem_total_bytes = int(mem_buf_m.group(2)) * 1024 * 1024
        elif mem_alt_m:
            mem_total_bytes = int(mem_alt_m.group(1)) * 1024 * 1024
            mem_used_bytes = int(mem_alt_m.group(2)) * 1024 * 1024

        if mem_pct_m:
            mem_percent = float(mem_pct_m.group(1))
        elif mem_total_bytes:
            mem_percent = (mem_used_bytes / mem_total_bytes * 100.0)

        # Temperature
        temp_celsius: float | None = None
        temp_m = _RE_RE_TEMP.search(re_output)
        if temp_m:
            temp_celsius = float(temp_m.group(1))

        return DeviceHealth(
            cpu_percent=round(cpu_percent, 2),
            memory_used_bytes=mem_used_bytes,
            memory_total_bytes=mem_total_bytes,
            memory_percent=round(mem_percent, 2),
            temperature_celsius=temp_celsius,
        )

    # ------------------------------------------------------------------
    # Ping / Traceroute
    # ------------------------------------------------------------------

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        output = await self.send_command(
            f"ping {target} count {count} rapid", timeout=30 + count * 2
        )
        result: dict[str, Any] = {"raw_output": output, "success": False}

        m = _RE_JUNOS_PING.search(output)
        if m:
            sent = int(m.group(1))
            recv = int(m.group(2))
            result["sent"] = sent
            result["received"] = recv
            result["success_rate"] = int((recv / sent * 100) if sent else 0)
            result["success"] = recv > 0

        rtt_m = _RE_JUNOS_PING_RTT.search(output)
        if rtt_m:
            result["rtt_min"] = float(rtt_m.group(1))
            result["rtt_avg"] = float(rtt_m.group(2))
            result["rtt_max"] = float(rtt_m.group(3))
            result["rtt_stddev"] = float(rtt_m.group(4))

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
    def _split_detail_blocks(output: str) -> dict[str, str]:
        """Split ``show interfaces detail`` into per-interface blocks."""
        blocks: dict[str, str] = {}
        current_name: str | None = None
        current_lines: list[str] = []

        for line in output.splitlines():
            m = _RE_INTF_DETAIL_BLOCK.match(line)
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
