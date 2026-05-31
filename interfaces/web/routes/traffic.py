"""Traffic analysis API routes."""
from typing import Optional
from fastapi import APIRouter
router = APIRouter()

def _ctx():
    from interfaces.web.app import ctx
    return ctx

def _engine():
    from operations.traffic import TrafficAnalyzer
    return TrafficAnalyzer(_ctx().db)

@router.get("/trends/{device_id}")
async def get_trends(device_id: str, hours: int = 24):
    return await _engine().get_traffic_trends(device_id, hours=hours)

@router.get("/top")
async def top_interfaces(count: int = 10):
    return {"top_interfaces": await _engine().get_top_talkers(count=count)}

@router.get("/report/{device_id}")
async def bandwidth_report(device_id: str):
    return await _engine().get_bandwidth_report(device_id)

@router.get("/utilization/{device_id}")
async def interface_utilization(device_id: str):
    return await _engine().get_interface_utilization(device_id)
