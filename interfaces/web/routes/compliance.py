"""Compliance reporting API routes."""
from typing import Optional
from fastapi import APIRouter, HTTPException
router = APIRouter()

def _ctx():
    from interfaces.web.app import ctx
    return ctx

def _engine():
    from operations.compliance import ComplianceEngine
    return ComplianceEngine(_ctx().db)

@router.post("/run")
async def run_check(body: dict):
    device_id = body.get("device_id")
    ruleset = body.get("ruleset", "cis-network")
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id required")
    return await _engine().run_check(device_id, ruleset)

@router.get("/report/{device_id}")
async def get_report(device_id: str):
    result = await _engine().get_latest_report(device_id)
    if not result:
        raise HTTPException(status_code=404, detail="No compliance report found")
    return result

@router.get("/rules")
async def list_rules(ruleset: str = "cis-network"):
    return {"rules": _engine().list_rules(ruleset)}

@router.get("/history/{device_id}")
async def get_history(device_id: str, limit: int = 20):
    return {"history": await _engine().get_history(device_id, limit=limit)}
