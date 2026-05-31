"""Network topology discovery via CDP/LLDP and ARP."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# CDP OIDs (Cisco)
OID_CDP_CACHE_DEVICE_ID = "1.3.6.1.4.1.9.9.23.1.2.1.1.6"
OID_CDP_CACHE_DEVICE_PORT = "1.3.6.1.4.1.9.9.23.1.2.1.1.7"
OID_CDP_CACHE_PLATFORM = "1.3.6.1.4.1.9.9.23.1.2.1.1.8"
OID_CDP_CACHE_ADDRESS = "1.3.6.1.4.1.9.9.23.1.2.1.1.4"

# LLDP OIDs (IEEE 802.1AB)
OID_LLDP_REM_SYS_NAME = "1.0.8802.1.1.2.1.4.1.1.9"
OID_LLDP_REM_PORT_ID = "1.0.8802.1.1.2.1.4.1.1.7"
OID_LLDP_REM_PORT_DESC = "1.0.8802.1.1.2.1.4.1.1.8"
OID_LLDP_REM_SYS_DESC = "1.0.8802.1.1.2.1.4.1.1.10"


class TopologyMapper:
    """Discover L2/L3 topology via CDP/LLDP neighbor tables."""

    def __init__(self, db, credential_manager=None) -> None:
        self._db = db
        self._cred_mgr = credential_manager

    async def discover_device_neighbors(self, device_id: str) -> list[dict[str, Any]]:
        """Discover neighbors for a single device via SSH commands."""
        device = await self._db.get_device(device_id)
        if not device:
            return []

        neighbors: list[dict] = []
        now = datetime.now(timezone.utc).isoformat()

        # Try CDP via SSH
        try:
            output = await self._send_command(device, "show cdp neighbors detail")
            if output:
                neighbors.extend(self._parse_cdp_output(output, device_id, now))
        except Exception as exc:
            logger.debug("CDP discovery failed for %s: %s", device_id, exc)

        # Try LLDP via SSH
        try:
            output = await self._send_command(device, "show lldp neighbors detail")
            if output:
                neighbors.extend(self._parse_lldp_output(output, device_id, now))
        except Exception as exc:
            logger.debug("LLDP discovery failed for %s: %s", device_id, exc)

        # Clear old neighbors and store new ones
        await self._db.execute(
            "DELETE FROM topology_neighbors WHERE device_id = ?", (device_id,)
        )
        for n in neighbors:
            await self._db.execute(
                """INSERT INTO topology_neighbors
                   (id, device_id, local_interface, neighbor_device_id, neighbor_hostname,
                    neighbor_ip, neighbor_port, neighbor_platform, protocol, discovered_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid4()), device_id, n.get("local_interface", ""),
                 n.get("neighbor_device_id"), n.get("neighbor_hostname", ""),
                 n.get("neighbor_ip", ""), n.get("neighbor_port", ""),
                 n.get("neighbor_platform", ""), n.get("protocol", "cdp"), now),
            )

        return neighbors

    async def discover_full_topology(self) -> dict[str, Any]:
        """Discover topology across all devices."""
        devices = await self._db.list_devices()
        all_neighbors = []
        for device in devices:
            try:
                neighbors = await self.discover_device_neighbors(device["id"])
                all_neighbors.extend(neighbors)
            except Exception as exc:
                logger.warning("Topology discovery failed for %s: %s", device.get("hostname"), exc)

        graph = await self.get_topology_graph()
        return {
            "total_neighbors": len(all_neighbors),
            "nodes": graph.get("nodes", []),
            "edges": graph.get("edges", []),
        }

    async def get_topology_graph(self) -> dict[str, Any]:
        """Build a vis.js compatible graph from stored neighbor data."""
        devices = await self._db.list_devices()
        neighbors = await self._db.fetch_all(
            "SELECT * FROM topology_neighbors ORDER BY device_id"
        )

        # Build node list from devices
        nodes = []
        device_map = {}
        for d in devices:
            device_map[d["id"]] = d
            device_map[d.get("hostname", "").lower()] = d
            nodes.append({
                "id": d["id"],
                "label": d.get("hostname", d["id"][:8]),
                "hostname": d.get("hostname", ""),
                "ip_address": d.get("ip_address", ""),
                "status": d.get("status", "unknown"),
                "device_type": d.get("device_type", ""),
            })

        # Build edge list from neighbors
        edges = []
        seen_edges = set()
        for n in neighbors:
            source = n["device_id"]
            target = n.get("neighbor_device_id") or ""

            # Try to correlate neighbor to a known device
            if not target:
                hostname = (n.get("neighbor_hostname") or "").lower().split(".")[0]
                if hostname in device_map:
                    target = device_map[hostname]["id"]

            edge_key = tuple(sorted([source, target])) if target else (source, n.get("neighbor_hostname", ""))
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)

            edges.append({
                "from": source,
                "to": target or n.get("neighbor_hostname", "unknown"),
                "label": f"{n.get('local_interface', '')} <-> {n.get('neighbor_port', '')}",
                "protocol": n.get("protocol", "cdp"),
            })

        return {"nodes": nodes, "edges": edges, "neighbors": neighbors}

    async def get_device_neighbors(self, device_id: str) -> list[dict[str, Any]]:
        return await self._db.fetch_all(
            "SELECT * FROM topology_neighbors WHERE device_id = ? ORDER BY local_interface",
            (device_id,),
        )

    async def save_snapshot(self, name: str) -> dict[str, Any]:
        graph = await self.get_topology_graph()
        snapshot_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO topology_snapshots (id, name, snapshot_data, device_count, link_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (snapshot_id, name, json.dumps(graph), len(graph["nodes"]), len(graph["edges"]), now),
        )
        return {"id": snapshot_id, "name": name, "created_at": now}

    async def list_snapshots(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self._db.fetch_all(
            "SELECT id, name, device_count, link_count, created_at FROM topology_snapshots ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    async def _send_command(self, device: dict, command: str) -> str:
        """Connect to device and execute a command."""
        if not self._cred_mgr:
            return ""
        try:
            from devices.registry import get_device_class
            creds = await self._cred_mgr.get_credentials(device.get("credential_id", ""))
            device_cls = get_device_class(device["device_type"])
            dev = device_cls(
                host=device.get("ip_address", ""),
                username=creds.get("username", ""),
                password=creds.get("password", ""),
                port=device.get("port", 22),
                device_type=device["device_type"],
                enable_secret=creds.get("enable_secret", ""),
            )
            await dev.connect()
            try:
                return await dev.send_command(command, timeout=30)
            finally:
                await dev.disconnect()
        except Exception as exc:
            logger.debug("Command '%s' failed on %s: %s", command, device.get("hostname"), exc)
            return ""

    def _parse_cdp_output(self, output: str, device_id: str, timestamp: str) -> list[dict]:
        """Parse 'show cdp neighbors detail' output."""
        neighbors = []
        current: dict[str, Any] = {}
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("Device ID:"):
                if current:
                    neighbors.append(current)
                current = {"protocol": "cdp", "device_id": device_id}
                current["neighbor_hostname"] = line.split(":", 1)[1].strip()
            elif "IP address:" in line.lower() or "IPv4 Address:" in line:
                current["neighbor_ip"] = line.split(":", 1)[1].strip()
            elif line.startswith("Platform:"):
                parts = line.split(",")
                current["neighbor_platform"] = parts[0].split(":", 1)[1].strip()
            elif line.startswith("Interface:"):
                parts = line.split(",")
                current["local_interface"] = parts[0].split(":", 1)[1].strip()
                if len(parts) > 1 and "Port ID" in parts[1]:
                    current["neighbor_port"] = parts[1].split(":", 1)[1].strip()
        if current and current.get("neighbor_hostname"):
            neighbors.append(current)
        return neighbors

    def _parse_lldp_output(self, output: str, device_id: str, timestamp: str) -> list[dict]:
        """Parse 'show lldp neighbors detail' output."""
        neighbors = []
        current: dict[str, Any] = {}
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("Local Intf:") or line.startswith("Local Port id:"):
                if current and current.get("neighbor_hostname"):
                    neighbors.append(current)
                current = {"protocol": "lldp", "device_id": device_id}
                current["local_interface"] = line.split(":", 1)[1].strip()
            elif line.startswith("System Name:"):
                current["neighbor_hostname"] = line.split(":", 1)[1].strip()
            elif line.startswith("Port id:") or line.startswith("Port Description:"):
                if "neighbor_port" not in current:
                    current["neighbor_port"] = line.split(":", 1)[1].strip()
            elif line.startswith("Management Addresses:") or "Management Address:" in line:
                pass  # next line may have IP
            elif "IP:" in line or line.replace(".", "").isdigit():
                parts = line.split(":")
                if len(parts) > 1:
                    current["neighbor_ip"] = parts[-1].strip()
            elif line.startswith("System Description:"):
                current["neighbor_platform"] = line.split(":", 1)[1].strip()[:100]
        if current and current.get("neighbor_hostname"):
            neighbors.append(current)
        return neighbors
