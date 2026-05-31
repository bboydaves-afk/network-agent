"""Cisco NX-OS device implementation (Nexus switches).

NX-OS is largely CLI-compatible with classic IOS, so this class inherits
most behaviour from ``CiscoIOSDevice`` and only overrides where the two
platforms diverge (e.g. Netmiko device type, ``show version`` output
format, and health data sources).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.registry import register_device
from devices.vendors.cisco_ios import (
    CiscoIOSDevice,
    _RE_IP_BRIEF,
    _RE_SERIAL,
    _RE_UPTIME,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NX-OS specific regex patterns
# ---------------------------------------------------------------------------

# show version — NX-OS format
_RE_NXOS_VERSION = re.compile(
    r"(?:NXOS|system|kickstart):\s+version\s+(\S+)", re.IGNORECASE
)
_RE_NXOS_VERSION_ALT = re.compile(
    r"(?:NXOS\s+image|Software).*?[Vv]ersion\s+(\S+)", re.IGNORECASE
)
_RE_NXOS_PLATFORM = re.compile(
    r"(?:cisco\s+Nexus\s*(\S+)|Hardware\s+cisco\s+Nexus\s*(\S+))", re.IGNORECASE
)
_RE_NXOS_HOSTNAME = re.compile(
    r"Device name:\s*(\S+)", re.IGNORECASE
)

# show environment temperature
_RE_NXOS_TEMP = re.compile(
    r"(\S+)\s+(\d+)\s+\(\S+\)\s+\S+\s+\S+\s+(ok|fail|warn)",
    re.IGNORECASE,
)

# show module — slot status
_RE_NXOS_MODULE = re.compile(
    r"^\s*(\d+)\s+\d+\s+(.+?)\s{2,}(\S+)\s*$", re.MULTILINE
)


@register_device("cisco_nxos")
class CiscoNXOSDevice(CiscoIOSDevice):
    """Cisco NX-OS device driver (Nexus 3000/5000/7000/9000).

    Inherits the bulk of parsing from :class:`CiscoIOSDevice` and overrides
    only where NX-OS has distinct output.
    """

    netmiko_type = "cisco_nxos"

    # ------------------------------------------------------------------
    # Facts (override to pick up NX-OS specific fields)
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        output = await self.send_command("show version")

        hostname_m = _RE_NXOS_HOSTNAME.search(output)
        version_m = (
            _RE_NXOS_VERSION.search(output)
            or _RE_NXOS_VERSION_ALT.search(output)
        )
        platform_m = _RE_NXOS_PLATFORM.search(output)
        serial_m = _RE_SERIAL.search(output)
        uptime_m = _RE_UPTIME.search(output)

        hostname = hostname_m.group(1) if hostname_m else self.host
        version = version_m.group(1).rstrip(",") if version_m else ""
        model = ""
        if platform_m:
            model = platform_m.group(1) or platform_m.group(2) or ""
            model = f"Nexus {model}" if model else ""
        serial = serial_m.group(1) if serial_m else ""
        uptime_str = uptime_m.group(1).strip() if uptime_m else ""
        uptime_seconds = self._parse_uptime(uptime_str)

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
    # Health (enhanced with NX-OS module + temperature data)
    # ------------------------------------------------------------------

    async def get_health(self) -> DeviceHealth:
        health = await super().get_health()

        # Temperature — show environment temperature
        try:
            env_output = await self.send_command("show environment temperature")
            temps: list[float] = []
            for m in _RE_NXOS_TEMP.finditer(env_output):
                temps.append(float(m.group(2)))
            if temps:
                health.temperature_celsius = max(temps)
                health.extra["temperature_max"] = max(temps)
        except Exception:
            pass

        # Module status — show module
        try:
            mod_output = await self.send_command("show module")
            modules = _RE_NXOS_MODULE.findall(mod_output)
            if modules:
                health.extra["modules"] = [
                    {"slot": s[0], "model": s[1].strip(), "status": s[2]}
                    for s in modules
                ]
        except Exception:
            pass

        return health
