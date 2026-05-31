"""YAML-based runbook parser, data model, validation, and serialization."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .exceptions import RunbookValidationError

logger = logging.getLogger(__name__)

# The complete set of supported action types.
SUPPORTED_ACTIONS: frozenset[str] = frozenset(
    {
        "run_command",
        "poll_device",
        "backup_config",
        "backup_all",
        "deploy_config",
        "rollback_config",
        "check_interface_errors",
        "check_device_health",
        "ping_test",
        "check_port",
        "scan_subnet",
        "notify",
        "log",
        "wait",
        "condition",
        "escalate",
        "resolve_alert",
        "http_request",
    }
)

VALID_ON_FAILURE: frozenset[str] = frozenset({"continue", "abort", "escalate"})

VALID_TRIGGER_TYPES: frozenset[str] = frozenset(
    {"alert", "schedule", "webhook", "manual"}
)

# Allowed characters for runbook names: alphanumeric, hyphens, underscores.
_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Runbook:
    """In-memory representation of a parsed YAML runbook definition."""

    name: str
    version: str = "1.0"
    description: str = ""
    enabled: bool = True
    tags: list[str] = field(default_factory=list)

    # Trigger configuration
    trigger_type: str = "manual"
    trigger_alert_match: dict[str, Any] | None = None
    trigger_schedule_cron: str | None = None
    trigger_schedule_timezone: str = "UTC"
    trigger_webhook_match: dict[str, Any] | None = None

    # Conditions that must be true for the runbook to execute
    conditions: list[dict[str, Any]] = field(default_factory=list)

    # Cooldown settings (all in seconds)
    cooldown: dict[str, int] = field(
        default_factory=lambda: {
            "per_device": 0,
            "per_rule": 0,
            "global_": 0,
        }
    )

    # Execution limits
    limits: dict[str, Any] = field(
        default_factory=lambda: {
            "max_duration_seconds": 600,
            "max_concurrent": 1,
            "retry_on_failure": False,
            "retry_count": 0,
        }
    )

    # Ordered action steps
    actions: list[dict[str, Any]] = field(default_factory=list)

    # Escalation policy
    escalation: dict[str, Any] = field(
        default_factory=lambda: {"levels": []}
    )

    # Filesystem path of the source YAML file (None for in-memory runbooks)
    file_path: str | None = None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_runbook(data: dict[str, Any]) -> list[str]:
    """Validate a raw dict (parsed from YAML) against the runbook schema.

    Returns a list of human-readable error strings.  An empty list means the
    data is valid.
    """
    errors: list[str] = []

    # -- name --
    name = data.get("name")
    if not name:
        errors.append("'name' is required")
    elif not isinstance(name, str):
        errors.append("'name' must be a string")
    elif not _NAME_PATTERN.match(name):
        errors.append(
            f"'name' must be alphanumeric with hyphens/underscores, got {name!r}"
        )

    # -- trigger --
    trigger = data.get("trigger", {})
    if not isinstance(trigger, dict):
        errors.append("'trigger' must be a mapping")
        trigger = {}

    trigger_type = trigger.get("type", "manual")
    if trigger_type not in VALID_TRIGGER_TYPES:
        errors.append(
            f"trigger.type must be one of {sorted(VALID_TRIGGER_TYPES)}, "
            f"got {trigger_type!r}"
        )

    if trigger_type == "schedule":
        cron = trigger.get("schedule", {}).get("cron") if isinstance(trigger.get("schedule"), dict) else None
        if not cron:
            errors.append(
                "trigger.schedule.cron is required when trigger.type is 'schedule'"
            )
        else:
            cron_errors = _validate_cron(cron)
            if cron_errors:
                errors.append(f"trigger.schedule.cron is invalid: {cron_errors}")

    if trigger_type == "alert":
        alert_match = trigger.get("alert_match")
        if not alert_match or not isinstance(alert_match, dict):
            errors.append(
                "trigger.alert_match is required when trigger.type is 'alert'"
            )

    # -- actions --
    actions = data.get("actions")
    if not actions:
        errors.append("'actions' must be a non-empty list")
    elif not isinstance(actions, list):
        errors.append("'actions' must be a list")
    else:
        for idx, action in enumerate(actions):
            if not isinstance(action, dict):
                errors.append(f"actions[{idx}] must be a mapping")
                continue
            if not action.get("name"):
                errors.append(f"actions[{idx}].name is required")
            if not action.get("action"):
                errors.append(f"actions[{idx}].action is required")
            elif action["action"] not in SUPPORTED_ACTIONS:
                errors.append(
                    f"actions[{idx}].action {action['action']!r} is not a supported "
                    f"action type. Supported: {sorted(SUPPORTED_ACTIONS)}"
                )
            on_fail = action.get("on_failure", "abort")
            if on_fail not in VALID_ON_FAILURE:
                errors.append(
                    f"actions[{idx}].on_failure must be one of "
                    f"{sorted(VALID_ON_FAILURE)}, got {on_fail!r}"
                )

    return errors


def _validate_cron(expr: str) -> str:
    """Lightweight cron expression validation.

    Accepts 5-field cron (minute hour dom month dow).  Returns an error string
    or empty string if valid.
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        return f"Expected 5 fields, got {len(parts)}"

    field_names = ["minute", "hour", "day-of-month", "month", "day-of-week"]
    field_ranges = [
        (0, 59),
        (0, 23),
        (1, 31),
        (1, 12),
        (0, 7),
    ]

    for i, (part, fname, (lo, hi)) in enumerate(
        zip(parts, field_names, field_ranges)
    ):
        err = _validate_cron_field(part, fname, lo, hi)
        if err:
            return err
    return ""


