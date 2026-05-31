"""Site management API routes."""

from typing import Optional

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


def _ctx():
    from interfaces.web.app import ctx
    return ctx


def _mgr():
    from operations.sites import SiteManager
    return SiteManager(_ctx().db)


@router.get("/")
async def list_sites(region: Optional[str] = None):
    return {"sites": await _mgr().list_sites(region=region)}


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_site(body: dict):
    site = await _mgr().create_site(
        name=body["name"],
        location=body.get("location"),
        region=body.get("region"),
        description=body.get("description"),
        contact=body.get("contact"),
    )
    return site


@router.get("/summaries")
async def all_site_summaries():
    return {"sites": await _mgr().get_all_site_summaries()}


@router.get("/{site_id}")
async def get_site(site_id: str):
    site = await _mgr().get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


@router.put("/{site_id}")
async def update_site(site_id: str, body: dict):
    existing = await _mgr().get_site(site_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Site not found")
    await _mgr().update_site(site_id, **{k: v for k, v in body.items() if v is not None})
    return await _mgr().get_site(site_id)


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(site_id: str):
    deleted = await _mgr().delete_site(site_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Site not found")
    return None


@router.get("/{site_id}/devices")
async def site_devices(site_id: str):
    return {"devices": await _mgr().get_site_devices(site_id)}


@router.get("/{site_id}/summary")
async def site_summary(site_id: str):
    return await _mgr().get_site_summary(site_id)


@router.post("/{site_id}/assign/{device_id}")
async def assign_device(site_id: str, device_id: str):
    updated = await _mgr().assign_device_to_site(device_id, site_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"status": "assigned", "device_id": device_id, "site_id": site_id}


@router.post("/unassign/{device_id}")
async def unassign_device(device_id: str):
    updated = await _mgr().assign_device_to_site(device_id, None)
    if not updated:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"status": "unassigned", "device_id": device_id}
