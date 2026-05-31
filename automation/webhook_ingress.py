"""FastAPI routes for accepting inbound webhook events from external systems.

Provides a ``/webhooks/ingest`` endpoint that:
1. Accepts JSON payloads from PRTG, Zabbix, Nagios, generic syslog forwarders, etc.
2. Normalizes them into a common ``WebhookEvent`` structure.
3. Logs the event to the audit trail.
4. Matches the event against webhook-triggered runbooks and executes them.

Usage::

    from automation.webhook_ingress import create_webhook_router

    router = create_webhook_router(automation_engine, audit_logger, db)
    app.include_router(router, prefix="/webhooks")
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Request, HTTPException, Header, Depends
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class WebhookEvent(BaseModel):
    """Normalized webhook event structure used internally."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    source: str = "unknown"
    event_type: str = "generic"
    severity: str = "info"
    device_ip: Optional[str] = None
    device_hostname: Optional[str] = None
    message: str = ""
    raw_data: Optional[dict] = None
    timestamp: Optional[str] = None
    tags: dict[str, str] = Field(default_factory=dict)


class WebhookResponse(BaseModel):
    """Standard response for webhook ingestion."""

    status: str = "accepted"
    event_id: str = ""
    message: str = ""
    runbooks_triggered: int = 0


# ---------------------------------------------------------------------------
# Source-specific parsers
# ---------------------------------------------------------------------------


def _parse_prtg_event(data: dict) -> WebhookEvent:
    """Parse a PRTG Network Monitor webhook payload.

    PRTG sends fields like ``host``, ``device``, ``sensor``, ``status``,
    ``message``, ``datetime``, ``linkdevice``, ``linksensor``.
    """
    return WebhookEvent(
        source="prtg",
        event_type=data.get("eventtype", data.get("sensortype", "sensor_alert")),
        severity=_map_prtg_severity(data.get("status", "")),
        device_ip=data.get("host", data.get("hostip", "")),
        device_hostname=data.get("device", data.get("devicename", "")),
        message=data.get("message", data.get("text", data.get("shortmessage", ""))),
        raw_data=data,
        timestamp=data.get("datetime", datetime.now(timezone.utc).isoformat()),
        tags=_extract_tags(data, prefix="prtg_"),
    )


def _parse_zabbix_event(data: dict) -> WebhookEvent:
    """Parse a Zabbix webhook payload.

    Zabbix media-type webhooks typically send ``host_name``, ``host_ip``,
    ``trigger_severity``, ``event_source``, ``subject``, ``message``.
    """
    return WebhookEvent(
        source="zabbix",
        event_type=data.get("event_source", data.get("trigger_name", "trigger")),
        severity=_map_zabbix_severity(
            data.get("severity", data.get("trigger_severity", ""))
        ),
        device_ip=data.get("host_ip", data.get("ip", "")),
        device_hostname=data.get("host_name", data.get("hostname", "")),
        message=data.get("subject", data.get("message", "")),
        raw_data=data,
        timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        tags=_extract_tags(data, prefix="zabbix_"),
    )


def _parse_nagios_event(data: dict) -> WebhookEvent:
    """Parse a Nagios / Icinga webhook payload.

    Common fields: ``host``, ``service``, ``state``, ``output``, ``type``.
    """
    state = data.get("state", data.get("host_state", "")).upper()
    severity_map = {
        "CRITICAL": "critical",
        "DOWN": "critical",
        "WARNING": "warning",
        "UNREACHABLE": "critical",
        "UNKNOWN": "warning",
        "UP": "info",
        "OK": "info",
    }
    return WebhookEvent(
        source="nagios",
        event_type=data.get("type", data.get("notification_type", "host_alert")),
        severity=severity_map.get(state, "info"),
        device_ip=data.get("host_address", data.get("ip", "")),
        device_hostname=data.get("host", data.get("hostname", "")),
        message=data.get("output", data.get("plugin_output", data.get("message", ""))),
        raw_data=data,
        timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        tags=_extract_tags(data, prefix="nagios_"),
    )


