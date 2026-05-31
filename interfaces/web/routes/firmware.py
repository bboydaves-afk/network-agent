"""Firmware management API routes."""
from typing import Optional
from fastapi import APIRouter, HTTPException, status
router = APIRouter()

def _ctx():
    from interfaces.web.app import ctx
    return ctx

def _engine():
    from operations.firmware import FirmwareManager
    return FirmwareManager(_ctx().db)

@router.get("/status")
async def firmware_status():
    return await _engine().check_compliance()

@router.get("/catalog")
async def list_catalog():
    return {"catalog": await _engine().list_catalog()}

@router.post("/catalog", status_code=status.HTTP_201_CREATED)
async def add_catalog_entry(body: dict):
    return await _engine().add_catalog_entry(**body)

@router.get("/eol")
async def eol_devices():
    return {"devices": await _engine().get_eol_devices()}

@router.get("/matrix")
async def version_matrix():
    return await _engine().get_version_matrix()

@router.get("/cve")
async def cve_affected():
    return {"devices": await _engine().get_cve_affected()}
