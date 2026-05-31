"""Global device-type registry for dynamic vendor look-up."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from devices.base import BaseDevice

logger = logging.getLogger(__name__)

# Maps device-type strings (e.g. "cisco_ios") to concrete device classes.
_DEVICE_REGISTRY: dict[str, Type["BaseDevice"]] = {}


def register_device(device_type: str):
    """Class decorator that registers a device implementation.

    Usage::

        @register_device("cisco_ios")
        class CiscoIOSDevice(SSHDevice):
            ...
    """

    def decorator(cls: Type["BaseDevice"]) -> Type["BaseDevice"]:
        if device_type in _DEVICE_REGISTRY:
            logger.warning(
                "Device type %r already registered (%s) -- overwriting with %s",
                device_type,
                _DEVICE_REGISTRY[device_type].__name__,
                cls.__name__,
            )
        _DEVICE_REGISTRY[device_type] = cls
        # Stash the key on the class itself for convenience.
        cls._registered_type = device_type  # type: ignore[attr-defined]
        return cls

    return decorator


def get_device_class(device_type: str) -> Type["BaseDevice"]:
    """Return the class registered under *device_type*, or raise KeyError."""
    try:
        return _DEVICE_REGISTRY[device_type]
    except KeyError:
        available = ", ".join(sorted(_DEVICE_REGISTRY)) or "(none)"
        raise KeyError(
            f"Unknown device type {device_type!r}. Available: {available}"
        ) from None


def list_device_types() -> list[str]:
    """Return a sorted list of all registered device-type strings."""
    return sorted(_DEVICE_REGISTRY)
