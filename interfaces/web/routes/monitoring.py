"""Monitoring API routes."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _ctx():
    from interfaces.web.app import ctx
    return ctx


@router.get("/dashboard")
async def monitoring_dashboard():
    """Return aggregated monitoring dashboard data.

    Includes device counts by status, overall averages for key metrics,
    and recent alerts.
    """
    ctx = _ctx()
    devices = await ctx.db.list_devices()
    active_alerts = await ctx.db.get_alerts(status="active")

    # Compute per-status counts
    status_counts = {"online": 0, "offline": 0, "degraded": 0, "unknown": 0}
    for d in devices:
        s = d.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    # Gather latest CPU / memory metrics across all devices
    cpu_values = []
    mem_values = []
    for d in devices:
        device_id = d.get("id") or d.get("device_id")
        try:
            cpu_metrics = await ctx.db.get_metrics(
                device_id, metric_name="cpu_utilization", limit=1
            )
            if cpu_metrics:
                cpu_values.append(cpu_metrics[0].get("value", 0))
            mem_metrics = await ctx.db.get_metrics(
                device_id, metric_name="memory_utilization", limit=1
            )
            if mem_metrics:
                mem_values.append(mem_metrics[0].get("value", 0))
        except Exception:
            pass

    avg_cpu = round(sum(cpu_values) / len(cpu_values), 1) if cpu_values else 0
    avg_mem = round(sum(mem_values) / len(mem_values), 1) if mem_values else 0

    # Recent alerts (last 10)
    recent_alerts = await ctx.db.get_alerts(limit=10)

    return {
        "device_counts": status_counts,
        "total_devices": len(devices),
        "avg_cpu": avg_cpu,
        "avg_memory": avg_mem,
        "active_alerts": len(active_alerts),
        "recent_alerts": recent_alerts,
    }


@router.get("/metrics/{device_id}")
async def get_device_metrics(
    device_id: str,
    metric_name: Optional[str] = None,
    hours: int = 24,
):
    """Return time-series metrics for a device."""
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    if metric_name:
        metrics = await ctx.db.get_metrics(
            device_id, metric_name=metric_name, since=since
        )
    else:
        metrics = await ctx.db.get_metrics(device_id, since=since)

    return {"device_id": device_id, "hours": hours, "metrics": metrics}


@router.post("/poll/{device_id}")
async def poll_single_device(device_id: str):
    """Trigger an immediate metrics poll for a single device."""
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    try:
        result = await ctx.monitor.poll_device(device_id)
        # Broadcast fresh metric via WebSocket
        from interfaces.web.websockets import manager

        await manager.broadcast("metrics", {
            "type": "metric_update",
            "device_id": device_id,
            "data": result,
        })
        return {"device_id": device_id, "success": True, "data": result}
    except Exception as exc:
        return {"device_id": device_id, "success": False, "error": str(exc)}


@router.post("/poll-all")
async def poll_all_devices():
    """Trigger an immediate metrics poll for every device."""
    ctx = _ctx()
    devices = await ctx.db.list_devices()
    results = []
    for d in devices:
        device_id = d.get("id") or d.get("device_id")
        try:
            result = await ctx.monitor.poll_device(device_id)
            results.append({"device_id": device_id, "success": True, "data": result})
        except Exception as exc:
            results.append({"device_id": device_id, "success": False, "error": str(exc)})

    succeeded = sum(1 for r in results if r["success"])
    return {
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }
