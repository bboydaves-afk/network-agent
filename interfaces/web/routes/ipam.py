"""IPAM API routes."""
from typing import Optional
from fastapi import APIRouter, HTTPException, status
router = APIRouter()

def _ctx():
    from interfaces.web.app import ctx
    return ctx

def _engine():
    from operations.ipam import IPAMManager
    return IPAMManager(_ctx().db)

@router.get("/subnets")
async def list_subnets(site_id: Optional[str] = None):
    return {"subnets": await _engine().list_subnets(site_id=site_id)}

@router.post("/subnets", status_code=status.HTTP_201_CREATED)
async def add_subnet(body: dict):
    return await _engine().add_subnet(**body)

@router.get("/subnets/{subnet_id}")
async def get_subnet(subnet_id: str):
    subnet = await _engine().get_subnet(subnet_id)
    if not subnet:
        raise HTTPException(status_code=404, detail="Subnet not found")
    return subnet

@router.delete("/subnets/{subnet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subnet(subnet_id: str):
    deleted = await _engine().delete_subnet(subnet_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Subnet not found")

@router.get("/subnets/{subnet_id}/utilization")
async def get_utilization(subnet_id: str):
    return await _engine().get_utilization(subnet_id)

@router.get("/subnets/{subnet_id}/free")
async def find_free(subnet_id: str, count: int = 5):
    return {"free_ips": await _engine().find_free_ips(subnet_id, count=count)}

@router.post("/subnets/{subnet_id}/scan")
async def scan_subnet(subnet_id: str):
    return await _engine().scan_subnet(subnet_id)

@router.get("/subnets/{subnet_id}/addresses")
async def list_addresses(subnet_id: str):
    return {"addresses": await _engine().list_addresses(subnet_id)}

@router.post("/addresses/reserve")
async def reserve_ip(body: dict):
    return await _engine().reserve_ip(**body)
