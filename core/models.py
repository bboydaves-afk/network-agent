"""Pydantic v2 data models for Network Agent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class DeviceStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNREACHABLE = "unreachable"
    MAINTENANCE = "maintenance"


class DeviceProtocol(str, Enum):
    SSH = "ssh"
    TELNET = "telnet"
    SNMP = "snmp"
    NETCONF = "netconf"
    RESTCONF = "restconf"
    API = "api"
    SERIAL = "serial"


class BackupType(str, Enum):
    RUNNING = "running"
    STARTUP = "startup"
    FULL = "full"


class DeployStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class AlertStatus(str, Enum):
    FIRING = "firing"
    RESOLVED = "resolved"
    ACKNOWLEDGED = "acknowledged"


class AlertCondition(str, Enum):
    GREATER_THAN = "gt"
    LESS_THAN = "lt"
    EQUAL = "eq"
    NOT_EQUAL = "ne"
    GREATER_EQUAL = "gte"
    LESS_EQUAL = "lte"


class ChangeRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"


class ChangeRequestPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class CredentialRotationStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class IPAddressStatus(str, Enum):
    ACTIVE = "active"
    RESERVED = "reserved"
    INACTIVE = "inactive"
    DHCP = "dhcp"


class SyslogSeverity(int, Enum):
    EMERGENCY = 0
    ALERT = 1
    CRITICAL = 2
    ERROR = 3
    WARNING = 4
    NOTICE = 5
    INFO = 6
    DEBUG = 7


class ChatRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


# ---------------------------------------------------------------------------
# Device models
# ---------------------------------------------------------------------------

class Device(BaseModel):
    """Full device representation stored in the database."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str = Field(default_factory=_new_id, description="Unique device identifier")
    hostname: str = Field(..., min_length=1, max_length=255, description="Device hostname")
    ip_address: str = Field(..., description="Management IP address")
    device_type: str = Field(..., description="Device type key, e.g. 'cisco_ios'")
    protocol: DeviceProtocol = Field(default=DeviceProtocol.SSH, description="Connection protocol")
    port: int = Field(default=22, ge=1, le=65535, description="Connection port")
    credential_id: Optional[str] = Field(default=None, description="FK to stored credential")
    location: Optional[str] = Field(default=None, max_length=255, description="Physical location")
    model: Optional[str] = Field(default=None, max_length=255, description="Hardware model")
    serial_number: Optional[str] = Field(default=None, max_length=255, description="Serial number")
    os_version: Optional[str] = Field(default=None, max_length=255, description="OS/firmware version")
    status: DeviceStatus = Field(default=DeviceStatus.ACTIVE, description="Current status")
    last_seen: Optional[datetime] = Field(default=None, description="Last successful contact")
    created_at: datetime = Field(default_factory=_utcnow, description="Record creation time")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary key-value metadata")
    site_id: Optional[str] = Field(default=None, description="FK to site")

    @field_validator("ip_address")
    @classmethod
    def validate_ip_address(cls, v: str) -> str:
        parts = v.strip().split(".")
        if len(parts) == 4:
            try:
                if all(0 <= int(p) <= 255 for p in parts):
                    return v.strip()
            except ValueError:
                pass
        # Allow hostnames / IPv6 as well
        if ":" in v or v.replace("-", "").replace(".", "").isalnum():
            return v.strip()
        raise ValueError(f"Invalid IP address or hostname: {v!r}")


class DeviceCreate(BaseModel):
    """Payload for creating a new device."""

    model_config = ConfigDict(use_enum_values=True)

    hostname: str = Field(..., min_length=1, max_length=255)
    ip_address: str = Field(...)
    device_type: str = Field(...)
    protocol: DeviceProtocol = Field(default=DeviceProtocol.SSH)
    port: int = Field(default=22, ge=1, le=65535)
    credential_id: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None, max_length=255)
    tags: list[str] = Field(default_factory=list)
    site_id: Optional[str] = Field(default=None, description="FK to site")

    @field_validator("ip_address")
    @classmethod
    def validate_ip_address(cls, v: str) -> str:
        parts = v.strip().split(".")
        if len(parts) == 4:
            try:
                if all(0 <= int(p) <= 255 for p in parts):
                    return v.strip()
            except ValueError:
                pass
        if ":" in v or v.replace("-", "").replace(".", "").isalnum():
            return v.strip()
        raise ValueError(f"Invalid IP address or hostname: {v!r}")


