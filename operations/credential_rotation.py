"""Credential rotation engine for automated password management."""

from __future__ import annotations

import hashlib
import logging
import secrets
import string
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class CredentialRotator:
    """Rotate device credentials with verification."""

    def __init__(self, db, credential_manager) -> None:
        self._db = db
        self._cred_mgr = credential_manager

    def _generate_password(self, length: int = 24) -> str:
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        while True:
            password = "".join(secrets.choice(alphabet) for _ in range(length))
            has_upper = any(c.isupper() for c in password)
            has_lower = any(c.islower() for c in password)
            has_digit = any(c.isdigit() for c in password)
            has_special = any(c in "!@#$%^&*" for c in password)
            if has_upper and has_lower and has_digit and has_special:
                return password

    async def rotate_credential(
        self, credential_id: str, initiated_by: str = "system",
    ) -> dict[str, Any]:
        rotation_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        cred = await self._db.get_credential(credential_id)
        if not cred:
            return {"error": "Credential not found"}

        # Find devices using this credential
        devices = await self._db.fetch_all(
            "SELECT * FROM devices WHERE credential_id = ?", (credential_id,)
        )

        new_password = self._generate_password()
        old_hash = hashlib.sha256((cred.get("password") or "").encode()).hexdigest()[:16]
        new_hash = hashlib.sha256(new_password.encode()).hexdigest()[:16]

        await self._db.execute(
            """INSERT INTO credential_rotations
               (id, credential_id, old_password_hash, new_password_hash, status,
                devices_total, devices_updated, devices_failed, started_at, initiated_by)
               VALUES (?, ?, ?, ?, 'in_progress', ?, 0, 0, ?, ?)""",
            (rotation_id, credential_id, old_hash, new_hash, len(devices), now, initiated_by),
        )

        updated = 0
        failed = 0
        failures = []

        for device in devices:
            try:
                success = await self._update_device_password(device, cred, new_password)
                if success:
                    updated += 1
                else:
                    failed += 1
                    failures.append({"device_id": device["id"], "error": "Update returned false"})
            except Exception as exc:
                failed += 1
                failures.append({"device_id": device["id"], "error": str(exc)})

        # Update stored credential if at least some devices succeeded
        import json
        if updated > 0:
            await self._cred_mgr.update_password(credential_id, new_password)

        status = "completed" if failed == 0 else "partial" if updated > 0 else "failed"
        completed_at = datetime.now(timezone.utc).isoformat()

        await self._db.execute(
            """UPDATE credential_rotations
               SET status = ?, devices_updated = ?, devices_failed = ?,
                   failure_details = ?, completed_at = ?
               WHERE id = ?""",
            (status, updated, failed, json.dumps(failures), completed_at, rotation_id),
        )

        return {
            "rotation_id": rotation_id,
            "status": status,
            "devices_updated": updated,
            "devices_failed": failed,
            "failures": failures,
        }

    async def _update_device_password(
        self, device: dict, old_creds: dict, new_password: str,
    ) -> bool:
        """Connect to device and change password. Returns True on success."""
        try:
            from devices.registry import get_device_class
            decrypted = await self._cred_mgr.get_credentials(device.get("credential_id", ""))
            device_cls = get_device_class(device["device_type"])
            dev = device_cls(
                host=device.get("ip_address", ""),
                username=decrypted.get("username", ""),
                password=decrypted.get("password", ""),
                port=device.get("port", 22),
                device_type=device["device_type"],
                enable_secret=decrypted.get("enable_secret", ""),
            )
            await dev.connect()
            try:
                # Send password change commands (vendor-specific)
                username = decrypted.get("username", "")
                commands = [
                    f"username {username} secret {new_password}",
                ]
                await dev.send_config(commands)
            finally:
                await dev.disconnect()
            return True
        except Exception as exc:
            logger.warning("Password update failed for %s: %s", device.get("hostname"), exc)
            raise

    async def verify_all_devices(self, credential_id: str) -> dict[str, Any]:
        """Test login on all devices using this credential."""
        devices = await self._db.fetch_all(
            "SELECT * FROM devices WHERE credential_id = ?", (credential_id,)
        )
        success_count = 0
        failure_count = 0
        failures = []

        for device in devices:
            try:
                from devices.registry import get_device_class
                creds = await self._cred_mgr.get_credentials(credential_id)
                device_cls = get_device_class(device["device_type"])
                dev = device_cls(
                    host=device.get("ip_address", ""),
                    username=creds.get("username", ""),
                    password=creds.get("password", ""),
                    port=device.get("port", 22),
                    device_type=device["device_type"],
                )
                await dev.connect()
                await dev.disconnect()
                success_count += 1
            except Exception as exc:
                failure_count += 1
                failures.append({"device_id": device["id"], "hostname": device.get("hostname"), "error": str(exc)})

        return {
            "credential_id": credential_id,
            "total": len(devices),
            "success_count": success_count,
            "failure_count": failure_count,
            "failures": failures,
        }

    async def get_rotation_history(
        self, credential_id: str | None = None, limit: int = 20,
    ) -> list[dict[str, Any]]:
        if credential_id:
            return await self._db.fetch_all(
                "SELECT * FROM credential_rotations WHERE credential_id = ? ORDER BY started_at DESC LIMIT ?",
                (credential_id, limit),
            )
        return await self._db.fetch_all(
            "SELECT * FROM credential_rotations ORDER BY started_at DESC LIMIT ?", (limit,)
        )
