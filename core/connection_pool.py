"""Connection pool manager for network device connections."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from core.device_registry import create_device
from core.exceptions import DeviceConnectionError

logger = logging.getLogger(__name__)


class ConnectionPool:
    """Manages a pool of reusable network device connections.

    Connections are keyed by ``device_id``.  Idle connections are automatically
    cleaned up after ``idle_timeout`` seconds.  The pool enforces a maximum
    number of simultaneous connections.
    """

    def __init__(self, max_connections: int = 50, idle_timeout: float = 300) -> None:
        self.max_connections = max_connections
        self.idle_timeout = idle_timeout

        # Active connections keyed by device_id.
        self._connections: dict[str, Any] = {}
        # Timestamp of last access for each device_id.
        self._last_used: dict[str, float] = {}
        # Serialise pool mutations.
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_connection(self, device_id: str, device_type: str, **conn_kwargs: Any) -> Any:
        """Return an existing connection or create a new one.

        Parameters
        ----------
        device_id:
            Unique device identifier used as the pool key.
        device_type:
            Registered device type string (e.g. ``"cisco_ios"``).
        **conn_kwargs:
            Forwarded to the device class constructor (host, username, ...).

        Returns
        -------
        BaseDevice
            A connected device instance.

        Raises
        ------
        DeviceConnectionError
            If the pool is full or the connection cannot be established.
        """
        async with self._lock:
            # Reuse existing connection if available.
            if device_id in self._connections:
                device = self._connections[device_id]
                self._last_used[device_id] = time.monotonic()
                logger.debug("Reusing pooled connection for device %s", device_id)
                return device

            # Enforce capacity.
            if len(self._connections) >= self.max_connections:
                # Try to free idle connections before refusing.
                await self._cleanup_idle_unlocked()
                if len(self._connections) >= self.max_connections:
                    raise DeviceConnectionError(
                        device_id=device_id,
                        message=(
                            f"Connection pool is full ({self.max_connections} connections). "
                            "Release idle connections or increase max_connections."
                        ),
                    )

            # Create and store a new connection.
            try:
                device = create_device(device_type, **conn_kwargs)
                # If the device object exposes an async ``connect`` method, call it.
                connect = getattr(device, "connect", None)
                if asyncio.iscoroutinefunction(connect):
                    await connect()
                elif callable(connect):
                    connect()
            except Exception as exc:
                raise DeviceConnectionError(
                    device_id=device_id,
                    message=f"Failed to create connection: {exc}",
                ) from exc

            self._connections[device_id] = device
            self._last_used[device_id] = time.monotonic()
            logger.info(
                "Created new pooled connection for device %s (pool size: %d)",
                device_id,
                len(self._connections),
            )
            return device

    async def release_connection(self, device_id: str) -> None:
        """Explicitly release (disconnect and remove) a pooled connection."""
        async with self._lock:
            device = self._connections.pop(device_id, None)
            self._last_used.pop(device_id, None)

        if device is not None:
            await self._disconnect(device, device_id)
            logger.info("Released connection for device %s", device_id)

    async def close_all(self) -> None:
        """Disconnect and remove every connection in the pool."""
        async with self._lock:
            device_items = list(self._connections.items())
            self._connections.clear()
            self._last_used.clear()

        for dev_id, device in device_items:
            await self._disconnect(device, dev_id)

        logger.info("All pooled connections closed (%d total)", len(device_items))

    async def cleanup_idle(self) -> None:
        """Close connections that have been idle longer than ``idle_timeout``."""
        async with self._lock:
            await self._cleanup_idle_unlocked()

    @asynccontextmanager
    async def connection(self, device_id: str, device_type: str, **conn_kwargs: Any) -> AsyncIterator[Any]:
        """Async context manager for borrowing a connection from the pool.

        Usage::

            async with pool.connection(dev_id, "cisco_ios", host="10.0.0.1") as device:
                output = await device.send_command("show version")

        The connection is *not* released when the context manager exits; it
        remains in the pool for reuse.  Call ``release_connection`` or
        ``close_all`` to tear it down explicitly.
        """
        device = await self.get_connection(device_id, device_type, **conn_kwargs)
        try:
            yield device
        finally:
            # Update last-used timestamp so the connection stays alive.
            async with self._lock:
                if device_id in self._connections:
                    self._last_used[device_id] = time.monotonic()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Current number of connections in the pool."""
        return len(self._connections)

    @property
    def active_device_ids(self) -> list[str]:
        """List of device IDs with an active pooled connection."""
        return list(self._connections)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _cleanup_idle_unlocked(self) -> None:
        """Remove idle connections.  MUST be called while holding ``_lock``."""
        now = time.monotonic()
        stale_ids = [
            dev_id
            for dev_id, ts in self._last_used.items()
            if (now - ts) > self.idle_timeout
        ]

        for dev_id in stale_ids:
            device = self._connections.pop(dev_id, None)
            self._last_used.pop(dev_id, None)
            if device is not None:
                await self._disconnect(device, dev_id)
                logger.info("Closed idle connection for device %s", dev_id)

    @staticmethod
    async def _disconnect(device: Any, device_id: str) -> None:
        """Attempt to gracefully disconnect a device object."""
        disconnect = getattr(device, "disconnect", None) or getattr(device, "close", None)
        if disconnect is None:
            return
        try:
            if asyncio.iscoroutinefunction(disconnect):
                await disconnect()
            elif callable(disconnect):
                disconnect()
        except Exception:
            logger.exception("Error disconnecting device %s", device_id)
