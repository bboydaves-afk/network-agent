"""Firewall policy and rule management engine.

Provides vendor-abstracted firewall rule CRUD, NAT management,
zone management, and address/service object management for
Fortinet, Palo Alto, pfSense, and Sophos UTM devices.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


class FirewallError(Exception):
    """Raised when a firewall operation fails."""


class FirewallManager:
    """Manage firewall rules, NAT, zones, and objects across vendors."""

    # Map device_type to vendor identifier
    VENDOR_MAP = {
        "fortinet": "fortinet",
        "paloalto": "paloalto",
        "pfsense": "pfsense",
        "sophos": "sophos",
    }

    def __init__(self, db, config_manager=None):
        self._db = db
        self._config_mgr = config_manager

    # ------------------------------------------------------------------
    # Rule Management
    # ------------------------------------------------------------------

    async def get_rules(self, device_id: str) -> list[dict]:
        """Get all firewall rules for a device from DB."""
        return await self._db.get_firewall_rules_by_device(device_id)

    async def sync_rules(self, device_id: str) -> dict:
        """Sync firewall rules from a live device to DB.

        Connects to the device, pulls current rules via vendor-specific
        method, normalizes them, clears old DB rules, inserts fresh ones.
        Returns summary dict with rule_count.
        """
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise FirewallError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)

        # Get device connection via config_manager
        cm = self._get_config_manager()
        _device_record, device = await cm._get_device_and_connect(device_id)

        try:
            raw_rules = await self._fetch_vendor_rules(device, vendor)

            await self._db.clear_firewall_rules_for_device(device_id)

            now = datetime.now(timezone.utc).isoformat()
            count = 0
            for rule_data in raw_rules:
                rule_data["id"] = str(uuid4())
                rule_data["device_id"] = device_id
                rule_data["synced_at"] = now
                rule_data["created_at"] = now
                await self._db.add_firewall_rule(rule_data)
                count += 1

            return {"device_id": device_id, "rules_synced": count, "vendor": vendor}
        finally:
            await device.disconnect()
    async def create_rule(self, device_id: str, rule_data: dict) -> dict:
        """Create a firewall rule on a device and store in DB.

        Generates vendor-specific commands, deploys them via
        config_manager.deploy_config(), then stores the rule in DB.
        """
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise FirewallError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_create_rule_commands(vendor, rule_data)

        if not commands:
            raise FirewallError(f"No commands generated for vendor '{vendor}'")

        cm = self._get_config_manager()
        deploy_result = await cm.deploy_config(
            device_id=device_id,
            commands=commands,
        )

        rule_data["id"] = str(uuid4())
        rule_data["device_id"] = device_id
        rule_data["created_at"] = datetime.now(timezone.utc).isoformat()

        for field in ("source_addresses", "dest_addresses", "services"):
            if field not in rule_data:
                rule_data[field] = []

        rule_id = await self._db.add_firewall_rule(rule_data)

        return {
            "rule_id": rule_id,
            "device_id": device_id,
            "deploy_id": deploy_result.get("id") if isinstance(deploy_result, dict) else None,
            "commands_sent": commands,
            "status": "created",
        }

    async def delete_rule(self, device_id: str, rule_id: str) -> dict:
        """Delete a firewall rule from device and DB."""
        rule = await self._db.get_firewall_rule(rule_id)
        if not rule:
            raise FirewallError(f"Rule {rule_id} not found")

        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise FirewallError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_delete_rule_commands(vendor, rule)

        if commands:
            cm = self._get_config_manager()
            await cm.deploy_config(device_id=device_id, commands=commands)

        await self._db.delete_firewall_rule(rule_id)
        return {"rule_id": rule_id, "status": "deleted"}

    async def modify_rule(self, device_id: str, rule_id: str, changes: dict) -> dict:
        """Modify an existing firewall rule on device and in DB."""
        rule = await self._db.get_firewall_rule(rule_id)
        if not rule:
            raise FirewallError(f"Rule {rule_id} not found")

        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise FirewallError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_modify_rule_commands(vendor, rule, changes)

        if commands:
            cm = self._get_config_manager()
            await cm.deploy_config(device_id=device_id, commands=commands)

        await self._db.update_firewall_rule(rule_id, changes)
        return {"rule_id": rule_id, "status": "modified", "changes": changes}

    # ------------------------------------------------------------------
    # NAT Management
    # ------------------------------------------------------------------

    async def get_nat_rules(self, device_id: str) -> list[dict]:
        """Get all NAT rules for a device from DB."""
        return await self._db.get_nat_rules_by_device(device_id)

    async def create_nat_rule(self, device_id: str, rule_data: dict) -> dict:
        """Create a NAT rule on a device and store in DB."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise FirewallError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_create_nat_commands(vendor, rule_data)

        if commands:
            cm = self._get_config_manager()
            await cm.deploy_config(device_id=device_id, commands=commands)

        rule_data["id"] = str(uuid4())
        rule_data["device_id"] = device_id
        rule_data["created_at"] = datetime.now(timezone.utc).isoformat()

        rule_id = await self._db.add_nat_rule(rule_data)
        return {"rule_id": rule_id, "status": "created", "commands_sent": commands}

    async def delete_nat_rule(self, device_id: str, rule_id: str) -> dict:
        """Delete a NAT rule from DB."""
        rule = await self._db.get_nat_rule(rule_id)
        if not rule:
            raise FirewallError(f"NAT rule {rule_id} not found")

        await self._db.delete_nat_rule(rule_id)
        return {"rule_id": rule_id, "status": "deleted"}
    # ------------------------------------------------------------------
    # Zone Management
    # ------------------------------------------------------------------

    async def get_zones(self, device_id: str) -> list[dict]:
        """Get all firewall zones for a device from DB."""
        return await self._db.get_firewall_zones_by_device(device_id)

    async def sync_zones(self, device_id: str) -> dict:
        """Sync firewall zones from live device to DB."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise FirewallError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)

        cm = self._get_config_manager()
        _device_record, device = await cm._get_device_and_connect(device_id)

        try:
            zones = await self._fetch_vendor_zones(device, vendor)
            await self._db.clear_firewall_zones_for_device(device_id)

            now = datetime.now(timezone.utc).isoformat()
            count = 0
            for zone_data in zones:
                zone_data["id"] = str(uuid4())
                zone_data["device_id"] = device_id
                zone_data["synced_at"] = now
                await self._db.add_firewall_zone(zone_data)
                count += 1

            return {"device_id": device_id, "zones_synced": count}
        finally:
            await device.disconnect()

    # ------------------------------------------------------------------
    # Object Management
    # ------------------------------------------------------------------

    async def get_objects(self, device_id: str) -> list[dict]:
        """Get all firewall objects (address/service) for a device from DB."""
        return await self._db.get_firewall_objects_by_device(device_id)

    async def create_object(self, device_id: str, obj_data: dict) -> dict:
        """Create a firewall address or service object on a device and store in DB."""
        device_info = await self._db.get_device(device_id)
        if not device_info:
            raise FirewallError(f"Device {device_id} not found")

        vendor = self._get_vendor(device_info)
        commands = self._generate_create_object_commands(vendor, obj_data)

        if commands:
            cm = self._get_config_manager()
            await cm.deploy_config(device_id=device_id, commands=commands)

        obj_data["id"] = str(uuid4())
        obj_data["device_id"] = device_id
        obj_data["created_at"] = datetime.now(timezone.utc).isoformat()

        obj_id = await self._db.add_firewall_object(obj_data)
        return {"object_id": obj_id, "status": "created"}

    async def delete_object(self, device_id: str, obj_id: str) -> dict:
        """Delete a firewall object from DB."""
        obj = await self._db.get_firewall_object(obj_id)
        if not obj:
            raise FirewallError(f"Object {obj_id} not found")

        await self._db.delete_firewall_object(obj_id)
        return {"object_id": obj_id, "status": "deleted"}

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    async def get_summary(self, device_id: str) -> dict:
        """Return a summary of all firewall state for a device."""
        rules = await self._db.get_firewall_rules_by_device(device_id)
        nat_rules = await self._db.get_nat_rules_by_device(device_id)
        zones = await self._db.get_firewall_zones_by_device(device_id)
        objects = await self._db.get_firewall_objects_by_device(device_id)

        action_counts: dict[str, int] = {}
        for r in rules:
            action = r.get("action", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1

        last_sync = None
        for r in rules:
            s = r.get("synced_at")
            if s and (last_sync is None or s > last_sync):
                last_sync = s

        return {
            "device_id": device_id,
            "rule_count": len(rules),
            "nat_rule_count": len(nat_rules),
            "zone_count": len(zones),
            "object_count": len(objects),
            "action_breakdown": action_counts,
            "enabled_rules": sum(1 for r in rules if r.get("enabled")),
            "disabled_rules": sum(1 for r in rules if not r.get("enabled")),
            "last_sync": last_sync,
        }
    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_config_manager(self):
        if not self._config_mgr:
            raise FirewallError(
                'FirewallManager requires a ConfigManager instance. '
                'Pass config_manager= to the constructor.'
            )
        return self._config_mgr

    def _get_vendor(self, device_info: dict) -> str:
        dtype = device_info.get('device_type', '').lower()
        vendor = self.VENDOR_MAP.get(dtype)
        if not vendor:
            raise FirewallError(
                f'Unsupported firewall vendor: {dtype!r}. '
                f'Supported: {list(self.VENDOR_MAP.keys())}'
            )
        return vendor
    # ------------------------------------------------------------------
    # Fetch rules from live device
    # ------------------------------------------------------------------

    async def _fetch_vendor_rules(self, device, vendor: str) -> list[dict]:
        if vendor == 'fortinet':
            return await self._parse_fortinet_policies(device)
        elif vendor == 'paloalto':
            return await self._parse_paloalto_rules(device)
        elif vendor == 'pfsense':
            return await self._parse_pfsense_rules(device)
        elif vendor == 'sophos':
            return await self._parse_sophos_rules(device)
        return []

    async def _parse_fortinet_policies(self, device) -> list[dict]:
        output = await device.send_command('show firewall policy', timeout=60)

        rules: list[dict] = []
        current_rule: dict | None = None
        position = 0

        for line in output.splitlines():
            line = line.strip()

            if line.startswith('edit '):
                position += 1
                current_rule = {
                    'policy_id': line.split()[1],
                    'name': '',
                    'source_zone': '',
                    'dest_zone': '',
                    'source_addresses': ['any'],
                    'dest_addresses': ['any'],
                    'services': ['ALL'],
                    'action': 'deny',
                    'enabled': True,
                    'log_enabled': False,
                    'position': position,
                }
            elif current_rule and line.startswith('set '):
                parts = line.split(None, 2)
                if len(parts) >= 3:
                    key, val = parts[1], parts[2].strip(chr(34))
                    if key == 'name':
                        current_rule['name'] = val
                    elif key == 'srcintf':
                        current_rule['source_zone'] = val
                    elif key == 'dstintf':
                        current_rule['dest_zone'] = val
                    elif key == 'srcaddr':
                        current_rule['source_addresses'] = [
                            v.strip(chr(34)) for v in parts[2].split()
                        ]
                    elif key == 'dstaddr':
                        current_rule['dest_addresses'] = [
                            v.strip(chr(34)) for v in parts[2].split()
                        ]
                    elif key == 'service':
                        current_rule['services'] = [
                            v.strip(chr(34)) for v in parts[2].split()
                        ]
                    elif key == 'action':
                        current_rule['action'] = (
                            'allow' if val == 'accept' else 'deny'
                        )
                    elif key == 'status':
                        current_rule['enabled'] = val != 'disable'
                    elif key == 'logtraffic':
                        current_rule['log_enabled'] = val != 'disable'
            elif line == 'next' and current_rule:
                if not current_rule['name']:
                    current_rule['name'] = fpolicy-{current_rule[policy_id]}
                rules.append(current_rule)
                current_rule = None

        if current_rule:
            if not current_rule['name']:
                current_rule['name'] = fpolicy-{current_rule[policy_id]}
            rules.append(current_rule)

        return rules
    async def _parse_paloalto_rules(self, device) -> list[dict]:
        output = await device.send_command(
            'show running security-policy', timeout=60
        )

        rules: list[dict] = []
        current_rule: dict | None = None
        position = 0

        for line in output.splitlines():
            line = line.strip()

            m = re.match(r"(?:Rule\s+)?'([^']+)'\s*\{", line)
            if not m:
                m = re.match(r'"([^"]+)"\s*\{', line)
            if not m:
                m = re.match(r'(\S+)\s*\{', line)

            if m and line.endswith('{'):
                position += 1
                current_rule = {
                    'name': m.group(1),
                    'source_zone': '',
                    'dest_zone': '',
                    'source_addresses': ['any'],
                    'dest_addresses': ['any'],
                    'services': ['any'],
                    'action': 'deny',
                    'enabled': True,
                    'log_enabled': False,
                    'position': position,
                }
                continue

            if current_rule is None:
                continue

            kv_m = re.match(r'([\w\-]+):\s+(.+);?', line)
            if kv_m:
                key = kv_m.group(1).lower().replace('-', '_')
                raw_val = kv_m.group(2).strip().rstrip(';')

                if key == 'from':
                    current_rule['source_zone'] = raw_val
                elif key == 'to':
                    current_rule['dest_zone'] = raw_val
                elif key == 'source':
                    current_rule['source_addresses'] = [
                        s.strip() for s in raw_val.split()
                    ]
                elif key == 'destination':
                    current_rule['dest_addresses'] = [
                        s.strip() for s in raw_val.split()
                    ]
                elif key == 'service':
                    current_rule['services'] = [
                        s.strip() for s in raw_val.split()
                    ]
                elif key == 'action':
                    current_rule['action'] = (
                        'allow' if raw_val in ('allow', 'permit') else 'deny'
                    )
                elif key in ('log_start', 'log_end'):
                    if raw_val.lower() == 'yes':
                        current_rule['log_enabled'] = True
                elif key == 'disabled':
                    current_rule['enabled'] = raw_val.lower() != 'yes'

            if line == '}':
                if current_rule:
                    rules.append(current_rule)
                    current_rule = None

        if current_rule:
            rules.append(current_rule)

        return rules
    async def _parse_pfsense_rules(self, device) -> list[dict]:
        output = await device.send_command('pfctl -sr', timeout=15)
        rules: list[dict] = []
        position = 0
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            m = re.match(
                r'(pass|block)\s+(?:(in|out)\s+)?(log\s+)?(quick\s+)?(?:on\s+(\S+)\s+)?(.*)',
                line,
            )
            if not m:
                continue
            position += 1
            action_str = m.group(1)
            log_flag = bool(m.group(3))
            iface = m.group(5) or ''
            rest = m.group(6) or ''
            rule = {
                'name': f'pf-rule-{position}',
                'source_zone': iface,
                'dest_zone': '',
                'source_addresses': ['any'],
                'dest_addresses': ['any'],
                'services': ['any'],
                'action': 'allow' if action_str == 'pass' else 'deny',
                'enabled': True,
                'log_enabled': log_flag,
                'position': position,
            }
            from_match = re.search(r'from\s+(\S+)', rest)
            to_match = re.search(r'to\s+(\S+)', rest)
            port_match = re.search(r'port\s*=?\s*(\S+)', rest)
            if from_match:
                rule['source_addresses'] = [from_match.group(1)]
            if to_match:
                rule['dest_addresses'] = [to_match.group(1)]
            if port_match:
                rule['services'] = [port_match.group(1)]
            rules.append(rule)
        return rules
    async def _fetch_vendor_zones(self, device, vendor: str) -> list[dict]:
        if vendor == 'fortinet':
            return await self._parse_fortinet_zones(device)
        elif vendor == 'paloalto':
            return await self._parse_paloalto_zones(device)
        elif vendor == 'pfsense':
            return await self._parse_pfsense_zones(device)
        elif vendor == 'sophos':
            return await self._parse_sophos_zones(device)
        return []

    async def _parse_sophos_rules(self, device) -> list[dict]:
        """Parse Sophos UTM firewall rules via device.get_policies()."""
        raw_rules = await device.get_policies()
        rules: list[dict] = []
        for i, raw in enumerate(raw_rules):
            # Skip raw iptables fallback entries
            if "raw" in raw and len(raw) == 1:
                continue

            action = raw.get("action", "drop")
            normalized_action = "allow" if action in ("accept", "allow") else "deny"

            sources = raw.get("sources", [])
            destinations = raw.get("destinations", [])
            services = raw.get("services", [])

            rule = {
                'policy_id': raw.get("_ref", ""),
                'name': raw.get("name", f"sophos-rule-{i + 1}"),
                'source_zone': '',
                'dest_zone': '',
                'source_addresses': sources if isinstance(sources, list) else [sources],
                'dest_addresses': destinations if isinstance(destinations, list) else [destinations],
                'services': services if isinstance(services, list) else [services],
                'action': normalized_action,
                'enabled': bool(raw.get("status", True)),
                'log_enabled': bool(raw.get("log", False)),
                'position': raw.get("position", i + 1),
                'comment': raw.get("comment", ""),
            }
            rules.append(rule)
        return rules

    async def _parse_sophos_zones(self, device) -> list[dict]:
        """Parse Sophos UTM interface networks as zones."""
        zones: list[dict] = []
        try:
            raw_zones = await device.get_zones()
            for item in raw_zones:
                zones.append({
                    'name': item.get('name', ''),
                    'interfaces': [item.get('interface', '')],
                    'security_level': 0,
                    'description': item.get('comment', ''),
                })
        except Exception:
            pass
        return zones

    # ------------------------------------------------------------------
    # Command generation
    # ------------------------------------------------------------------

    def _generate_create_rule_commands(
        self, vendor: str, rule_data: dict
    ) -> list[str]:
        """Generate vendor-specific commands to create a firewall rule."""
        if vendor == 'sophos':
            return self._sophos_create_rule_commands(rule_data)
        elif vendor == 'fortinet':
            return self._fortinet_create_rule_commands(rule_data)
        elif vendor == 'paloalto':
            return self._paloalto_create_rule_commands(rule_data)
        elif vendor == 'pfsense':
            return self._pfsense_create_rule_commands(rule_data)
        return []

    def _generate_delete_rule_commands(
        self, vendor: str, rule: dict
    ) -> list[str]:
        """Generate vendor-specific commands to delete a firewall rule."""
        if vendor == 'sophos':
            ref = rule.get("policy_id", "")
            if ref:
                return [f"DELETE /api/objects/packetfilter/packetfilter/{ref}"]
        elif vendor == 'fortinet':
            policy_id = rule.get("policy_id", "")
            if policy_id:
                return [
                    "config firewall policy",
                    f"    delete {policy_id}",
                    "end",
                ]
        elif vendor == 'paloalto':
            name = rule.get("name", "")
            if name:
                return [f"delete rulebase security rules {name}"]
        elif vendor == 'pfsense':
            # pfSense uses pfctl; rules are managed via config.xml
            pass
        return []

    def _generate_modify_rule_commands(
        self, vendor: str, rule: dict, changes: dict
    ) -> list[str]:
        """Generate vendor-specific commands to modify a firewall rule."""
        if vendor == 'sophos':
            ref = rule.get("policy_id", "")
            if not ref:
                return []
            payload: dict = {}
            if "name" in changes:
                payload["name"] = changes["name"]
            if "action" in changes:
                payload["action"] = (
                    "accept" if changes["action"] in ("allow", "accept") else "drop"
                )
            if "enabled" in changes:
                payload["status"] = changes["enabled"]
            if "log_enabled" in changes:
                payload["log"] = changes["log_enabled"]
            if "comment" in changes:
                payload["comment"] = changes["comment"]
            if "source_addresses" in changes:
                payload["sources"] = changes["source_addresses"]
            if "dest_addresses" in changes:
                payload["destinations"] = changes["dest_addresses"]
            if "services" in changes:
                payload["services"] = changes["services"]
            if payload:
                return [
                    f"PUT /api/objects/packetfilter/packetfilter/{ref} "
                    f"{json.dumps(payload)}"
                ]
        elif vendor == 'fortinet':
            policy_id = rule.get("policy_id", "")
            if not policy_id:
                return []
            cmds = ["config firewall policy", f"    edit {policy_id}"]
            field_map = {
                "name": "name",
                "source_zone": "srcintf",
                "dest_zone": "dstintf",
                "action": "action",
            }
            for key, forti_key in field_map.items():
                if key in changes:
                    val = changes[key]
                    if key == "action":
                        val = "accept" if val in ("allow", "accept") else "deny"
                    cmds.append(f"        set {forti_key} {val}")
            for key, forti_key in [
                ("source_addresses", "srcaddr"),
                ("dest_addresses", "dstaddr"),
                ("services", "service"),
            ]:
                if key in changes:
                    addrs = changes[key]
                    if isinstance(addrs, list):
                        addrs = " ".join(f'"{a}"' for a in addrs)
                    cmds.append(f"        set {forti_key} {addrs}")
            if "enabled" in changes:
                cmds.append(
                    f"        set status {'enable' if changes['enabled'] else 'disable'}"
                )
            if "log_enabled" in changes:
                cmds.append(
                    f"        set logtraffic {'all' if changes['log_enabled'] else 'disable'}"
                )
            cmds.extend(["    next", "end"])
            return cmds
        elif vendor == 'paloalto':
            name = rule.get("name", "")
            if not name:
                return []
            cmds: list[str] = []
            pa_map = {
                "source_zone": "from",
                "dest_zone": "to",
                "action": "action",
            }
            for key, pa_key in pa_map.items():
                if key in changes:
                    val = changes[key]
                    if key == "action":
                        val = "allow" if val in ("allow", "accept") else "deny"
                    cmds.append(f"set rulebase security rules {name} {pa_key} {val}")
            for key, pa_key in [
                ("source_addresses", "source"),
                ("dest_addresses", "destination"),
                ("services", "service"),
            ]:
                if key in changes:
                    vals = changes[key]
                    if isinstance(vals, list):
                        vals = " ".join(vals)
                    cmds.append(f"set rulebase security rules {name} {pa_key} {vals}")
            if "enabled" in changes:
                cmds.append(
                    f"set rulebase security rules {name} disabled "
                    f"{'no' if changes['enabled'] else 'yes'}"
                )
            return cmds
        return []

    def _generate_create_nat_commands(
        self, vendor: str, rule_data: dict
    ) -> list[str]:
        """Generate vendor-specific commands to create a NAT rule."""
        if vendor == 'sophos':
            payload = {
                "name": rule_data.get("name", ""),
                "mode": rule_data.get("nat_type", "source"),
                "status": True,
            }
            for field, api_field in [
                ("original_source", "source"),
                ("original_dest", "destination"),
                ("original_service", "service"),
                ("translated_source", "translation_source"),
                ("translated_dest", "translation_destination"),
                ("translated_service", "translation_service"),
            ]:
                if rule_data.get(field):
                    payload[api_field] = rule_data[field]
            if rule_data.get("comment"):
                payload["comment"] = rule_data["comment"]
            return [
                f"POST /api/objects/packetfilter/nat/ {json.dumps(payload)}"
            ]
        elif vendor == 'fortinet':
            cmds = ["config firewall central-snat-map", "    edit 0"]
            if rule_data.get("source_zone"):
                cmds.append(f'        set srcintf "{rule_data["source_zone"]}"')
            if rule_data.get("dest_zone"):
                cmds.append(f'        set dstintf "{rule_data["dest_zone"]}"')
            if rule_data.get("original_source"):
                cmds.append(f'        set orig-addr "{rule_data["original_source"]}"')
            if rule_data.get("translated_source"):
                cmds.append(f'        set dst-addr "{rule_data["translated_source"]}"')
            cmds.extend(["    next", "end"])
            return cmds
        elif vendor == 'paloalto':
            name = rule_data.get("name", "nat-rule")
            nat_type = rule_data.get("nat_type", "source")
            cmds = []
            if rule_data.get("source_zone"):
                cmds.append(f"set rulebase nat rules {name} from {rule_data['source_zone']}")
            if rule_data.get("dest_zone"):
                cmds.append(f"set rulebase nat rules {name} to {rule_data['dest_zone']}")
            if rule_data.get("original_source"):
                cmds.append(f"set rulebase nat rules {name} source {rule_data['original_source']}")
            if rule_data.get("original_dest"):
                cmds.append(f"set rulebase nat rules {name} destination {rule_data['original_dest']}")
            if nat_type == "source" and rule_data.get("translated_source"):
                cmds.append(
                    f"set rulebase nat rules {name} source-translation "
                    f"dynamic-ip-and-port translated-address {rule_data['translated_source']}"
                )
            elif nat_type == "destination" and rule_data.get("translated_dest"):
                cmds.append(
                    f"set rulebase nat rules {name} destination-translation "
                    f"translated-address {rule_data['translated_dest']}"
                )
            return cmds
        return []

    def _generate_create_object_commands(
        self, vendor: str, obj_data: dict
    ) -> list[str]:
        """Generate vendor-specific commands to create an address/service object."""
        obj_type = obj_data.get("object_type", "address")
        name = obj_data.get("name", "")
        value = obj_data.get("value", "")

        if vendor == 'sophos':
            if obj_type in ("address", "host"):
                payload = {
                    "name": name,
                    "address": value,
                    "comment": obj_data.get("description", ""),
                }
                return [
                    f"POST /api/objects/network/host/ {json.dumps(payload)}"
                ]
            elif obj_type == "network":
                payload = {
                    "name": name,
                    "address": value,
                    "comment": obj_data.get("description", ""),
                }
                return [
                    f"POST /api/objects/network/network/ {json.dumps(payload)}"
                ]
            elif obj_type in ("service", "service-group"):
                payload = {
                    "name": name,
                    "comment": obj_data.get("description", ""),
                }
                if value:
                    payload["protocol"] = value
                if obj_data.get("members"):
                    payload["members"] = obj_data["members"]
                return [
                    f"POST /api/objects/service/ {json.dumps(payload)}"
                ]
            elif obj_type == "address-group":
                payload = {
                    "name": name,
                    "members": obj_data.get("members", []),
                    "comment": obj_data.get("description", ""),
                }
                return [
                    f"POST /api/objects/network/group/ {json.dumps(payload)}"
                ]
        elif vendor == 'fortinet':
            if obj_type in ("address", "host", "network"):
                cmds = [
                    "config firewall address",
                    f'    edit "{name}"',
                ]
                if "/" in value:
                    cmds.append(f"        set type ipmask")
                    cmds.append(f"        set subnet {value}")
                else:
                    cmds.append(f"        set type ipmask")
                    cmds.append(f"        set subnet {value}/32")
                if obj_data.get("description"):
                    cmds.append(f'        set comment "{obj_data["description"]}"')
                cmds.extend(["    next", "end"])
                return cmds
            elif obj_type == "address-group":
                members = obj_data.get("members", [])
                member_str = " ".join(f'"{m}"' for m in members)
                return [
                    "config firewall addrgrp",
                    f'    edit "{name}"',
                    f"        set member {member_str}",
                    "    next",
                    "end",
                ]
        elif vendor == 'paloalto':
            if obj_type in ("address", "host"):
                return [f"set address {name} ip-netmask {value}/32"]
            elif obj_type == "network":
                return [f"set address {name} ip-netmask {value}"]
            elif obj_type == "address-group":
                members = obj_data.get("members", [])
                member_str = " ".join(members)
                return [f"set address-group {name} static [{member_str}]"]
        return []

    # ------------------------------------------------------------------
    # Sophos-specific command helpers
    # ------------------------------------------------------------------

    def _sophos_create_rule_commands(self, rule_data: dict) -> list[str]:
        """Generate Sophos UTM API command to create a firewall rule."""
        action = rule_data.get("action", "deny")
        api_action = "accept" if action in ("allow", "accept") else "drop"

        payload: dict = {
            "name": rule_data.get("name", ""),
            "action": api_action,
            "status": rule_data.get("enabled", True),
            "log": rule_data.get("log_enabled", False),
        }
        if rule_data.get("sources") or rule_data.get("source_addresses"):
            payload["sources"] = rule_data.get("sources") or rule_data.get(
                "source_addresses", []
            )
        if rule_data.get("destinations") or rule_data.get("dest_addresses"):
            payload["destinations"] = rule_data.get(
                "destinations"
            ) or rule_data.get("dest_addresses", [])
        if rule_data.get("services"):
            payload["services"] = rule_data["services"]
        if rule_data.get("comment"):
            payload["comment"] = rule_data["comment"]
        if rule_data.get("group"):
            payload["group"] = rule_data["group"]

        return [
            f"POST /api/objects/packetfilter/packetfilter/ {json.dumps(payload)}"
        ]

    # ------------------------------------------------------------------
    # Fortinet command helpers
    # ------------------------------------------------------------------

    def _fortinet_create_rule_commands(self, rule_data: dict) -> list[str]:
        """Generate FortiOS CLI commands to create a firewall policy."""
        action = rule_data.get("action", "deny")
        forti_action = "accept" if action in ("allow", "accept") else "deny"

        cmds = ["config firewall policy", "    edit 0"]
        if rule_data.get("name"):
            cmds.append(f'        set name "{rule_data["name"]}"')
        if rule_data.get("source_zone"):
            cmds.append(f'        set srcintf "{rule_data["source_zone"]}"')
        if rule_data.get("dest_zone"):
            cmds.append(f'        set dstintf "{rule_data["dest_zone"]}"')

        src_addrs = rule_data.get("source_addresses", ["all"])
        dst_addrs = rule_data.get("dest_addresses", ["all"])
        services = rule_data.get("services", ["ALL"])

        cmds.append(
            f'        set srcaddr {" ".join(f"{a!r}" for a in src_addrs)}'
        )
        cmds.append(
            f'        set dstaddr {" ".join(f"{a!r}" for a in dst_addrs)}'
        )
        cmds.append(
            f'        set service {" ".join(f"{s!r}" for s in services)}'
        )
        cmds.append(f"        set action {forti_action}")

        if rule_data.get("log_enabled"):
            cmds.append("        set logtraffic all")

        cmds.append("        set schedule always")
        cmds.extend(["    next", "end"])
        return cmds

    # ------------------------------------------------------------------
    # Palo Alto command helpers
    # ------------------------------------------------------------------

    def _paloalto_create_rule_commands(self, rule_data: dict) -> list[str]:
        """Generate PAN-OS CLI commands to create a security rule."""
        name = rule_data.get("name", "new-rule")
        action = rule_data.get("action", "deny")
        pa_action = "allow" if action in ("allow", "accept") else "deny"

        cmds = [f"set rulebase security rules {name} action {pa_action}"]
        if rule_data.get("source_zone"):
            cmds.append(f"set rulebase security rules {name} from {rule_data['source_zone']}")
        if rule_data.get("dest_zone"):
            cmds.append(f"set rulebase security rules {name} to {rule_data['dest_zone']}")

        src_addrs = rule_data.get("source_addresses", ["any"])
        dst_addrs = rule_data.get("dest_addresses", ["any"])
        services = rule_data.get("services", ["any"])

        cmds.append(
            f"set rulebase security rules {name} source {' '.join(src_addrs)}"
        )
        cmds.append(
            f"set rulebase security rules {name} destination {' '.join(dst_addrs)}"
        )
        cmds.append(
            f"set rulebase security rules {name} service {' '.join(services)}"
        )

        if rule_data.get("log_enabled"):
            cmds.append(f"set rulebase security rules {name} log-start yes")

        return cmds

    # ------------------------------------------------------------------
    # pfSense command helpers
    # ------------------------------------------------------------------

    def _pfsense_create_rule_commands(self, rule_data: dict) -> list[str]:
        """Generate pfSense easyrule command to create a firewall rule.

        pfSense rules are typically managed via the XML config or WebGUI.
        ``easyrule`` is the closest CLI equivalent.
        """
        action = rule_data.get("action", "deny")
        pf_action = "pass" if action in ("allow", "accept") else "block"
        interface = rule_data.get("source_zone", "wan")
        proto = "tcp"

        src = "any"
        dst = "any"
        dst_port = "any"

        src_addrs = rule_data.get("source_addresses", [])
        dst_addrs = rule_data.get("dest_addresses", [])
        services = rule_data.get("services", [])

        if src_addrs and src_addrs != ["any"]:
            src = src_addrs[0]
        if dst_addrs and dst_addrs != ["any"]:
            dst = dst_addrs[0]
        if services and services != ["any"]:
            svc = services[0]
            if svc.isdigit():
                dst_port = svc
            else:
                dst_port = svc

        return [
            f"easyrule {pf_action} {interface} {proto} {src} {dst} {dst_port}"
        ]