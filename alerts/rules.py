"""Predefined alert rules and a helper to seed them into the database."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from core.database import Database

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default rule definitions
# ---------------------------------------------------------------------------

DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "id": "rule-cpu-critical",
        "name": "CPU Critical",
        "description": "CPU usage exceeds 90%",
        "metric_name": "cpu_percent",
        "condition": "gt",
        "threshold": 90.0,
        "severity": "critical",
        "enabled": True,
        "device_id": None,
        "tag": None,
    },
    {
        "id": "rule-cpu-warning",
        "name": "CPU Warning",
        "description": "CPU usage exceeds 75%",
        "metric_name": "cpu_percent",
        "condition": "gt",
        "threshold": 75.0,
        "severity": "warning",
        "enabled": True,
        "device_id": None,
        "tag": None,
    },
    {
        "id": "rule-memory-critical",
        "name": "Memory Critical",
        "description": "Memory usage exceeds 90%",
        "metric_name": "memory_percent",
        "condition": "gt",
        "threshold": 90.0,
        "severity": "critical",
        "enabled": True,
        "device_id": None,
        "tag": None,
    },
    {
        "id": "rule-memory-warning",
        "name": "Memory Warning",
        "description": "Memory usage exceeds 80%",
        "metric_name": "memory_percent",
        "condition": "gt",
        "threshold": 80.0,
        "severity": "warning",
        "enabled": True,
        "device_id": None,
        "tag": None,
    },
    {
        "id": "rule-interface-errors",
        "name": "Interface Errors",
        "description": "Total interface input errors exceed 100",
        "metric_name": "interface_in_errors",
        "condition": "gt",
        "threshold": 100.0,
        "severity": "warning",
        "enabled": True,
        "device_id": None,
        "tag": None,
    },
    {
        "id": "rule-device-unreachable",
        "name": "Device Unreachable",
        "description": "Device is not reachable (reachability metric drops to 0)",
        "metric_name": "device_reachable",
        "condition": "lt",
        "threshold": 1.0,
        "severity": "critical",
        "enabled": True,
        "device_id": None,
        "tag": None,
    },
]


async def load_default_rules(db: Database) -> list[dict[str, Any]]:
    """Seed the default alert rules into the database if they are not already
    present.

    Rules are matched by their ``id`` field.  Existing rules are never
    overwritten, so operators can safely customise thresholds after the
    initial seed.

    Parameters
    ----------
    db:
        An initialised ``Database`` instance.

    Returns
    -------
    list[dict]
        The rules that were actually inserted (skipping duplicates).
    """
    try:
        existing_rules = await db.get_alert_rules()
    except Exception:
        existing_rules = []

    existing_ids: set[str] = {r.get("id", "") for r in existing_rules}
    inserted: list[dict[str, Any]] = []

    for rule in DEFAULT_RULES:
        if rule["id"] in existing_ids:
            logger.debug("Default rule %s already exists; skipping.", rule["id"])
            continue

        try:
            await db.add_alert_rule(
                id=rule["id"],
                name=rule["name"],
                metric_name=rule["metric_name"],
                condition=rule["condition"],
                threshold=rule["threshold"],
                device_filter=rule.get("device_id"),
                enabled=rule.get("enabled", True),
            )
            inserted.append(rule)
            logger.info("Seeded default alert rule: %s", rule["name"])
        except Exception:
            logger.exception("Failed to seed alert rule %s.", rule["id"])

    if inserted:
        logger.info("Seeded %d default alert rule(s).", len(inserted))
    else:
        logger.debug("All default alert rules already present.")

    return inserted
