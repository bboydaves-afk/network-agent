"""Vendor-specific device implementations.

Importing this package triggers registration of all vendor device classes
via the ``@register_device`` decorator, making them available through
``devices.registry.get_device_class()``.
"""

from . import (
    cisco_ios,
    cisco_iosxe,
    cisco_nxos,
    cisco_asa,
    juniper_junos,
    fortinet,
    paloalto,
    mikrotik,
    aruba,
    aruba_aoscx,
    pfsense,
    sophos,
    serial_vendors,
)

__all__ = [
    "cisco_ios",
    "cisco_iosxe",
    "cisco_nxos",
    "cisco_asa",
    "juniper_junos",
    "fortinet",
    "paloalto",
    "mikrotik",
    "aruba",
    "aruba_aoscx",
    "pfsense",
    "sophos",
    "serial_vendors",
]