def _validate_cron_field(
    field_val: str, name: str, lo: int, hi: int
) -> str:
    """Validate a single cron field.  Returns error string or ''."""
    # Handle comma-separated list
    for item in field_val.split(","):
        item = item.strip()
        if not item:
            return f"{name}: empty sub-expression"

        # Handle step: */5, 1-10/2
        step_parts = item.split("/")
        if len(step_parts) > 2:
            return f"{name}: too many '/' in {item!r}"

        base = step_parts[0]
        if len(step_parts) == 2:
            step_val = step_parts[1]
            if not step_val.isdigit() or int(step_val) == 0:
                return f"{name}: step value must be a positive integer, got {step_val!r}"

        if base == "*":
            continue

        # Handle range: 1-5
        if "-" in base:
            range_parts = base.split("-")
            if len(range_parts) != 2:
                return f"{name}: invalid range {base!r}"
            for rp in range_parts:
                if not rp.isdigit():
                    return f"{name}: non-numeric value in range {base!r}"
                val = int(rp)
                if val < lo or val > hi:
                    return f"{name}: value {val} out of range [{lo}-{hi}]"
            continue

        # Plain numeric value
        if not base.isdigit():
            return f"{name}: non-numeric value {base!r}"
        val = int(base)
        if val < lo or val > hi:
            return f"{name}: value {val} out of range [{lo}-{hi}]"

    return ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_runbook_dict(data: dict[str, Any], file_path: str | None = None) -> Runbook:
    """Convert a validated dict into a :class:`Runbook` instance."""
    trigger = data.get("trigger", {})
    if not isinstance(trigger, dict):
        trigger = {}

    trigger_type = trigger.get("type", "manual")

    # Alert match
    trigger_alert_match = trigger.get("alert_match")

    # Schedule
    schedule_block = trigger.get("schedule", {}) or {}
    trigger_schedule_cron = schedule_block.get("cron") if isinstance(schedule_block, dict) else None
    trigger_schedule_timezone = (
        schedule_block.get("timezone", "UTC") if isinstance(schedule_block, dict) else "UTC"
    )

    # Webhook
    trigger_webhook_match = trigger.get("webhook_match")

    # Cooldown
    raw_cooldown = data.get("cooldown", {}) or {}
    cooldown = {
        "per_device": int(raw_cooldown.get("per_device", 0)),
        "per_rule": int(raw_cooldown.get("per_rule", 0)),
        "global_": int(raw_cooldown.get("global", raw_cooldown.get("global_", 0))),
    }

    # Limits
    raw_limits = data.get("limits", {}) or {}
    limits = {
        "max_duration_seconds": int(raw_limits.get("max_duration_seconds", 600)),
        "max_concurrent": int(raw_limits.get("max_concurrent", 1)),
        "retry_on_failure": bool(raw_limits.get("retry_on_failure", False)),
        "retry_count": int(raw_limits.get("retry_count", 0)),
    }

    # Actions -- normalize each step
    raw_actions = data.get("actions", [])
    actions: list[dict[str, Any]] = []
    for raw_act in raw_actions:
        act: dict[str, Any] = {
            "name": raw_act.get("name", ""),
            "action": raw_act.get("action", ""),
            "params": raw_act.get("params", {}),
            "output_var": raw_act.get("output_var"),
            "on_failure": raw_act.get("on_failure", "abort"),
            "timeout": int(raw_act.get("timeout", 60)),
            "on_true": raw_act.get("on_true"),
            "on_false": raw_act.get("on_false"),
        }
        actions.append(act)

    # Escalation
    raw_escalation = data.get("escalation", {}) or {}
    escalation: dict[str, Any] = {"levels": []}
    for level_def in raw_escalation.get("levels", []):
        escalation["levels"].append(
            {
                "level": int(level_def.get("level", 1)),
                "channels": list(level_def.get("channels", [])),
                "wait_minutes": int(level_def.get("wait_minutes", 5)),
                "message_prefix": str(level_def.get("message_prefix", "")),
            }
        )

    return Runbook(
        name=data.get("name", ""),
        version=str(data.get("version", "1.0")),
        description=str(data.get("description", "")),
        enabled=bool(data.get("enabled", True)),
        tags=list(data.get("tags", [])),
        trigger_type=trigger_type,
        trigger_alert_match=trigger_alert_match,
        trigger_schedule_cron=trigger_schedule_cron,
        trigger_schedule_timezone=trigger_schedule_timezone,
        trigger_webhook_match=trigger_webhook_match,
        conditions=list(data.get("conditions", [])),
        cooldown=cooldown,
        limits=limits,
        actions=actions,
        escalation=escalation,
        file_path=file_path,
    )