class DeviceUpdate(BaseModel):
    """Payload for updating a device. All fields optional."""

    model_config = ConfigDict(use_enum_values=True)

    hostname: Optional[str] = Field(default=None, min_length=1, max_length=255)
    ip_address: Optional[str] = Field(default=None)
    device_type: Optional[str] = Field(default=None)
    protocol: Optional[DeviceProtocol] = Field(default=None)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    credential_id: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None, max_length=255)
    model: Optional[str] = Field(default=None, max_length=255)
    serial_number: Optional[str] = Field(default=None, max_length=255)
    os_version: Optional[str] = Field(default=None, max_length=255)
    status: Optional[DeviceStatus] = Field(default=None)
    metadata: Optional[dict[str, Any]] = Field(default=None)
    site_id: Optional[str] = Field(default=None, description="FK to site")


# ---------------------------------------------------------------------------
# Credential models
# ---------------------------------------------------------------------------

class Credential(BaseModel):
    """Stored credential (password fields may be encrypted ciphertext)."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(default_factory=_new_id, description="Unique credential identifier")
    name: str = Field(..., min_length=1, max_length=255, description="Friendly name")
    username: str = Field(..., min_length=1, max_length=255)
    password: Optional[str] = Field(default=None, description="Encrypted password")
    ssh_key_path: Optional[str] = Field(default=None, description="Path to SSH private key")
    snmp_community: Optional[str] = Field(default=None, description="Encrypted SNMP community")
    enable_secret: Optional[str] = Field(default=None, description="Encrypted enable secret")
    created_at: datetime = Field(default_factory=_utcnow)


class CredentialCreate(BaseModel):
    """Payload for creating a new credential."""

    name: str = Field(..., min_length=1, max_length=255)
    username: str = Field(..., min_length=1, max_length=255)
    password: Optional[str] = Field(default=None)
    ssh_key_path: Optional[str] = Field(default=None)
    snmp_community: Optional[str] = Field(default=None)
    enable_secret: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# Configuration backup / deploy models
# ---------------------------------------------------------------------------

class ConfigBackup(BaseModel):
    """A stored configuration backup."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str = Field(default_factory=_new_id)
    device_id: str = Field(...)
    config_text: str = Field(...)
    config_hash: str = Field(...)
    backup_type: BackupType = Field(default=BackupType.RUNNING)
    file_path: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class ConfigDeploy(BaseModel):
    """Record of a configuration deployment."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str = Field(default_factory=_new_id)
    device_id: str = Field(...)
    config_diff: str = Field(...)
    status: DeployStatus = Field(default=DeployStatus.PENDING)
    applied_by: Optional[str] = Field(default=None)
    applied_at: Optional[datetime] = Field(default=None)
    rollback_to: Optional[str] = Field(default=None, description="Config backup ID to rollback to")


# ---------------------------------------------------------------------------
# Monitoring models
# ---------------------------------------------------------------------------

class Metric(BaseModel):
    """A single time-series metric data point."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(default_factory=_new_id)
    device_id: str = Field(...)
    metric_name: str = Field(..., min_length=1, max_length=255)
    metric_value: float = Field(...)
    interface: Optional[str] = Field(default=None, max_length=255)
    timestamp: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Alert models
# ---------------------------------------------------------------------------

