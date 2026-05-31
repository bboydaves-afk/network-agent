"""VLAN management engine.

Provides vendor-abstracted VLAN CRUD, interface assignment,
and sync from live devices for all supported vendors:
Cisco IOS/IOS-XE, Juniper JunOS, Fortinet, Palo Alto,
MikroTik, Aruba, pfSense, and Sophos.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


class VlanError(Exception):
    """Raised when a VLAN operation fails."""


class VlanManager:
    """Manage VLANs and interface assignments across vendors."""

    # All switch/router vendors that support VLANs
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
            raise VlanError(
                "VlanManager requires a ConfigManager instance. "
                "Pass config_manager= to the constructor."
            )
        return self._config_mgr

    def _get_vendor(self, device_info: dict) -> str:
        dtype = device_info.get("device_type", "").lower()
        vendor = self.VENDOR_MAP.get(dtype)
        if not vendor:
            raise VlanError(
                f"Unsupported vendor for VLAN management: {dtype!r}. "
                f"Supported: {list(self.VENDOR_MAP.keys())}"
            )
        return vendor

    # ------------------------------------------------------------------
    # Read operations (from DB)
    # ------------------------------------------------------------------

    async def list_vlans(self, device_id: str) -> list[dict]:
        """List all VLANs for a device from DB."""
        return await self._db.get_vlans_by_device(device_id)

    async def get_vlan(self, device_id: str, vlan_id: int) -> dict | None:
        """Get a specific VLAN by device and VLAN number."""
        return await self._db.get_vlan_by_number(device_id, vlan_id)

    async def get_vlan_interfaces(self, device_id: str, vlan_id: int | None = None) -> list[dict]:
        """Get interface assignments, optionally filtered by VLAN."""
        return await self._db.get_vlan_interfaces(device_id, vlan_id)

    async def get_vlan_summary(self, device_id: str) -> dict:
        """Return a summary of VLANs and interface assignments for a device."""
        vlans = await self._db.get_vlans_by_device(device_id)
        interfaces = await self._db.get_vlan_interfaces(device_id)

        vlan_map = {}
        for v in vlans:
            vlan_map[v["vlan_id"]] = {
                "vlan_id": v["vlan_id"],
                "name": v.get("name", ""),
                "status": v.get("status", "active"),
                "description": v.get("description", ""),
                "interfaces": [],
            }

        for iface in interfaces:
            vid = iface["vlan_id"]
            if vid in vlan_map:
                vlan_map[vid]["interfaces"].append({
                    "interface": iface["interface_name"],
                    "mode": iface["mode"],
                })

        return {
            "device_id": device_id,
            "vlan_count": len(vlans),
            "vlans": list(vlan_map.values()),
        }

    # ------------------------------------------------------------------
    # Sync from live device
    # ------------------------------------------------------------------

    async def sync_vlans(self, device_id: str) -> dict:
        """Pull VLANs from a live device and store them in DB.

        Connects to the device, fetches VLANs via vendor-specific
        commands, clears old DB records, and inserts fresh ones.
        """
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise VlanError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        cm = self._get_config_manager()
        _record, device = await cm._get_device_and_connect(device_id)

        try:
            raw_vlans = await self._fetch_vendor_vlans(device, vendor)
            await self._db.clear_vlans_for_device(device_id)

            now = datetime.now(timezone.utc).isoformat()
            count = 0
            for vlan_data in raw_vlans:
                await self._db.add_vlan(
                    id=str(uuid4()),
                    device_id=device_id,
                    vlan_id=vlan_data["vlan_id"],
                    name=vlan_data.get("name"),
                    status=vlan_data.get("status", "active"),
                    description=vlan_data.get("description"),
                    synced_at=now,
                    created_at=now,
                )
                count += 1

            return {"device_id": device_id, "vlans_synced": count, "vendor": vendor}
        finally:
            await device.disconnect()

    # ------------------------------------------------------------------
    # Write operations (deploy to device + DB)
    # ------------------------------------------------------------------

    async def create_vlan(
        self,
        device_id: str,
        vlan_id: int,
        name: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Create a VLAN on a device and store in DB."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise VlanError(f"Device {device_id} not found")

        # Check if VLAN already exists
        existing = await self._db.get_vlan_by_number(device_id, vlan_id)
        if existing:
            raise VlanError(f"VLAN {vlan_id} already exists on device {device_id}")

        vendor = self._get_vendor(device_info)
        commands = self._generate_create_vlan_commands(vendor, vlan_id, name, description)

        if not commands:
            raise VlanError(f"No commands generated for vendor '{vendor}'")

        cm = self._get_config_manager()
        await cm.deploy_config(device_id=device_id, commands=commands)

        now = datetime.now(timezone.utc).isoformat()
        db_id = str(uuid4())
        await self._db.add_vlan(
            id=db_id,
            device_id=device_id,
            vlan_id=vlan_id,
            name=name,
            description=description,
            created_at=now,
        )

        return {
            "vlan_id": vlan_id,
            "device_id": device_id,
            "name": name,
            "status": "created",
            "commands_sent": commands,
        }

    async def delete_vlan(self, device_id: str, vlan_id: int) -> dict:
        """Delete a VLAN from a device and DB."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise VlanError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_delete_vlan_commands(vendor, vlan_id)

        if commands:
            cm = self._get_config_manager()
            await cm.deploy_config(device_id=device_id, commands=commands)

        await self._db.delete_vlan(device_id, vlan_id)
        return {"vlan_id": vlan_id, "device_id": device_id, "status": "deleted"}

    async def assign_interface(
        self,
        device_id: str,
        vlan_id: int,
        interface: str,
        mode: str = "access",
    ) -> dict:
        """Assign an interface to a VLAN on the device and in DB.

        mode: access, trunk, tagged, untagged
        """
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise VlanError(f"Device {device_id} not found")

        # Verify VLAN exists
        vlan = await self._db.get_vlan_by_number(device_id, vlan_id)
        if not vlan:
            raise VlanError(f"VLAN {vlan_id} not found on device {device_id}. Sync or create it first.")

        vendor = self._get_vendor(device_info)
        commands = self._generate_assign_interface_commands(vendor, vlan_id, interface, mode)

        if not commands:
            raise VlanError(f"No commands generated for vendor '{vendor}'")

        cm = self._get_config_manager()
        await cm.deploy_config(device_id=device_id, commands=commands)

        now = datetime.now(timezone.utc).isoformat()
        await self._db.add_vlan_interface(
            id=str(uuid4()),
            device_id=device_id,
            vlan_id=vlan_id,
            interface_name=interface,
            mode=mode,
            created_at=now,
        )

        return {
            "vlan_id": vlan_id,
            "interface": interface,
            "mode": mode,
            "device_id": device_id,
            "status": "assigned",
            "commands_sent": commands,
        }

    # ------------------------------------------------------------------
    # Vendor: fetch VLANs from device
    # ------------------------------------------------------------------

    async def _fetch_vendor_vlans(self, device, vendor: str) -> list[dict]:
        """Connect to device and parse VLAN list per vendor."""
        if vendor == "cisco_ios":
            output = await device.send_command("show vlan brief")
            return self._parse_cisco_vlans(output)
        elif vendor == "juniper":
            output = await device.send_command("show vlans")
            return self._parse_juniper_vlans(output)
        elif vendor == "fortinet":
            output = await device.send_command("get system interface")
            return self._parse_fortinet_vlans(output)
        elif vendor == "paloalto":
            output = await device.send_command("show network vlan all")
            return self._parse_paloalto_vlans(output)
        elif vendor == "mikrotik":
            output = await device.send_command("/interface vlan print detail")
            return self._parse_mikrotik_vlans(output)
        elif vendor == "aruba":
            output = await device.send_command("show vlan")
            return self._parse_aruba_vlans(output)
        elif vendor == "pfsense":
            output = await device.send_command("ifconfig -a")
            return self._parse_pfsense_vlans(output)
        elif vendor == "sophos":
            # Sophos uses REST API for VLAN interfaces
            output = await device.send_command(
                "GET /api/objects/network/interface_network/"
            )
            return self._parse_sophos_vlans(output)
        else:
            raise VlanError(f"Unsupported vendor for VLAN fetch: {vendor}")

    # ------------------------------------------------------------------
    # Vendor parsers
    # ------------------------------------------------------------------

    def _parse_cisco_vlans(self, output: str) -> list[dict]:
        """Parse 'show vlan brief' output.

        Example:
        VLAN Name                             Status    Ports
        ---- -------------------------------- --------- ---------------------
        1    default                          active    Gi0/1, Gi0/2
        10   MGMT                            active    Gi0/3
        """
        vlans = []
        for line in output.splitlines():
            m = re.match(r"^(\d+)\s+(\S+)\s+(active|act/unsup|suspend)\s*(.*)", line, re.IGNORECASE)
            if m:
                vlans.append({
                    "vlan_id": int(m.group(1)),
                    "name": m.group(2),
                    "status": "active" if "act" in m.group(3).lower() else m.group(3),
                })
        return vlans

    def _parse_juniper_vlans(self, output: str) -> list[dict]:
        """Parse 'show vlans' output.

        Example:
        Routing instance        VLAN name             Tag          Interfaces
        default-switch          MGMT                  10           ge-0/0/1.0
        default-switch          DATA                  20           ge-0/0/2.0
        """
        vlans = []
        for line in output.splitlines():
            m = re.match(r"^\S+\s+(\S+)\s+(\d+)\s*(.*)", line)
            if m:
                vlans.append({
                    "vlan_id": int(m.group(2)),
                    "name": m.group(1),
                    "status": "active",
                })
        return vlans

    def _parse_fortinet_vlans(self, output: str) -> list[dict]:
        """Parse Fortinet VLAN interfaces from 'get system interface'.
        Looks for interfaces with vlanid set."""
        vlans = []
        current_name = None
        current_vlan_id = None
        for line in output.splitlines():
            name_m = re.match(r"^== \[ (\S+) \]", line)
            if name_m:
                if current_name and current_vlan_id:
                    vlans.append({
                        "vlan_id": current_vlan_id,
                        "name": current_name,
                        "status": "active",
                    })
                current_name = name_m.group(1)
                current_vlan_id = None
            vlan_m = re.match(r"\s*vlanid\s*:\s*(\d+)", line)
            if vlan_m:
                vid = int(vlan_m.group(1))
                if vid > 0:
                    current_vlan_id = vid
        if current_name and current_vlan_id:
            vlans.append({
                "vlan_id": current_vlan_id,
                "name": current_name,
                "status": "active",
            })
        return vlans

    def _parse_paloalto_vlans(self, output: str) -> list[dict]:
        """Parse 'show network vlan all' output."""
        vlans = []
        for line in output.splitlines():
            m = re.match(r"^(\S+)\s+(\d+)\s*(.*)", line)
            if m:
                vlans.append({
                    "vlan_id": int(m.group(2)),
                    "name": m.group(1),
                    "status": "active",
                })
        return vlans

    def _parse_mikrotik_vlans(self, output: str) -> list[dict]:
        """Parse '/interface vlan print detail' output.

        Example:
         0   name="vlan10" ... vlan-id=10 interface=ether1 ...
        """
        vlans = []
        for line in output.splitlines():
            vid_m = re.search(r"vlan-id=(\d+)", line)
            name_m = re.search(r'name="([^"]+)"', line)
            if vid_m:
                vlans.append({
                    "vlan_id": int(vid_m.group(1)),
                    "name": name_m.group(1) if name_m else f"vlan{vid_m.group(1)}",
                    "status": "active",
                })
        return vlans

    def _parse_aruba_vlans(self, output: str) -> list[dict]:
        """Parse 'show vlan' output (similar to Cisco format)."""
        vlans = []
        for line in output.splitlines():
            m = re.match(r"^\s*(\d+)\s+(\S+)\s*", line)
            if m:
                vid = int(m.group(1))
                if vid > 0:
                    vlans.append({
                        "vlan_id": vid,
                        "name": m.group(2),
                        "status": "active",
                    })
        return vlans

    def _parse_pfsense_vlans(self, output: str) -> list[dict]:
        """Parse ifconfig output for VLAN sub-interfaces.
        Looks for interfaces like 'em0.10' (parent.vlan_id)."""
        vlans = []
        for line in output.splitlines():
            m = re.match(r"^(\S+)\.(\d+):", line)
            if m:
                vlans.append({
                    "vlan_id": int(m.group(2)),
                    "name": f"{m.group(1)}.{m.group(2)}",
                    "status": "active",
                })
        return vlans

    def _parse_sophos_vlans(self, output: str) -> list[dict]:
        """Parse Sophos REST API response for VLAN interfaces.
        REST response may be JSON array or text."""
        vlans = []
        try:
            import json
            data = json.loads(output) if isinstance(output, str) else output
            if isinstance(data, list):
                for item in data:
                    if item.get("type") == "vlan" or "vlan" in item.get("name", "").lower():
                        vid = item.get("vlan_tag") or item.get("vlanid") or 0
                        if vid:
                            vlans.append({
                                "vlan_id": int(vid),
                                "name": item.get("name", ""),
                                "status": "active",
                                "description": item.get("comment", ""),
                            })
        except (ValueError, TypeError, KeyError):
            logger.warning("Failed to parse Sophos VLAN response as JSON")
        return vlans

    # ------------------------------------------------------------------
    # Vendor: command generators
    # ------------------------------------------------------------------

    def _generate_create_vlan_commands(
        self, vendor: str, vlan_id: int, name: str | None, description: str | None
    ) -> list[str]:
        """Generate vendor-specific commands to create a VLAN."""
        vname = name or f"VLAN{vlan_id}"

        if vendor == "cisco_ios":
            cmds = [f"vlan {vlan_id}", f"name {vname}"]
            if description:
                cmds.append(f"description {description}")
            cmds.append("exit")
            return cmds

        elif vendor == "juniper":
            cmds = [
                f"set vlans {vname} vlan-id {vlan_id}",
            ]
            if description:
                cmds.append(f"set vlans {vname} description \"{description}\"")
            return cmds

        elif vendor == "fortinet":
            parent = "port1"  # default parent, user can adjust
            cmds = [
                "config system interface",
                f'edit "{vname}"',
                "set type vlan",
                f"set vlanid {vlan_id}",
                f'set interface "{parent}"',
            ]
            if description:
                cmds.append(f'set description "{description}"')
            cmds += ["next", "end"]
            return cmds

        elif vendor == "paloalto":
            cmds = [f"set network vlan {vname}"]
            return cmds

        elif vendor == "mikrotik":
            parent = "ether1"  # default parent
            cmds = [
                f'/interface vlan add name={vname} vlan-id={vlan_id} interface={parent}'
            ]
            if description:
                cmds.append(f'/interface vlan set [find name={vname}] comment="{description}"')
            return cmds

        elif vendor == "aruba":
            cmds = [f"vlan {vlan_id}", f"name {vname}"]
            if description:
                cmds.append(f"description {description}")
            cmds.append("exit")
            return cmds

        elif vendor == "pfsense":
            # pfSense: create VLAN via shell
            parent = "em0"  # default parent
            cmds = [f"ifconfig {parent} vlan {vlan_id} vlandev {parent}"]
            return cmds

        elif vendor == "sophos":
            import json
            payload = {
                "name": vname,
                "type": "vlan",
                "vlan_tag": vlan_id,
                "status": True,
            }
            if description:
                payload["comment"] = description
            return [f"POST /api/objects/network/interface_network/ {json.dumps(payload)}"]

        return []

    def _generate_delete_vlan_commands(self, vendor: str, vlan_id: int) -> list[str]:
        """Generate vendor-specific commands to delete a VLAN."""
        if vendor == "cisco_ios":
            return [f"no vlan {vlan_id}"]

        elif vendor == "juniper":
            # Need VLAN name for Juniper — use generic pattern
            return [f"delete vlans VLAN{vlan_id}"]

        elif vendor == "fortinet":
            return [
                "config system interface",
                f'delete "VLAN{vlan_id}"',
                "end",
            ]

        elif vendor == "paloalto":
            return [f"delete network vlan VLAN{vlan_id}"]

        elif vendor == "mikrotik":
            return [f'/interface vlan remove [find vlan-id={vlan_id}]']

        elif vendor == "aruba":
            return [f"no vlan {vlan_id}"]

        elif vendor == "pfsense":
            return [f"ifconfig vlan{vlan_id} destroy"]

        elif vendor == "sophos":
            return [f"DELETE /api/objects/network/interface_network/VLAN{vlan_id}"]

        return []

    def _generate_assign_interface_commands(
        self, vendor: str, vlan_id: int, interface: str, mode: str
    ) -> list[str]:
        """Generate vendor-specific commands to assign an interface to a VLAN."""
        if vendor == "cisco_ios":
            if mode == "trunk":
                return [
                    f"interface {interface}",
                    "switchport mode trunk",
                    f"switchport trunk allowed vlan add {vlan_id}",
                    "exit",
                ]
            else:  # access
                return [
                    f"interface {interface}",
                    "switchport mode access",
                    f"switchport access vlan {vlan_id}",
                    "exit",
                ]

        elif vendor == "juniper":
            vname = f"VLAN{vlan_id}"
            if mode == "trunk" or mode == "tagged":
                return [
                    f"set interfaces {interface} unit 0 family ethernet-switching "
                    f"port-mode trunk",
                    f"set interfaces {interface} unit 0 family ethernet-switching "
                    f"vlan members {vname}",
                ]
            else:  # access
                return [
                    f"set interfaces {interface} unit 0 family ethernet-switching "
                    f"port-mode access",
                    f"set interfaces {interface} unit 0 family ethernet-switching "
                    f"vlan members {vname}",
                ]

        elif vendor == "fortinet":
            # Fortinet VLANs are bound to parent interfaces at creation
            return [
                "config system interface",
                f'edit "VLAN{vlan_id}"',
                f'set interface "{interface}"',
                "next",
                "end",
            ]

        elif vendor == "paloalto":
            return [f"set network vlan VLAN{vlan_id} interface {interface}"]

        elif vendor == "mikrotik":
            if mode == "trunk" or mode == "tagged":
                return [
                    f"/interface bridge vlan add bridge=bridge tagged={interface} vlan-ids={vlan_id}"
                ]
            else:  # access / untagged
                return [
                    f"/interface bridge vlan add bridge=bridge untagged={interface} vlan-ids={vlan_id}",
                    f"/interface bridge port set [find interface={interface}] pvid={vlan_id}",
                ]

        elif vendor == "aruba":
            if mode == "trunk":
                return [
                    f"interface {interface}",
                    "switchport mode trunk",
                    f"switchport trunk allowed vlan add {vlan_id}",
                    "exit",
                ]
            else:  # access
                return [
                    f"interface {interface}",
                    "switchport mode access",
                    f"switchport access vlan {vlan_id}",
                    "exit",
                ]

        elif vendor == "pfsense":
            return [f"ifconfig {interface} vlan {vlan_id} vlandev {interface}"]

        elif vendor == "sophos":
            import json
            payload = {"interface": interface}
            return [
                f"PUT /api/objects/network/interface_network/VLAN{vlan_id} "
                f"{json.dumps(payload)}"
            ]

        return []
