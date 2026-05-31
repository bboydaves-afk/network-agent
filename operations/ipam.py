"""IP Address Management (IPAM) engine."""

from __future__ import annotations

import ipaddress
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

OID_ARP_TABLE = "1.3.6.1.2.1.4.22.1.2"


class IPAMManager:
    """Manage subnets and IP address allocations."""

    def __init__(self, db, credential_manager=None) -> None:
        self._db = db
        self._cred_mgr = credential_manager

    async def add_subnet(
        self, network: str, prefix_length: int, name: str | None = None,
        vlan_id: int | None = None, site_id: str | None = None,
        description: str | None = None, gateway: str | None = None,
        dns_servers: list[str] | None = None,
    ) -> dict[str, Any]:
        subnet_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        import json
        await self._db.execute(
            """INSERT INTO subnets
               (id, network, prefix_length, vlan_id, name, site_id, description, gateway, dns_servers, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (subnet_id, network, prefix_length, vlan_id, name, site_id, description,
             gateway, json.dumps(dns_servers or []), now),
        )
        return await self.get_subnet(subnet_id)

    async def get_subnet(self, subnet_id: str) -> dict[str, Any] | None:
        return await self._db.fetch_one("SELECT * FROM subnets WHERE id = ?", (subnet_id,))

    async def list_subnets(self, site_id: str | None = None) -> list[dict[str, Any]]:
        if site_id:
            subnets = await self._db.fetch_all(
                "SELECT * FROM subnets WHERE site_id = ? ORDER BY network", (site_id,)
            )
        else:
            subnets = await self._db.fetch_all("SELECT * FROM subnets ORDER BY network")

        # Enrich with utilization
        for s in subnets:
            util = await self.get_utilization(s["id"])
            s["utilization_percent"] = util.get("percent", 0)
            s["used"] = util.get("used", 0)
            s["total_hosts"] = util.get("total", 0)
        return subnets

    async def delete_subnet(self, subnet_id: str) -> bool:
        cursor = await self._db.execute("DELETE FROM subnets WHERE id = ?", (subnet_id,))
        return cursor.rowcount > 0

    async def get_utilization(self, subnet_id: str) -> dict[str, Any]:
        subnet = await self.get_subnet(subnet_id)
        if not subnet:
            return {"error": "Subnet not found"}

        net = ipaddress.ip_network(f"{subnet['network']}/{subnet['prefix_length']}", strict=False)
        total_hosts = max(net.num_addresses - 2, 1)  # exclude network and broadcast

        addresses = await self._db.fetch_all(
            "SELECT COUNT(*) as cnt FROM ip_addresses WHERE subnet_id = ?", (subnet_id,)
        )
        used = addresses[0]["cnt"] if addresses else 0

        return {
            "subnet_id": subnet_id,
            "network": str(net),
            "total": total_hosts,
            "used": used,
            "free": total_hosts - used,
            "percent": round((used / total_hosts) * 100, 1) if total_hosts > 0 else 0,
        }

    async def find_free_ips(self, subnet_id: str, count: int = 10) -> list[str]:
        subnet = await self.get_subnet(subnet_id)
        if not subnet:
            return []

        net = ipaddress.ip_network(f"{subnet['network']}/{subnet['prefix_length']}", strict=False)
        assigned = await self._db.fetch_all(
            "SELECT address FROM ip_addresses WHERE subnet_id = ?", (subnet_id,)
        )
        assigned_set = {r["address"] for r in assigned}

        # Also exclude gateway
        gateway = subnet.get("gateway")
        if gateway:
            assigned_set.add(gateway)

        free = []
        for host in net.hosts():
            if str(host) not in assigned_set:
                free.append(str(host))
                if len(free) >= count:
                    break
        return free

    async def reserve_ip(
        self, subnet_id: str, address: str, hostname: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        addr_id = str(uuid4())
        await self._db.execute(
            """INSERT OR REPLACE INTO ip_addresses
               (id, subnet_id, address, hostname, status, notes, last_seen)
               VALUES (?, ?, ?, ?, 'reserved', ?, ?)""",
            (addr_id, subnet_id, address, hostname, notes,
             datetime.now(timezone.utc).isoformat()),
        )
        return {"id": addr_id, "address": address, "status": "reserved"}

    async def list_addresses(self, subnet_id: str) -> list[dict[str, Any]]:
        return await self._db.fetch_all(
            "SELECT * FROM ip_addresses WHERE subnet_id = ? ORDER BY address",
            (subnet_id,),
        )

    async def scan_subnet(self, subnet_id: str) -> dict[str, Any]:
        """Ping sweep a subnet and update IP records."""
        subnet = await self.get_subnet(subnet_id)
        if not subnet:
            return {"error": "Subnet not found"}

        net = ipaddress.ip_network(f"{subnet['network']}/{subnet['prefix_length']}", strict=False)
        now = datetime.now(timezone.utc).isoformat()
        found = 0

        import asyncio
        import subprocess
        import sys

        async def _ping(ip_str: str) -> bool:
            flag = "-n" if sys.platform == "win32" else "-c"
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ping", flag, "1", "-w", "1000" if sys.platform == "win32" else "1", ip_str,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                code = await asyncio.wait_for(proc.wait(), timeout=3)
                return code == 0
            except Exception:
                return False

        sem = asyncio.Semaphore(50)

        async def _check(ip):
            async with sem:
                if await _ping(str(ip)):
                    return str(ip)
            return None

        tasks = [_check(ip) for ip in net.hosts()]
        results = await asyncio.gather(*tasks)
        alive_ips = [ip for ip in results if ip]

        for ip in alive_ips:
            existing = await self._db.fetch_one(
                "SELECT id FROM ip_addresses WHERE subnet_id = ? AND address = ?",
                (subnet_id, ip),
            )
            if existing:
                await self._db.execute(
                    "UPDATE ip_addresses SET status = 'active', last_seen = ? WHERE id = ?",
                    (now, existing["id"]),
                )
            else:
                await self._db.execute(
                    """INSERT INTO ip_addresses (id, subnet_id, address, status, last_seen)
                       VALUES (?, ?, ?, 'active', ?)""",
                    (str(uuid4()), subnet_id, ip, now),
                )
            found += 1

        return {"subnet": str(net), "alive": found, "scanned": net.num_addresses}
