"""Serial console vendor drivers.

Each class inherits from ``SerialDevice`` and delegates vendor-specific
parsing (``get_facts``, ``get_interfaces``, ``get_health``, ``ping``,
``traceroute``) to the corresponding SSH vendor class.  Only
``send_command`` (the transport) differs -- everything above it is
reused verbatim.
"""

from __future__ import annotations

import re
from typing import Any

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.registry import register_device
from devices.serial_device import SerialDevice

# ---------------------------------------------------------------------------
# Import vendor SSH classes for method delegation
# ---------------------------------------------------------------------------
from devices.vendors.cisco_ios import CiscoIOSDevice
from devices.vendors.cisco_iosxe import CiscoIOSXEDevice
from devices.vendors.juniper_junos import JuniperJunOSDevice
from devices.vendors.fortinet import FortinetDevice
from devices.vendors.paloalto import PaloAltoDevice
from devices.vendors.mikrotik import MikroTikDevice
from devices.vendors.aruba import ArubaDevice
from devices.vendors.pfsense import PfSenseDevice
from devices.vendors.sophos import SophosDevice
from devices.vendors.cisco_nxos import CiscoNXOSDevice
from devices.vendors.cisco_asa import CiscoASADevice
from devices.vendors.aruba_aoscx import ArubaAOSCXDevice


# ===================================================================
# Cisco IOS — Serial
# ===================================================================

@register_device("cisco_ios_serial")
class CiscoIOSSerialDevice(SerialDevice):
    """Cisco IOS device driver via serial console."""

    prompt_pattern = re.compile(
        r"[\r\n]([\w\-\.]+)(?:\([^\)]+\))?[#>]\s*$"
    )
    config_mode_command = "configure terminal"
    config_mode_exit = "end"
    enable_command = "enable"
    disable_paging_command = "terminal length 0"

    # Inherit static helpers from SSH vendor (must re-wrap as staticmethod)
    _split_interface_blocks = staticmethod(CiscoIOSDevice._split_interface_blocks)
    _parse_uptime = staticmethod(CiscoIOSDevice._parse_uptime)

    # Reuse SSH vendor parsing ----------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        return await CiscoIOSDevice.get_config(self, config_type)

    async def get_facts(self) -> DeviceFacts:
        return await CiscoIOSDevice.get_facts(self)

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await CiscoIOSDevice.get_interfaces(self)

    async def get_health(self) -> DeviceHealth:
        return await CiscoIOSDevice.get_health(self)

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        return await CiscoIOSDevice.ping(self, target, count)

    async def traceroute(self, target: str) -> dict[str, Any]:
        return await CiscoIOSDevice.traceroute(self, target)


# ===================================================================
# Cisco IOS-XE — Serial
# ===================================================================

@register_device("cisco_iosxe_serial")
class CiscoIOSXESerialDevice(SerialDevice):
    """Cisco IOS-XE device driver via serial console."""

    prompt_pattern = re.compile(
        r"[\r\n]([\w\-\.]+)(?:\([^\)]+\))?[#>]\s*$"
    )
    config_mode_command = "configure terminal"
    config_mode_exit = "end"
    enable_command = "enable"
    disable_paging_command = "terminal length 0"

    _split_interface_blocks = staticmethod(CiscoIOSDevice._split_interface_blocks)
    _parse_uptime = staticmethod(CiscoIOSDevice._parse_uptime)

    async def get_config(self, config_type: str = "running") -> str:
        return await CiscoIOSXEDevice.get_config(self, config_type)

    async def get_facts(self) -> DeviceFacts:
        return await CiscoIOSXEDevice.get_facts(self)

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await CiscoIOSXEDevice.get_interfaces(self)

    async def get_health(self) -> DeviceHealth:
        return await CiscoIOSXEDevice.get_health(self)

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        return await CiscoIOSXEDevice.ping(self, target, count)

    async def traceroute(self, target: str) -> dict[str, Any]:
        return await CiscoIOSXEDevice.traceroute(self, target)


# ===================================================================
# Juniper JunOS — Serial
# ===================================================================

