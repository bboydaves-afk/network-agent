"""Device abstraction layer.

This package provides a unified interface for interacting with network
devices across multiple vendors and transport protocols (SSH, SNMP,
NETCONF, RESTCONF).

Quick start::

    from devices.registry import get_device_class

    DeviceClass = get_device_class("cisco_ios")
    async with DeviceClass(host="10.0.0.1", username="admin", password="s3cret") as dev:
        facts = await dev.get_facts()
        health = await dev.get_health()
"""

from devices.base import BaseDevice
from devices.registry import get_device_class, list_device_types, register_device
from devices.ssh_device import SSHDevice
from devices.snmp_device import SNMPDevice
from devices.netconf_device import NETCONFDevice
from devices.restconf_device import RESTCONFDevice
from devices.serial_device import SerialDevice

# Import vendors to trigger @register_device decorators
import devices.vendors  # noqa: F401

__all__ = [
    "BaseDevice",
    "SSHDevice",
    "SerialDevice",
    "SNMPDevice",
    "NETCONFDevice",
    "RESTCONFDevice",
    "get_device_class",
    "list_device_types",
    "register_device",
]
