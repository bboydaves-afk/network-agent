"""Safety classification for network operations.

Every tool exposed to the AI agent is tagged with a safety level:

* **SAFE** -- Read-only operations with no side effects.
* **WRITE** -- Creates data (e.g. a backup) but is non-destructive.
* **DESTRUCTIVE** -- Modifies live device configuration and could cause an
  outage if misused.  These always require explicit user confirmation.
"""

from __future__ import annotations

import re
from enum import Enum


class SafetyLevel(Enum):
    """Severity classification for an agent tool invocation."""

    SAFE = "safe"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


# ---------------------------------------------------------------------------
# Tool -> safety-level mapping
# ---------------------------------------------------------------------------

TOOL_SAFETY: dict[str, SafetyLevel] = {
    "list_devices": SafetyLevel.SAFE,
    "get_device_status": SafetyLevel.SAFE,
    "ping_device": SafetyLevel.SAFE,
    "traceroute": SafetyLevel.SAFE,
    "show_interfaces": SafetyLevel.SAFE,
    "get_config": SafetyLevel.SAFE,
    "diff_configs": SafetyLevel.SAFE,
    "check_port": SafetyLevel.SAFE,
    "get_metrics": SafetyLevel.SAFE,
    "get_alerts": SafetyLevel.SAFE,
    "backup_config": SafetyLevel.WRITE,
    "discover_network": SafetyLevel.WRITE,
    "deploy_config": SafetyLevel.DESTRUCTIVE,
    "run_command": SafetyLevel.DESTRUCTIVE,
    "list_sites": SafetyLevel.SAFE,
    "get_site_summary": SafetyLevel.SAFE,
    "create_site": SafetyLevel.WRITE,
    # Topology
    "discover_topology": SafetyLevel.WRITE,
    "get_topology": SafetyLevel.SAFE,
    "get_device_neighbors": SafetyLevel.SAFE,
    # Firmware
    "check_firmware_status": SafetyLevel.SAFE,
    "list_eol_devices": SafetyLevel.SAFE,
    "list_firmware_catalog": SafetyLevel.SAFE,
    # IPAM
    "list_subnets": SafetyLevel.SAFE,
    "get_subnet_utilization": SafetyLevel.SAFE,
    "find_free_ips": SafetyLevel.SAFE,
    "scan_arp_table": SafetyLevel.WRITE,
    # Traffic
    "get_traffic_trends": SafetyLevel.SAFE,
    "get_top_interfaces": SafetyLevel.SAFE,
    # Syslog
    "search_syslog": SafetyLevel.SAFE,
    "get_syslog_stats": SafetyLevel.SAFE,
    # Compliance
    "run_compliance_check": SafetyLevel.WRITE,
    "get_compliance_report": SafetyLevel.SAFE,
    "list_compliance_rules": SafetyLevel.SAFE,
    # Change Management
    "create_change_request": SafetyLevel.WRITE,
    "list_change_requests": SafetyLevel.SAFE,
    "approve_change": SafetyLevel.DESTRUCTIVE,
    "reject_change": SafetyLevel.WRITE,
    "execute_change": SafetyLevel.DESTRUCTIVE,
    "rollback_change": SafetyLevel.DESTRUCTIVE,
    "get_change_history": SafetyLevel.SAFE,
    "list_pending_approvals": SafetyLevel.SAFE,
    # Credential Rotation
    "rotate_credential": SafetyLevel.DESTRUCTIVE,
    "schedule_rotation": SafetyLevel.DESTRUCTIVE,
    "verify_credentials": SafetyLevel.DESTRUCTIVE,
    # Firewall Management
    "get_firewall_rules": SafetyLevel.SAFE,
    "get_nat_rules": SafetyLevel.SAFE,
    "get_firewall_zones": SafetyLevel.SAFE,
    "get_firewall_objects": SafetyLevel.SAFE,
    "get_firewall_summary": SafetyLevel.SAFE,
    "sync_firewall_rules": SafetyLevel.WRITE,
    "create_address_object": SafetyLevel.WRITE,
    "create_service_object": SafetyLevel.WRITE,
    "create_firewall_rule": SafetyLevel.DESTRUCTIVE,
    "delete_firewall_rule": SafetyLevel.DESTRUCTIVE,
    "modify_firewall_rule": SafetyLevel.DESTRUCTIVE,
    "create_nat_rule": SafetyLevel.DESTRUCTIVE,
    # ACL Management
    "list_acls": SafetyLevel.SAFE,
    "sync_acls": SafetyLevel.WRITE,
    "create_acl": SafetyLevel.DESTRUCTIVE,
    "delete_acl": SafetyLevel.DESTRUCTIVE,
    "add_acl_entry": SafetyLevel.DESTRUCTIVE,
    "remove_acl_entry": SafetyLevel.DESTRUCTIVE,
    "bind_acl": SafetyLevel.DESTRUCTIVE,
    "unbind_acl": SafetyLevel.DESTRUCTIVE,
    # Routing Management
    "get_routing_table": SafetyLevel.SAFE,
    "sync_routes": SafetyLevel.WRITE,
    "create_static_route": SafetyLevel.DESTRUCTIVE,
    "delete_static_route": SafetyLevel.DESTRUCTIVE,
    "get_ospf_status": SafetyLevel.SAFE,
    "sync_ospf": SafetyLevel.WRITE,
    "configure_ospf": SafetyLevel.DESTRUCTIVE,
    "add_ospf_network": SafetyLevel.DESTRUCTIVE,
    # VLAN Management
    "list_vlans": SafetyLevel.SAFE,
    "get_vlan_summary": SafetyLevel.SAFE,
    "sync_vlans": SafetyLevel.WRITE,
    "create_vlan": SafetyLevel.DESTRUCTIVE,
    "delete_vlan": SafetyLevel.DESTRUCTIVE,
    "assign_vlan_interface": SafetyLevel.DESTRUCTIVE,
    # Serial Console
    "list_serial_ports": SafetyLevel.SAFE,
    "serial_connect": SafetyLevel.WRITE,
    "serial_send_command": SafetyLevel.DESTRUCTIVE,
    "serial_send_config": SafetyLevel.DESTRUCTIVE,
    "serial_send_break": SafetyLevel.DESTRUCTIVE,
    "serial_get_facts": SafetyLevel.SAFE,
    "serial_disconnect": SafetyLevel.WRITE,
}


