"""Site management engine for multi-site device grouping."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class SiteManager:
    """Manage sites and device-to-site assignments."""

    def __init__(self, db) -> None:
        self._db = db

    async def create_site(
        self,
        name: str,
        location: str | None = None,
        region: str | None = None,
        description: str | None = None,
        contact: str | None = None,
    ) -> dict[str, Any]:
        site_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO sites (id, name, location, region, description, contact, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (site_id, name, location, region, description, contact, now),
        )
        return await self.get_site(site_id)

    async def list_sites(self, region: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM sites"
        params: list[Any] = []
        if region:
            query += " WHERE region = ?"
            params.append(region)
        query += " ORDER BY name"
        return await self._db.fetch_all(query, params)

    async def get_site(self, site_id: str) -> dict[str, Any] | None:
        return await self._db.fetch_one("SELECT * FROM sites WHERE id = ?", (site_id,))

    async def update_site(self, site_id: str, **fields: Any) -> bool:
        if not fields:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [site_id]
        cursor = await self._db.execute(
            f"UPDATE sites SET {set_clause} WHERE id = ?", values
        )
        return cursor.rowcount > 0

    async def delete_site(self, site_id: str) -> bool:
        cursor = await self._db.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        return cursor.rowcount > 0

    async def get_site_devices(self, site_id: str) -> list[dict[str, Any]]:
        return await self._db.fetch_all(
            "SELECT * FROM devices WHERE site_id = ? ORDER BY hostname", (site_id,)
        )

    async def assign_device_to_site(self, device_id: str, site_id: str | None) -> bool:
        cursor = await self._db.execute(
            "UPDATE devices SET site_id = ? WHERE id = ?", (site_id, device_id)
        )
        return cursor.rowcount > 0

    async def get_site_summary(self, site_id: str) -> dict[str, Any]:
        site = await self.get_site(site_id)
        if not site:
            return {"error": "Site not found"}
        devices = await self.get_site_devices(site_id)
        total = len(devices)
        online = sum(1 for d in devices if d.get("status") == "online")
        offline = sum(1 for d in devices if d.get("status") == "offline")
        degraded = sum(1 for d in devices if d.get("status") == "degraded")
        return {
            **site,
            "total_devices": total,
            "online": online,
            "offline": offline,
            "degraded": degraded,
            "unknown": total - online - offline - degraded,
        }

    async def get_all_site_summaries(self) -> list[dict[str, Any]]:
        sites = await self.list_sites()
        summaries = []
        for site in sites:
            summary = await self.get_site_summary(site["id"])
            summaries.append(summary)
        return summaries
