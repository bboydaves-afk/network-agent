"""Network discovery API routes."""

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _ctx():
    from interfaces.web.app import ctx
    return ctx


@router.post("/scan")
async def scan_subnet(body: dict):
    """Scan a subnet via SNMP to discover network devices.

    Body::

        {
            "subnet": "192.168.1.0/24",
            "community": "public",
            "timeout": 2
        }
    """
    ctx = _ctx()
    subnet = body.get("subnet")
    if not subnet:
        raise HTTPException(status_code=400, detail="Missing 'subnet' parameter")

    community = body.get("community", "public")
    timeout = body.get("timeout", 2)

    try:
        results = await ctx.discovery.scan_subnet(
            subnet, community=community, timeout=timeout
        )
        return {
            "subnet": subnet,
            "discovered": len(results),
            "devices": results,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Scan failed: {exc}")


@router.post("/ping-sweep")
async def ping_sweep(body: dict):
    """Perform a ping sweep across a subnet.

    Body::

        {"subnet": "10.0.0.0/24"}
    """
    ctx = _ctx()
    subnet = body.get("subnet")
    if not subnet:
        raise HTTPException(status_code=400, detail="Missing 'subnet' parameter")

    try:
        results = await ctx.discovery.ping_sweep(subnet)
        alive = [r for r in results if r.get("alive")]
        return {
            "subnet": subnet,
            "total_scanned": len(results),
            "alive": len(alive),
            "results": results,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ping sweep failed: {exc}")


@router.post("/auto-discover")
async def auto_discover(body: dict):
    """Discover devices on a subnet and automatically add them to inventory.

    Body::

        {
            "subnet": "192.168.1.0/24",
            "community": "public",
            "credential_id": "cred-uuid-here"
        }
    """
    ctx = _ctx()
    subnet = body.get("subnet")
    if not subnet:
        raise HTTPException(status_code=400, detail="Missing 'subnet' parameter")

    community = body.get("community", "public")
    credential_id = body.get("credential_id")

    try:
        discovered = await ctx.discovery.scan_subnet(subnet, community=community)
        added = []
        skipped = []

        for dev in discovered:
            ip = dev.get("ip") or dev.get("hostname")
            # Check if device already exists
            existing = await ctx.db.list_devices()
            already_exists = any(
                d.get("hostname") == ip or d.get("ip_address") == ip
                for d in existing
            )

            if already_exists:
                skipped.append({"ip": ip, "reason": "already exists"})
                continue

            from core.models import DeviceCreate

            new_device = DeviceCreate(
                hostname=dev.get("hostname", ip),
                ip_address=ip,
                device_type=dev.get("device_type", "unknown"),
                vendor=dev.get("vendor", "unknown"),
                credential_id=credential_id,
            )
            created = await ctx.db.add_device(new_device)
            added.append(created)

        return {
            "subnet": subnet,
            "discovered": len(discovered),
            "added": len(added),
            "skipped": len(skipped),
            "added_devices": added,
            "skipped_devices": skipped,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Auto-discover failed: {exc}")
