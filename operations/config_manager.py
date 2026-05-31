"""Configuration management: backup, diff, deploy, rollback, and templating."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.database import Database
from core.credentials import CredentialManager
from core.exceptions import (
    ConfigBackupError,
    ConfigDeployError,
    ConfigRollbackError,
    DeviceConnectionError,
)
from core.models import InterfaceInfo
from devices.registry import get_device_class
from operations.templates import TemplateManager

logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages device configuration lifecycle: backups, diffs, deployments,
    rollbacks, and Jinja2-based template deployments."""

    def __init__(
        self,
        db: Database,
        credential_manager: CredentialManager,
        data_dir: str = "./data",
    ) -> None:
        self._db = db
        self._cred_mgr = credential_manager
        self._data_dir = Path(data_dir)
        self._config_dir = self._data_dir / "configs"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._template_mgr = TemplateManager(
            template_dir=str(self._data_dir / "templates")
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_device_and_connect(self, device_id: str):
        """Look up a device in the DB, resolve its credentials, instantiate
        the vendor driver, and open a connection.  Returns ``(device_record,
        device_instance)``."""
        device_record = await self._db.get_device(device_id)
        if device_record is None:
            raise ConfigBackupError(device_id, f"Device {device_id!r} not found in database")

        creds = await self._cred_mgr.get_credentials(device_record.get("credential_id", ""))
        device_cls = get_device_class(device_record["device_type"])

        device = device_cls(
            host=device_record["host"],
            username=creds.get("username", ""),
            password=creds.get("password", ""),
            port=device_record.get("port", 22),
            device_type=device_record["device_type"],
            enable_secret=creds.get("enable_secret", ""),
            ssh_key_path=creds.get("ssh_key_path", ""),
            timeout=device_record.get("timeout", 30),
        )
        try:
            await device.connect()
        except Exception as exc:
            raise DeviceConnectionError(
                device_id, f"Cannot connect to {device_record['host']}: {exc}"
            ) from exc
        return device_record, device

    def _save_config_file(self, device_id: str, config_text: str) -> str:
        """Persist *config_text* to disk and return the file path."""
        device_dir = self._config_dir / device_id
        device_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        file_path = device_dir / f"{timestamp}.cfg"
        file_path.write_text(config_text, encoding="utf-8")
        return str(file_path)

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def backup_config(
        self,
        device_id: str,
        backup_type: str = "manual",
    ) -> dict[str, Any]:
        """Fetch the running configuration from a device, persist it to disk
        and record it in the database.

        If the configuration hash matches the most recent backup the operation
        is skipped and the existing backup record is returned.

        Returns
        -------
        dict
            The ConfigBackup record stored in the database.
        """
        device_record, device = await self._get_device_and_connect(device_id)
        try:
            config_text = await device.get_config("running")
        except Exception as exc:
            raise ConfigBackupError(device_id, f"Failed to fetch config: {exc}") from exc
        finally:
            await device.disconnect()

        config_hash = self._sha256(config_text)

        # Check for duplicate against the latest backup.
        existing = await self._db.get_config_backups(device_id, limit=1)
        if existing and existing[0].get("config_hash") == config_hash:
            logger.info(
                "Config for device %s unchanged (hash=%s); skipping backup.",
                device_id,
                config_hash[:12],
            )
            return existing[0]

        file_path = self._save_config_file(device_id, config_text)
        backup_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        backup = {
            "id": backup_id,
            "device_id": device_id,
            "backup_type": backup_type,
            "config_text": config_text,
            "config_hash": config_hash,
            "file_path": file_path,
            "created_at": now,
        }
        await self._db.add_config_backup(backup)
        logger.info(
            "Backed up config for device %s (backup_id=%s, hash=%s).",
            device_id,
            backup_id,
            config_hash[:12],
        )
        return backup

    async def backup_all(self, tag: str | None = None) -> list[dict[str, Any]]:
        """Backup all devices concurrently.

        Parameters
        ----------
        tag:
            If provided, only devices whose ``tags`` field contains this value
            will be backed up.
        """
        devices = await self._db.list_devices()

        if tag:
            devices = [
                d for d in devices
                if tag in (d.get("tags") or "")
            ]

        if not devices:
            logger.warning("No devices found for backup_all (tag=%s).", tag)
            return []

        tasks = [self._safe_backup(d["id"]) for d in devices]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    async def _safe_backup(self, device_id: str) -> dict[str, Any] | None:
        """Wrapper that logs failures instead of propagating them so that
        ``backup_all`` continues with remaining devices."""
        try:
            return await self.backup_config(device_id, backup_type="scheduled")
        except Exception:
            logger.exception("Failed to backup device %s.", device_id)
            return None

    async def get_config_history(
        self, device_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return recent config backups for a device."""
        return await self._db.get_config_backups(device_id, limit=limit)

    async def diff_configs(self, backup_id_1: str, backup_id_2: str) -> str:
        """Generate a unified diff between two configuration backups.

        Returns a formatted diff string.
        """
        backup_a = await self._db.get_config_backup(backup_id_1)
        backup_b = await self._db.get_config_backup(backup_id_2)

        if backup_a is None:
            raise ConfigBackupError(
                message=f"Backup {backup_id_1!r} not found."
            )
        if backup_b is None:
            raise ConfigBackupError(
                message=f"Backup {backup_id_2!r} not found."
            )

        lines_a = (backup_a["config_text"] or "").splitlines(keepends=True)
        lines_b = (backup_b["config_text"] or "").splitlines(keepends=True)

        label_a = f"backup {backup_id_1} ({backup_a.get('created_at', '')})"
        label_b = f"backup {backup_id_2} ({backup_b.get('created_at', '')})"

        diff = difflib.unified_diff(lines_a, lines_b, fromfile=label_a, tofile=label_b)
        return "".join(diff)

    async def deploy_config(
        self,
        device_id: str,
        commands: list[str],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Push configuration commands to a device.

        If *dry_run* is ``True``, nothing is sent; the commands are returned for
        review.  Otherwise a pre-change backup is taken before deployment.

        Returns a ConfigDeploy record dict.
        """
        deploy_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        if dry_run:
            deploy = {
                "id": deploy_id,
                "device_id": device_id,
                "commands": commands,
                "status": "dry_run",
                "result": "Dry-run: commands not sent.",
                "created_at": now,
            }
            logger.info("Dry-run deploy for device %s: %s", device_id, commands)
            return deploy

        # Pre-change backup.
        try:
            await self.backup_config(device_id, backup_type="pre-change")
        except Exception:
            logger.warning("Pre-change backup failed for device %s; proceeding anyway.", device_id)

        device_record, device = await self._get_device_and_connect(device_id)
        try:
            output = await device.send_config(commands)
            status = "success"
        except Exception as exc:
            output = str(exc)
            status = "failed"
            logger.error("Deploy to %s failed: %s", device_id, exc)
        finally:
            await device.disconnect()

        deploy = {
            "id": deploy_id,
            "device_id": device_id,
            "commands": commands,
            "status": status,
            "result": output,
            "created_at": now,
        }
        await self._db.add_config_deploy(deploy)

        if status == "failed":
            raise ConfigDeployError(device_id, f"Deployment failed: {output}")

        logger.info("Config deployed to device %s (deploy_id=%s).", device_id, deploy_id)
        return deploy

    async def rollback_config(
        self, device_id: str, backup_id: str
    ) -> dict[str, Any]:
        """Restore a device to a previously backed-up configuration.

        The backup config text is converted into a list of commands (one per
        line, excluding common non-command lines) and deployed.

        Returns a ConfigDeploy record dict.
        """
        backup = await self._db.get_config_backup(backup_id)
        if backup is None:
            raise ConfigRollbackError(device_id, f"Backup {backup_id!r} not found.")
        if backup.get("device_id") != device_id:
            raise ConfigRollbackError(
                device_id,
                f"Backup {backup_id!r} does not belong to device {device_id!r}.",
            )

        config_text: str = backup.get("config_text", "")
        commands = self._parse_config_to_commands(config_text)

        if not commands:
            raise ConfigRollbackError(device_id, "Backup contains no deployable commands.")

        deploy_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Pre-rollback backup.
        try:
            await self.backup_config(device_id, backup_type="pre-rollback")
        except Exception:
            logger.warning(
                "Pre-rollback backup failed for device %s; proceeding anyway.", device_id
            )

        device_record, device = await self._get_device_and_connect(device_id)
        try:
            output = await device.send_config(commands)
            status = "success"
        except Exception as exc:
            output = str(exc)
            status = "failed"
            logger.error("Rollback on %s failed: %s", device_id, exc)
        finally:
            await device.disconnect()

        deploy = {
            "id": deploy_id,
            "device_id": device_id,
            "commands": commands,
            "status": status,
            "result": output,
            "deploy_type": "rollback",
            "rollback_backup_id": backup_id,
            "created_at": now,
        }
        await self._db.add_config_deploy(deploy)

        if status == "failed":
            raise ConfigRollbackError(device_id, f"Rollback failed: {output}")

        logger.info(
            "Rolled back device %s to backup %s (deploy_id=%s).",
            device_id,
            backup_id,
            deploy_id,
        )
        return deploy

    async def deploy_template(
        self,
        device_id: str,
        template_name: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        """Render a Jinja2 template and deploy the resulting commands.

        Returns a ConfigDeploy record dict.
        """
        rendered = self._template_mgr.render(template_name, variables)
        commands = [
            line.strip()
            for line in rendered.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        if not commands:
            raise ConfigDeployError(
                device_id,
                f"Template {template_name!r} rendered no deployable commands.",
            )

        logger.info(
            "Deploying template %s to device %s with %d commands.",
            template_name,
            device_id,
            len(commands),
        )
        return await self.deploy_config(device_id, commands)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_config_to_commands(config_text: str) -> list[str]:
        """Convert a full running-config dump into a list of commands suitable
        for re-deployment.

        Lines that are clearly comments, version markers, or build metadata are
        stripped out.
        """
        skip_prefixes = (
            "!",
            "#",
            "building configuration",
            "current configuration",
            "version ",
            "end",
            "boot-",
            "last configuration change",
            "nvram:",
        )
        commands: list[str] = []
        for raw_line in config_text.splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            lower = line.lstrip().lower()
            if any(lower.startswith(p) for p in skip_prefixes):
                continue
            commands.append(line)
        return commands
