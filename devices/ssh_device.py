"""SSH-based device driver built on top of Netmiko."""

from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Optional

from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
    ReadTimeout,
)

from core.exceptions import (
    DeviceAuthenticationError,
    DeviceCommandError,
    DeviceConnectionError,
    DeviceTimeoutError,
)
from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.base import BaseDevice

logger = logging.getLogger(__name__)

# Shared thread pool for all SSH operations (Netmiko is blocking).
_SSH_EXECUTOR = ThreadPoolExecutor(max_workers=32, thread_name_prefix="ssh")


class SSHDevice(BaseDevice):
    """Base SSH device using Netmiko for CLI interaction.

    Subclasses should set ``netmiko_type`` and override parsing methods to
    handle vendor-specific output.
    """

    netmiko_type: str = "generic"  # override in subclasses

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._net_connect: Any = None  # Netmiko ConnectHandler instance

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    async def _run_sync(self, func, *args, **kwargs):
        """Run a blocking callable in the thread-pool executor."""
        loop = self._get_loop()
        return await loop.run_in_executor(
            _SSH_EXECUTOR, partial(func, *args, **kwargs)
        )

    def _build_netmiko_params(self) -> dict[str, Any]:
        """Build the keyword dict for ``ConnectHandler``."""
        params: dict[str, Any] = {
            "device_type": self.netmiko_type,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "timeout": self.timeout,
            "conn_timeout": self.timeout,
            "banner_timeout": self.timeout,
            "auth_timeout": self.timeout,
        }
        if self.enable_secret:
            params["secret"] = self.enable_secret
        if self.ssh_key_path:
            params["use_keys"] = True
            params["key_file"] = self.ssh_key_path
        params.update(self._extra)
        return params

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open an SSH session via Netmiko."""
        if self._connected and self._net_connect:
            return
        try:
            params = self._build_netmiko_params()
            self._net_connect = await self._run_sync(ConnectHandler, **params)
            # Enter enable mode if a secret is configured and the platform
            # supports it (Netmiko raises an exception for unsupported types).
            if self.enable_secret:
                try:
                    await self._run_sync(self._net_connect.enable)
                except Exception:
                    pass  # not all platforms have enable mode
            self._connected = True
            logger.info("SSH connected to %s (%s)", self.host, self.netmiko_type)
        except NetmikoAuthenticationException as exc:
            raise DeviceAuthenticationError(
                device_id=self.host,
                message=f"SSH authentication failed: {exc}",
            ) from exc
        except NetmikoTimeoutException as exc:
            raise DeviceTimeoutError(
                device_id=self.host,
                message=f"SSH connection timed out: {exc}",
                timeout_seconds=self.timeout,
            ) from exc
        except Exception as exc:
            raise DeviceConnectionError(
                device_id=self.host,
                message=f"SSH connection failed: {exc}",
            ) from exc

    async def disconnect(self) -> None:
        """Close the SSH session."""
        if self._net_connect:
            try:
                await self._run_sync(self._net_connect.disconnect)
            except Exception:
                logger.debug("Ignoring error during SSH disconnect for %s", self.host)
            finally:
                self._net_connect = None
                self._connected = False
                logger.info("SSH disconnected from %s", self.host)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def send_command(self, command: str, timeout: int = 30) -> str:
        """Send a single show / operational command and return output."""
        self._require_connection()
        try:
            output: str = await self._run_sync(
                self._net_connect.send_command,
                command,
                read_timeout=timeout,
            )
            return output
        except ReadTimeout as exc:
            raise DeviceTimeoutError(
                device_id=self.host,
                message=f"Command timed out: {command!r}",
                timeout_seconds=timeout,
            ) from exc
        except Exception as exc:
            raise DeviceCommandError(
                device_id=self.host,
                message=f"Command failed: {exc}",
                command=command,
            ) from exc

    async def send_config(self, commands: list[str]) -> str:
        """Send a list of configuration commands."""
        self._require_connection()
        try:
            output: str = await self._run_sync(
                self._net_connect.send_config_set, commands
            )
            return output
        except Exception as exc:
            raise DeviceCommandError(
                device_id=self.host,
                message=f"Config push failed: {exc}",
                command="; ".join(commands),
            ) from exc

    async def get_config(self, config_type: str = "running") -> str:
        """Retrieve the device configuration (default: running-config)."""
        cmd_map = {
            "running": "show running-config",
            "startup": "show startup-config",
        }
        cmd = cmd_map.get(config_type, f"show {config_type}-config")
        return await self.send_command(cmd, timeout=60)

    # ------------------------------------------------------------------
    # Default fact / interface / health parsers
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        """Generic fact collection -- subclasses should override."""
        output = await self.send_command("show version")
        hostname_match = re.search(r"(\S+)\s+uptime", output, re.IGNORECASE)
        uptime_match = re.search(r"uptime is (.+)", output, re.IGNORECASE)
        return DeviceFacts(
            hostname=hostname_match.group(1) if hostname_match else self.host,
            uptime=uptime_match.group(1).strip() if uptime_match else "",
        )

    async def get_interfaces(self) -> list[InterfaceInfo]:
        """Generic interface list -- subclasses should override."""
        output = await self.send_command("show ip interface brief")
        interfaces: list[InterfaceInfo] = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 6 and not line.startswith("Interface"):
                interfaces.append(
                    InterfaceInfo(
                        name=parts[0],
                        ip_address=parts[1] if parts[1] != "unassigned" else "",
                        status=parts[4].lower(),
                        protocol_status=parts[5].lower(),
                    )
                )
        return interfaces

    async def get_health(self) -> DeviceHealth:
        """Generic health -- subclasses should override."""
        return DeviceHealth()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_connection(self) -> None:
        if not self._connected or self._net_connect is None:
            raise DeviceConnectionError(
                device_id=self.host,
                message="Not connected -- call connect() first",
            )
