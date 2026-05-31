"""Abstract base device class for all network devices."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Optional

from core.models import DeviceFacts, DeviceHealth, InterfaceInfo


class BaseDevice(ABC):
    """Abstract base class for all network device interactions.

    Every concrete device driver (SSH, SNMP, NETCONF, RESTCONF, etc.) must
    inherit from this class and implement the abstract methods.

    Supports the async context-manager protocol::

        async with SomeDevice(host="10.0.0.1", ...) as dev:
            facts = await dev.get_facts()
    """

    def __init__(
        self,
        host: str,
        username: str = "",
        password: str = "",
        port: int = 22,
        device_type: str = "",
        enable_secret: str = "",
        ssh_key_path: str = "",
        timeout: int = 30,
        **kwargs: Any,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.device_type = device_type
        self.enable_secret = enable_secret
        self.ssh_key_path = ssh_key_path
        self.timeout = timeout
        self._connected: bool = False
        self._extra: dict[str, Any] = kwargs

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """Return ``True`` if the device session is currently open."""
        return self._connected

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Open a session to the device."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close the device session."""
        ...

    @abstractmethod
    async def get_config(self, config_type: str = "running") -> str:
        """Retrieve the device configuration.

        Parameters
        ----------
        config_type:
            One of ``"running"``, ``"startup"``, or ``"candidate"``
            (support varies by vendor).
        """
        ...

    @abstractmethod
    async def send_command(self, command: str, timeout: int = 30) -> str:
        """Execute a single command and return its output."""
        ...

    @abstractmethod
    async def send_config(self, commands: list[str]) -> str:
        """Push one or more configuration commands to the device."""
        ...

    @abstractmethod
    async def get_facts(self) -> DeviceFacts:
        """Return basic facts (hostname, model, serial, version, uptime)."""
        ...

    @abstractmethod
    async def get_interfaces(self) -> list[InterfaceInfo]:
        """Return a list of interfaces with status and counters."""
        ...

    @abstractmethod
    async def get_health(self) -> DeviceHealth:
        """Return health metrics (CPU, memory, temperature)."""
        ...

    # ------------------------------------------------------------------
    # Default utility commands (overridable)
    # ------------------------------------------------------------------

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        """Ping *target* from this device and return raw output."""
        output = await self.send_command(f"ping {target} count {count}")
        return {"raw_output": output}

    async def traceroute(self, target: str) -> dict[str, Any]:
        """Run traceroute to *target* from this device."""
        output = await self.send_command(f"traceroute {target}")
        return {"raw_output": output}

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "BaseDevice":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} host={self.host}>"
