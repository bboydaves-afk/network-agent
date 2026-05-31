"""pfSense device implementation (FreeBSD shell via SSH)."""

from __future__ import annotations

import logging
import re
from typing import Any

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.registry import register_device
from devices.ssh_device import SSHDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for FreeBSD / pfSense CLI output
# ---------------------------------------------------------------------------

# uname -a
_RE_PF_UNAME_VERSION = re.compile(
    r"FreeBSD\s+(\S+)\s+(\S+)", re.IGNORECASE
)

# pfctl -s info (state table statistics)
_RE_PF_STATES_CURRENT = re.compile(
    r"current entries\s+(\d+)", re.IGNORECASE
)
_RE_PF_STATES_TOTAL = re.compile(
    r"total entries\s+(\d+)", re.IGNORECASE
)

# ifconfig output
_RE_PF_IFCONFIG_HEADER = re.compile(
    r"^(\S+):\s+flags=", re.MULTILINE
)
_RE_PF_IFCONFIG_STATUS = re.compile(
    r"status:\s+(\w+)", re.IGNORECASE
)
_RE_PF_IFCONFIG_INET = re.compile(
    r"inet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+(0x[0-9a-fA-F]+|[\d\.]+)",
    re.IGNORECASE,
)
_RE_PF_IFCONFIG_ETHER = re.compile(
    r"ether\s+([0-9a-fA-F:]+)", re.IGNORECASE
)
_RE_PF_IFCONFIG_MTU = re.compile(
    r"mtu\s+(\d+)", re.IGNORECASE
)
_RE_PF_IFCONFIG_MEDIA = re.compile(
    r"media:\s+(.+)", re.IGNORECASE
)
_RE_PF_IFCONFIG_DESC = re.compile(
    r"description:\s+(.+)", re.IGNORECASE
)
_RE_PF_IFCONFIG_RX_BYTES = re.compile(
    r"(?:RX|input).*?(\d+)\s+bytes", re.IGNORECASE | re.DOTALL
)
_RE_PF_IFCONFIG_TX_BYTES = re.compile(
    r"(?:TX|output).*?(\d+)\s+bytes", re.IGNORECASE | re.DOTALL
)
_RE_PF_IFCONFIG_RX_ERRORS = re.compile(
    r"(?:RX|input).*?(\d+)\s+errors", re.IGNORECASE | re.DOTALL
)
_RE_PF_IFCONFIG_TX_ERRORS = re.compile(
    r"(?:TX|output).*?(\d+)\s+errors", re.IGNORECASE | re.DOTALL
)
_RE_PF_IFCONFIG_IN_LINE = re.compile(
    r"^\s+input:.*?(\d+)\s+packets\s+(\d+)\s+bytes.*?(\d+)\s+errors",
    re.MULTILINE | re.IGNORECASE,
)
_RE_PF_IFCONFIG_OUT_LINE = re.compile(
    r"^\s+output:.*?(\d+)\s+packets\s+(\d+)\s+bytes.*?(\d+)\s+errors",
    re.MULTILINE | re.IGNORECASE,
)

# top -b output for CPU / memory
_RE_PF_CPU = re.compile(
    r"CPU:\s+([\d\.]+)%\s+user,\s+([\d\.]+)%\s+nice,\s+([\d\.]+)%\s+system.*?([\d\.]+)%\s+idle",
    re.IGNORECASE,
)
_RE_PF_CPU_ALT = re.compile(
    r"CPU\s+usage:\s+([\d\.]+)%\s+used", re.IGNORECASE
)

# sysctl output
_RE_PF_MEM_PHYS = re.compile(r"hw\.physmem:\s+(\d+)", re.IGNORECASE)
_RE_PF_MEM_REAL = re.compile(r"hw\.realmem:\s+(\d+)", re.IGNORECASE)
_RE_PF_MEM_PAGE_SIZE = re.compile(r"hw\.pagesize:\s+(\d+)", re.IGNORECASE)
_RE_PF_MEM_FREE_PAGES = re.compile(r"vm\.stats\.vm\.v_free_count:\s+(\d+)", re.IGNORECASE)
_RE_PF_MEM_INACTIVE_PAGES = re.compile(r"vm\.stats\.vm\.v_inactive_count:\s+(\d+)", re.IGNORECASE)
_RE_PF_CPU_COUNT = re.compile(r"hw\.ncpu:\s+(\d+)", re.IGNORECASE)
_RE_PF_HOSTNAME = re.compile(r"kern\.hostname:\s+(\S+)", re.IGNORECASE)

