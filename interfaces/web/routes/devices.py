"""Device management API routes."""

from typing import Optional

from fastapi import APIRouter, HTTPException, status

from core.models import DeviceCreate, DeviceUpdate

router = APIRouter()


def _ctx():
    """Lazy import of the shared application context."""
    from interfaces.web.app import ctx
    return ctx


# ---- List / Create ----------------------------------------------------------


@router.get("/")
async def list_devices(
    tag: Optional[str] = None,
    status_filter: Optional[str] = None,
    device_type: Optional[str] = None,
):
    """Return all devices, optionally filtered by tag, status, or type."""
    ctx = _ctx()
    devices = await ctx.db.list_devices()

    if tag:
        devices = [d for d in devices if tag in d.get("tags", [])]
    if status_filter:
        devices = [d for d in devices if d.get("status") == status_filter]
    if device_type:
        devices = [d for d in devices if d.get("device_type") == device_type]

    return {"devices": devices, "total": len(devices)}


@router.post("/", status_code=status.HTTP_201_CREATED)
async def add_device(device: DeviceCreate):
    """Add a new device to the inventory."""
    ctx = _ctx()
    created = await ctx.db.add_device(device)
    return created


# ---- Single device -----------------------------------------------------------


@router.get("/{device_id}")
async def get_device(device_id: str):
    """Return details for a single device."""
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.put("/{device_id}")
async def update_device(device_id: str, update: DeviceUpdate):
    """Update an existing device."""
    ctx = _ctx()
    existing = await ctx.db.get_device(device_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Device not found")
    updated = await ctx.db.update_device(device_id, update)
    return updated


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(device_id: str):
    """Remove a device from the inventory."""
    ctx = _ctx()
    existing = await ctx.db.get_device(device_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Device not found")
    await ctx.db.delete_device(device_id)
    return None


# ---- Connectivity test -------------------------------------------------------


@router.post("/{device_id}/test")
async def test_device_connectivity(device_id: str):
    """Attempt to connect to the device, retrieve facts, and report results."""
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    try:
        result = await ctx.troubleshooter.test_connectivity(device_id)
        return {
            "device_id": device_id,
            "success": True,
            "details": result,
        }
    except Exception as exc:
        return {
            "device_id": device_id,
            "success": False,
            "error": str(exc),
        }


# ---- Tags --------------------------------------------------------------------


@router.get("/{device_id}/tags")
async def get_device_tags(device_id: str):
    """Return the tag list for a device."""
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"device_id": device_id, "tags": device.get("tags", [])}


@router.post("/{device_id}/tags")
async def add_device_tag(device_id: str, body: dict):
    """Add a tag to a device.  Body: ``{"tag": "some-tag"}``."""
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    tag = body.get("tag")
    if not tag:
        raise HTTPException(status_code=400, detail="Missing 'tag' in request body")

    tags = device.get("tags", [])
    if tag not in tags:
        tags.append(tag)
        await ctx.db.update_device(device_id, DeviceUpdate(tags=tags))
    return {"device_id": device_id, "tags": tags}


@router.delete("/{device_id}/tags/{tag}")
async def remove_device_tag(device_id: str, tag: str):
    """Remove a tag from a device."""
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    tags = device.get("tags", [])
    if tag in tags:
        tags.remove(tag)
        await ctx.db.update_device(device_id, DeviceUpdate(tags=tags))
    return {"device_id": device_id, "tags": tags}
