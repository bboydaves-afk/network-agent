"""Firewall management API routes."""

from typing import Optional

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


def _ctx():
    from interfaces.web.app import ctx
    return ctx


def _mgr():
    from operations.firewall import FirewallManager
    return FirewallManager(_ctx().db, getattr(_ctx(), "config_manager", None))


@router.get("/rules")
async def list_rules(device_id: Optional[str] = None):
    if device_id:
        rules = await _mgr().get_rules(device_id)
    else:
        rules = []
    return {"rules": rules}


@router.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_rule(body: dict):
    device_id = body.pop("device_id", None)
    if not device_id:
        raise HTTPException(400, "device_id required")
    try:
        result = await _mgr().create_rule(device_id, body)
        return result
    except Exception as e:
        raise HTTPException(400, str(e))


@router.put("/rules/{rule_id}")
async def modify_rule(rule_id: str, body: dict):
    device_id = body.pop("device_id", None)
    if not device_id:
        raise HTTPException(400, "device_id required")
    try:
        result = await _mgr().modify_rule(device_id, rule_id, body)
        return result
    except Exception as e:
        raise HTTPException(400, str(e))


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: str, device_id: Optional[str] = None):
    if not device_id:
        raise HTTPException(400, "device_id required")
    try:
        await _mgr().delete_rule(device_id, rule_id)
        return None
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/sync/{device_id}")
async def sync_rules(device_id: str):
    try:
        result = await _mgr().sync_rules(device_id)
        return result
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/nat")
async def list_nat_rules(device_id: Optional[str] = None):
    if device_id:
        rules = await _mgr().get_nat_rules(device_id)
    else:
        rules = []
    return {"nat_rules": rules}


@router.post("/nat", status_code=status.HTTP_201_CREATED)
async def create_nat_rule(body: dict):
    device_id = body.pop("device_id", None)
    if not device_id:
        raise HTTPException(400, "device_id required")
    try:
        result = await _mgr().create_nat_rule(device_id, body)
        return result
    except Exception as e:
        raise HTTPException(400, str(e))


@router.delete("/nat/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_nat_rule(rule_id: str, device_id: Optional[str] = None):
    if not device_id:
        raise HTTPException(400, "device_id required")
    try:
        await _mgr().delete_nat_rule(device_id, rule_id)
        return None
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/zones")
async def list_zones(device_id: Optional[str] = None):
    if device_id:
        zones = await _mgr().get_zones(device_id)
    else:
        zones = []
    return {"zones": zones}


@router.post("/zones/sync/{device_id}")
async def sync_zones(device_id: str):
    try:
        result = await _mgr().sync_zones(device_id)
        return result
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/objects")
async def list_objects(device_id: Optional[str] = None):
    if device_id:
        objects = await _mgr().get_objects(device_id)
    else:
        objects = []
    return {"objects": objects}


@router.post("/objects", status_code=status.HTTP_201_CREATED)
async def create_object(body: dict):
    device_id = body.pop("device_id", None)
    if not device_id:
        raise HTTPException(400, "device_id required")
    try:
        result = await _mgr().create_object(device_id, body)
        return result
    except Exception as e:
        raise HTTPException(400, str(e))


@router.delete("/objects/{obj_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_object(obj_id: str, device_id: Optional[str] = None):
    if not device_id:
        raise HTTPException(400, "device_id required")
    try:
        await _mgr().delete_object(device_id, obj_id)
        return None
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/summary/{device_id}")
async def get_summary(device_id: str):
    try:
        result = await _mgr().get_summary(device_id)
        return result
    except Exception as e:
        raise HTTPException(400, str(e))
