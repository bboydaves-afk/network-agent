"""Serial console management API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional

router = APIRouter()


def _ctx():
    from interfaces.web.app import ctx
    return ctx


class CommandRequest(BaseModel):
    command: str
    timeout: int = 30


class ConfigRequest(BaseModel):
    commands: list[str]


class BreakRequest(BaseModel):
    duration: float = 0.5


@router.get("/ports")
async def list_serial_ports():
    """List available serial ports on this machine."""
    c = _ctx()
    ports = await c.serial_manager.list_serial_ports()
    return {"ports": ports, "total": len(ports)}


@router.post("/{device_id}/connect")
async def connect_serial(device_id: str):
    """Open a serial console session to a device."""
    c = _ctx()
    try:
        result = await c.serial_manager.connect_serial(device_id)
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{device_id}/disconnect")
async def disconnect_serial(device_id: str):
    """Close an active serial session."""
    c = _ctx()
    try:
        result = await c.serial_manager.disconnect_serial(device_id)
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{device_id}/command")
async def send_command(device_id: str, body: CommandRequest):
    """Send a command over an active serial session."""
    c = _ctx()
    try:
        result = await c.serial_manager.send_command(
            device_id, body.command, timeout=body.timeout
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{device_id}/config")
async def send_config(device_id: str, body: ConfigRequest):
    """Send configuration commands over an active serial session."""
    c = _ctx()
    try:
        result = await c.serial_manager.send_config(device_id, body.commands)
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{device_id}/break")
async def send_break(device_id: str, body: BreakRequest = BreakRequest()):
    """Send a serial break signal (for password recovery)."""
    c = _ctx()
    try:
        result = await c.serial_manager.send_break(device_id, duration=body.duration)
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{device_id}/facts")
async def get_facts(device_id: str):
    """Get device facts over serial console."""
    c = _ctx()
    try:
        result = await c.serial_manager.get_facts(device_id)
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
