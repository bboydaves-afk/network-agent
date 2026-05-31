"""Routing management engine.

Provides vendor-abstracted route table sync, static route CRUD,
and OSPF configuration/neighbor management for all supported vendors:
Cisco IOS/IOS-XE, Juniper JunOS, Fortinet, Palo Alto,
MikroTik, Aruba, pfSense, and Sophos.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


class RoutingError(Exception):
    """Raised when a routing operation fails."""


class RoutingManager:
    """Manage routing tables, static routes, and OSPF across vendors."""

    VENDOR_MAP = {
        "cisco_ios": "cisco_ios",
        "cisco_iosxe": "cisco_ios",
        "juniper_junos": "juniper",
        "fortinet": "fortinet",
        "paloalto": "paloalto",
        "mikrotik": "mikrotik",
        "aruba": "aruba",
        "pfsense": "pfsense",
        "sophos": "sophos",
    }

    def __init__(self, db, config_manager=None):
        self._db = db
        self._config_mgr = config_manager

    def _get_config_manager(self):
        if not self._config_mgr:
            raise RoutingError(
                "RoutingManager requires a ConfigManager instance."
            )
        return self._config_mgr

    def _get_vendor(self, device_info: dict) -> str:
        dtype = device_info.get("device_type", "").lower()
        vendor = self.VENDOR_MAP.get(dtype)
        if not vendor:
            raise RoutingError(
                f"Unsupported vendor for routing: {dtype!r}. "
                f"Supported: {list(self.VENDOR_MAP.keys())}"
            )
        return vendor

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_routing_table(self, device_id: str, protocol: str | None = None) -> list[dict]:
        """Get the routing table from DB."""
        return await self._db.get_routes_by_device(device_id, protocol)

    async def get_ospf_status(self, device_id: str) -> dict:
        """Get OSPF config, neighbors, and areas from DB."""
        config = await self._db.get_ospf_config(device_id)
        if not config:
            return {"device_id": device_id, "ospf_enabled": False}

        neighbors = await self._db.get_ospf_neighbors(config["id"])
        areas = await self._db.get_ospf_areas(config["id"])

        return {
            "device_id": device_id,
            "ospf_enabled": True,
            "process_id": config.get("process_id", 1),
            "router_id": config.get("router_id"),
            "status": config.get("status"),
            "neighbor_count": len(neighbors),
            "neighbors": neighbors,
            "area_count": len(areas),
            "areas": areas,
        }

    # ------------------------------------------------------------------
    # Sync from live device
    # ------------------------------------------------------------------

    async def sync_routes(self, device_id: str) -> dict:
        """Pull routing table from live device and store in DB."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise RoutingError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        cm = self._get_config_manager()
        _record, device = await cm._get_device_and_connect(device_id)

        try:
            raw_routes = await self._fetch_vendor_routes(device, vendor)
            await self._db.clear_routes_for_device(device_id)

            now = datetime.now(timezone.utc).isoformat()
            count = 0
            for route in raw_routes:
                await self._db.add_route(
                    id=str(uuid4()),
                    device_id=device_id,
                    destination=route["destination"],
                    prefix_length=route["prefix_length"],
                    next_hop=route.get("next_hop"),
                    metric=route.get("metric", 0),
                    protocol=route.get("protocol", "unknown"),
                    admin_distance=route.get("admin_distance", 0),
                    interface=route.get("interface"),
                    vrf=route.get("vrf"),
                    synced_at=now,
                    created_at=now,
                )
                count += 1

            return {"device_id": device_id, "routes_synced": count, "vendor": vendor}
        finally:
            await device.disconnect()

    async def sync_ospf(self, device_id: str) -> dict:
        """Pull OSPF neighbors and config from live device."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise RoutingError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        cm = self._get_config_manager()
        _record, device = await cm._get_device_and_connect(device_id)

        try:
            ospf_data = await self._fetch_vendor_ospf(device, vendor)
            now = datetime.now(timezone.utc).isoformat()

            # Upsert OSPF config
            config = await self._db.get_ospf_config(device_id)
            config_id = config["id"] if config else str(uuid4())

            await self._db.add_ospf_config(
                id=config_id,
                device_id=device_id,
                process_id=ospf_data.get("process_id", 1),
                router_id=ospf_data.get("router_id"),
                status="active" if ospf_data.get("neighbors") else "configured",
                created_at=now,
                synced_at=now,
            )

            # Clear and re-add neighbors
            await self._db.clear_ospf_neighbors(config_id)
            for nbr in ospf_data.get("neighbors", []):
                await self._db.add_ospf_neighbor(
                    id=str(uuid4()),
                    ospf_config_id=config_id,
                    neighbor_ip=nbr["neighbor_ip"],
                    state=nbr.get("state", "UNKNOWN"),
                    neighbor_id=nbr.get("neighbor_id"),
                    interface=nbr.get("interface"),
                    area=nbr.get("area", "0"),
                    priority=nbr.get("priority", 1),
                    dead_time=nbr.get("dead_time"),
                    created_at=now,
                )

            return {
                "device_id": device_id,
                "neighbors_synced": len(ospf_data.get("neighbors", [])),
                "vendor": vendor,
            }
        finally:
            await device.disconnect()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create_static_route(
        self,
        device_id: str,
        destination: str,
        prefix_length: int,
        next_hop: str,
        metric: int = 0,
        vrf: str | None = None,
    ) -> dict:
        """Create a static route on a device."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise RoutingError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_static_route_commands(
            vendor, destination, prefix_length, next_hop, metric, vrf
        )

        if not commands:
            raise RoutingError(f"No commands generated for vendor '{vendor}'")

        cm = self._get_config_manager()
        await cm.deploy_config(device_id=device_id, commands=commands)

        now = datetime.now(timezone.utc).isoformat()
        await self._db.add_route(
            id=str(uuid4()),
            device_id=device_id,
            destination=destination,
            prefix_length=prefix_length,
            next_hop=next_hop,
            metric=metric,
            protocol="static",
            admin_distance=1,
            vrf=vrf,
            created_at=now,
        )

        return {
            "device_id": device_id,
            "destination": f"{destination}/{prefix_length}",
            "next_hop": next_hop,
            "status": "created",
            "commands_sent": commands,
        }

    async def delete_static_route(
        self,
        device_id: str,
        destination: str,
        prefix_length: int,
        next_hop: str,
    ) -> dict:
        """Delete a static route from a device."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise RoutingError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_delete_static_route_commands(
            vendor, destination, prefix_length, next_hop
        )

        if commands:
            cm = self._get_config_manager()
            await cm.deploy_config(device_id=device_id, commands=commands)

        await self._db.delete_route(device_id, destination, prefix_length, next_hop)
        return {
            "device_id": device_id,
            "destination": f"{destination}/{prefix_length}",
            "status": "deleted",
        }

    async def configure_ospf(
        self,
        device_id: str,
        process_id: int = 1,
        router_id: str | None = None,
        networks: list[dict] | None = None,
    ) -> dict:
        """Configure OSPF on a device. networks: [{"network": "10.0.0.0", "wildcard": "0.0.0.255", "area": "0"}]"""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise RoutingError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_ospf_config_commands(
            vendor, process_id, router_id, networks or []
        )

        if not commands:
            raise RoutingError(f"No OSPF commands generated for vendor '{vendor}'")

        cm = self._get_config_manager()
        await cm.deploy_config(device_id=device_id, commands=commands)

        now = datetime.now(timezone.utc).isoformat()
        config_id = str(uuid4())
        await self._db.add_ospf_config(
            id=config_id,
            device_id=device_id,
            process_id=process_id,
            router_id=router_id,
            status="configured",
            created_at=now,
        )

        for net in (networks or []):
            await self._db.add_ospf_area(
                id=str(uuid4()),
                ospf_config_id=config_id,
                area_id=net.get("area", "0"),
                networks=json.dumps([net.get("network", "")]),
                created_at=now,
            )

        return {
            "device_id": device_id,
            "process_id": process_id,
            "router_id": router_id,
            "networks_configured": len(networks or []),
            "status": "configured",
            "commands_sent": commands,
        }

    async def add_ospf_network(
        self,
        device_id: str,
        network: str,
        wildcard: str,
        area: str = "0",
    ) -> dict:
        """Add a network to OSPF advertisement."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise RoutingError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_add_ospf_network_commands(
            vendor, network, wildcard, area
        )

        if not commands:
            raise RoutingError(f"No commands generated for vendor '{vendor}'")

        cm = self._get_config_manager()
        await cm.deploy_config(device_id=device_id, commands=commands)

        return {
            "device_id": device_id,
            "network": network,
            "wildcard": wildcard,
            "area": area,
            "status": "added",
            "commands_sent": commands,
        }

    # ------------------------------------------------------------------
    # Vendor: fetch routes
    # ------------------------------------------------------------------

    async def _fetch_vendor_routes(self, device, vendor: str) -> list[dict]:
        """Parse routing table per vendor."""
        if vendor == "cisco_ios":
            output = await device.send_command("show ip route")
            return self._parse_cisco_routes(output)
        elif vendor == "juniper":
            output = await device.send_command("show route")
            return self._parse_juniper_routes(output)
        elif vendor == "fortinet":
            output = await device.send_command("get router info routing-table all")
            return self._parse_fortinet_routes(output)
        elif vendor == "paloalto":
            output = await device.send_command("show routing route")
            return self._parse_paloalto_routes(output)
        elif vendor == "mikrotik":
            output = await device.send_command("/ip route print detail")
            return self._parse_mikrotik_routes(output)
        elif vendor == "aruba":
            output = await device.send_command("show ip route")
            return self._parse_cisco_routes(output)  # Similar format
        elif vendor == "pfsense":
            output = await device.send_command("netstat -rn")
            return self._parse_pfsense_routes(output)
        elif vendor == "sophos":
            output = await device.send_command("ip route show")
            return self._parse_sophos_routes(output)
        else:
            raise RoutingError(f"Unsupported vendor for route fetch: {vendor}")

    async def _fetch_vendor_ospf(self, device, vendor: str) -> dict:
        """Fetch OSPF neighbor data per vendor."""
        if vendor == "cisco_ios":
            output = await device.send_command("show ip ospf neighbor")
            neighbors = self._parse_cisco_ospf_neighbors(output)
            return {"neighbors": neighbors}
        elif vendor == "juniper":
            output = await device.send_command("show ospf neighbor")
            neighbors = self._parse_juniper_ospf_neighbors(output)
            return {"neighbors": neighbors}
        elif vendor == "fortinet":
            output = await device.send_command("get router info ospf neighbor")
            neighbors = self._parse_fortinet_ospf_neighbors(output)
            return {"neighbors": neighbors}
        elif vendor == "paloalto":
            output = await device.send_command("show routing protocol ospf neighbor")
            return {"neighbors": self._parse_paloalto_ospf_neighbors(output)}
        elif vendor == "mikrotik":
            output = await device.send_command("/routing ospf neighbor print detail")
            return {"neighbors": self._parse_mikrotik_ospf_neighbors(output)}
        elif vendor == "aruba":
            output = await device.send_command("show ip ospf neighbor")
            return {"neighbors": self._parse_cisco_ospf_neighbors(output)}
        else:
            return {"neighbors": []}

    # ------------------------------------------------------------------
    # Route parsers
    # ------------------------------------------------------------------

    def _parse_cisco_routes(self, output: str) -> list[dict]:
        """Parse 'show ip route' output."""
        routes = []
        proto_map = {
            "C": "connected", "S": "static", "O": "ospf",
            "B": "bgp", "R": "rip", "D": "eigrp", "L": "local",
        }
        for line in output.splitlines():
            m = re.match(
                r"^([CSOBRDL\*]+)\s+(\d+\.\d+\.\d+\.\d+)/(\d+)"
                r"(?:\s+\[(\d+)/(\d+)\])?"
                r"(?:\s+via\s+(\d+\.\d+\.\d+\.\d+))?"
                r"(?:.*?,\s+(\S+))?",
                line.strip()
            )
            if m:
                proto_char = m.group(1).strip("* ")[:1]
                routes.append({
                    "destination": m.group(2),
                    "prefix_length": int(m.group(3)),
                    "admin_distance": int(m.group(4)) if m.group(4) else 0,
                    "metric": int(m.group(5)) if m.group(5) else 0,
                    "next_hop": m.group(6) or "",
                    "interface": m.group(7) or "",
                    "protocol": proto_map.get(proto_char, "unknown"),
                })
        return routes

    def _parse_juniper_routes(self, output: str) -> list[dict]:
        """Parse 'show route' output."""
        routes = []
        current_dest = None
        current_pl = None
        for line in output.splitlines():
            dest_m = re.match(r"^(\d+\.\d+\.\d+\.\d+)/(\d+)\s+", line)
            if dest_m:
                current_dest = dest_m.group(1)
                current_pl = int(dest_m.group(2))
            nh_m = re.search(r">\s+to\s+(\S+)\s+via\s+(\S+)", line)
            if nh_m and current_dest:
                proto = "static"
                if "OSPF" in line:
                    proto = "ospf"
                elif "Direct" in line or "Local" in line:
                    proto = "connected"
                elif "BGP" in line:
                    proto = "bgp"
                routes.append({
                    "destination": current_dest,
                    "prefix_length": current_pl,
                    "next_hop": nh_m.group(1),
                    "interface": nh_m.group(2),
                    "protocol": proto,
                })
        return routes

    def _parse_fortinet_routes(self, output: str) -> list[dict]:
        """Parse FortiOS routing table output."""
        routes = []
        for line in output.splitlines():
            m = re.match(
                r"^([CSOBK\*]+)\s+(\d+\.\d+\.\d+\.\d+)/(\d+)"
                r".*?via\s+(\d+\.\d+\.\d+\.\d+)",
                line.strip()
            )
            if m:
                proto_char = m.group(1).strip("* ")[:1]
                proto_map = {"C": "connected", "S": "static", "O": "ospf", "B": "bgp", "K": "kernel"}
                routes.append({
                    "destination": m.group(2),
                    "prefix_length": int(m.group(3)),
                    "next_hop": m.group(4),
                    "protocol": proto_map.get(proto_char, "unknown"),
                })
        return routes

    def _parse_paloalto_routes(self, output: str) -> list[dict]:
        """Parse PAN-OS routing table output."""
        routes = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 5 and "/" in parts[0]:
                try:
                    dest, pl = parts[0].split("/")
                    routes.append({
                        "destination": dest,
                        "prefix_length": int(pl),
                        "next_hop": parts[1] if parts[1] != "0.0.0.0" else "",
                        "interface": parts[2] if len(parts) > 2 else "",
                        "protocol": parts[-1].lower() if parts[-1] in ("static", "connect", "ospf", "bgp") else "unknown",
                    })
                except (ValueError, IndexError):
                    pass
        return routes

    def _parse_mikrotik_routes(self, output: str) -> list[dict]:
        """Parse '/ip route print detail' output."""
        routes = []
        for line in output.splitlines():
            dst_m = re.search(r"dst-address=(\S+)", line)
            gw_m = re.search(r"gateway=(\S+)", line)
            if dst_m:
                try:
                    dest, pl = dst_m.group(1).split("/")
                    proto = "static"
                    if "ospf" in line.lower():
                        proto = "ospf"
                    elif "connect" in line.lower():
                        proto = "connected"
                    routes.append({
                        "destination": dest,
                        "prefix_length": int(pl),
                        "next_hop": gw_m.group(1) if gw_m else "",
                        "protocol": proto,
                    })
                except (ValueError, IndexError):
                    pass
        return routes

    def _parse_pfsense_routes(self, output: str) -> list[dict]:
        """Parse 'netstat -rn' output."""
        routes = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0] not in ("Destination", "Routing"):
                dest = parts[0]
                gw = parts[1]
                iface = parts[-1] if len(parts) > 3 else ""
                if "/" in dest:
                    d, pl = dest.split("/")
                else:
                    d = dest
                    pl = 32 if dest != "default" else 0
                    if dest == "default":
                        d = "0.0.0.0"
                routes.append({
                    "destination": d,
                    "prefix_length": int(pl),
                    "next_hop": gw if gw != "link#" else "",
                    "interface": iface,
                    "protocol": "connected" if "link" in gw else "static",
                })
        return routes

    def _parse_sophos_routes(self, output: str) -> list[dict]:
        """Parse Linux 'ip route show' output."""
        routes = []
        for line in output.splitlines():
            m = re.match(r"^(\S+)\s+via\s+(\S+)\s+dev\s+(\S+)", line)
            if m:
                dest = m.group(1)
                if "/" in dest:
                    d, pl = dest.split("/")
                elif dest == "default":
                    d, pl = "0.0.0.0", 0
                else:
                    d, pl = dest, 32
                routes.append({
                    "destination": d,
                    "prefix_length": int(pl),
                    "next_hop": m.group(2),
                    "interface": m.group(3),
                    "protocol": "static",
                })
            else:
                m2 = re.match(r"^(\S+)\s+dev\s+(\S+)", line)
                if m2:
                    dest = m2.group(1)
                    if "/" in dest:
                        d, pl = dest.split("/")
                    else:
                        d, pl = dest, 32
                    routes.append({
                        "destination": d,
                        "prefix_length": int(pl),
                        "next_hop": "",
                        "interface": m2.group(2),
                        "protocol": "connected",
                    })
        return routes

    # ------------------------------------------------------------------
    # OSPF neighbor parsers
    # ------------------------------------------------------------------

    def _parse_cisco_ospf_neighbors(self, output: str) -> list[dict]:
        """Parse 'show ip ospf neighbor' output."""
        neighbors = []
        for line in output.splitlines():
            m = re.match(
                r"^(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)",
                line.strip()
            )
            if m:
                neighbors.append({
                    "neighbor_id": m.group(1),
                    "priority": int(m.group(2)),
                    "state": m.group(3).split("/")[0],
                    "dead_time": m.group(4),
                    "neighbor_ip": m.group(5),
                    "interface": m.group(6),
                })
        return neighbors

    def _parse_juniper_ospf_neighbors(self, output: str) -> list[dict]:
        """Parse 'show ospf neighbor' output."""
        neighbors = []
        for line in output.splitlines():
            m = re.match(r"^(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\S+)", line.strip())
            if m:
                neighbors.append({
                    "neighbor_ip": m.group(1),
                    "interface": m.group(2),
                    "state": m.group(3),
                    "priority": int(m.group(4)),
                    "neighbor_id": m.group(1),
                })
        return neighbors

    def _parse_fortinet_ospf_neighbors(self, output: str) -> list[dict]:
        """Parse FortiOS OSPF neighbor output."""
        neighbors = []
        for line in output.splitlines():
            m = re.match(
                r"^\s*(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)",
                line
            )
            if m:
                neighbors.append({
                    "neighbor_id": m.group(1),
                    "priority": int(m.group(2)),
                    "state": m.group(3),
                    "dead_time": m.group(4),
                    "neighbor_ip": m.group(5),
                    "interface": m.group(6),
                })
        return neighbors

    def _parse_paloalto_ospf_neighbors(self, output: str) -> list[dict]:
        """Parse PAN-OS OSPF neighbor output."""
        neighbors = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                try:
                    ip_parts = parts[0].split(".")
                    if len(ip_parts) == 4:
                        neighbors.append({
                            "neighbor_ip": parts[0],
                            "neighbor_id": parts[0],
                            "state": parts[1] if len(parts) > 1 else "UNKNOWN",
                            "interface": parts[2] if len(parts) > 2 else "",
                        })
                except (ValueError, IndexError):
                    pass
        return neighbors

    def _parse_mikrotik_ospf_neighbors(self, output: str) -> list[dict]:
        """Parse MikroTik OSPF neighbor output."""
        neighbors = []
        for line in output.splitlines():
            addr_m = re.search(r"address=(\S+)", line)
            rid_m = re.search(r"router-id=(\S+)", line)
            state_m = re.search(r"state=\"([^\"]+)\"", line)
            iface_m = re.search(r"interface=(\S+)", line)
            if addr_m:
                neighbors.append({
                    "neighbor_ip": addr_m.group(1),
                    "neighbor_id": rid_m.group(1) if rid_m else "",
                    "state": state_m.group(1) if state_m else "UNKNOWN",
                    "interface": iface_m.group(1) if iface_m else "",
                })
        return neighbors

    # ------------------------------------------------------------------
    # Command generators
    # ------------------------------------------------------------------

    def _generate_static_route_commands(
        self, vendor: str, dest: str, pl: int, nh: str, metric: int, vrf: str | None
    ) -> list[str]:
        """Generate commands to create a static route."""
        mask = self._prefix_to_mask(pl)

        if vendor == "cisco_ios":
            cmd = f"ip route {dest} {mask} {nh}"
            if metric:
                cmd += f" {metric}"
            if vrf:
                cmd = f"ip route vrf {vrf} {dest} {mask} {nh}"
            return [cmd]

        elif vendor == "juniper":
            cmd = f"set routing-options static route {dest}/{pl} next-hop {nh}"
            if metric:
                cmd += f" metric {metric}"
            return [cmd]

        elif vendor == "fortinet":
            cmds = [
                "config router static",
                "edit 0",
                f"set dst {dest} {mask}",
                f"set gateway {nh}",
            ]
            if metric:
                cmds.append(f"set distance {metric}")
            cmds += ["next", "end"]
            return cmds

        elif vendor == "paloalto":
            name = f"static-{dest}-{pl}"
            return [
                f"set network virtual-router default routing-table ip "
                f"static-route {name} destination {dest}/{pl} nexthop ip-address {nh}"
            ]

        elif vendor == "mikrotik":
            cmd = f"/ip route add dst-address={dest}/{pl} gateway={nh}"
            if metric:
                cmd += f" distance={metric}"
            return [cmd]

        elif vendor == "aruba":
            cmd = f"ip route {dest} {mask} {nh}"
            if metric:
                cmd += f" {metric}"
            return [cmd]

        elif vendor == "pfsense":
            return [f"route add -net {dest}/{pl} {nh}"]

        elif vendor == "sophos":
            return [f"ip route add {dest}/{pl} via {nh}"]

        return []

    def _generate_delete_static_route_commands(
        self, vendor: str, dest: str, pl: int, nh: str
    ) -> list[str]:
        """Generate commands to delete a static route."""
        mask = self._prefix_to_mask(pl)

        if vendor == "cisco_ios":
            return [f"no ip route {dest} {mask} {nh}"]
        elif vendor == "juniper":
            return [f"delete routing-options static route {dest}/{pl}"]
        elif vendor == "fortinet":
            return [
                "config router static",
                f'delete [find dst="{dest} {mask}" gateway="{nh}"]',
                "end",
            ]
        elif vendor == "paloalto":
            name = f"static-{dest}-{pl}"
            return [f"delete network virtual-router default routing-table ip static-route {name}"]
        elif vendor == "mikrotik":
            return [f"/ip route remove [find dst-address={dest}/{pl} gateway={nh}]"]
        elif vendor == "aruba":
            return [f"no ip route {dest} {mask} {nh}"]
        elif vendor == "pfsense":
            return [f"route delete -net {dest}/{pl} {nh}"]
        elif vendor == "sophos":
            return [f"ip route del {dest}/{pl} via {nh}"]
        return []

    def _generate_ospf_config_commands(
        self, vendor: str, process_id: int, router_id: str | None, networks: list[dict]
    ) -> list[str]:
        """Generate commands to configure OSPF."""
        if vendor == "cisco_ios":
            cmds = [f"router ospf {process_id}"]
            if router_id:
                cmds.append(f"router-id {router_id}")
            for net in networks:
                cmds.append(
                    f"network {net['network']} {net.get('wildcard', '0.0.0.255')} "
                    f"area {net.get('area', '0')}"
                )
            cmds.append("exit")
            return cmds

        elif vendor == "juniper":
            cmds = []
            for net in networks:
                area = net.get("area", "0")
                iface = net.get("interface", "")
                if iface:
                    cmds.append(f"set protocols ospf area {area} interface {iface}")
            if router_id:
                cmds.append(f"set routing-options router-id {router_id}")
            return cmds

        elif vendor == "fortinet":
            cmds = ["config router ospf"]
            if router_id:
                cmds.append(f"set router-id {router_id}")
            cmds.append("config area")
            areas_seen = set()
            for net in networks:
                area = net.get("area", "0.0.0.0")
                if area not in areas_seen:
                    cmds.extend([f'edit {area}', "next"])
                    areas_seen.add(area)
            cmds.append("end")
            cmds.append("config network")
            for i, net in enumerate(networks, 1):
                cmds.extend([
                    f"edit {i}",
                    f"set prefix {net['network']} {net.get('wildcard', '0.0.0.255')}",
                    f"set area {net.get('area', '0.0.0.0')}",
                    "next",
                ])
            cmds.extend(["end", "end"])
            return cmds

        elif vendor == "paloalto":
            cmds = ["set network virtual-router default protocol ospf enable yes"]
            if router_id:
                cmds.append(f"set network virtual-router default protocol ospf router-id {router_id}")
            for net in networks:
                area = net.get("area", "0")
                iface = net.get("interface", "")
                if iface:
                    cmds.append(
                        f"set network virtual-router default protocol ospf area {area} interface {iface} enable yes"
                    )
            return cmds

        elif vendor == "mikrotik":
            cmds = [f"/routing ospf instance add name=default router-id={router_id or '0.0.0.0'}"]
            areas_seen = set()
            for net in networks:
                area = net.get("area", "0.0.0.0")
                if area not in areas_seen:
                    cmds.append(f"/routing ospf area add name=area{area} area-id={area} instance=default")
                    areas_seen.add(area)
                iface = net.get("interface", "")
                if iface:
                    cmds.append(f"/routing ospf interface add interface={iface} area=area{area}")
            return cmds

        elif vendor == "aruba":
            cmds = [f"router ospf {process_id}"]
            if router_id:
                cmds.append(f"router-id {router_id}")
            for net in networks:
                cmds.append(
                    f"network {net['network']} {net.get('area', '0')}"
                )
            cmds.append("exit")
            return cmds

        return []

    def _generate_add_ospf_network_commands(
        self, vendor: str, network: str, wildcard: str, area: str
    ) -> list[str]:
        """Generate commands to add a network to OSPF."""
        if vendor == "cisco_ios":
            return [f"router ospf 1", f"network {network} {wildcard} area {area}", "exit"]
        elif vendor == "juniper":
            return [f"set protocols ospf area {area} interface {network}"]
        elif vendor == "fortinet":
            return [
                "config router ospf",
                "config network",
                "edit 0",
                f"set prefix {network} {wildcard}",
                f"set area {area}",
                "next", "end", "end",
            ]
        elif vendor == "mikrotik":
            return [f"/routing ospf interface add interface={network} area=area{area}"]
        elif vendor == "aruba":
            return [f"router ospf 1", f"network {network} area {area}", "exit"]
        return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _prefix_to_mask(prefix_length: int) -> str:
        """Convert prefix length to dotted-decimal subnet mask."""
        bits = (0xFFFFFFFF << (32 - prefix_length)) & 0xFFFFFFFF
        return f"{(bits >> 24) & 0xFF}.{(bits >> 16) & 0xFF}.{(bits >> 8) & 0xFF}.{bits & 0xFF}"
