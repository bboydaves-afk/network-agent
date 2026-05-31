"""Device type registry - re-exports from devices.registry for backward compatibility.

The canonical registry lives in ``devices.registry``. This module
re-exports the key functions so that code importing from
``core.device_registry`` continues to work.
"""

from __future__ import annotations

from devices.registry import (
    get_device_class,
    list_device_types,
    register_device,
    _DEVICE_REGISTRY,
)


def create_device(device_type: str, **kwargs):
    """Factory function: instantiate a registered device class.

    Ensures all vendor modules have been imported first, then looks up
    *device_type* in the registry and returns an instance.
    """
    import_vendors()
    cls = get_device_class(device_type)
    return cls(**kwargs)


def get_supported_devices() -> list[str]:
    """Return a sorted list of all registered device type keys."""
    import_vendors()
    return list_device_types()


_vendors_imported = False


def import_vendors() -> None:
    """Import all vendor modules so their ``@register_device`` decorators run."""
    global _vendors_imported
    if _vendors_imported:
        return

    import importlib
    import logging

    logger = logging.getLogger(__name__)

    vendor_modules = [
        "devices.vendors.cisco_ios",
        "devices.vendors.cisco_iosxe",
        "devices.vendors.juniper_junos",
        "devices.vendors.fortinet",
        "devices.vendors.paloalto",
        "devices.vendors.mikrotik",
        "devices.vendors.aruba",
        "devices.vendors.pfsense",
        "devices.vendors.sophos",
    ]

    for module_name in vendor_modules:
        try:
            importlib.import_module(module_name)
            logger.debug("Imported vendor module %s", module_name)
        except ImportError:
            logger.debug("Vendor module %s not found, skipping", module_name)
        except Exception:
            logger.exception("Error importing vendor module %s", module_name)

    _vendors_imported = True