def get_safety_level(tool_name: str) -> SafetyLevel:
    """Return the safety level for *tool_name*.

    Unknown tools are treated as DESTRUCTIVE out of caution.
    """
    return TOOL_SAFETY.get(tool_name, SafetyLevel.DESTRUCTIVE)


def requires_confirmation(tool_name: str) -> bool:
    """Return ``True`` if *tool_name* should prompt the user for confirmation."""
    return get_safety_level(tool_name) == SafetyLevel.DESTRUCTIVE


# ---------------------------------------------------------------------------
# Dangerous CLI command patterns -- regex-based with word boundaries
# ---------------------------------------------------------------------------
# Each entry is a tuple: (compiled_regex, human_readable_description).
# Patterns use word boundaries (\b) to reduce false positives (e.g. "reload"
# won't match "no reload timeout" when anchored properly).

_DANGEROUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    # ------- Device reload / reboot / halt --------------------------------
    (re.compile(r"\breload\b(?!\s+timeout)", re.IGNORECASE),
     "Device reload (will cause an outage)"),
    (re.compile(r"\breboot\b", re.IGNORECASE),
     "Device reboot"),
    (re.compile(r"\brequest\s+system\s+(halt|reboot|power-off|shutdown)\b", re.IGNORECASE),
     "System halt/reboot request (Juniper)"),
    (re.compile(r"\bfactory[- ]?reset\b", re.IGNORECASE),
     "Factory reset (erases all configuration)"),

    # ------- Erase / wipe configuration -----------------------------------
    (re.compile(r"\bwrite\s+erase\b", re.IGNORECASE),
     "Erase startup configuration"),
    (re.compile(r"\berase\s+(startup|flash|nvram)\b", re.IGNORECASE),
     "Erase startup config, flash, or NVRAM"),
    (re.compile(r"\bdelete\s+(startup|flash|nvram|system)\b", re.IGNORECASE),
     "Delete critical system files"),

    # ------- Interface shutdown / no shutdown -----------------------------
    (re.compile(r"^\s*shutdown\s*$", re.IGNORECASE),
     "Interface shutdown (will disable the interface)"),
    (re.compile(r"^\s*no\s+shutdown\s*$", re.IGNORECASE),
     "Interface enable (no shutdown)"),

    # ------- Disabling security features ----------------------------------
    (re.compile(r"\bno\s+(ip\s+)?access-list\b", re.IGNORECASE),
     "Removing an access list (may remove security filtering)"),
    (re.compile(r"\bno\s+ip\s+access-group\b", re.IGNORECASE),
     "Unbinding ACL from interface"),
    (re.compile(r"\bno\s+authentication\b", re.IGNORECASE),
     "Disabling authentication"),
    (re.compile(r"\bno\s+enable\s+(secret|password)\b", re.IGNORECASE),
     "Removing enable password/secret"),
    (re.compile(r"\bno\s+service\s+password-encryption\b", re.IGNORECASE),
     "Disabling password encryption"),
    (re.compile(r"\bno\s+crypto\b", re.IGNORECASE),
     "Removing cryptographic configuration (VPN/SSH keys)"),
    (re.compile(r"\bno\s+ip\s+ssh\b", re.IGNORECASE),
     "Disabling SSH access"),
    (re.compile(r"\bno\s+aaa\b", re.IGNORECASE),
     "Disabling AAA authentication"),
    (re.compile(r"\bno\s+logging\b", re.IGNORECASE),
     "Disabling logging"),

    # ------- Routing disruption -------------------------------------------
    (re.compile(r"\bno\s+router\s+(ospf|bgp|eigrp|rip)\b", re.IGNORECASE),
     "Removing routing protocol (will drop routes)"),
    (re.compile(r"\bclear\s+ip\s+(bgp|ospf|route)\b", re.IGNORECASE),
     "Clearing routing state (will cause reconvergence)"),
    (re.compile(r"\bredistribute\b", re.IGNORECASE),
     "Route redistribution (can cause routing loops)"),

    # ------- Spanning tree disruption -------------------------------------
    (re.compile(r"\bno\s+spanning-tree\b", re.IGNORECASE),
     "Disabling spanning tree (risk of broadcast storms)"),
    (re.compile(r"\bspanning-tree\s+mode\b", re.IGNORECASE),
     "Changing spanning tree mode (may cause temporary loops)"),

    # ------- SNMP community changes ---------------------------------------
    (re.compile(r"\bsnmp-server\s+community\b.*\b(rw|read-write)\b", re.IGNORECASE),
     "Setting read-write SNMP community string"),

    # ------- Firewall (Fortinet / Palo Alto / pfSense / general) ----------
    (re.compile(r"\bexecute\s+(reboot|shutdown|format|factoryreset)\b", re.IGNORECASE),
     "Fortinet execute command (reboot/shutdown/format/reset)"),
    (re.compile(r"\bdiagnose\s+sys\s+session\s+clear\b", re.IGNORECASE),
     "Clear all firewall sessions (drops active connections)"),
    (re.compile(r"\breset\s+(vpn|ike|ipsec)\b", re.IGNORECASE),
     "Resetting VPN tunnels"),
    (re.compile(r"\bdelete\s+(firewall|policy|rule)\b", re.IGNORECASE),
     "Deleting firewall policy/rules"),
    (re.compile(r"\bset\s+policy\s+.*\baction\s+accept\b.*\bany\b.*\bany\b", re.IGNORECASE),
     "Creating an any-any allow rule (bypasses all security)"),
    (re.compile(r"\bpfctl\s+-[dF]\b", re.IGNORECASE),
     "Disabling or flushing pfSense firewall rules"),
    (re.compile(r"\biptables\s+(-F|--flush)\b", re.IGNORECASE),
     "Flushing all iptables rules"),
    (re.compile(r"\bnft\s+(flush|delete)\s+ruleset\b", re.IGNORECASE),
     "Flushing nftables ruleset"),

    # ------- Server / OS level (if running commands on hosts) -------------
    (re.compile(r"\brm\s+-(r|f|rf|fr)\b", re.IGNORECASE),
     "Recursive or forced file deletion"),
    (re.compile(r"\bformat\s+[a-zA-Z]:", re.IGNORECASE),
     "Formatting a disk drive"),
    (re.compile(r"\bmkfs\b", re.IGNORECASE),
     "Creating filesystem (will wipe disk)"),
    (re.compile(r"\bfdisk\b", re.IGNORECASE),
     "Disk partitioning tool"),
    (re.compile(r"\bdd\s+.*\bof=", re.IGNORECASE),
     "Low-level disk write (dd)"),
    (re.compile(r"\bsystemctl\s+(stop|disable|mask)\b", re.IGNORECASE),
     "Stopping/disabling a system service"),
    (re.compile(r"\bservice\s+\S+\s+stop\b", re.IGNORECASE),
     "Stopping a service"),

    # ------- Catch-all clear/reset ----------------------------------------
    (re.compile(r"^\s*clear\s+", re.IGNORECASE),
     "Clear command (may reset counters, sessions, or state)"),
    (re.compile(r"^\s*reset\s+", re.IGNORECASE),
     "Reset command"),
]


def check_dangerous_command(command: str) -> tuple[bool, str]:
    """Check if a raw CLI command contains a dangerous operation.

    Uses regex with word boundaries for accurate matching -- avoids
    false positives like "reload timeout" or "no reload standby".

    Returns
    -------
    tuple[bool, str]
        ``(is_dangerous, human_readable_reason)``
    """
    cmd_stripped = command.strip()
    for pattern, description in _DANGEROUS_PATTERNS:
        if pattern.search(cmd_stripped):
            return True, f"DANGEROUS: {description}"
    return False, ""