class AlertRule(BaseModel):
    """Definition of an alert rule."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str = Field(default_factory=_new_id)
    name: str = Field(..., min_length=1, max_length=255)
    device_filter: Optional[str] = Field(default=None, description="Glob or regex to match device IDs")
    metric_name: str = Field(...)
    condition: AlertCondition = Field(...)
    threshold: float = Field(...)
    duration_seconds: int = Field(default=0, ge=0, description="Sustained duration before firing")
    channel: str = Field(default="slack", description="Notification channel name")
    enabled: bool = Field(default=True)


class AlertRuleCreate(BaseModel):
    """Payload for creating a new alert rule."""

    model_config = ConfigDict(use_enum_values=True)

    name: str = Field(..., min_length=1, max_length=255)
    device_filter: Optional[str] = Field(default=None)
    metric_name: str = Field(...)
    condition: AlertCondition = Field(...)
    threshold: float = Field(...)
    duration_seconds: int = Field(default=0, ge=0)
    channel: str = Field(default="slack")
    enabled: bool = Field(default=True)


class Alert(BaseModel):
    """An alert instance that has fired."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: str = Field(default_factory=_new_id)
    rule_id: str = Field(...)
    device_id: str = Field(...)
    metric_value: float = Field(...)
    message: str = Field(...)
    status: AlertStatus = Field(default=AlertStatus.FIRING)
    fired_at: datetime = Field(default_factory=_utcnow)
    resolved_at: Optional[datetime] = Field(default=None)


# ---------------------------------------------------------------------------
# Device health / facts models
# ---------------------------------------------------------------------------

class DeviceHealth(BaseModel):
    """Snapshot of device health metrics."""

    cpu_percent: float = Field(..., ge=0, le=100)
    memory_percent: float = Field(..., ge=0, le=100)
    uptime_seconds: int = Field(..., ge=0)
    temperature: Optional[float] = Field(default=None, description="Temperature in Celsius")


class InterfaceInfo(BaseModel):
    """Information about a single network interface."""

    name: str = Field(...)
    status: str = Field(default="unknown", description="up / down / admin_down")
    speed: Optional[str] = Field(default=None, description="e.g. '1Gbps'")
    mtu: Optional[int] = Field(default=None, ge=0)
    ip_address: Optional[str] = Field(default=None)
    mac_address: Optional[str] = Field(default=None)
    in_octets: int = Field(default=0, ge=0)
    out_octets: int = Field(default=0, ge=0)
    in_errors: int = Field(default=0, ge=0)
    out_errors: int = Field(default=0, ge=0)


class DeviceFacts(BaseModel):
    """Collected facts about a device."""

    hostname: str = Field(...)
    vendor: Optional[str] = Field(default=None)
    model: Optional[str] = Field(default=None)
    serial_number: Optional[str] = Field(default=None)
    os_version: Optional[str] = Field(default=None)
    uptime: Optional[str] = Field(default=None, description="Human-readable uptime string")


# ---------------------------------------------------------------------------
# Site models
# ---------------------------------------------------------------------------

class Site(BaseModel):
    """A physical or logical site grouping devices."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(default_factory=_new_id)
    name: str = Field(..., min_length=1, max_length=255)
    location: Optional[str] = Field(default=None, max_length=500)
    region: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    contact: Optional[str] = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=_utcnow)


class SiteCreate(BaseModel):
    """Payload for creating a new site."""

    name: str = Field(..., min_length=1, max_length=255)
    location: Optional[str] = Field(default=None, max_length=500)
    region: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    contact: Optional[str] = Field(default=None, max_length=255)


class SiteUpdate(BaseModel):
    """Payload for updating a site."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    location: Optional[str] = Field(default=None, max_length=500)
    region: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    contact: Optional[str] = Field(default=None, max_length=255)


# ---------------------------------------------------------------------------
# Topology models
# ---------------------------------------------------------------------------

class TopologyNeighbor(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=_new_id)
    device_id: str = Field(...)
    local_interface: str = Field(...)
    neighbor_device_id: Optional[str] = Field(default=None)
    neighbor_hostname: Optional[str] = Field(default=None)
    neighbor_ip: Optional[str] = Field(default=None)
    neighbor_port: Optional[str] = Field(default=None)
    neighbor_platform: Optional[str] = Field(default=None)
    protocol: str = Field(default="cdp")
    discovered_at: datetime = Field(default_factory=_utcnow)


class TopologySnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=_new_id)
    name: Optional[str] = Field(default=None)
    snapshot_data: str = Field(...)
    device_count: int = Field(default=0)
    link_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Firmware models
# ---------------------------------------------------------------------------

class FirmwareCatalogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=_new_id)
    vendor: str = Field(...)
    model_pattern: Optional[str] = Field(default=None)
    version: str = Field(...)
    release_date: Optional[str] = Field(default=None)
    eol_date: Optional[str] = Field(default=None)
    eos_date: Optional[str] = Field(default=None)
    cve_list: list[str] = Field(default_factory=list)
    download_url: Optional[str] = Field(default=None)
    is_recommended: bool = Field(default=False)
    notes: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class FirmwareCatalogCreate(BaseModel):
    vendor: str = Field(...)
    model_pattern: Optional[str] = Field(default=None)
    version: str = Field(...)
    release_date: Optional[str] = Field(default=None)
    eol_date: Optional[str] = Field(default=None)
    eos_date: Optional[str] = Field(default=None)
    cve_list: list[str] = Field(default_factory=list)
    download_url: Optional[str] = Field(default=None)
    is_recommended: bool = Field(default=False)
    notes: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# IPAM models
# ---------------------------------------------------------------------------

class Subnet(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=_new_id)
    network: str = Field(...)
    prefix_length: int = Field(...)
    vlan_id: Optional[int] = Field(default=None)
    name: Optional[str] = Field(default=None)
    site_id: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    gateway: Optional[str] = Field(default=None)
    dns_servers: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


class SubnetCreate(BaseModel):
    network: str = Field(...)
    prefix_length: int = Field(...)
    vlan_id: Optional[int] = Field(default=None)
    name: Optional[str] = Field(default=None)
    site_id: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    gateway: Optional[str] = Field(default=None)
    dns_servers: list[str] = Field(default_factory=list)


class IPAddress(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)
    id: str = Field(default_factory=_new_id)
    subnet_id: str = Field(...)
    address: str = Field(...)
    hostname: Optional[str] = Field(default=None)
    mac_address: Optional[str] = Field(default=None)
    device_id: Optional[str] = Field(default=None)
    interface: Optional[str] = Field(default=None)
    status: IPAddressStatus = Field(default=IPAddressStatus.ACTIVE)
    last_seen: Optional[datetime] = Field(default=None)
    notes: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# Syslog models
# ---------------------------------------------------------------------------

class SyslogMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=_new_id)
    device_id: Optional[str] = Field(default=None)
    timestamp: datetime = Field(default_factory=_utcnow)
    facility: Optional[int] = Field(default=None)
    severity: Optional[int] = Field(default=None)
    hostname: Optional[str] = Field(default=None)
    app_name: Optional[str] = Field(default=None)
    message: str = Field(...)
    raw: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Compliance models
# ---------------------------------------------------------------------------

class ComplianceResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=_new_id)
    device_id: str = Field(...)
    ruleset_name: str = Field(...)
    total_checks: int = Field(default=0)
    passed: int = Field(default=0)
    failed: int = Field(default=0)
    score: float = Field(default=0.0)
    details: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Change Management models
# ---------------------------------------------------------------------------

class ChangeRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)
    id: str = Field(default_factory=_new_id)
    device_id: str = Field(...)
    title: str = Field(...)
    config_commands: str = Field(...)
    config_diff: Optional[str] = Field(default=None)
    requested_by: str = Field(...)
    approved_by: Optional[str] = Field(default=None)
    status: ChangeRequestStatus = Field(default=ChangeRequestStatus.PENDING)
    priority: ChangeRequestPriority = Field(default=ChangeRequestPriority.NORMAL)
    notes: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    approved_at: Optional[datetime] = Field(default=None)
    applied_at: Optional[datetime] = Field(default=None)
    rejected_at: Optional[datetime] = Field(default=None)
    deploy_id: Optional[str] = Field(default=None)


class ChangeRequestCreate(BaseModel):
    device_id: str = Field(...)
    title: str = Field(...)
    config_commands: str = Field(...)
    requested_by: str = Field(...)
    priority: str = Field(default="normal")
    notes: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# Credential Rotation models
