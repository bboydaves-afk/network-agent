"""Firmware/OS version management and compliance tracking."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class FirmwareManager:
    """Track firmware versions, EOL dates, and CVEs."""

    def __init__(self, db) -> None:
        self._db = db

    async def add_firmware_entry(
        self, vendor: str, version: str, model_pattern: str | None = None,
        release_date: str | None = None, eol_date: str | None = None,
        eos_date: str | None = None, cve_list: list[str] | None = None,
        download_url: str | None = None, is_recommended: bool = False,
        notes: str | None = None,
    ) -> dict[str, Any]:
        entry_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO firmware_catalog
               (id, vendor, model_pattern, version, release_date, eol_date, eos_date,
                cve_list, download_url, is_recommended, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry_id, vendor, model_pattern, version, release_date, eol_date, eos_date,
             json.dumps(cve_list or []), download_url, 1 if is_recommended else 0, notes, now),
        )
        return {"id": entry_id, "vendor": vendor, "version": version}

    async def get_catalog(self, vendor: str | None = None) -> list[dict[str, Any]]:
        if vendor:
            return await self._db.fetch_all(
                "SELECT * FROM firmware_catalog WHERE vendor = ? ORDER BY version", (vendor,)
            )
        return await self._db.fetch_all("SELECT * FROM firmware_catalog ORDER BY vendor, version")

    async def check_compliance(self) -> list[dict[str, Any]]:
        """Check all devices against the firmware catalog."""
        devices = await self._db.list_devices()
        catalog = await self.get_catalog()
        results = []
        for device in devices:
            status = self._check_device(device, catalog)
            results.append(status)
        return results

    async def check_device_firmware(self, device_id: str) -> dict[str, Any]:
        device = await self._db.get_device(device_id)
        if not device:
            return {"error": "Device not found"}
        catalog = await self.get_catalog()
        return self._check_device(device, catalog)

    def _check_device(self, device: dict, catalog: list[dict]) -> dict[str, Any]:
        """Compare device version against catalog entries."""
        vendor = (device.get("device_type", "") or "").split("_")[0]
        current = device.get("os_version", "") or ""
        model = device.get("model", "") or ""

        matching = [e for e in catalog if e.get("vendor", "").lower() == vendor.lower()]
        if model:
            model_matches = [e for e in matching if e.get("model_pattern") and
                            e["model_pattern"].lower() in model.lower()]
            if model_matches:
                matching = model_matches

        recommended = [e for e in matching if e.get("is_recommended")]
        eol_entries = [e for e in matching if e.get("eol_date") and
                      e["version"] == current and e["eol_date"] < datetime.now(timezone.utc).isoformat()]
        cve_entries = [e for e in matching if e["version"] == current and
                      json.loads(e.get("cve_list", "[]"))]

        status = "unknown"
        recommended_version = ""
        if recommended:
            recommended_version = recommended[0]["version"]
            if current == recommended_version:
                status = "compliant"
            else:
                status = "outdated"
        if eol_entries:
            status = "eol"
        if cve_entries:
            status = "vulnerable"
        if not current:
            status = "unknown"

        return {
            "device_id": device["id"],
            "hostname": device.get("hostname", ""),
            "vendor": vendor,
            "model": model,
            "current_version": current,
            "recommended_version": recommended_version,
            "status": status,
            "eol": bool(eol_entries),
            "cve_count": sum(len(json.loads(e.get("cve_list", "[]"))) for e in cve_entries),
        }

    async def get_eol_devices(self) -> list[dict[str, Any]]:
        results = await self.check_compliance()
        return [r for r in results if r.get("status") == "eol"]

    async def get_cve_affected(self) -> list[dict[str, Any]]:
        results = await self.check_compliance()
        return [r for r in results if r.get("cve_count", 0) > 0]

    async def get_version_matrix(self) -> list[dict[str, Any]]:
        """Group devices by vendor and version."""
        devices = await self._db.list_devices()
        matrix: dict[str, dict[str, int]] = {}
        for d in devices:
            vendor = (d.get("device_type", "") or "").split("_")[0]
            version = d.get("os_version", "unknown") or "unknown"
            key = f"{vendor}|{version}"
            if key not in matrix:
                matrix[key] = {"vendor": vendor, "version": version, "count": 0}
            matrix[key]["count"] += 1
        return sorted(matrix.values(), key=lambda x: (x["vendor"], x["version"]))