def _parse_syslog_event(data: dict) -> WebhookEvent:
    """Parse a forwarded syslog event (e.g. from rsyslog/syslog-ng HTTP output)."""
    facility_severity = data.get("severity", data.get("priority", "info"))
    if isinstance(facility_severity, int):
        # Syslog numeric severity: 0-2 = critical, 3-4 = warning, 5-7 = info
        if facility_severity <= 2:
            severity = "critical"
        elif facility_severity <= 4:
            severity = "warning"
        else:
            severity = "info"
    else:
        severity = str(facility_severity).lower()
        if severity not in ("critical", "warning", "info"):
            severity = "info"

    return WebhookEvent(
        source="syslog",
        event_type=data.get("facility", data.get("program", "syslog")),
        severity=severity,
        device_ip=data.get("fromhost-ip", data.get("source_ip", data.get("host", ""))),
        device_hostname=data.get(
            "fromhost", data.get("hostname", data.get("source", ""))
        ),
        message=data.get("msg", data.get("message", data.get("text", ""))),
        raw_data=data,
        timestamp=data.get("timestamp", data.get("timereported", datetime.now(timezone.utc).isoformat())),
    )


def _parse_snmp_trap_event(data: dict) -> WebhookEvent:
    """Parse an SNMP trap forwarded via webhook (e.g. from snmptrapd + script)."""
    oid = data.get("oid", data.get("trap_oid", ""))
    enterprise = data.get("enterprise", "")

    # Determine severity from trap type or specific OID patterns
    trap_type = data.get("trap_type", data.get("generic_trap", ""))
    if str(trap_type) in ("0", "linkDown", "coldStart"):
        severity = "critical"
    elif str(trap_type) in ("3", "linkUp", "warmStart"):
        severity = "warning"
    else:
        severity = "info"

    return WebhookEvent(
        source="snmp_trap",
        event_type=oid or str(trap_type) or "trap",
        severity=severity,
        device_ip=data.get("agent_ip", data.get("source_ip", data.get("host", ""))),
        device_hostname=data.get("hostname", data.get("agent_hostname", "")),
        message=data.get("message", data.get("varbinds", data.get("description", f"SNMP Trap: {oid}"))),
        raw_data=data,
        timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        tags={"oid": oid, "enterprise": enterprise},
    )


def _parse_generic_event(data: dict) -> WebhookEvent:
    """Parse a generic / unknown webhook payload using best-effort field extraction."""
    return WebhookEvent(
        source=data.get("source", "unknown"),
        event_type=data.get("event_type", data.get("type", "generic")),
        severity=_normalize_severity(
            data.get("severity", data.get("priority", data.get("level", "info")))
        ),
        device_ip=data.get(
            "device_ip",
            data.get("host", data.get("ip", data.get("host_ip", ""))),
        ),
        device_hostname=data.get(
            "device_hostname",
            data.get("hostname", data.get("device", data.get("host_name", ""))),
        ),
        message=data.get(
            "message",
            data.get("text", data.get("description", data.get("summary", ""))),
        ),
        raw_data=data,
        timestamp=data.get(
            "timestamp", data.get("time", datetime.now(timezone.utc).isoformat())
        ),
    )


# ---------------------------------------------------------------------------
# Severity mapping helpers
# ---------------------------------------------------------------------------


def _map_prtg_severity(status: str) -> str:
    """Map a PRTG status string to a normalized severity level."""
    s = status.lower()
    if any(keyword in s for keyword in ("down", "error", "critical", "failed")):
        return "critical"
    if any(keyword in s for keyword in ("warning", "unusual", "degraded")):
        return "warning"
    return "info"


def _map_zabbix_severity(sev: str) -> str:
    """Map a Zabbix severity string or numeric level to a normalized severity."""
    s = str(sev).lower().strip()
    if s in ("disaster", "high", "5", "4"):
        return "critical"
    if s in ("average", "warning", "3", "2"):
        return "warning"
    return "info"


