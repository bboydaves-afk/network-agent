"""Syslog API routes."""
from typing import Optional
from fastapi import APIRouter
router = APIRouter()

def _ctx():
    from interfaces.web.app import ctx
    return ctx

def _engine():
    from operations.syslog import SyslogReceiver
    return SyslogReceiver(_ctx().db)

@router.get("/")
async def search_syslog(
    device_id: Optional[str] = None,
    severity: Optional[int] = None,
    query: Optional[str] = None,
    limit: int = 100,
):
    return {"messages": await _engine().search_messages(device_id=device_id, min_severity=severity, query=query, limit=limit)}

@router.get("/stats")
async def syslog_stats():
    return await _engine().get_stats()

@router.get("/stream-config")
async def stream_config():
    """Return WebSocket URL for syslog stream."""
    return {"ws_url": "/ws/syslog"}
