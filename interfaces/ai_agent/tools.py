"""Tool definitions for the Claude AI agent.

Each tool is described in the Anthropic tool-use format with a name,
description, and JSON-Schema ``input_schema``.  The list is passed directly
to ``client.messages.create(tools=NETWORK_TOOLS, ...)``.
"""

NETWORK_TOOLS = [
    {
        "name": "list_devices",
        "description": "List all network devices in the inventory. Optionally filter by tag, status, or device type.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "Filter by device tag",
                },
                "status": {
                    "type": "string",
                    "enum": ["online", "offline", "degraded", "unknown"],
                    "description": "Filter by status",
                },
                "device_type": {
                    "type": "string",
                    "description": "Filter by device type (e.g. cisco_ios, juniper_junos)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_device_status",
        "description": "Get detailed status and health information for a specific device by its ID or hostname.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Device ID or hostname to check",
                },
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "ping_device",
        "description": "Ping a target IP or hostname to test connectivity. Can ping from the agent host or from a specific network device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "IP address or hostname to ping",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of pings (default 4)",
                    "default": 4,
                },
                "source_device_id": {
                    "type": "string",
                    "description": "Optional: device ID to ping FROM (instead of local machine)",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "traceroute",
        "description": "Run traceroute to a target to show the network path and hop-by-hop latency.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "IP address or hostname",
                },
                "source_device_id": {
                    "type": "string",
                    "description": "Optional: run traceroute from this device",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "show_interfaces",
        "description": "Show all interfaces on a network device with their status, speed, IP, and error counters.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Device ID to query",
                },
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_config",
        "description": "Retrieve the current running configuration from a network device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Device ID",
                },
                "config_type": {
                    "type": "string",
                    "enum": ["running", "startup"],
                    "default": "running",
                },
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "diff_configs",
        "description": "Compare two configuration backup versions and show the differences.",
        "input_schema": {
            "type": "object",
            "properties": {
                "backup_id_1": {
                    "type": "string",
                    "description": "First config backup ID",
                },
                "backup_id_2": {
                    "type": "string",
                    "description": "Second config backup ID",
                },
            },
            "required": ["backup_id_1", "backup_id_2"],
        },
    },
    {
        "name": "backup_config",
        "description": "Create a backup of a device's current running configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Device ID to backup",
                },
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "check_port",
        "description": "Check if a specific TCP port is open on a target host.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Target IP or hostname",
                },
                "port": {
                    "type": "integer",
                    "description": "TCP port number",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 5,
                },
            },
            "required": ["target", "port"],
        },
    },
    {
        "name": "get_metrics",
        "description": "Get historical monitoring metrics for a device (CPU, memory, bandwidth, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Device ID",
                },
                "metric_name": {
                    "type": "string",
                    "description": "Metric name (cpu_percent, memory_percent, interface_in_octets, etc.)",
                },
                "hours": {
                    "type": "integer",
                    "description": "Hours of history (default 24)",
                    "default": 24,
                },
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_alerts",
        "description": "Get current active alerts across all devices.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "acknowledged", "resolved"],
                    "default": "active",
                },
            },
            "required": [],
        },
    },
    {
        "name": "deploy_config",
        "description": (
            "Deploy configuration commands to a network device. "
            "THIS IS A DESTRUCTIVE OPERATION that modifies device configuration. "
            "Always confirm with the user before executing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Device ID to configure",
                },
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of configuration commands to send",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, only validate without applying",
                    "default": False,
                },
            },
            "required": ["device_id", "commands"],
        },
    },
    {
        "name": "discover_network",
        "description": "Scan a network subnet to discover devices using SNMP.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subnet": {
                    "type": "string",
                    "description": "Subnet to scan (e.g. 192.168.1.0/24)",
                },
                "community": {
                    "type": "string",
                    "description": "SNMP community string",
                    "default": "public",
                },
            },
            "required": ["subnet"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Execute an arbitrary CLI command on a network device. "
            "THIS IS A DESTRUCTIVE OPERATION. Confirm with the user before executing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Device ID",
                },
                "command": {
                    "type": "string",
                    "description": "CLI command to execute",
                },
            },
            "required": ["device_id", "command"],
        },
    },
    {
        "name": "list_sites",
        "description": "List all sites (physical or logical groupings of network devices).",
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "Optional: filter by region",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_site_summary",
        "description": "Get summary information for a site including device counts and statuses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_id": {
                    "type": "string",
                    "description": "Site ID to get summary for",
                },
            },
            "required": ["site_id"],
        },
    },
    {
        "name": "create_site",
        "description": "Create a new site for grouping network devices by physical or logical location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Site name (must be unique)",
                },
                "location": {
                    "type": "string",
                    "description": "Physical location or address",
                },
                "region": {
                    "type": "string",
                    "description": "Region identifier (e.g. 'us-east', 'eu-west')",
                },
                "description": {
                    "type": "string",
                    "description": "Site description",
                },
                "contact": {
                    "type": "string",
                    "description": "Contact person or team",
                },
            },
            "required": ["name"],
        },
    },
    # ---- Topology tools ----
    {
        "name": "discover_topology",
        "description": "Discover network topology by running CDP/LLDP discovery across devices.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Optional: discover for a specific device only"},
            },
            "required": [],
        },
    },
    {
        "name": "get_topology",
        "description": "Get the current network topology graph with nodes and edges for visualization.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_device_neighbors",
        "description": "Get CDP/LLDP neighbors for a specific device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID to get neighbors for"},
            },
            "required": ["device_id"],
        },
    },
    # ---- Firmware tools ----
    {
        "name": "check_firmware_status",
        "description": "Check firmware compliance status across all devices against the firmware catalog.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_eol_devices",
        "description": "List devices running end-of-life firmware versions.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_firmware_catalog",
        "description": "List all entries in the firmware version catalog.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ---- IPAM tools ----
    {
        "name": "list_subnets",
        "description": "List all managed IP subnets with optional site filtering.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_id": {"type": "string", "description": "Optional: filter by site"},
            },
            "required": [],
        },
    },
    {
        "name": "get_subnet_utilization",
        "description": "Get IP address utilization statistics for a subnet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subnet_id": {"type": "string", "description": "Subnet ID"},
            },
            "required": ["subnet_id"],
        },
    },
    {
        "name": "find_free_ips",
        "description": "Find available (free) IP addresses in a subnet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subnet_id": {"type": "string", "description": "Subnet ID"},
                "count": {"type": "integer", "description": "Number of free IPs to find (default 5)", "default": 5},
            },
            "required": ["subnet_id"],
        },
    },
    {
        "name": "scan_arp_table",
        "description": "Scan a subnet via ARP/ping sweep to discover active IP addresses. This modifies the IPAM database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subnet_id": {"type": "string", "description": "Subnet ID to scan"},
            },
            "required": ["subnet_id"],
        },
    },
    # ---- Traffic tools ----
    {
        "name": "get_traffic_trends",
        "description": "Get traffic bandwidth trends for a device's interfaces over a time period.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "hours": {"type": "integer", "description": "Hours of history (default 24)", "default": 24},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_top_interfaces",
        "description": "Get the top interfaces by bandwidth utilization across all devices.",
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of top interfaces (default 10)", "default": 10},
            },
            "required": [],
        },
    },
    # ---- Syslog tools ----
    {
        "name": "search_syslog",
        "description": "Search syslog messages with optional filters for device, severity, and text query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Filter by device ID"},
                "severity": {"type": "integer", "description": "Minimum severity level (0=emergency, 7=debug)"},
                "query": {"type": "string", "description": "Text search query"},
                "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
            },
            "required": [],
        },
    },
    {
        "name": "get_syslog_stats",
        "description": "Get syslog message statistics (counts by severity, recent activity).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ---- Compliance tools ----
    {
        "name": "run_compliance_check",
        "description": "Run a compliance check against a device using a specified ruleset. This stores results in the database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID to check"},
                "ruleset": {"type": "string", "description": "Ruleset name (default: cis-network)", "default": "cis-network"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_compliance_report",
        "description": "Get the latest compliance report for a device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "list_compliance_rules",
        "description": "List all checks in a compliance ruleset.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ruleset": {"type": "string", "description": "Ruleset name (default: cis-network)", "default": "cis-network"},
            },
            "required": [],
        },
    },
    # ---- Change Management tools ----
    {
        "name": "create_change_request",
        "description": "Create a change request for configuration changes that require approval before deployment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Target device ID"},
                "title": {"type": "string", "description": "Brief description of the change"},
                "config_commands": {"type": "string", "description": "Configuration commands (newline-separated)"},
                "requested_by": {"type": "string", "description": "Who is requesting (default: ai-agent)", "default": "ai-agent"},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "critical"], "default": "normal"},
                "notes": {"type": "string", "description": "Additional notes"},
            },
            "required": ["device_id", "title", "config_commands"],
        },
    },
    {
        "name": "list_change_requests",
        "description": "List change requests with optional status and device filters.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "approved", "rejected", "applied", "failed"]},
                "device_id": {"type": "string", "description": "Filter by device ID"},
            },
            "required": [],
        },
    },
    {
        "name": "approve_change",
        "description": "Approve a pending change request. THIS IS A DESTRUCTIVE OPERATION - it enables config deployment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "change_id": {"type": "string", "description": "Change request ID to approve"},
                "approved_by": {"type": "string", "description": "Approver name", "default": "ai-agent"},
            },
            "required": ["change_id"],
        },
    },
    {
        "name": "reject_change",
        "description": "Reject a pending change request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "change_id": {"type": "string", "description": "Change request ID to reject"},
                "reason": {"type": "string", "description": "Reason for rejection"},
            },
            "required": ["change_id"],
        },
    },
    {
        "name": "execute_change",
        "description": "Execute an approved change request with full pipeline: pre-checks → deploy → post-checks → auto-rollback on failure. THIS IS A DESTRUCTIVE OPERATION.",
        "input_schema": {
            "type": "object",
            "properties": {
                "change_id": {"type": "string", "description": "Approved change request ID to execute"},
            },
            "required": ["change_id"],
        },
    },
    {
        "name": "rollback_change",
        "description": "Rollback an applied or failed change request using its rollback plan. THIS IS A DESTRUCTIVE OPERATION.",
        "input_schema": {
            "type": "object",
            "properties": {
                "change_id": {"type": "string", "description": "Change request ID to rollback"},
                "executed_by": {"type": "string", "description": "Who is executing the rollback", "default": "ai-agent"},
            },
            "required": ["change_id"],
        },
    },
    {
        "name": "get_change_history",
        "description": "Get the full change history for a device — all change requests with their approval and rollback records.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID to get history for"},
                "limit": {"type": "integer", "description": "Max number of records", "default": 50},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "list_pending_approvals",
        "description": "List all change requests that are awaiting approval.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ---- Credential Rotation tools ----
    {
        "name": "rotate_credential",
        "description": "Rotate a stored credential - generates new password and updates all assigned devices. THIS IS A DESTRUCTIVE OPERATION.",
        "input_schema": {
            "type": "object",
            "properties": {
                "credential_id": {"type": "string", "description": "Credential ID to rotate"},
                "initiated_by": {"type": "string", "description": "Who initiated (default: ai-agent)", "default": "ai-agent"},
            },
            "required": ["credential_id"],
        },
    },
    {
        "name": "schedule_rotation",
        "description": "Schedule automatic credential rotation. THIS IS A DESTRUCTIVE OPERATION.",
        "input_schema": {
            "type": "object",
            "properties": {
                "credential_id": {"type": "string", "description": "Credential ID"},
                "cron_expression": {"type": "string", "description": "Cron schedule (e.g. '0 2 1 * *' for monthly)"},
            },
            "required": ["credential_id", "cron_expression"],
        },
    },
    {
        "name": "verify_credentials",
        "description": "Verify a credential works on all assigned devices by testing connections. THIS IS A DESTRUCTIVE OPERATION.",
        "input_schema": {
            "type": "object",
            "properties": {
                "credential_id": {"type": "string", "description": "Credential ID to verify"},
            },
            "required": ["credential_id"],
        },
    },
    # ---- Firewall Management tools ----
    {
        "name": "get_firewall_rules",
        "description": "List firewall rules for a device. Returns rule name, zones, addresses, services, action, and status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "The device ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_nat_rules",
        "description": "List NAT rules for a device. Returns NAT type, zones, original and translated addresses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "The device ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_firewall_zones",
        "description": "List firewall zones for a device. Returns zone names, associated interfaces, and security levels.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "The device ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_firewall_objects",
        "description": "List firewall address and service objects for a device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "The device ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_firewall_summary",
        "description": "Get a summary of firewall state for a device: rule counts, NAT counts, zone counts, object counts, action breakdown.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "The device ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "sync_firewall_rules",
        "description": "Sync firewall rules from a live device to the database. Pulls current rules via SSH and stores them locally.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "The device ID to sync from"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "create_address_object",
        "description": "Create a firewall address object on a device. Supports IP/subnet and FQDN addresses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "The device ID"},
                "name": {"type": "string", "description": "Object name"},
                "object_type": {"type": "string", "enum": ["address", "address-group"], "description": "Object type"},
                "value": {"type": "string", "description": "IP address, subnet (CIDR), or FQDN"},
                "members": {"type": "array", "items": {"type": "string"}, "description": "Group members (for address-group type)"},
                "description": {"type": "string", "description": "Object description"},
            },
            "required": ["device_id", "name", "object_type"],
        },
    },
    {
        "name": "create_service_object",
        "description": "Create a firewall service object on a device. Specify protocol/port.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "The device ID"},
                "name": {"type": "string", "description": "Service name"},
                "object_type": {"type": "string", "enum": ["service", "service-group"], "description": "Object type"},
                "value": {"type": "string", "description": "Protocol/port (e.g. 'tcp/443', '80')"},
                "members": {"type": "array", "items": {"type": "string"}, "description": "Group members (for service-group type)"},
                "description": {"type": "string", "description": "Service description"},
            },
            "required": ["device_id", "name", "object_type"],
        },
    },
    {
        "name": "create_firewall_rule",
        "description": "Create a new firewall rule on a device. Generates vendor-specific commands and deploys them. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "The device ID"},
                "name": {"type": "string", "description": "Rule name"},
                "source_zone": {"type": "string", "description": "Source zone/interface"},
                "dest_zone": {"type": "string", "description": "Destination zone/interface"},
                "source_addresses": {"type": "array", "items": {"type": "string"}, "description": "Source addresses"},
                "dest_addresses": {"type": "array", "items": {"type": "string"}, "description": "Destination addresses"},
                "services": {"type": "array", "items": {"type": "string"}, "description": "Services/ports"},
                "action": {"type": "string", "enum": ["allow", "deny", "reject", "drop"], "description": "Rule action"},
                "log_enabled": {"type": "boolean", "description": "Enable logging"},
                "comment": {"type": "string", "description": "Rule comment"},
            },
            "required": ["device_id", "name", "action"],
        },
    },
    {
        "name": "delete_firewall_rule",
        "description": "Delete a firewall rule from a device. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "The device ID"},
                "rule_id": {"type": "string", "description": "The rule ID to delete"},
            },
            "required": ["device_id", "rule_id"],
        },
    },
    {
        "name": "modify_firewall_rule",
        "description": "Modify an existing firewall rule on a device. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "The device ID"},
                "rule_id": {"type": "string", "description": "The rule ID to modify"},
                "changes": {"type": "object", "description": "Fields to change (e.g. action, source_addresses, services)"},
            },
            "required": ["device_id", "rule_id", "changes"],
        },
    },
    {
        "name": "create_nat_rule",
        "description": "Create a NAT rule on a device. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "The device ID"},
                "name": {"type": "string", "description": "NAT rule name"},
                "nat_type": {"type": "string", "enum": ["source", "destination", "static", "hide"], "description": "NAT type"},
                "source_zone": {"type": "string", "description": "Source zone"},
                "dest_zone": {"type": "string", "description": "Destination zone"},
                "original_source": {"type": "string", "description": "Original source address"},
                "original_dest": {"type": "string", "description": "Original destination address"},
                "original_service": {"type": "string", "description": "Original service/port"},
                "translated_source": {"type": "string", "description": "Translated source address"},
                "translated_dest": {"type": "string", "description": "Translated destination address"},
                "translated_service": {"type": "string", "description": "Translated service/port"},
            },
            "required": ["device_id", "name", "nat_type"],
        },
    },
    # ---- ACL Management tools ----
    {
        "name": "list_acls",
        "description": "List all access control lists (ACLs) on a device with entry counts and bindings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "sync_acls",
        "description": "Sync ACLs from a live device to the database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "create_acl",
        "description": "Create a new ACL on a device. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "name": {"type": "string", "description": "ACL name"},
                "acl_type": {"type": "string", "enum": ["standard", "extended"], "default": "extended"},
                "description": {"type": "string", "description": "ACL description"},
            },
            "required": ["device_id", "name"],
        },
    },
    {
        "name": "delete_acl",
        "description": "Delete an ACL from a device. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "acl_name": {"type": "string", "description": "ACL name to delete"},
            },
            "required": ["device_id", "acl_name"],
        },
    },
    {
        "name": "add_acl_entry",
        "description": "Add an entry (ACE) to an ACL. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "acl_name": {"type": "string", "description": "ACL name"},
                "sequence": {"type": "integer", "description": "Sequence number"},
                "action": {"type": "string", "enum": ["permit", "deny"], "description": "permit or deny"},
                "protocol": {"type": "string", "description": "Protocol (ip, tcp, udp, icmp)", "default": "ip"},
                "source": {"type": "string", "description": "Source address (default: any)", "default": "any"},
                "destination": {"type": "string", "description": "Destination address (default: any)", "default": "any"},
                "source_wildcard": {"type": "string", "description": "Source wildcard mask"},
                "dest_wildcard": {"type": "string", "description": "Destination wildcard mask"},
                "dest_port": {"type": "string", "description": "Destination port or port name"},
                "log_enabled": {"type": "boolean", "description": "Enable logging", "default": False},
            },
            "required": ["device_id", "acl_name", "sequence", "action"],
        },
    },
    {
        "name": "remove_acl_entry",
        "description": "Remove an entry from an ACL by sequence number. DESTRUCTIVE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "acl_name": {"type": "string", "description": "ACL name"},
                "sequence": {"type": "integer", "description": "Sequence number to remove"},
            },
            "required": ["device_id", "acl_name", "sequence"],
        },
    },
    {
        "name": "bind_acl",
        "description": "Apply an ACL to an interface. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "acl_name": {"type": "string", "description": "ACL name"},
                "interface": {"type": "string", "description": "Interface name"},
                "direction": {"type": "string", "enum": ["in", "out"], "description": "Direction (in/out)", "default": "in"},
            },
            "required": ["device_id", "acl_name", "interface"],
        },
    },
    {
        "name": "unbind_acl",
        "description": "Remove an ACL from an interface. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "acl_name": {"type": "string", "description": "ACL name"},
                "interface": {"type": "string", "description": "Interface name"},
                "direction": {"type": "string", "enum": ["in", "out"], "description": "Direction (in/out)", "default": "in"},
            },
            "required": ["device_id", "acl_name", "interface"],
        },
    },
    # ---- Routing Management tools ----
    {
        "name": "get_routing_table",
        "description": "Get the routing table for a device. Optionally filter by protocol (static, ospf, connected, bgp).",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "protocol": {"type": "string", "description": "Filter by protocol (static, ospf, connected, bgp)"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "sync_routes",
        "description": "Sync the routing table from a live device to the database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID to sync routes from"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "create_static_route",
        "description": "Create a static route on a device. DESTRUCTIVE: modifies device routing table.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "destination": {"type": "string", "description": "Destination network (e.g. 10.0.0.0)"},
                "prefix_length": {"type": "integer", "description": "Prefix length (e.g. 24)"},
                "next_hop": {"type": "string", "description": "Next-hop IP address"},
                "metric": {"type": "integer", "description": "Route metric (default 0)", "default": 0},
                "vrf": {"type": "string", "description": "VRF name (optional)"},
            },
            "required": ["device_id", "destination", "prefix_length", "next_hop"],
        },
    },
    {
        "name": "delete_static_route",
        "description": "Delete a static route from a device. DESTRUCTIVE: modifies device routing table.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "destination": {"type": "string", "description": "Destination network"},
                "prefix_length": {"type": "integer", "description": "Prefix length"},
                "next_hop": {"type": "string", "description": "Next-hop IP address"},
            },
            "required": ["device_id", "destination", "prefix_length", "next_hop"],
        },
    },
    {
        "name": "get_ospf_status",
        "description": "Get OSPF configuration, neighbors, and areas for a device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "sync_ospf",
        "description": "Sync OSPF neighbors and config from a live device to the database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "configure_ospf",
        "description": "Configure OSPF on a device with process ID, router ID, and networks. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "process_id": {"type": "integer", "description": "OSPF process ID (default 1)", "default": 1},
                "router_id": {"type": "string", "description": "Router ID (e.g. 1.1.1.1)"},
                "networks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "network": {"type": "string"},
                            "wildcard": {"type": "string"},
                            "area": {"type": "string"},
                            "interface": {"type": "string"},
                        },
                    },
                    "description": "Networks to advertise [{network, wildcard, area}]",
                },
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "add_ospf_network",
        "description": "Add a network to OSPF advertisement. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "network": {"type": "string", "description": "Network address (e.g. 10.0.0.0)"},
                "wildcard": {"type": "string", "description": "Wildcard mask (e.g. 0.0.0.255)"},
                "area": {"type": "string", "description": "OSPF area (default 0)", "default": "0"},
            },
            "required": ["device_id", "network", "wildcard"],
        },
    },
    # ---- VLAN Management tools ----
    {
        "name": "list_vlans",
        "description": "List all VLANs configured on a network device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID to list VLANs for"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_vlan_summary",
        "description": "Get a summary of VLANs and their interface assignments on a device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "sync_vlans",
        "description": "Sync VLANs from a live device to the database. Connects to the device and pulls current VLAN configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID to sync VLANs from"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "create_vlan",
        "description": "Create a new VLAN on a network device. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "vlan_id": {"type": "integer", "description": "VLAN number (1-4094)"},
                "name": {"type": "string", "description": "VLAN name (e.g. MGMT, DATA, VOICE)"},
                "description": {"type": "string", "description": "VLAN description"},
            },
            "required": ["device_id", "vlan_id"],
        },
    },
    {
        "name": "delete_vlan",
        "description": "Delete a VLAN from a network device. DESTRUCTIVE: modifies device configuration and removes interface assignments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "vlan_id": {"type": "integer", "description": "VLAN number to delete"},
            },
            "required": ["device_id", "vlan_id"],
        },
    },
    {
        "name": "assign_vlan_interface",
        "description": "Assign a switch port or interface to a VLAN. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "vlan_id": {"type": "integer", "description": "VLAN number"},
                "interface": {"type": "string", "description": "Interface name (e.g. GigabitEthernet0/1, ge-0/0/1)"},
                "mode": {
                    "type": "string",
                    "enum": ["access", "trunk", "tagged", "untagged"],
                    "description": "Port mode (default: access)",
                    "default": "access",
                },
            },
            "required": ["device_id", "vlan_id", "interface"],
        },
    },
    # ---- Serial Console Management ----------------------------------
    {
        "name": "list_serial_ports",
        "description": "List all available serial (COM/tty) ports on the agent host machine. No device connection required.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "serial_connect",
        "description": "Open a serial console session to a managed device. The device must have serial_port set in its metadata.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID to connect to via serial console"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "serial_send_command",
        "description": "Send a CLI command to a device via serial console and return the output. Requires an active serial session (use serial_connect first). DESTRUCTIVE: executes arbitrary CLI commands.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "command": {"type": "string", "description": "CLI command to execute (e.g. 'show version', 'show running-config')"},
                "timeout": {"type": "integer", "description": "Command timeout in seconds (default 30)", "default": 30},
            },
            "required": ["device_id", "command"],
        },
    },
    {
        "name": "serial_send_config",
        "description": "Send configuration commands to a device via serial console. Automatically enters and exits config mode. DESTRUCTIVE: modifies device configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of configuration commands to send",
                },
            },
            "required": ["device_id", "commands"],
        },
    },
    {
        "name": "serial_send_break",
        "description": "Send a serial break signal to a device. Used for password recovery on Cisco devices (interrupts boot process). DESTRUCTIVE: can interrupt device operation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
                "duration": {"type": "number", "description": "Break duration in seconds (default 0.5)", "default": 0.5},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "serial_get_facts",
        "description": "Get device facts (hostname, model, version, serial number, uptime) via an active serial console session.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "serial_disconnect",
        "description": "Close an active serial console session for a device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID"},
            },
            "required": ["device_id"],
        },
    },
]