def _normalize_severity(raw: Any) -> str:
    """Normalize an arbitrary severity value to critical/warning/info."""
    s = str(raw).lower().strip()
    if s in ("critical", "emergency", "alert", "fatal", "error", "high", "p1"):
        return "critical"
    if s in ("warning", "warn", "medium", "average", "p2"):
        return "warning"
    return "info"


def _extract_tags(data: dict, prefix: str = "") -> dict[str, str]:
    """Extract key-value tags from known metadata fields in the payload."""
    tags: dict[str, str] = {}
    tag_fields = ("tags", "labels", "annotations")
    for field in tag_fields:
        val = data.get(field)
        if isinstance(val, dict):
            for k, v in val.items():
                tags[f"{prefix}{k}"] = str(v)
        elif isinstance(val, str):
            # Comma-separated tags: "tag1,tag2,tag3"
            for tag in val.split(","):
                tag = tag.strip()
                if tag:
                    tags[f"{prefix}{tag}"] = "true"
    return tags


# ---------------------------------------------------------------------------
# Parser registry
# ---------------------------------------------------------------------------


PARSERS: dict[str, Any] = {
    "prtg": _parse_prtg_event,
    "zabbix": _parse_zabbix_event,
    "nagios": _parse_nagios_event,
    "icinga": _parse_nagios_event,  # Icinga uses the same format
    "syslog": _parse_syslog_event,
    "snmp_trap": _parse_snmp_trap_event,
    "snmptrap": _parse_snmp_trap_event,
}


# ---------------------------------------------------------------------------
# HMAC signature verification
# ---------------------------------------------------------------------------


def _verify_signature(
    body: bytes,
    signature: str | None,
    secret: str | None,
) -> bool:
    """Verify an HMAC-SHA256 signature if both a signature header and a
    shared secret are provided.

    Returns True if verification passes or if no verification is needed.
    """
    if not secret:
        # No secret configured; signature verification disabled
        return True
    if not signature:
        # Secret is configured but the request has no signature header
        return False

    # Support "sha256=<hex>" format (GitHub-style)
    if signature.startswith("sha256="):
        signature = signature[7:]

    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Webhook matcher
# ---------------------------------------------------------------------------


