"""Routing management API routes."""

from typing import Optional

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


def _ctx():
    from interfaces.web.app import ctx
    return ctx


def _mgr():
    from operations.routing import RoutingManager
    return RoutingManager(_ctx().db, getattr(_ctx(), "config_manager", None))


@router.get("/table/{device_id}")
async def get_routing_table(device_id: str, protocol: Optional[str] = None):
    """Get routing table for a device."""
    routes = await _mgr().get_routing_table(device_id, protocol)
    return {"routes": routes, "count": len(routes)}


@router.post("/sync/{device_id}")
async def sync_routes(device_id: str):
    """Sync routing table from a live device."""
    try:
        return await _mgr().sync_routes(device_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/static", status_code=status.HTTP_201_CREATED)
async def create_static_route(body: dict):
    """Create a static route."""
    for field in ("device_id", "destination", "prefix_length", "next_hop"):
        if field not in body:
            raise HTTPException(400, f"{field} required")
    try:
        return await _mgr().create_static_route(
            device_id=body["device_id"],
            destination=body["destination"],
            prefix_length=int(body["prefix_length"]),
            next_hop=body["next_hop"],
            metric=body.get("metric", 0),
            vrf=body.get("vrf"),
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.delete("/static", status_code=status.HTTP_204_NO_CONTENT)
async def delete_static_route(body: dict):
    """Delete a static route."""
    for field in ("device_id", "destination", "prefix_length", "next_hop"):
        if field not in body:
            raise HTTPException(400, f"{field} required")
    try:
        await _mgr().delete_static_route(
            body["device_id"], body["destination"],
            int(body["prefix_length"]), body["next_hop"],
        )
        return None
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/ospf/{device_id}")
async def get_ospf_status(device_id: str):
    """Get OSPF status for a device."""
    try:
        return await _mgr().get_ospf_status(device_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/ospf/sync/{device_id}")
async def sync_ospf(device_id: str):
    """Sync OSPF neighbors from a live device."""
    try:
        return await _mgr().sync_ospf(device_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/ospf/config", status_code=status.HTTP_201_CREATED)
async def configure_ospf(body: dict):
    """Configure OSPF on a device."""
    device_id = body.get("device_id")
    if not device_id:
        raise HTTPException(400, "device_id required")
    try:
        return await _mgr().configure_ospf(
            device_id=device_id,
            process_id=body.get("process_id", 1),
            router_id=body.get("router_id"),
            networks=body.get("networks"),
        )
    except Exception as e:
        raise HTTPException(400, str(e))
