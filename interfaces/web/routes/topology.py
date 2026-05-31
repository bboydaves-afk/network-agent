"""Topology API routes."""
from typing import Optional
from fastapi import APIRouter, HTTPException
router = APIRouter()

def _ctx():
    from interfaces.web.app import ctx
    return ctx

def _engine():
    from operations.topology import TopologyMapper
    return TopologyMapper(_ctx().db)

@router.get("/")
async def get_topology():
    return await _engine().get_topology_graph()

@router.post("/discover")
async def discover_topology(body: dict = {}):
    device_id = body.get("device_id")
    return await _engine().discover_topology(device_id=device_id)

@router.get("/neighbors/{device_id}")
async def get_neighbors(device_id: str):
    return {"neighbors": await _engine().get_device_neighbors(device_id)}

@router.get("/snapshots")
async def list_snapshots():
    return {"snapshots": await _engine().list_snapshots()}

@router.post("/snapshots")
async def save_snapshot(body: dict = {}):
    return await _engine().save_snapshot(name=body.get("name"))
