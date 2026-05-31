"""Interface utilization and traffic trending analysis."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)


class TrafficAnalyzer:
    """Analyze interface bandwidth and traffic patterns."""

    def __init__(self, db) -> None:
        self._db = db

    async def get_interface_utilization(
        self, device_id: str, interface: str | None = None, hours: int = 24,
    ) -> dict[str, Any]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        query = "SELECT * FROM metrics WHERE device_id = ? AND metric_name LIKE 'interface_bps_%' AND timestamp >= ?"
        params: list[Any] = [device_id, since]
        if interface:
            query += " AND interface = ?"
            params.append(interface)
        query += " ORDER BY timestamp"
        metrics = await self._db.fetch_all(query, params)

        # Group by interface
        interfaces: dict[str, dict] = {}
        for m in metrics:
            iface = m.get("interface", "unknown")
            if iface not in interfaces:
                interfaces[iface] = {"name": iface, "data_in": [], "data_out": []}
            if "bps_in" in m["metric_name"]:
                interfaces[iface]["data_in"].append({"timestamp": m["timestamp"], "value": m["metric_value"]})
            elif "bps_out" in m["metric_name"]:
                interfaces[iface]["data_out"].append({"timestamp": m["timestamp"], "value": m["metric_value"]})

        return {"device_id": device_id, "hours": hours, "interfaces": list(interfaces.values())}

    async def get_traffic_trends(self, device_id: str, hours: int = 24) -> dict[str, Any]:
        return await self.get_interface_utilization(device_id, hours=hours)

    async def get_top_talkers(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get interfaces with highest recent bandwidth usage."""
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        rows = await self._db.fetch_all(
            """SELECT device_id, interface, metric_name, AVG(metric_value) as avg_bps
               FROM metrics
               WHERE metric_name IN ('interface_bps_in', 'interface_bps_out')
                 AND timestamp >= ?
               GROUP BY device_id, interface, metric_name
               ORDER BY avg_bps DESC
               LIMIT ?""",
            (since, limit * 2),
        )

        # Merge in/out per interface
        combined: dict[str, dict] = {}
        for r in rows:
            key = f"{r['device_id']}:{r['interface']}"
            if key not in combined:
                device = await self._db.get_device(r["device_id"])
                combined[key] = {
                    "device_id": r["device_id"],
                    "hostname": device.get("hostname", "") if device else "",
                    "interface": r["interface"],
                    "bps_in": 0, "bps_out": 0, "utilization": 0,
                }
            if "bps_in" in r["metric_name"]:
                combined[key]["bps_in"] = round(r["avg_bps"], 0)
            elif "bps_out" in r["metric_name"]:
                combined[key]["bps_out"] = round(r["avg_bps"], 0)

        # Sort by total bandwidth
        results = sorted(combined.values(), key=lambda x: x["bps_in"] + x["bps_out"], reverse=True)
        return results[:limit]

    async def calculate_95th_percentile(
        self, device_id: str, interface: str, hours: int = 168,
    ) -> dict[str, Any]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = await self._db.fetch_all(
            """SELECT metric_value FROM metrics
               WHERE device_id = ? AND interface = ? AND metric_name = 'interface_bps_in'
                 AND timestamp >= ?
               ORDER BY metric_value""",
            (device_id, interface, since),
        )
        if not rows:
            return {"error": "No data"}
        values = [r["metric_value"] for r in rows]
        idx = int(len(values) * 0.95)
        return {
            "device_id": device_id, "interface": interface,
            "hours": hours, "data_points": len(values),
            "p95_bps_in": round(values[min(idx, len(values) - 1)], 0),
            "max_bps_in": round(max(values), 0),
            "avg_bps_in": round(sum(values) / len(values), 0),
        }