def _matches_webhook(runbook, event: WebhookEvent) -> bool:
    """Check whether a webhook event satisfies a runbook's webhook_match criteria.

    The ``webhook_match`` dict on a runbook may contain:
    - ``source``: exact match on event source
    - ``event_type``: exact match on event type
    - ``severity``: single string or list of accepted severities
    - ``device_hostname_pattern``: substring match on device hostname
    - ``message_pattern``: substring match on event message

    If ``webhook_match`` is empty or None, the runbook matches all webhook events.
    """
    match_criteria = getattr(runbook, "trigger_webhook_match", None)
    if not match_criteria:
        return True

    # Source filter
    if "source" in match_criteria:
        if match_criteria["source"].lower() != event.source.lower():
            return False

    # Event type filter
    if "event_type" in match_criteria:
        if match_criteria["event_type"].lower() != event.event_type.lower():
            return False

    # Severity filter (string or list)
    if "severity" in match_criteria:
        allowed = match_criteria["severity"]
        if isinstance(allowed, str):
            allowed = [allowed]
        allowed_lower = [s.lower() for s in allowed]
        if event.severity.lower() not in allowed_lower:
            return False

    # Hostname substring filter
    if "device_hostname_pattern" in match_criteria:
        pattern = match_criteria["device_hostname_pattern"].lower()
        hostname = (event.device_hostname or "").lower()
        if pattern not in hostname:
            return False

    # Message substring filter
    if "message_pattern" in match_criteria:
        pattern = match_criteria["message_pattern"].lower()
        message = (event.message or "").lower()
        if pattern not in message:
            return False

    return True


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_webhook_router(
    automation_engine,
    audit_logger,
    db,
    *,
    webhook_secret: str | None = None,
) -> APIRouter:
    """Create and return a FastAPI APIRouter with webhook ingestion endpoints.

    Parameters
    ----------
    automation_engine : AutomationEngine
        Used to list runbooks and execute matching webhook-triggered runbooks.
    audit_logger : AuditLogger
        Used to log all incoming webhook events.
    db : Database
        Used to look up devices in the inventory.
    webhook_secret : str, optional
        If set, all incoming requests must include a valid HMAC-SHA256
        signature in the ``X-Webhook-Signature`` header.

    Returns
    -------
    APIRouter
        Mount this on your FastAPI app, e.g.
        ``app.include_router(router, prefix="/webhooks")``.
    """

    webhook_router = APIRouter(tags=["webhooks"])

    @webhook_router.post("/ingest", response_model=WebhookResponse)
    async def ingest_event(
        request: Request,
        x_source: Optional[str] = Header(None, alias="X-Webhook-Source"),
        x_signature: Optional[str] = Header(None, alias="X-Webhook-Signature"),
    ) -> WebhookResponse:
        """Accept an inbound webhook event from an external monitoring system.

        The source can be specified via (in priority order):
        1. ``X-Webhook-Source`` header
        2. ``source`` field in the JSON body
        3. ``?source=`` query parameter

        Known sources (prtg, zabbix, nagios, syslog, snmp_trap) receive
        specialized parsing.  Unknown sources use generic field extraction.
        """
        # --- Read and validate body ---
        raw_body = await request.body()
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail="Request body must be a JSON object",
            )

        # --- Signature verification ---
        if not _verify_signature(raw_body, x_signature, webhook_secret):
            logger.warning(
                "Webhook signature verification failed (source=%s, ip=%s)",
                x_source or "unknown",
                request.client.host if request.client else "unknown",
            )
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

        # --- Determine source ---
        source = (
            x_source
            or body.get("source")
            or request.query_params.get("source")
            or "unknown"
        ).lower().strip()

        # --- Parse event ---
        parser = PARSERS.get(source, _parse_generic_event)
        try:
            event = parser(body)
        except Exception as parse_err:
            logger.error(
                "Failed to parse webhook event from %s: %s", source, parse_err
            )
            event = _parse_generic_event(body)

        # Ensure the event has a timestamp
        if not event.timestamp:
            event.timestamp = datetime.now(timezone.utc).isoformat()

        # --- Audit log ---
        event_id = event.id
        try:
            audit_result = await audit_logger.log(
                actor=f"webhook:{source}",
                action_type="webhook_received",
                description=f"Webhook from {source}: {event.message[:200]}" if event.message else f"Webhook from {source}",
                device_id=event.device_ip or event.device_hostname or None,
                details={
                    "event_id": event_id,
                    "source": event.source,
                    "event_type": event.event_type,
                    "severity": event.severity,
                    "device_ip": event.device_ip,
                    "device_hostname": event.device_hostname,
                    "message": event.message[:500] if event.message else "",
                    "tags": event.tags,
                },
            )
            # If audit_logger.log returns an ID, use it
            if audit_result and isinstance(audit_result, str):
                event_id = audit_result
        except Exception as audit_err:
            logger.error("Failed to audit-log webhook event: %s", audit_err)

        # --- Device resolution ---
        device_id: str | None = None
        if event.device_ip or event.device_hostname:
            try:
                devices = await db.list_devices()
                for d in devices:
                    ip_match = (
                        event.device_ip
                        and d.get("ip_address") == event.device_ip
                    )
                    hostname_match = (
                        event.device_hostname
                        and d.get("hostname", "").lower()
                        == event.device_hostname.lower()
                    )
                    if ip_match or hostname_match:
                        device_id = d.get("id")
                        break
            except Exception as lookup_err:
                logger.debug("Device lookup failed: %s", lookup_err)

        # --- Build runbook execution context ---
        context: dict[str, Any] = {
            "trigger": "webhook",
            "trigger_type": "webhook",
            "event_id": event_id,
            "source": event.source,
            "event_type": event.event_type,
            "severity": event.severity,
            "device_id": device_id or event.device_ip or "",
            "device_ip": event.device_ip or "",
            "device_hostname": event.device_hostname or "",
            "message": event.message,
            "raw_data": event.raw_data,
            "timestamp": event.timestamp,
            "tags": event.tags,
        }

        # --- Match and execute webhook-triggered runbooks ---
        matched = 0
        errors: list[str] = []

        for runbook in automation_engine.list_runbooks():
            if not (runbook.trigger_type == "webhook" and runbook.enabled):
                continue
            if not _matches_webhook(runbook, event):
                continue

            try:
                await automation_engine.execute_runbook(
                    runbook.name,
                    context=context,
                )
                matched += 1
                logger.info(
                    "Webhook event triggered runbook '%s' (source=%s, event_type=%s)",
                    runbook.name,
                    event.source,
                    event.event_type,
                )
            except Exception as exec_err:
                errors.append(f"{runbook.name}: {exec_err}")
                logger.error(
                    "Webhook runbook '%s' failed: %s", runbook.name, exec_err
                )

        # Build response message
        parts: list[str] = [f"Event processed from {source}."]
        if matched:
            parts.append(f"{matched} runbook(s) triggered.")
        if errors:
            parts.append(f"{len(errors)} runbook(s) failed.")

        return WebhookResponse(
            status="accepted",
            event_id=event_id,
            message=" ".join(parts),
            runbooks_triggered=matched,
        )

    @webhook_router.post("/ingest/{source}", response_model=WebhookResponse)
    async def ingest_event_with_source(
        request: Request,
        source: str,
        x_signature: Optional[str] = Header(None, alias="X-Webhook-Signature"),
    ) -> WebhookResponse:
        """Accept an inbound webhook with the source specified in the URL path.

        Equivalent to ``POST /ingest`` with ``X-Webhook-Source: <source>`` header.
        Example: ``POST /webhooks/ingest/prtg``
        """
        # Reuse the main ingest endpoint by injecting the source header
        return await ingest_event(
            request,
            x_source=source.lower(),
            x_signature=x_signature,
        )

    @webhook_router.post("/test")
    async def test_webhook() -> dict[str, str]:
        """Health-check endpoint to verify webhook ingress is operational."""
        return {
            "status": "ok",
            "message": "Webhook ingress is operational",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @webhook_router.get("/sources")
    async def list_sources() -> dict[str, Any]:
        """List all known webhook source parsers and their expected fields."""
        source_info: dict[str, dict[str, Any]] = {}
        for name in PARSERS:
            source_info[name] = {
                "description": (PARSERS[name].__doc__ or "").strip().split("\n")[0],
                "supported": True,
            }
        source_info["generic"] = {
            "description": "Generic/unknown webhook payload with best-effort field extraction",
            "supported": True,
        }
        return {
            "sources": source_info,
            "total": len(source_info),
            "signature_required": webhook_secret is not None,
        }

    @webhook_router.get("/runbooks")
    async def list_webhook_runbooks() -> dict[str, Any]:
        """List all runbooks that are configured to trigger on webhook events."""
        webhook_runbooks: list[dict[str, Any]] = []
        for runbook in automation_engine.list_runbooks():
            if runbook.trigger_type == "webhook" and runbook.enabled:
                match_criteria = getattr(runbook, "trigger_webhook_match", None)
                webhook_runbooks.append(
                    {
                        "name": runbook.name,
                        "description": getattr(runbook, "description", ""),
                        "webhook_match": match_criteria or {},
                        "enabled": runbook.enabled,
                    }
                )
        return {
            "runbooks": webhook_runbooks,
            "total": len(webhook_runbooks),
        }

    return webhook_router
