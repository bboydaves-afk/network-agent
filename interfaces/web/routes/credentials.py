"""Credential rotation API routes."""
from typing import Optional
from fastapi import APIRouter, HTTPException
router = APIRouter()

def _ctx():
    from interfaces.web.app import ctx
    return ctx

def _engine():
    from operations.credential_rotation import CredentialRotator
    return CredentialRotator(_ctx().db)

@router.post("/rotate")
async def rotate_credential(body: dict):
    credential_id = body.get("credential_id")
    if not credential_id:
        raise HTTPException(status_code=400, detail="credential_id required")
    return await _engine().rotate_credential(credential_id, initiated_by=body.get("initiated_by", "web-admin"))

@router.post("/verify")
async def verify_credentials(body: dict):
    credential_id = body.get("credential_id")
    if not credential_id:
        raise HTTPException(status_code=400, detail="credential_id required")
    return await _engine().verify_all_devices(credential_id)

@router.get("/rotations")
async def rotation_history(credential_id: Optional[str] = None, limit: int = 20):
    return {"rotations": await _engine().get_rotation_history(credential_id=credential_id, limit=limit)}

@router.get("/rotations/{rotation_id}")
async def get_rotation(rotation_id: str):
    rotation = await _engine().get_rotation(rotation_id)
    if not rotation:
        raise HTTPException(status_code=404, detail="Rotation not found")
    return rotation