@register_device("juniper_junos_serial")
class JuniperJunOSSerialDevice(SerialDevice):
    """Juniper JunOS device driver via serial console."""

    prompt_pattern = re.compile(
        r"[\r\n][\w\-\.]+@[\w\-\.]+"       # user@hostname
        r"(?::[\w\/~]+)?"                    # optional :path
        r"[#>%]\s*$"
    )
    config_mode_command = "configure"
    config_mode_exit = "commit and-quit"
    enable_command = "cli"
    disable_paging_command = "set cli screen-length 0"

    _split_detail_blocks = staticmethod(JuniperJunOSDevice._split_detail_blocks)

    async def get_config(self, config_type: str = "running") -> str:
        return await JuniperJunOSDevice.get_config(self, config_type)

    async def get_facts(self) -> DeviceFacts:
        return await JuniperJunOSDevice.get_facts(self)

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await JuniperJunOSDevice.get_interfaces(self)

    async def get_health(self) -> DeviceHealth:
        return await JuniperJunOSDevice.get_health(self)

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        return await JuniperJunOSDevice.ping(self, target, count)

    async def traceroute(self, target: str) -> dict[str, Any]:
        return await JuniperJunOSDevice.traceroute(self, target)

    async def send_config(self, commands: list[str]) -> str:
        return await JuniperJunOSDevice.send_config(self, commands)


# ===================================================================
# Fortinet FortiGate — Serial
# ===================================================================

@register_device("fortinet_serial")
class FortinetSerialDevice(SerialDevice):
    """Fortinet FortiGate device driver via serial console."""

    prompt_pattern = re.compile(
        r"[\r\n]([\w\-\.]+)\s*[#$]\s*$"
    )
    config_mode_command = "config system global"
    config_mode_exit = "end"
    enable_command = ""
    disable_paging_command = "config system console\nset output standard\nend"

    _split_intf_blocks = staticmethod(FortinetDevice._split_intf_blocks)

    async def get_config(self, config_type: str = "running") -> str:
        return await FortinetDevice.get_config(self, config_type)

    async def get_facts(self) -> DeviceFacts:
        return await FortinetDevice.get_facts(self)

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await FortinetDevice.get_interfaces(self)

    async def get_health(self) -> DeviceHealth:
        return await FortinetDevice.get_health(self)

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        return await FortinetDevice.ping(self, target, count)

    async def traceroute(self, target: str) -> dict[str, Any]:
        return await FortinetDevice.traceroute(self, target)


# ===================================================================
# Palo Alto Networks — Serial
# ===================================================================

@register_device("paloalto_serial")
class PaloAltoSerialDevice(SerialDevice):
    """Palo Alto Networks PAN-OS device driver via serial console."""

    prompt_pattern = re.compile(
        r"[\r\n][\w\-\.@]+"
        r"(?:\([^\)]+\))?"
        r"[#>]\s*$"
    )
    config_mode_command = "configure"
    config_mode_exit = "exit"
    enable_command = ""
    disable_paging_command = "set cli pager off"

    def _parse_intf_table(self, output):
        return PaloAltoDevice._parse_intf_table(self, output)

    def _parse_intf_blocks(self, output):
        return PaloAltoDevice._parse_intf_blocks(self, output)

    async def get_config(self, config_type: str = "running") -> str:
        return await PaloAltoDevice.get_config(self, config_type)

    async def get_facts(self) -> DeviceFacts:
        return await PaloAltoDevice.get_facts(self)

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await PaloAltoDevice.get_interfaces(self)

    async def get_health(self) -> DeviceHealth:
        return await PaloAltoDevice.get_health(self)

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        return await PaloAltoDevice.ping(self, target, count)

    async def traceroute(self, target: str) -> dict[str, Any]:
        return await PaloAltoDevice.traceroute(self, target)


# ===================================================================
# MikroTik RouterOS — Serial
# ===================================================================

