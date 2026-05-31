"""Serial console management operations."""

from __future__ import annotations

import json
import logging
from typing import Any

from core.credentials import CredentialManager
from core.database import Database
from core.exceptions import DeviceConnectionError, SerialConnectionError
from devices.registry import get_device_class
from devices.serial_device import SerialDevice

logger = logging.getLogger(__name__)


class SerialConsoleManager:
    """Manages serial console connections and operations.

    Unlike SSH connections (connect-execute-disconnect), serial console
    sessions are kept open because they represent a physical cable
    connection.  The ``_active_sessions`` dict tracks open sessions
    keyed by device ID.
    """

    def __init__(
        self,
        db: Database,
        credential_manager: CredentialManager,
    ) -> None:
        self._db = db
        self._cred_mgr = credential_manager
        self._active_sessions: dict[str, SerialDevice] = {}

    # ------------------------------------------------------------------
    # Port discovery
    # ------------------------------------------------------------------

    async def list_serial_ports(self) -> list[dict[str, Any]]:
        """Enumerate available serial ports on this machine."""
        from serial.tools.list_ports import comports

        ports: list[dict[str, Any]] = []
        for info in comports():
            ports.append({
                "device": info.device,
                "name": info.name,
                "description": info.description,
                "hwid": info.hwid,
                "vid": info.vid,
                "pid": info.pid,
                "serial_number": info.serial_number,
                "manufacturer": info.manufacturer,
                "product": info.product,
            })
        return ports

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def connect_serial(self, device_id: str) -> dict[str, Any]:
        """Open a serial session to a registered device.

        Reads serial parameters from ``device.metadata`` and credentials
        from the credential store.
        """
        if device_id in self._active_sessions:
            dev = self._active_sessions[device_id]
            return {
                "status": "already_connected",
                "device_id": device_id,
                "serial_port": dev.serial_port,
            }

        device_record = await self._db.get_device(device_id)
        if device_record is None:
            raise SerialConnectionError(
                device_id=device_id,
                message=f"Device {device_id!r} not found in database",
            )

        metadata = device_record.get("metadata", {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}

        serial_port = metadata.get("serial_port", "")
        if not serial_port:
            raise SerialConnectionError(
                device_id=device_id,
                message="No serial_port in device metadata. "
                "Set metadata.serial_port to the COM/tty port path.",
            )

        creds = await self._cred_mgr.get_credentials(
            device_record.get("credential_id", "")
        )

        device_type = device_record["device_type"]
        serial_type = (
            device_type
            if device_type.endswith("_serial")
            else f"{device_type}_serial"
        )

        device_cls = get_device_class(serial_type)
        device = device_cls(
            host=device_record.get("host", device_record.get("ip_address", "")),
            username=creds.get("username", ""),
            password=creds.get("password", ""),
            device_type=serial_type,
            enable_secret=creds.get("enable_secret", ""),
            timeout=device_record.get("timeout", 30),
            serial_port=serial_port,
            baudrate=metadata.get("baudrate", 9600),
            bytesize=metadata.get("bytesize", 8),
            parity=metadata.get("parity", "N"),
            stopbits=metadata.get("stopbits", 1),
            xonxoff=metadata.get("xonxoff", False),
            rtscts=metadata.get("rtscts", False),
        )

        await device.connect()
        self._active_sessions[device_id] = device

        return {
            "status": "connected",
            "device_id": device_id,
            "serial_port": serial_port,
            "baudrate": device.baudrate,
        }

    async def disconnect_serial(self, device_id: str) -> dict[str, Any]:
        """Close an active serial session."""
        device = self._active_sessions.pop(device_id, None)
        if device is None:
            return {"status": "not_connected", "device_id": device_id}
        await device.disconnect()
        return {"status": "disconnected", "device_id": device_id}

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def send_command(
        self, device_id: str, command: str, timeout: int = 30
    ) -> dict[str, Any]:
        """Send a command over an active serial session."""
        device = self._get_session(device_id)
        output = await device.send_command(command, timeout=timeout)
        return {"device_id": device_id, "command": command, "output": output}

    async def send_config(
        self, device_id: str, commands: list[str]
    ) -> dict[str, Any]:
        """Send config commands over an active serial session."""
        device = self._get_session(device_id)
        output = await device.send_config(commands)
        return {"device_id": device_id, "commands": commands, "output": output}

    async def send_break(
        self, device_id: str, duration: float = 0.5
    ) -> dict[str, Any]:
        """Send a serial break signal (for password recovery)."""
        device = self._get_session(device_id)
        output = await device.send_break(duration=duration)
        return {
            "device_id": device_id,
            "break_sent": True,
            "duration": duration,
            "output": output,
        }

    async def get_facts(self, device_id: str) -> dict[str, Any]:
        """Get device facts over an active serial session."""
        device = self._get_session(device_id)
        facts = await device.get_facts()
        return facts.model_dump()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def disconnect_all(self) -> None:
        """Close all active serial sessions."""
        for device_id in list(self._active_sessions):
            await self.disconnect_serial(device_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_session(self, device_id: str) -> SerialDevice:
        """Return the active serial session or raise."""
        device = self._active_sessions.get(device_id)
        if device is None:
            raise SerialConnectionError(
                device_id=device_id,
                message="No active serial session. Call connect_serial first.",
            )
        return device