# pfctl -sr (rules)
_RE_PF_RULE = re.compile(
    r"^(pass|block|match)\s+(in|out)\s+(?:quick\s+)?(?:on\s+(\S+)\s+)?"
    r"(?:inet6?\s+)?(?:proto\s+(\S+)\s+)?"
    r"(?:from\s+(\S+)\s+)?"
    r"(?:to\s+(\S+)\s*)?"
    r"(?:port\s+=?\s*(\S+)\s*)?"
    r"(?:flags\s+\S+\s*)?"
    r"(?:keep state)?",
    re.MULTILINE | re.IGNORECASE,
)

# Disk (df)
_RE_PF_DF = re.compile(
    r"^(/dev/\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)%\s+(/\S*)",
    re.MULTILINE,
)


def _hex_netmask_to_dotted(hex_mask: str) -> str:
    """Convert ``0xffffff00`` to ``255.255.255.0``."""
    if hex_mask.startswith("0x"):
        val = int(hex_mask, 16)
        return ".".join(str((val >> (8 * i)) & 0xFF) for i in range(3, -1, -1))
    return hex_mask


@register_device("pfsense")
class PfSenseDevice(SSHDevice):
    """pfSense device driver (FreeBSD-based firewall, SSH shell access).

    pfSense runs FreeBSD and does not have a Netmiko-style CLI. We
    use the ``"linux"`` Netmiko device type (which works for any
    Unix-like shell) and execute standard FreeBSD / pfSense commands.
    """

    netmiko_type = "linux"

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        """Retrieve the pfSense XML configuration file."""
        if config_type in ("running", "full"):
            return await self.send_command("cat /cf/conf/config.xml", timeout=30)
        elif config_type == "backup":
            return await self.send_command("cat /cf/conf/backup/*.xml", timeout=60)
        else:
            return await self.send_command("cat /cf/conf/config.xml", timeout=30)

    async def send_config(self, commands: list[str]) -> str:
        """Execute shell commands (e.g. for XML config manipulation).

        pfSense does not have a dedicated config mode; commands are
        regular shell commands.
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
        uname_output = await self.send_command("uname -a")
        sysctl_output = await self.send_command(
            "sysctl kern.hostname hw.physmem hw.ncpu"
        )

        # pfSense version
        pf_version = ""
        try:
            pf_ver_output = await self.send_command(
                "cat /etc/version 2>/dev/null || cat /etc/pf.os 2>/dev/null || echo unknown"
            )
            pf_version = pf_ver_output.strip().splitlines()[0] if pf_ver_output.strip() else ""
        except Exception:
            pass

        # Also try pfSense-specific command
        if not pf_version or pf_version == "unknown":
            try:
                pf_ver_output = await self.send_command(
                    "cat /etc/version.buildtime 2>/dev/null || true"
                )
            except Exception:
                pass

        # Hostname
        hostname = self.host
        hostname_m = _RE_PF_HOSTNAME.search(sysctl_output)
        if hostname_m:
            hostname = hostname_m.group(1)

        # OS version from uname
        os_version = ""
        uname_m = _RE_PF_UNAME_VERSION.search(uname_output)
        if uname_m:
            os_version = uname_m.group(2)

        if pf_version and pf_version != "unknown":
            os_version = f"pfSense {pf_version} (FreeBSD {os_version})"
        else:
            os_version = f"FreeBSD {os_version}"

        # Model from hardware info
        model = ""
        try:
            hw_output = await self.send_command("sysctl hw.model 2>/dev/null || true")
            hw_m = re.search(r"hw\.model:\s+(.+)", hw_output)
            if hw_m:
                model = hw_m.group(1).strip()
        except Exception:
            pass

        # Serial
        serial = ""
        try:
            serial_output = await self.send_command(
                "sysctl kern.hostuuid 2>/dev/null || kenv smbios.system.serial 2>/dev/null || true"
            )
            serial_m = re.search(r"(?:kern\.hostuuid|smbios\.system\.serial):\s*(\S+)", serial_output)
            if serial_m:
                serial = serial_m.group(1)
        except Exception:
            pass

        # Uptime
        uptime_str = ""
        uptime_seconds = 0
        try:
            uptime_output = await self.send_command("sysctl kern.boottime")
            # kern.boottime: { sec = 1672531200, usec = 0 } ...
            bt_m = re.search(r"sec\s*=\s*(\d+)", uptime_output)
            if bt_m:
                import time
                boot_time = int(bt_m.group(1))
                uptime_seconds = int(time.time()) - boot_time
                days = uptime_seconds // 86400
                hours = (uptime_seconds % 86400) // 3600
                minutes = (uptime_seconds % 3600) // 60
                uptime_str = f"{days} days, {hours} hours, {minutes} minutes"
        except Exception:
            pass

        # Fallback: parse uptime command
        if not uptime_str:
            try:
                uptime_output = await self.send_command("uptime")
                up_m = re.search(r"up\s+(.+?),\s+\d+\s+user", uptime_output)
                if up_m:
                    uptime_str = up_m.group(1).strip()
            except Exception:
                pass

        return DeviceFacts(
            hostname=hostname,
            vendor="Netgate/pfSense",
            model=model,
            serial_number=serial,
            os_version=os_version,
            uptime=uptime_str,
            uptime_seconds=uptime_seconds,
        )

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    async def get_interfaces(self) -> list[InterfaceInfo]:
        ifconfig_output = await self.send_command("ifconfig -a")

        # Split into per-interface blocks
        blocks = self._split_ifconfig_blocks(ifconfig_output)

        # Get pfSense interface assignments
        pf_intf_map: dict[str, str] = {}
        try:
            assign_output = await self.send_command(
                "cat /cf/conf/config.xml | grep -A2 '<if>' | head -50"
            )
            # Rough parse: extract interface -> pfSense name pairs
            for m in re.finditer(r"<if>(\S+)</if>.*?<descr>([^<]*)</descr>", assign_output, re.DOTALL):
                pf_intf_map[m.group(1)] = m.group(2)
        except Exception:
            pass

        interfaces: list[InterfaceInfo] = []
        for name, block in blocks.items():
            # Skip loopback and other virtual interfaces
            # (but include them -- user may want to see them)

            # Status
            status_m = _RE_PF_IFCONFIG_STATUS.search(block)
            if status_m:
                raw_status = status_m.group(1).lower()
                if raw_status == "active":
                    status = "up"
                    proto = "up"
                elif raw_status == "no carrier":
                    status = "down"
                    proto = "down"
                else:
                    status = raw_status
                    proto = raw_status
            else:
                # Check flags for UP
                if "<UP," in block or ",UP>" in block or ",UP," in block:
                    status = "up"
                    proto = "up"
                else:
                    status = "down"
                    proto = "down"

            # IP address
            ip_m = _RE_PF_IFCONFIG_INET.search(block)
            ip_address = ip_m.group(1) if ip_m else ""
            subnet_mask = ""
            if ip_m:
                raw_mask = ip_m.group(2)
                if raw_mask.startswith("0x"):
                    subnet_mask = _hex_netmask_to_dotted(raw_mask)
                else:
                    subnet_mask = raw_mask

            # MAC
            mac_m = _RE_PF_IFCONFIG_ETHER.search(block)
            mac = mac_m.group(1).lower() if mac_m else ""

            # MTU
            mtu_m = _RE_PF_IFCONFIG_MTU.search(block)
            mtu = int(mtu_m.group(1)) if mtu_m else 0

            # Speed / media
            media_m = _RE_PF_IFCONFIG_MEDIA.search(block)
            speed = media_m.group(1).strip() if media_m else ""

            # Description (pfSense assignment or ifconfig description)
            description = pf_intf_map.get(name, "")
            desc_m = _RE_PF_IFCONFIG_DESC.search(block)
            if desc_m and not description:
                description = desc_m.group(1).strip()

            # Counters (FreeBSD ifconfig shows input/output lines)
            in_octets = 0
            out_octets = 0
            in_errors = 0
            out_errors = 0

            in_line_m = _RE_PF_IFCONFIG_IN_LINE.search(block)
            if in_line_m:
                in_octets = int(in_line_m.group(2))
                in_errors = int(in_line_m.group(3))

            out_line_m = _RE_PF_IFCONFIG_OUT_LINE.search(block)
            if out_line_m:
                out_octets = int(out_line_m.group(2))
                out_errors = int(out_line_m.group(3))

            # Fallback counters from rx/tx byte patterns
            if not in_octets:
                rx_m = _RE_PF_IFCONFIG_RX_BYTES.search(block)
                if rx_m:
                    in_octets = int(rx_m.group(1))
            if not out_octets:
                tx_m = _RE_PF_IFCONFIG_TX_BYTES.search(block)
                if tx_m:
                    out_octets = int(tx_m.group(1))

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
        # CPU: use top
        cpu_percent = 0.0
        try:
            top_output = await self.send_command("top -b -d 1 | head -10", timeout=15)
            cpu_m = _RE_PF_CPU.search(top_output)
            if cpu_m:
                idle = float(cpu_m.group(4))
                cpu_percent = 100.0 - idle
            else:
                # Try sysctl
                loadavg_output = await self.send_command("sysctl vm.loadavg")
                load_m = re.search(r"vm\.loadavg:\s*\{\s*([\d\.]+)", loadavg_output)
                if load_m:
                    # Rough: load / ncpus * 100
                    ncpu_out = await self.send_command("sysctl -n hw.ncpu")
                    ncpu = int(ncpu_out.strip()) if ncpu_out.strip().isdigit() else 1
                    cpu_percent = min(float(load_m.group(1)) / ncpu * 100.0, 100.0)
        except Exception:
            pass

        # Memory: use sysctl
        mem_total = 0
        mem_used = 0
        try:
            sysctl_output = await self.send_command(
                "sysctl hw.physmem hw.pagesize vm.stats.vm.v_free_count "
                "vm.stats.vm.v_inactive_count vm.stats.vm.v_cache_count"
            )
            phys_m = _RE_PF_MEM_PHYS.search(sysctl_output)
            page_m = _RE_PF_MEM_PAGE_SIZE.search(sysctl_output)
            free_m = _RE_PF_MEM_FREE_PAGES.search(sysctl_output)
            inactive_m = _RE_PF_MEM_INACTIVE_PAGES.search(sysctl_output)
            cache_m = re.search(r"vm\.stats\.vm\.v_cache_count:\s+(\d+)", sysctl_output)

            if phys_m:
                mem_total = int(phys_m.group(1))

            if page_m and free_m:
                page_size = int(page_m.group(1))
                free_pages = int(free_m.group(1))
                inactive_pages = int(inactive_m.group(1)) if inactive_m else 0
                cache_pages = int(cache_m.group(1)) if cache_m else 0
                # "used" = total - (free + inactive + cache) * pagesize
                available = (free_pages + inactive_pages + cache_pages) * page_size
                mem_used = max(mem_total - available, 0)
        except Exception:
            pass

        mem_pct = (mem_used / mem_total * 100.0) if mem_total else 0.0

        # Disk: df
        disk_total = 0
        disk_used = 0
        try:
            df_output = await self.send_command("df -k /")
            for m in _RE_PF_DF.finditer(df_output):
                disk_total = int(m.group(2)) * 1024  # KB -> bytes
                disk_used = int(m.group(3)) * 1024
                break
            # Fallback: simpler parsing
            if not disk_total:
                for line in df_output.splitlines():
                    parts = line.split()
                    if len(parts) >= 4 and parts[0].startswith("/"):
                        disk_total = int(parts[1]) * 1024
                        disk_used = int(parts[2]) * 1024
                        break
        except Exception:
            pass

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
    # Firewall-specific
    # ------------------------------------------------------------------

    async def get_pf_rules(self) -> list[dict[str, Any]]:
        """Retrieve pf firewall rules via ``pfctl -sr``."""
        output = await self.send_command("pfctl -sr", timeout=15)
        rules: list[dict[str, Any]] = []
        for i, line in enumerate(output.splitlines()):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            rule: dict[str, Any] = {"line": i, "raw": line}

            m = _RE_PF_RULE.match(line)
            if m:
                rule["action"] = m.group(1)           # pass/block/match
                rule["direction"] = m.group(2)         # in/out
                rule["interface"] = m.group(3) or ""
                rule["protocol"] = m.group(4) or ""
                rule["source"] = m.group(5) or "any"
                rule["destination"] = m.group(6) or "any"
                rule["port"] = m.group(7) or ""
            else:
                # Parse action at minimum
                parts = line.split()
                if parts:
                    rule["action"] = parts[0]

            if "quick" in line:
                rule["quick"] = True

            rules.append(rule)

        return rules

    async def get_state_count(self) -> int:
        """Return the number of active pf states."""
        output = await self.send_command("pfctl -s info")
        m = _RE_PF_STATES_CURRENT.search(output)
        if m:
            return int(m.group(1))
        return 0

    # ------------------------------------------------------------------
    # Ping / Traceroute
    # ------------------------------------------------------------------

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        output = await self.send_command(
            f"ping -c {count} {target}", timeout=30 + count * 2
        )
        result: dict[str, Any] = {"raw_output": output, "success": False}

        m = re.search(
            r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received",
            output, re.IGNORECASE,
        )
        if m:
            sent = int(m.group(1))
            recv = int(m.group(2))
            result["sent"] = sent
            result["received"] = recv
            result["success_rate"] = int((recv / sent * 100) if sent else 0)
            result["success"] = recv > 0

        rtt_m = re.search(
            r"min/avg/max/(?:std-dev|mdev)\s+=\s+([\d\.]+)/([\d\.]+)/([\d\.]+)",
            output, re.IGNORECASE,
        )
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
    def _split_ifconfig_blocks(output: str) -> dict[str, str]:
        """Split ``ifconfig -a`` output into per-interface blocks."""
        blocks: dict[str, str] = {}
        current_name: str | None = None
        current_lines: list[str] = []

        for line in output.splitlines():
            m = _RE_PF_IFCONFIG_HEADER.match(line)
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
