"""VLAN management API routes."""

from typing import Optional

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


def _ctx():
    from interfaces.web.app import ctx
    return ctx


def _mgr():
    from operations.vlan import VlanManager
    return VlanManager(_ctx().db, getattr(_ctx(), "config_manager", None))


@router.get("/{device_id}")
async def list_vlans(device_id: str):
    """List all VLANs for a device."""
    vlans = await _mgr().list_vlans(device_id)
    return {"vlans": vlans, "count": len(vlans)}


@router.get("/{device_id}/summary")
async def get_vlan_summary(device_id: str):
    """Get VLAN summary with interface assignments."""
    try:
        return await _mgr().get_vlan_summary(device_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/sync/{device_id}")
async def sync_vlans(device_id: str):
    """Sync VLANs from a live device to the database."""
    try:
        return await _mgr().sync_vlans(device_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_vlan(body: dict):
    """Create a VLAN on a device."""
    device_id = body.get("device_id")
    vlan_id = body.get("vlan_id")
    if not device_id or vlan_id is None:
        raise HTTPException(400, "device_id and vlan_id required")
    try:
        return await _mgr().create_vlan(
            device_id=device_id,
            vlan_id=int(vlan_id),
            name=body.get("name"),
            description=body.get("description"),
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.delete("/{device_id}/{vlan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vlan(device_id: str, vlan_id: int):
    """Delete a VLAN from a device."""
    try:
        await _mgr().delete_vlan(device_id, vlan_id)
        return None
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/assign", status_code=status.HTTP_201_CREATED)
async def assign_interface(body: dict):
    """Assign an interface to a VLAN."""
    device_id = body.get("device_id")
    vlan_id = body.get("vlan_id")
    interface = body.get("interface")
    if not device_id or vlan_id is None or not interface:
        raise HTTPException(400, "device_id, vlan_id, and interface required")
    try:
        return await _mgr().assign_interface(
            device_id=device_id,
            vlan_id=int(vlan_id),
            interface=interface,
            mode=body.get("mode", "access"),
        )
    except Exception as e:
        raise HTTPException(400, str(e))