@register_device("mikrotik_serial")
class MikroTikSerialDevice(SerialDevice):
    """MikroTik RouterOS device driver via serial console."""

    prompt_pattern = re.compile(
        r"[\r\n]\[[\w\-\.@]+\]\s*[>/#]\s*$"
    )
    config_mode_command = ""  # RouterOS has no config mode
    config_mode_exit = ""
    enable_command = ""
    disable_paging_command = ""  # RouterOS uses 'without-paging' flag

    async def get_config(self, config_type: str = "running") -> str:
        return await MikroTikDevice.get_config(self, config_type)

    async def get_facts(self) -> DeviceFacts:
        return await MikroTikDevice.get_facts(self)

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await MikroTikDevice.get_interfaces(self)

    async def get_health(self) -> DeviceHealth:
        return await MikroTikDevice.get_health(self)

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        return await MikroTikDevice.ping(self, target, count)

    async def traceroute(self, target: str) -> dict[str, Any]:
        return await MikroTikDevice.traceroute(self, target)

    async def send_config(self, commands: list[str]) -> str:
        """MikroTik has no config mode -- send commands directly."""
        self._require_connection()
        all_output: list[str] = []
        for cmd in commands:
            out = await self.send_command(cmd)
            all_output.append(out)
        return "\n".join(all_output)


# ===================================================================
# Aruba AOS-Switch — Serial
# ===================================================================

@register_device("aruba_serial")
class ArubaSerialDevice(SerialDevice):
    """Aruba AOS-Switch device driver via serial console."""

    prompt_pattern = re.compile(
        r"[\r\n]([\w\-\.]+)(?:\([^\)]+\))?[#>]\s*$"
    )
    config_mode_command = "configure terminal"
    config_mode_exit = "end"
    enable_command = "enable"
    disable_paging_command = "no page"

    _split_intf_blocks = staticmethod(ArubaDevice._split_intf_blocks)

    async def get_config(self, config_type: str = "running") -> str:
        return await ArubaDevice.get_config(self, config_type)

    async def get_facts(self) -> DeviceFacts:
        return await ArubaDevice.get_facts(self)

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await ArubaDevice.get_interfaces(self)

    async def get_health(self) -> DeviceHealth:
        return await ArubaDevice.get_health(self)

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        return await ArubaDevice.ping(self, target, count)

    async def traceroute(self, target: str) -> dict[str, Any]:
        return await ArubaDevice.traceroute(self, target)


# ===================================================================
# pfSense — Serial
# ===================================================================

@register_device("pfsense_serial")
class PfSenseSerialDevice(SerialDevice):
    """pfSense device driver via serial console."""

    prompt_pattern = re.compile(
        r"[\r\n][\w\-\.@~]+[#$%]\s*$"
    )
    config_mode_command = ""  # Shell-based, no config mode
    config_mode_exit = ""
    enable_command = ""
    disable_paging_command = ""

    _split_ifconfig_blocks = staticmethod(PfSenseDevice._split_ifconfig_blocks)

    async def get_config(self, config_type: str = "running") -> str:
        return await PfSenseDevice.get_config(self, config_type)

    async def get_facts(self) -> DeviceFacts:
        return await PfSenseDevice.get_facts(self)

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await PfSenseDevice.get_interfaces(self)

    async def get_health(self) -> DeviceHealth:
        return await PfSenseDevice.get_health(self)

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        return await PfSenseDevice.ping(self, target, count)

    async def traceroute(self, target: str) -> dict[str, Any]:
        return await PfSenseDevice.traceroute(self, target)

    async def send_config(self, commands: list[str]) -> str:
        """pfSense uses shell commands -- send directly."""
        self._require_connection()
        all_output: list[str] = []
        for cmd in commands:
            out = await self.send_command(cmd)
            all_output.append(out)
        return "\n".join(all_output)


# ===================================================================
# Sophos — Serial
# ===================================================================

@register_device("sophos_serial")
class SophosSerialDevice(SerialDevice):
    """Sophos UTM device driver via serial console."""

    prompt_pattern = re.compile(
        r"[\r\n][\w\-\.@~]+[#$%]\s*$"
    )
    config_mode_command = ""  # Shell / API based
    config_mode_exit = ""
    enable_command = ""
    disable_paging_command = ""

    _parse_ifconfig = staticmethod(SophosDevice._parse_ifconfig)
    _split_ifconfig_blocks = staticmethod(SophosDevice._split_ifconfig_blocks)

    async def get_facts(self) -> DeviceFacts:
        return await SophosDevice.get_facts(self)

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await SophosDevice.get_interfaces(self)

    async def get_health(self) -> DeviceHealth:
        return await SophosDevice.get_health(self)

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        return await SophosDevice.ping(self, target, count)

    async def traceroute(self, target: str) -> dict[str, Any]:
        return await SophosDevice.traceroute(self, target)

    async def send_config(self, commands: list[str]) -> str:
        """Sophos uses shell commands -- send directly."""
        self._require_connection()
        all_output: list[str] = []
        for cmd in commands:
            out = await self.send_command(cmd)
            all_output.append(out)
        return "\n".join(all_output)


