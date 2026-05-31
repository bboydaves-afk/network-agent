"""ACL management API routes."""

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


def _ctx():
    from interfaces.web.app import ctx
    return ctx


def _mgr():
    from operations.acl import ACLManager
    return ACLManager(_ctx().db, getattr(_ctx(), "config_manager", None))


@router.get("/{device_id}")
async def list_acls(device_id: str):
    """List all ACLs for a device."""
    acls = await _mgr().list_acls(device_id)
    return {"acls": acls, "count": len(acls)}


@router.post("/sync/{device_id}")
async def sync_acls(device_id: str):
    """Sync ACLs from a live device."""
    try:
        return await _mgr().sync_acls(device_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_acl(body: dict):
    """Create an ACL."""
    device_id = body.get("device_id")
    name = body.get("name")
    if not device_id or not name:
        raise HTTPException(400, "device_id and name required")
    try:
        return await _mgr().create_acl(
            device_id, name,
            body.get("acl_type", "extended"),
            body.get("description"),
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.delete("/{device_id}/{acl_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_acl(device_id: str, acl_name: str):
    """Delete an ACL."""
    try:
        await _mgr().delete_acl(device_id, acl_name)
        return None
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/entry", status_code=status.HTTP_201_CREATED)
async def add_entry(body: dict):
    """Add an entry to an ACL."""
    for field in ("device_id", "acl_name", "sequence", "action"):
        if field not in body:
            raise HTTPException(400, f"{field} required")
    try:
        return await _mgr().add_entry(
            device_id=body["device_id"],
            acl_name=body["acl_name"],
            sequence=int(body["sequence"]),
            action=body["action"],
            protocol=body.get("protocol", "ip"),
            source=body.get("source", "any"),
            destination=body.get("destination", "any"),
            source_wildcard=body.get("source_wildcard"),
            dest_wildcard=body.get("dest_wildcard"),
            dest_port=body.get("dest_port"),
            log_enabled=body.get("log_enabled", False),
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.delete("/entry/{device_id}/{acl_name}/{sequence}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_entry(device_id: str, acl_name: str, sequence: int):
    """Remove an ACL entry."""
    try:
        await _mgr().remove_entry(device_id, acl_name, sequence)
        return None
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/bind", status_code=status.HTTP_201_CREATED)
async def bind_acl(body: dict):
    """Bind an ACL to an interface."""
    for field in ("device_id", "acl_name", "interface"):
        if field not in body:
            raise HTTPException(400, f"{field} required")
    try:
        return await _mgr().bind_acl(
            body["device_id"], body["acl_name"],
            body["interface"], body.get("direction", "in"),
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/unbind")
async def unbind_acl(body: dict):
    """Unbind an ACL from an interface."""
    for field in ("device_id", "acl_name", "interface"):
        if field not in body:
            raise HTTPException(400, f"{field} required")
    try:
        return await _mgr().unbind_acl(
            body["device_id"], body["acl_name"],
            body["interface"], body.get("direction", "in"),
        )
    except Exception as e:
        raise HTTPException(400, str(e))
