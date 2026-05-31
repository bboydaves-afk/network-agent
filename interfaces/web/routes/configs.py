"""Configuration management API routes."""

import difflib
from typing import Optional

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


def _ctx():
    from interfaces.web.app import ctx
    return ctx


# ---- Backup ------------------------------------------------------------------


@router.post("/backup/{device_id}")
async def backup_device_config(device_id: str):
    """Trigger a configuration backup for a single device."""
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    try:
        backup = await ctx.config_manager.backup_device(device_id)
        return backup
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}")


@router.post("/backup-all")
async def backup_all_configs(body: Optional[dict] = None):
    """Trigger configuration backup for all devices (optionally filtered by tag)."""
    ctx = _ctx()
    tag = body.get("tag") if body else None
    devices = await ctx.db.list_devices()

    if tag:
        devices = [d for d in devices if tag in d.get("tags", [])]

    results = []
    for device in devices:
        device_id = device.get("id") or device.get("device_id")
        try:
            backup = await ctx.config_manager.backup_device(device_id)
            results.append({"device_id": device_id, "success": True, "backup": backup})
        except Exception as exc:
            results.append({"device_id": device_id, "success": False, "error": str(exc)})

    succeeded = sum(1 for r in results if r["success"])
    return {
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }


# ---- History -----------------------------------------------------------------


@router.get("/history/{device_id}")
async def config_history(device_id: str, limit: int = 20):
    """Return configuration backup history for a device."""
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    backups = await ctx.db.get_config_backups(device_id, limit=limit)
    return {"device_id": device_id, "backups": backups}


# ---- Get backup content ------------------------------------------------------


@router.get("/backup/{backup_id}")
async def get_backup(backup_id: str):
    """Return the full content of a specific config backup."""
    ctx = _ctx()
    backup = await ctx.db.get_config_backup(backup_id)
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")
    return backup


# ---- Diff --------------------------------------------------------------------


@router.get("/diff/{backup_id_1}/{backup_id_2}")
async def diff_configs(backup_id_1: str, backup_id_2: str):
    """Return a unified diff between two config backups."""
    ctx = _ctx()
    b1 = await ctx.db.get_config_backup(backup_id_1)
    b2 = await ctx.db.get_config_backup(backup_id_2)

    if not b1:
        raise HTTPException(status_code=404, detail=f"Backup {backup_id_1} not found")
    if not b2:
        raise HTTPException(status_code=404, detail=f"Backup {backup_id_2} not found")

    content_1 = (b1.get("content") or "").splitlines(keepends=True)
    content_2 = (b2.get("content") or "").splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            content_1,
            content_2,
            fromfile=f"backup-{backup_id_1}",
            tofile=f"backup-{backup_id_2}",
        )
    )

    return {
        "backup_id_1": backup_id_1,
        "backup_id_2": backup_id_2,
        "diff": "".join(diff_lines),
        "has_changes": len(diff_lines) > 0,
    }


# ---- Deploy / Rollback -------------------------------------------------------


@router.post("/deploy/{device_id}")
async def deploy_config(device_id: str, body: dict):
    """Deploy configuration commands to a device.

    Body: ``{"commands": ["cmd1", "cmd2"], "dry_run": false}``
    """
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    commands = body.get("commands", [])
    dry_run = body.get("dry_run", False)

    if not commands:
        raise HTTPException(status_code=400, detail="No commands provided")

    try:
        result = await ctx.config_manager.deploy_config(
            device_id, commands, dry_run=dry_run
        )
        return {
            "device_id": device_id,
            "dry_run": dry_run,
            "success": True,
            "result": result,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Deploy failed: {exc}")


@router.post("/rollback/{device_id}/{backup_id}")
async def rollback_config(device_id: str, backup_id: str):
    """Rollback a device to a previous configuration backup."""
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    backup = await ctx.db.get_config_backup(backup_id)
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    try:
        result = await ctx.config_manager.rollback_config(device_id, backup_id)
        return {
            "device_id": device_id,
            "backup_id": backup_id,
            "success": True,
            "result": result,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Rollback failed: {exc}")