# ===================================================================
# Cisco NX-OS (Nexus) — Serial
# ===================================================================

@register_device("cisco_nxos_serial")
class CiscoNXOSSerialDevice(SerialDevice):
    """Cisco NX-OS (Nexus) device driver via serial console."""

    prompt_pattern = re.compile(
        r"[\r\n]([\w\-\.]+)(?:\([^\)]+\))?[#>]\s*$"
    )
    config_mode_command = "configure terminal"
    config_mode_exit = "end"
    enable_command = "enable"
    disable_paging_command = "terminal length 0"

    # Inherit static helpers from CiscoIOSDevice (NX-OS parsing reuses IOS)
    _split_interface_blocks = staticmethod(CiscoIOSDevice._split_interface_blocks)
    _parse_uptime = staticmethod(CiscoIOSDevice._parse_uptime)

    # Reuse NX-OS SSH vendor parsing ---------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        return await CiscoNXOSDevice.get_config(self, config_type)

    async def get_facts(self) -> DeviceFacts:
        return await CiscoNXOSDevice.get_facts(self)

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await CiscoNXOSDevice.get_interfaces(self)

    async def get_health(self) -> DeviceHealth:
        return await CiscoNXOSDevice.get_health(self)

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        return await CiscoNXOSDevice.ping(self, target, count)

    async def traceroute(self, target: str) -> dict[str, Any]:
        return await CiscoNXOSDevice.traceroute(self, target)


# ===================================================================
# Cisco ASA — Serial
# ===================================================================

@register_device("cisco_asa_serial")
class CiscoASASerialDevice(SerialDevice):
    """Cisco ASA firewall device driver via serial console."""

    prompt_pattern = re.compile(
        r"[\r\n]([\w\-\.]+)(?:\([^\)]+\))?[#>]\s*$"
    )
    config_mode_command = "configure terminal"
    config_mode_exit = "end"
    enable_command = "enable"
    disable_paging_command = "terminal pager lines 0"

    _split_interface_blocks = staticmethod(CiscoASADevice._split_interface_blocks)
    _parse_uptime = staticmethod(CiscoASADevice._parse_uptime)

    async def get_config(self, config_type: str = "running") -> str:
        return await CiscoASADevice.get_config(self, config_type)

    async def get_facts(self) -> DeviceFacts:
        return await CiscoASADevice.get_facts(self)

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await CiscoASADevice.get_interfaces(self)

    async def get_health(self) -> DeviceHealth:
        return await CiscoASADevice.get_health(self)

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        return await CiscoASADevice.ping(self, target, count)

    async def traceroute(self, target: str) -> dict[str, Any]:
        return await CiscoASADevice.traceroute(self, target)


# ===================================================================
# Aruba AOS-CX — Serial
# ===================================================================

@register_device("aruba_aoscx_serial")
class ArubaAOSCXSerialDevice(SerialDevice):
    """Aruba AOS-CX device driver via serial console."""

    prompt_pattern = re.compile(
        r"[\r\n]([\w\-\.]+)(?:\([^\)]+\))?[#>%]\s*$"
    )
    config_mode_command = "configure terminal"
    config_mode_exit = "end"
    enable_command = "enable"
    disable_paging_command = "no page"

    _split_intf_blocks = staticmethod(ArubaAOSCXDevice._split_intf_blocks)

    async def get_config(self, config_type: str = "running") -> str:
        return await ArubaAOSCXDevice.get_config(self, config_type)

    async def get_facts(self) -> DeviceFacts:
        return await ArubaAOSCXDevice.get_facts(self)

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await ArubaAOSCXDevice.get_interfaces(self)

    async def get_health(self) -> DeviceHealth:
        return await ArubaAOSCXDevice.get_health(self)

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        return await ArubaAOSCXDevice.ping(self, target, count)

    async def traceroute(self, target: str) -> dict[str, Any]:
        return await ArubaAOSCXDevice.traceroute(self, target)
