"""Cisco IOS-XE device implementation.

IOS-XE is largely CLI-compatible with classic IOS, so this class inherits
most behaviour from ``CiscoIOSDevice`` and only overrides where the two
platforms diverge (e.g. Netmiko device type, RESTCONF support, slightly
different ``show version`` formatting).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.registry import register_device
from devices.vendors.cisco_ios import CiscoIOSDevice

logger = logging.getLogger(__name__)

# IOS-XE ``show version`` may contain slightly different patterns.
_RE_IOSXE_VERSION = re.compile(
    r"(?:Cisco IOS XE Software.*?Version|IOS-XE.*?Version|Version)\s+([\S]+)",
    re.IGNORECASE,
)
_RE_IOSXE_PLATFORM = re.compile(
    r"(?:cisco\s+([\w\-\/]+)\s+\(|Cisco\s+([\w\-]+)\s+Virtual)", re.IGNORECASE
)
_RE_IOSXE_LICENSE = re.compile(
    r"License Level:\s+(\S+)", re.IGNORECASE
)
_RE_IOSXE_ROMMON = re.compile(
    r"ROM:\s+([\S ]+)", re.IGNORECASE
)

# ``show platform`` output for hardware health (IOS-XE specific)
_RE_PLATFORM_SLOT = re.compile(
    r"^\s*(\S+)\s+(\S+)\s+(ok|fail|disabled|ps-fail)", re.MULTILINE | re.IGNORECASE
)

# ``show environment`` temperature
_RE_ENV_TEMP = re.compile(
    r"(\S+)\s+Temperature Value:\s+([\d\.]+)\s+Degree", re.IGNORECASE
)


@register_device("cisco_iosxe")
class CiscoIOSXEDevice(CiscoIOSDevice):
    """Cisco IOS-XE device driver (ISR 4000, Catalyst 9000, CSR1000v, etc.).

    Inherits the bulk of parsing from :class:`CiscoIOSDevice` and overrides
    only where IOS-XE has distinct output.
    """

    netmiko_type = "cisco_xe"

    # ------------------------------------------------------------------
    # Facts (override to pick up IOS-XE specific fields)
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        """Parse ``show version`` with IOS-XE specific regexes first,
        falling back to the IOS parser for shared fields.
        """
        output = await self.send_command("show version")

        # Try IOS-XE specific patterns, fall back to inherited IOS patterns
        from devices.vendors.cisco_ios import (
            _RE_HOSTNAME,
            _RE_SERIAL,
            _RE_UPTIME,
            _RE_VERSION,
            _RE_MODEL,
            _RE_IP_BRIEF,
        )

        hostname_m = _RE_HOSTNAME.search(output)
        version_m = _RE_IOSXE_VERSION.search(output) or _RE_VERSION.search(output)
        model_m = _RE_IOSXE_PLATFORM.search(output) or _RE_MODEL.search(output)
        serial_m = _RE_SERIAL.search(output)
        uptime_m = _RE_UPTIME.search(output)
        license_m = _RE_IOSXE_LICENSE.search(output)
        rommon_m = _RE_IOSXE_ROMMON.search(output)

        hostname = hostname_m.group(1) if hostname_m else self.host
        version = version_m.group(1).rstrip(",") if version_m else ""
        model = ""
        if model_m:
            model = model_m.group(1) or model_m.group(2) or ""
        serial = serial_m.group(1) if serial_m else ""
        uptime_str = uptime_m.group(1).strip() if uptime_m else ""
        uptime_seconds = self._parse_uptime(uptime_str)

        extra: dict[str, Any] = {}
        if license_m:
            extra["license_level"] = license_m.group(1)
        if rommon_m:
            extra["rommon_version"] = rommon_m.group(1).strip()

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
            extra=extra,
        )

    # ------------------------------------------------------------------
    # Health (enhanced with IOS-XE platform data)
    # ------------------------------------------------------------------

    async def get_health(self) -> DeviceHealth:
        """Gather health metrics, adding IOS-XE specific ``show platform``
        and ``show environment`` data to the base IOS health check.
        """
        # Get base IOS health first
        health = await super().get_health()

        # Temperature (IOS-XE ``show environment``)
        try:
            env_output = await self.send_command("show environment")
            temp_matches = _RE_ENV_TEMP.findall(env_output)
            if temp_matches:
                # Use the highest temperature reading
                temps = [float(t[1]) for t in temp_matches]
                health.temperature_celsius = max(temps)
                health.extra["temperatures"] = {
                    name: float(val) for name, val in temp_matches
                }
        except Exception:
            pass

        # Platform health (slot status)
        try:
            plat_output = await self.send_command("show platform")
            slot_matches = _RE_PLATFORM_SLOT.findall(plat_output)
            if slot_matches:
                health.extra["platform_slots"] = [
                    {"slot": s[0], "type": s[1], "status": s[2]}
                    for s in slot_matches
                ]
        except Exception:
            pass

        return health

    # ------------------------------------------------------------------
    # RESTCONF convenience methods
    # ------------------------------------------------------------------

    async def get_facts_restconf(
        self, restconf_base: str = "https://localhost/restconf"
    ) -> DeviceFacts:
        """Retrieve facts via RESTCONF (IOS-XE native YANG model).

        This is a convenience method for environments where RESTCONF is
        available alongside SSH.  For pure RESTCONF access, use
        ``RESTCONFDevice`` instead.

        Parameters
        ----------
        restconf_base:
            Full RESTCONF base URL including scheme, host, and ``/restconf``.
        """
        import httpx

        url = f"{restconf_base}/data/Cisco-IOS-XE-native:native/version"
        headers = {"Accept": "application/yang-data+json"}
        async with httpx.AsyncClient(
            auth=(self.username, self.password),
            verify=False,
            timeout=self.timeout,
        ) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            version = str(data.get("Cisco-IOS-XE-native:version", ""))

        return DeviceFacts(
            hostname=self.host,
            vendor="Cisco",
            os_version=version,
        )

    async def get_interfaces_restconf(
        self, restconf_base: str = "https://localhost/restconf"
    ) -> list[InterfaceInfo]:
        """Retrieve interfaces via RESTCONF IETF interfaces model."""
        import httpx

        url = f"{restconf_base}/data/ietf-interfaces:interfaces"
        headers = {"Accept": "application/yang-data+json"}
        async with httpx.AsyncClient(
            auth=(self.username, self.password),
            verify=False,
            timeout=self.timeout,
        ) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        iface_list = data.get("ietf-interfaces:interfaces", {}).get("interface", [])
        interfaces: list[InterfaceInfo] = []
        for iface in iface_list:
            name = iface.get("name", "")
            enabled = iface.get("enabled", True)
            oper_status = iface.get("oper-status", "unknown")
            description = iface.get("description", "")
            mtu = iface.get("mtu", 0)
            phys_addr = iface.get("phys-address", "")

            ipv4 = iface.get("ietf-ip:ipv4", {})
            addrs = ipv4.get("address", [])
            ip_address = addrs[0].get("ip", "") if addrs else ""
            mask = str(addrs[0].get("netmask", "")) if addrs else ""

            stats = iface.get("statistics", {})

            if not enabled:
                status = "administratively down"
            elif oper_status == "up":
                status = "up"
            else:
                status = "down"

            interfaces.append(
                InterfaceInfo(
                    name=name,
                    status=status,
                    protocol_status="up" if oper_status == "up" else "down",
                    ip_address=ip_address,
                    subnet_mask=mask,
                    mtu=int(mtu) if mtu else 0,
                    mac_address=phys_addr,
                    description=description,
                    in_octets=int(stats.get("in-octets", 0)),
                    out_octets=int(stats.get("out-octets", 0)),
                    in_errors=int(stats.get("in-errors", 0)),
                    out_errors=int(stats.get("out-errors", 0)),
                    in_discards=int(stats.get("in-discards", 0)),
                    out_discards=int(stats.get("out-discards", 0)),
                )
            )

        return interfaces
