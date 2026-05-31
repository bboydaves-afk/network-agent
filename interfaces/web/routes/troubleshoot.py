"""Diagnostics / troubleshooting API routes."""

import asyncio
import socket
from typing import Optional

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _ctx():
    from interfaces.web.app import ctx
    return ctx


@router.post("/ping")
async def ping_test(body: dict):
    """Run a ping test.

    Body::

        {
            "target": "192.168.1.1",
            "count": 4,
            "source_device_id": null
        }
    """
    ctx = _ctx()
    target = body.get("target")
    if not target:
        raise HTTPException(status_code=400, detail="Missing 'target' parameter")

    count = body.get("count", 4)
    source_device_id = body.get("source_device_id")

    try:
        if source_device_id:
            result = await ctx.troubleshooter.ping(
                target, count=count, source_device_id=source_device_id
            )
        else:
            result = await ctx.troubleshooter.ping(target, count=count)
        return {"target": target, "success": True, "result": result}
    except Exception as exc:
        return {"target": target, "success": False, "error": str(exc)}


@router.post("/traceroute")
async def traceroute_test(body: dict):
    """Run a traceroute.

    Body::

        {
            "target": "8.8.8.8",
            "source_device_id": null
        }
    """
    ctx = _ctx()
    target = body.get("target")
    if not target:
        raise HTTPException(status_code=400, detail="Missing 'target' parameter")

    source_device_id = body.get("source_device_id")

    try:
        if source_device_id:
            result = await ctx.troubleshooter.traceroute(
                target, source_device_id=source_device_id
            )
        else:
            result = await ctx.troubleshooter.traceroute(target)
        return {"target": target, "success": True, "result": result}
    except Exception as exc:
        return {"target": target, "success": False, "error": str(exc)}


@router.post("/port-check")
async def port_check(body: dict):
    """Check whether a TCP port is open on a target host.

    Body::

        {
            "target": "192.168.1.1",
            "port": 22,
            "timeout": 3
        }
    """
    target = body.get("target")
    port = body.get("port")
    timeout = body.get("timeout", 3)

    if not target or port is None:
        raise HTTPException(
            status_code=400, detail="Missing 'target' or 'port' parameter"
        )

    loop = asyncio.get_event_loop()
    try:
        # Run the blocking socket check in a thread
        def _check():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                result = sock.connect_ex((target, int(port)))
                return result == 0
            finally:
                sock.close()

        is_open = await loop.run_in_executor(None, _check)
        return {
            "target": target,
            "port": port,
            "open": is_open,
            "status": "open" if is_open else "closed/filtered",
        }
    except Exception as exc:
        return {
            "target": target,
            "port": port,
            "open": False,
            "error": str(exc),
        }


@router.get("/health/{device_id}")
async def device_health(device_id: str):
    """Run a comprehensive health check on a device."""
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    try:
        result = await ctx.troubleshooter.health_check(device_id)
        return {"device_id": device_id, "success": True, "health": result}
    except Exception as exc:
        return {"device_id": device_id, "success": False, "error": str(exc)}


@router.get("/interfaces/{device_id}")
async def device_interfaces(device_id: str):
    """Return interface status for a device."""
    ctx = _ctx()
    device = await ctx.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    try:
        interfaces = await ctx.troubleshooter.get_interfaces(device_id)
        return {"device_id": device_id, "interfaces": interfaces}
    except Exception as exc:
        return {"device_id": device_id, "error": str(exc)}


@router.post("/dns")
async def dns_lookup(body: dict):
    """Perform a DNS lookup for a hostname.

    Body::

        {"hostname": "example.com"}
    """
    hostname = body.get("hostname")
    if not hostname:
        raise HTTPException(status_code=400, detail="Missing 'hostname' parameter")

    loop = asyncio.get_event_loop()

    try:
        def _resolve():
            results = []
            try:
                addr_info = socket.getaddrinfo(hostname, None)
                seen = set()
                for family, kind, proto, canonname, sockaddr in addr_info:
                    ip = sockaddr[0]
                    if ip not in seen:
                        seen.add(ip)
                        record_type = "AAAA" if family == socket.AF_INET6 else "A"
                        results.append({"type": record_type, "address": ip})
            except socket.gaierror as e:
                return {"error": str(e)}
            return results

        resolved = await loop.run_in_executor(None, _resolve)

        if isinstance(resolved, dict) and "error" in resolved:
            return {"hostname": hostname, "success": False, "error": resolved["error"]}

        return {"hostname": hostname, "success": True, "records": resolved}
    except Exception as exc:
        return {"hostname": hostname, "success": False, "error": str(exc)}