# ---------------------------------------------------------------------------

class CredentialRotation(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)
    id: str = Field(default_factory=_new_id)
    credential_id: str = Field(...)
    old_password_hash: Optional[str] = Field(default=None)
    new_password_hash: Optional[str] = Field(default=None)
    status: CredentialRotationStatus = Field(default=CredentialRotationStatus.PENDING)
    devices_total: int = Field(default=0)
    devices_updated: int = Field(default=0)
    devices_failed: int = Field(default=0)
    failure_details: list[dict] = Field(default_factory=list)
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    initiated_by: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# Chat / AI agent models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    """A single message in an AI chat session."""

    role: ChatRole = Field(...)
    content: str = Field(..., min_length=1)
    timestamp: datetime = Field(default_factory=_utcnow)


class ChatResponse(BaseModel):
    """Response from the AI agent."""

    message: str = Field(...)
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Tool calls the agent wants to execute",
    )
    confirmation_required: bool = Field(
        default=False,
        description="Whether the user must confirm before execution",
    )



# ---------------------------------------------------------------------------
# Firewall enumerations
# ---------------------------------------------------------------------------

class FirewallAction(str, Enum):
    allow = "allow"
    deny = "deny"
    reject = "reject"
    drop = "drop"


class NatType(str, Enum):
    source = "source"
    destination = "destination"
    static = "static"
    hide = "hide"


# ---------------------------------------------------------------------------
# Firewall models
# ---------------------------------------------------------------------------

class FirewallRule(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)
    id: str = Field(default_factory=_new_id)
    device_id: str
    policy_id: str | None = None
    name: str
    source_zone: str | None = None
    dest_zone: str | None = None
    source_addresses: list[str] = Field(default_factory=list)
    dest_addresses: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    action: FirewallAction = FirewallAction.deny
    enabled: bool = True
    log_enabled: bool = False
    position: int = 0
    comment: str | None = None
    synced_at: str | None = None
    created_at: str = Field(default_factory=_utcnow)


class FirewallRuleCreate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    name: str
    source_zone: str | None = None
    dest_zone: str | None = None
    source_addresses: list[str] = Field(default_factory=list)
    dest_addresses: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    action: FirewallAction = FirewallAction.deny
    enabled: bool = True
    log_enabled: bool = False
    position: int = 0
    comment: str | None = None


class NatRule(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)
    id: str = Field(default_factory=_new_id)
    device_id: str
    name: str
    nat_type: NatType
    source_zone: str | None = None
    dest_zone: str | None = None
    original_source: str | None = None
    original_dest: str | None = None
    original_service: str | None = None
    translated_source: str | None = None
    translated_dest: str | None = None
    translated_service: str | None = None
    enabled: bool = True
    comment: str | None = None
    synced_at: str | None = None
    created_at: str = Field(default_factory=_utcnow)


class NatRuleCreate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    name: str
    nat_type: NatType
    source_zone: str | None = None
    dest_zone: str | None = None
    original_source: str | None = None
    original_dest: str | None = None
    original_service: str | None = None
    translated_source: str | None = None
    translated_dest: str | None = None
    translated_service: str | None = None
    enabled: bool = True
    comment: str | None = None


class FirewallZone(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=_new_id)
    device_id: str
    name: str
    interfaces: list[str] = Field(default_factory=list)
    security_level: int = 0
    description: str | None = None
    synced_at: str | None = None


class FirewallZoneCreate(BaseModel):
    name: str
    interfaces: list[str] = Field(default_factory=list)
    security_level: int = 0
    description: str | None = None


class FirewallObject(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(default_factory=_new_id)
    device_id: str
    name: str
    object_type: str  # address, address-group, service, service-group
    value: str | None = None
    members: list[str] = Field(default_factory=list)
    description: str | None = None
    synced_at: str | None = None
    created_at: str = Field(default_factory=_utcnow)


class FirewallObjectCreate(BaseModel):
    name: str
    object_type: str
    value: str | None = None
    members: list[str] = Field(default_factory=list)
    description: str | None = None
