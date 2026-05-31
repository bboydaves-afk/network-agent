"""Access Control List (ACL) management engine.

Provides vendor-abstracted ACL CRUD, entry management,
and interface binding for Cisco IOS/IOS-XE, Juniper JunOS,
MikroTik, and Aruba. For Fortinet, Palo Alto, pfSense, and
Sophos (which use firewall policies instead of traditional ACLs),
this module delegates to the FirewallManager.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


class ACLError(Exception):
    """Raised when an ACL operation fails."""


class ACLManager:
    """Manage access control lists across vendors."""

    # Vendors that support traditional ACLs
    ACL_VENDORS = {
        "cisco_ios": "cisco_ios",
        "cisco_iosxe": "cisco_ios",
        "juniper_junos": "juniper",
        "mikrotik": "mikrotik",
        "aruba": "aruba",
    }

    # Vendors that use firewall policies instead of ACLs
    POLICY_VENDORS = {"fortinet", "paloalto", "pfsense", "sophos"}

    def __init__(self, db, config_manager=None):
        self._db = db
        self._config_mgr = config_manager

    def _get_config_manager(self):
        if not self._config_mgr:
            raise ACLError("ACLManager requires a ConfigManager instance.")
        return self._config_mgr

    def _get_vendor(self, device_info: dict) -> str:
        dtype = device_info.get("device_type", "").lower()
        if dtype in self.POLICY_VENDORS:
            raise ACLError(
                f"Device type '{dtype}' uses firewall policies, not ACLs. "
                "Use the firewall management tools instead."
            )
        vendor = self.ACL_VENDORS.get(dtype)
        if not vendor:
            raise ACLError(
                f"Unsupported vendor for ACL management: {dtype!r}. "
                f"Supported: {list(self.ACL_VENDORS.keys())}"
            )
        return vendor

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def list_acls(self, device_id: str) -> list[dict]:
        """List all ACLs for a device, with entry counts."""
        acls = await self._db.get_access_lists_by_device(device_id)
        for acl in acls:
            entries = await self._db.get_acl_entries(acl["id"])
            acl["entry_count"] = len(entries)
            bindings = await self._db.get_acl_bindings(acl["id"])
            acl["bindings"] = [
                {"interface": b["interface"], "direction": b["direction"]}
                for b in bindings
            ]
        return acls

    async def get_acl_detail(self, device_id: str, acl_name: str) -> dict | None:
        """Get full ACL detail including entries and bindings."""
        acl = await self._db.get_access_list_by_name(device_id, acl_name)
        if not acl:
            return None
        acl["entries"] = await self._db.get_acl_entries(acl["id"])
        acl["bindings"] = await self._db.get_acl_bindings(acl["id"])
        return acl

    # ------------------------------------------------------------------
    # Sync from live device
    # ------------------------------------------------------------------

    async def sync_acls(self, device_id: str) -> dict:
        """Pull ACLs from a live device and store in DB."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise ACLError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        cm = self._get_config_manager()
        _record, device = await cm._get_device_and_connect(device_id)

        try:
            raw_acls = await self._fetch_vendor_acls(device, vendor)
            await self._db.clear_acls_for_device(device_id)

            now = datetime.now(timezone.utc).isoformat()
            count = 0
            for acl_data in raw_acls:
                acl_id = str(uuid4())
                await self._db.add_access_list(
                    id=acl_id,
                    device_id=device_id,
                    name=acl_data["name"],
                    acl_type=acl_data.get("acl_type", "extended"),
                    synced_at=now,
                    created_at=now,
                )
                for entry in acl_data.get("entries", []):
                    await self._db.add_acl_entry(
                        id=str(uuid4()),
                        acl_id=acl_id,
                        sequence=entry.get("sequence", 0),
                        action=entry.get("action", "deny"),
                        protocol=entry.get("protocol"),
                        source=entry.get("source"),
                        source_wildcard=entry.get("source_wildcard"),
                        destination=entry.get("destination"),
                        dest_wildcard=entry.get("dest_wildcard"),
                        dest_port=entry.get("dest_port"),
                        log_enabled=entry.get("log_enabled", False),
                        created_at=now,
                    )
                count += 1

            return {"device_id": device_id, "acls_synced": count, "vendor": vendor}
        finally:
            await device.disconnect()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create_acl(
        self,
        device_id: str,
        name: str,
        acl_type: str = "extended",
        description: str | None = None,
    ) -> dict:
        """Create an ACL on a device."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise ACLError(f"Device {device_id} not found")

        existing = await self._db.get_access_list_by_name(device_id, name)
        if existing:
            raise ACLError(f"ACL '{name}' already exists on device {device_id}")

        vendor = self._get_vendor(device_info)
        commands = self._generate_create_acl_commands(vendor, name, acl_type)

        if commands:
            cm = self._get_config_manager()
            await cm.deploy_config(device_id=device_id, commands=commands)

        now = datetime.now(timezone.utc).isoformat()
        acl_id = str(uuid4())
        await self._db.add_access_list(
            id=acl_id,
            device_id=device_id,
            name=name,
            acl_type=acl_type,
            description=description,
            created_at=now,
        )

        return {"acl_id": acl_id, "name": name, "status": "created", "commands_sent": commands}

    async def delete_acl(self, device_id: str, acl_name: str) -> dict:
        """Delete an ACL from a device."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise ACLError(f"Device {device_id} not found")

        acl = await self._db.get_access_list_by_name(device_id, acl_name)
        if not acl:
            raise ACLError(f"ACL '{acl_name}' not found on device {device_id}")

        vendor = self._get_vendor(device_info)
        commands = self._generate_delete_acl_commands(vendor, acl_name)

        if commands:
            cm = self._get_config_manager()
            await cm.deploy_config(device_id=device_id, commands=commands)

        await self._db.delete_access_list(acl["id"])
        return {"acl_name": acl_name, "status": "deleted"}

    async def add_entry(
        self,
        device_id: str,
        acl_name: str,
        sequence: int,
        action: str,
        protocol: str = "ip",
        source: str = "any",
        destination: str = "any",
        source_wildcard: str | None = None,
        dest_wildcard: str | None = None,
        dest_port: str | None = None,
        log_enabled: bool = False,
    ) -> dict:
        """Add an entry (ACE) to an ACL."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise ACLError(f"Device {device_id} not found")

        acl = await self._db.get_access_list_by_name(device_id, acl_name)
        if not acl:
            raise ACLError(f"ACL '{acl_name}' not found on device {device_id}")

        vendor = self._get_vendor(device_info)
        commands = self._generate_add_entry_commands(
            vendor, acl_name, sequence, action, protocol,
            source, destination, source_wildcard, dest_wildcard,
            dest_port, log_enabled
        )

        if commands:
            cm = self._get_config_manager()
            await cm.deploy_config(device_id=device_id, commands=commands)

        now = datetime.now(timezone.utc).isoformat()
        entry_id = str(uuid4())
        await self._db.add_acl_entry(
            id=entry_id,
            acl_id=acl["id"],
            sequence=sequence,
            action=action,
            protocol=protocol,
            source=source,
            source_wildcard=source_wildcard,
            destination=destination,
            dest_wildcard=dest_wildcard,
            dest_port=dest_port,
            log_enabled=log_enabled,
            created_at=now,
        )

        return {"entry_id": entry_id, "acl_name": acl_name, "sequence": sequence, "status": "added"}

    async def remove_entry(self, device_id: str, acl_name: str, sequence: int) -> dict:
        """Remove an entry from an ACL by sequence number."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise ACLError(f"Device {device_id} not found")

        acl = await self._db.get_access_list_by_name(device_id, acl_name)
        if not acl:
            raise ACLError(f"ACL '{acl_name}' not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_remove_entry_commands(vendor, acl_name, sequence)

        if commands:
            cm = self._get_config_manager()
            await cm.deploy_config(device_id=device_id, commands=commands)

        await self._db.delete_acl_entry_by_sequence(acl["id"], sequence)
        return {"acl_name": acl_name, "sequence": sequence, "status": "removed"}

    async def bind_acl(
        self, device_id: str, acl_name: str, interface: str, direction: str = "in"
    ) -> dict:
        """Apply an ACL to an interface."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise ACLError(f"Device {device_id} not found")

        acl = await self._db.get_access_list_by_name(device_id, acl_name)
        if not acl:
            raise ACLError(f"ACL '{acl_name}' not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_bind_commands(vendor, acl_name, interface, direction)

        if commands:
            cm = self._get_config_manager()
            await cm.deploy_config(device_id=device_id, commands=commands)

        now = datetime.now(timezone.utc).isoformat()
        await self._db.add_acl_binding(
            id=str(uuid4()),
            acl_id=acl["id"],
            device_id=device_id,
            interface=interface,
            direction=direction,
            created_at=now,
        )

        return {
            "acl_name": acl_name, "interface": interface,
            "direction": direction, "status": "bound",
        }

    async def unbind_acl(
        self, device_id: str, acl_name: str, interface: str, direction: str = "in"
    ) -> dict:
        """Remove an ACL from an interface."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise ACLError(f"Device {device_id} not found")

        acl = await self._db.get_access_list_by_name(device_id, acl_name)
        if not acl:
            raise ACLError(f"ACL '{acl_name}' not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_unbind_commands(vendor, acl_name, interface, direction)

        if commands:
            cm = self._get_config_manager()
            await cm.deploy_config(device_id=device_id, commands=commands)

        # Remove matching binding
        bindings = await self._db.get_acl_bindings(acl["id"])
        for b in bindings:
            if b["interface"] == interface and b["direction"] == direction:
                await self._db.delete_acl_binding(b["id"])
                break

        return {
            "acl_name": acl_name, "interface": interface,
            "direction": direction, "status": "unbound",
        }

    # ------------------------------------------------------------------
    # Vendor: fetch ACLs
    # ------------------------------------------------------------------

    async def _fetch_vendor_acls(self, device, vendor: str) -> list[dict]:
        """Parse ACLs per vendor."""
        if vendor == "cisco_ios":
            output = await device.send_command("show access-lists")
            return self._parse_cisco_acls(output)
        elif vendor == "juniper":
            output = await device.send_command("show firewall filter")
            return self._parse_juniper_acls(output)
        elif vendor == "mikrotik":
            output = await device.send_command("/ip firewall filter print detail")
            return self._parse_mikrotik_acls(output)
        elif vendor == "aruba":
            output = await device.send_command("show access-list")
            return self._parse_cisco_acls(output)  # Similar format
        else:
            raise ACLError(f"Unsupported vendor for ACL fetch: {vendor}")

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_cisco_acls(self, output: str) -> list[dict]:
        """Parse 'show access-lists' output."""
        acls = []
        current_acl = None
        for line in output.splitlines():
            acl_m = re.match(r"^(Extended|Standard)\s+IP\s+access\s+list\s+(\S+)", line, re.IGNORECASE)
            if acl_m:
                if current_acl:
                    acls.append(current_acl)
                current_acl = {
                    "name": acl_m.group(2),
                    "acl_type": acl_m.group(1).lower(),
                    "entries": [],
                }
                continue
            if current_acl:
                entry_m = re.match(
                    r"^\s+(\d+)\s+(permit|deny)\s+(\S+)\s+(.*)", line, re.IGNORECASE
                )
                if entry_m:
                    parts = entry_m.group(4).split()
                    source = parts[0] if parts else "any"
                    src_wc = parts[1] if len(parts) > 1 and re.match(r"\d+\.\d+", parts[1]) else None
                    dest_idx = 2 if src_wc else 1
                    dest = parts[dest_idx] if len(parts) > dest_idx else "any"
                    dest_wc = parts[dest_idx + 1] if len(parts) > dest_idx + 1 and re.match(r"\d+\.\d+", parts[dest_idx + 1]) else None

                    current_acl["entries"].append({
                        "sequence": int(entry_m.group(1)),
                        "action": entry_m.group(2).lower(),
                        "protocol": entry_m.group(3).lower(),
                        "source": source,
                        "source_wildcard": src_wc,
                        "destination": dest,
                        "dest_wildcard": dest_wc,
                        "log_enabled": "log" in line.lower(),
                    })
        if current_acl:
            acls.append(current_acl)
        return acls

    def _parse_juniper_acls(self, output: str) -> list[dict]:
        """Parse 'show firewall filter' output."""
        acls = []
        current_acl = None
        for line in output.splitlines():
            filter_m = re.match(r"^Filter:\s+(\S+)", line)
            if filter_m:
                if current_acl:
                    acls.append(current_acl)
                current_acl = {"name": filter_m.group(1), "acl_type": "extended", "entries": []}
            if current_acl and "then" in line.lower():
                action = "permit" if "accept" in line.lower() else "deny"
                current_acl["entries"].append({
                    "action": action,
                    "sequence": len(current_acl["entries"]) + 1,
                })
        if current_acl:
            acls.append(current_acl)
        return acls

    def _parse_mikrotik_acls(self, output: str) -> list[dict]:
        """Parse MikroTik firewall filter output."""
        acl = {"name": "filter", "acl_type": "extended", "entries": []}
        seq = 0
        for line in output.splitlines():
            chain_m = re.search(r"chain=(\S+)", line)
            action_m = re.search(r"action=(\S+)", line)
            src_m = re.search(r"src-address=(\S+)", line)
            dst_m = re.search(r"dst-address=(\S+)", line)
            proto_m = re.search(r"protocol=(\S+)", line)
            port_m = re.search(r"dst-port=(\S+)", line)
            if action_m:
                seq += 1
                acl["entries"].append({
                    "sequence": seq,
                    "action": "permit" if action_m.group(1) == "accept" else "deny",
                    "protocol": proto_m.group(1) if proto_m else "ip",
                    "source": src_m.group(1) if src_m else "any",
                    "destination": dst_m.group(1) if dst_m else "any",
                    "dest_port": port_m.group(1) if port_m else None,
                })
        if acl["entries"]:
            return [acl]
        return []

    # ------------------------------------------------------------------
    # Command generators
    # ------------------------------------------------------------------

    def _generate_create_acl_commands(self, vendor: str, name: str, acl_type: str) -> list[str]:
        if vendor == "cisco_ios":
            return [f"ip access-list {acl_type} {name}", "exit"]
        elif vendor == "juniper":
            return [f"set firewall filter {name}"]
        elif vendor == "mikrotik":
            return []  # MikroTik ACLs are created implicitly with entries
        elif vendor == "aruba":
            return [f"ip access-list {acl_type} {name}", "exit"]
        return []

    def _generate_delete_acl_commands(self, vendor: str, name: str) -> list[str]:
        if vendor == "cisco_ios":
            return [f"no ip access-list extended {name}"]
        elif vendor == "juniper":
            return [f"delete firewall filter {name}"]
        elif vendor == "mikrotik":
            return [f'/ip firewall filter remove [find comment="{name}"]']
        elif vendor == "aruba":
            return [f"no ip access-list extended {name}"]
        return []

    def _generate_add_entry_commands(
        self, vendor, name, seq, action, protocol, source, dest,
        src_wc, dst_wc, dst_port, log
    ):
        if vendor == "cisco_ios":
            cmd = f"ip access-list extended {name}\n"
            entry = f"{seq} {action} {protocol}"
            entry += f" {source}" + (f" {src_wc}" if src_wc else "")
            entry += f" {dest}" + (f" {dst_wc}" if dst_wc else "")
            if dst_port:
                entry += f" eq {dst_port}"
            if log:
                entry += " log"
            return [f"ip access-list extended {name}", entry, "exit"]

        elif vendor == "juniper":
            term = f"term-{seq}"
            cmds = []
            if source and source != "any":
                cmds.append(f"set firewall filter {name} term {term} from source-address {source}")
            if dest and dest != "any":
                cmds.append(f"set firewall filter {name} term {term} from destination-address {dest}")
            if protocol and protocol != "ip":
                cmds.append(f"set firewall filter {name} term {term} from protocol {protocol}")
            if dst_port:
                cmds.append(f"set firewall filter {name} term {term} from destination-port {dst_port}")
            jaction = "accept" if action == "permit" else "discard"
            cmds.append(f"set firewall filter {name} term {term} then {jaction}")
            if log:
                cmds.append(f"set firewall filter {name} term {term} then log")
            return cmds

        elif vendor == "mikrotik":
            cmd = f"/ip firewall filter add chain=forward action={'accept' if action == 'permit' else 'drop'}"
            if protocol and protocol != "ip":
                cmd += f" protocol={protocol}"
            if source and source != "any":
                cmd += f" src-address={source}"
            if dest and dest != "any":
                cmd += f" dst-address={dest}"
            if dst_port:
                cmd += f" dst-port={dst_port}"
            if log:
                cmd += " log=yes"
            cmd += f' comment="{name}"'
            return [cmd]

        elif vendor == "aruba":
            entry = f"{seq} {action} {protocol}"
            entry += f" {source}" + (f" {src_wc}" if src_wc else "")
            entry += f" {dest}" + (f" {dst_wc}" if dst_wc else "")
            if dst_port:
                entry += f" eq {dst_port}"
            if log:
                entry += " log"
            return [f"ip access-list extended {name}", entry, "exit"]

        return []

    def _generate_remove_entry_commands(self, vendor, name, seq):
        if vendor == "cisco_ios":
            return [f"ip access-list extended {name}", f"no {seq}", "exit"]
        elif vendor == "juniper":
            return [f"delete firewall filter {name} term term-{seq}"]
        elif vendor == "mikrotik":
            return [f'/ip firewall filter remove [find comment="{name}"]']
        elif vendor == "aruba":
            return [f"ip access-list extended {name}", f"no {seq}", "exit"]
        return []

    def _generate_bind_commands(self, vendor, name, interface, direction):
        if vendor == "cisco_ios":
            return [f"interface {interface}", f"ip access-group {name} {direction}", "exit"]
        elif vendor == "juniper":
            io = "input" if direction == "in" else "output"
            return [f"set interfaces {interface} unit 0 family inet filter {io} {name}"]
        elif vendor == "mikrotik":
            # MikroTik uses chains, not interface bindings
            return []
        elif vendor == "aruba":
            return [f"interface {interface}", f"ip access-group {name} {direction}", "exit"]
        return []

    def _generate_unbind_commands(self, vendor, name, interface, direction):
        if vendor == "cisco_ios":
            return [f"interface {interface}", f"no ip access-group {name} {direction}", "exit"]
        elif vendor == "juniper":
            io = "input" if direction == "in" else "output"
            return [f"delete interfaces {interface} unit 0 family inet filter {io} {name}"]
        elif vendor == "aruba":
            return [f"interface {interface}", f"no ip access-group {name} {direction}", "exit"]
        return []