def load_runbook(file_path: str) -> Runbook:
    """Parse a single YAML file and return a validated :class:`Runbook`.

    Raises :class:`RunbookValidationError` if the file is invalid.
    """
    path = Path(file_path)
    if not path.is_file():
        raise RunbookValidationError(
            message=f"Runbook file not found: {file_path}",
            errors=[f"File does not exist: {file_path}"],
        )

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise RunbookValidationError(
            message=f"YAML parse error in {file_path}: {exc}",
            errors=[str(exc)],
        ) from exc

    if not isinstance(data, dict):
        raise RunbookValidationError(
            message=f"Runbook YAML must be a mapping, got {type(data).__name__}",
            runbook_name=str(path.stem),
            errors=["Top-level YAML must be a mapping"],
        )

    errors = validate_runbook(data)
    if errors:
        raise RunbookValidationError(
            message=f"Validation failed for {file_path}",
            runbook_name=data.get("name", str(path.stem)),
            errors=errors,
        )

    runbook = _parse_runbook_dict(data, file_path=str(path.resolve()))
    logger.info("Loaded runbook %r from %s", runbook.name, file_path)
    return runbook


def load_runbooks_from_dir(dir_path: str) -> dict[str, Runbook]:
    """Load all ``.yaml`` / ``.yml`` files from *dir_path*.

    Returns a dict mapping runbook name to :class:`Runbook`.  Files that fail
    validation are logged and skipped.
    """
    directory = Path(dir_path)
    if not directory.is_dir():
        logger.warning("Runbook directory does not exist: %s", dir_path)
        return {}

    runbooks: dict[str, Runbook] = {}
    for entry in sorted(directory.iterdir()):
        if entry.suffix.lower() not in (".yaml", ".yml"):
            continue
        try:
            rb = load_runbook(str(entry))
            if rb.name in runbooks:
                logger.warning(
                    "Duplicate runbook name %r -- file %s overwrites previous.",
                    rb.name,
                    entry.name,
                )
            runbooks[rb.name] = rb
        except RunbookValidationError as exc:
            logger.error("Skipping invalid runbook %s: %s", entry.name, exc)
        except Exception as exc:
            logger.error(
                "Unexpected error loading runbook %s: %s", entry.name, exc
            )

    logger.info(
        "Loaded %d runbook(s) from %s", len(runbooks), dir_path
    )
    return runbooks


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def runbook_to_yaml(runbook: Runbook) -> str:
    """Serialize a :class:`Runbook` back to a YAML string."""
    data: dict[str, Any] = {
        "name": runbook.name,
        "version": runbook.version,
        "description": runbook.description,
        "enabled": runbook.enabled,
        "tags": runbook.tags,
        "trigger": {
            "type": runbook.trigger_type,
        },
        "conditions": runbook.conditions,
        "cooldown": {
            "per_device": runbook.cooldown.get("per_device", 0),
            "per_rule": runbook.cooldown.get("per_rule", 0),
            "global": runbook.cooldown.get("global_", 0),
        },
        "limits": runbook.limits,
        "actions": runbook.actions,
        "escalation": runbook.escalation,
    }

    # Populate trigger sub-blocks
    if runbook.trigger_type == "alert" and runbook.trigger_alert_match:
        data["trigger"]["alert_match"] = runbook.trigger_alert_match
    if runbook.trigger_type == "schedule" and runbook.trigger_schedule_cron:
        data["trigger"]["schedule"] = {
            "cron": runbook.trigger_schedule_cron,
            "timezone": runbook.trigger_schedule_timezone,
        }
    if runbook.trigger_type == "webhook" and runbook.trigger_webhook_match:
        data["trigger"]["webhook_match"] = runbook.trigger_webhook_match

    return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)


def save_runbook(runbook: Runbook, dir_path: str) -> str:
    """Write *runbook* to disk as ``<dir_path>/<name>.yaml``.

    Returns the absolute file path of the written file.
    """
    directory = Path(dir_path)
    directory.mkdir(parents=True, exist_ok=True)

    file_name = f"{runbook.name}.yaml"
    file_path = directory / file_name

    yaml_content = runbook_to_yaml(runbook)
    file_path.write_text(yaml_content, encoding="utf-8")

    abs_path = str(file_path.resolve())
    runbook.file_path = abs_path
    logger.info("Saved runbook %r to %s", runbook.name, abs_path)
    return abs_path
