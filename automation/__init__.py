"""Autonomous runbook and auto-remediation engine.

This package provides:
- YAML-based runbook definitions with triggers, actions, and escalation
- Event-driven execution (alert-triggered, scheduled, webhook, manual)
- Sequential action chains with template variables and conditional branching
- Per-device locking, cooldown management, and concurrency limits
- Full audit trail of every autonomous action
"""

from .engine import AutomationEngine
from .executor import RunbookExecutor
from .runbook import Runbook, load_runbook, load_runbooks_from_dir
from .actions import ActionRegistry
from .scheduler import SchedulerManager
from .audit import AuditLogger
from .exceptions import AutomationError, RunbookError, RunbookValidationError

__all__ = [
    "AutomationEngine",
    "RunbookExecutor",
    "Runbook",
    "load_runbook",
    "load_runbooks_from_dir",
    "ActionRegistry",
    "SchedulerManager",
    "AuditLogger",
    "AutomationError",
    "RunbookError",
    "RunbookValidationError",
]
