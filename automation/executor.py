"""Runbook executor -- walks through a runbook's action chain sequentially."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from core.database import Database
from .actions import ActionRegistry
from .audit import AuditLogger
from .exceptions import RunbookExecutionError
from .runbook import Runbook

logger = logging.getLogger(__name__)

# DB DDL for the executions table (created on first use).
_CREATE_EXECUTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS runbook_executions (
    id TEXT PRIMARY KEY,
    runbook_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    trigger_type TEXT,
    context TEXT DEFAULT '{}',
    started_at TEXT,
    finished_at TEXT,
    duration_seconds REAL,
    action_count INTEGER DEFAULT 0,
    results TEXT DEFAULT '[]',
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_exec_runbook
    ON runbook_executions(runbook_name);
CREATE INDEX IF NOT EXISTS idx_exec_status
    ON runbook_executions(status);
"""

# Template variable pattern: {{var_name}} or {{var.nested.key}}
_TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")


class RunbookExecutor:
    """Executes a single instance of a :class:`Runbook`.

    Walks through the action list sequentially, resolving template variables,
    handling conditional branches, applying per-action timeouts, and recording
    results.  The execution state is persisted to the database so it can be
    audited after completion.
    """

    def __init__(
        self,
        execution_id: str,
        runbook: Runbook,
        context: dict[str, Any],
        action_registry: ActionRegistry,
        audit_logger: AuditLogger,
        db: Database,
        dry_run: bool = False,
        max_duration: int = 600,
    ) -> None:
        self._id = execution_id
        self._runbook = runbook
        self._context = dict(context)  # working copy
        self._action_registry = action_registry
        self._audit_logger = audit_logger
        self._db = db
        self._dry_run = dry_run
        self._max_duration = max_duration

        self._status: str = "pending"
        self._results: list[dict[str, Any]] = []
        self._started_at: datetime | None = None
        self._finished_at: datetime | None = None
        self._current_action: str | None = None
        self._table_ensured = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def execution_id(self) -> str:
        return self._id

    @property
    def status(self) -> str:
        return self._status

    @property
    def results(self) -> list[dict[str, Any]]:
        return list(self._results)

    @property
    def duration(self) -> float | None:
        if self._started_at is None:
            return None
        end = self._finished_at or datetime.now(timezone.utc)
        return (end - self._started_at).total_seconds()

    # ------------------------------------------------------------------
    # Table bootstrap
    # ------------------------------------------------------------------

    async def _ensure_table(self) -> None:
        if self._table_ensured:
            return
        try:
            conn = self._db._ensure_connected()
            await conn.executescript(_CREATE_EXECUTIONS_TABLE)
            await conn.commit()
            self._table_ensured = True
        except Exception as exc:
            logger.error("Failed to create runbook_executions table: %s", exc)

    # ------------------------------------------------------------------
    # Execution entry point
    # ------------------------------------------------------------------

    async def execute(self) -> dict[str, Any]:
        """Run the entire runbook action chain.

        Returns a summary dict with execution metadata and per-action results.
        """
        await self._ensure_table()

        self._status = "running"
        self._started_at = datetime.now(timezone.utc)

        # Persist initial execution record.
        await self._save_execution()

        # Audit: execution started.
        await self._audit_logger.log(
            actor="automation-engine",
            action_type="runbook_started",
            description=f"Runbook '{self._runbook.name}' execution started",
            runbook_name=self._runbook.name,
            execution_id=self._id,
            device_id=self._context.get("device_id"),
            details={"dry_run": self._dry_run, "trigger_type": self._runbook.trigger_type},
        )

        actions = self._runbook.actions
        idx = 0

        try:
            async with asyncio.timeout(self._max_duration):
                while idx < len(actions) and self._status == "running":
                    action_def = actions[idx]
                    action_name = action_def.get("name", f"action_{idx}")
                    action_type = action_def.get("action", "")
                    raw_params = action_def.get("params", {})
                    output_var = action_def.get("output_var")
                    on_failure = action_def.get("on_failure", "abort")
                    action_timeout = action_def.get("timeout", 60)
                    on_true = action_def.get("on_true")
                    on_false = action_def.get("on_false")

                    self._current_action = action_name

                    # Resolve template variables in params.
                    resolved_params = self._resolve_templates(raw_params)

                    # Audit: action starting.
                    await self._audit_logger.log(
                        actor="automation-engine",
                        action_type="action_started",
                        description=f"Action '{action_name}' ({action_type}) starting",
                        runbook_name=self._runbook.name,
                        execution_id=self._id,
                        device_id=self._context.get("device_id"),
                        details={"action_name": action_name, "action_type": action_type},
                    )

                    # Execute the action with its own timeout.
                    try:
                        async with asyncio.timeout(action_timeout):
                            result = await self._action_registry.execute(
                                action_type,
                                resolved_params,
                                self._context,
                                dry_run=self._dry_run,
                            )
                    except asyncio.TimeoutError:
                        result = {
                            "error": f"Action '{action_name}' timed out after {action_timeout}s"
                        }

                    # Store output variable in context if requested.
                    if output_var and isinstance(result, dict):
                        self._context[output_var] = result

                    step_result = {
                        "step": idx,
                        "name": action_name,
                        "action": action_type,
                        "result": result,
                        "success": "error" not in result,
                    }
                    self._results.append(step_result)

                    # Audit: action completed.
                    await self._audit_logger.log(
                        actor="automation-engine",
                        action_type="action_completed",
                        description=(
                            f"Action '{action_name}' completed "
                            f"({'success' if step_result['success'] else 'failure'})"
                        ),
                        runbook_name=self._runbook.name,
                        execution_id=self._id,
                        device_id=self._context.get("device_id"),
                        details={"action_name": action_name, "result_keys": list(result.keys()) if isinstance(result, dict) else []},
                        result="success" if step_result["success"] else "failure",
                    )

                    # Handle failure.
                    if not step_result["success"]:
                        if on_failure == "abort":
                            self._status = "failed"
                            logger.error(
                                "Runbook %s aborted at action '%s': %s",
                                self._runbook.name,
                                action_name,
                                result.get("error", "unknown error"),
                            )
                            break
                        elif on_failure == "escalate":
                            self._status = "escalated"
                            # Fire escalation.
                            await self._action_registry.execute(
                                "escalate",
                                {
                                    "level": 1,
                                    "message": (
                                        f"Runbook '{self._runbook.name}' escalated at "
                                        f"action '{action_name}': {result.get('error', '')}"
                                    ),
                                },
                                self._context,
                                dry_run=self._dry_run,
                            )
                            break
                        # on_failure == "continue": just move on.

                    # Handle condition jumps (on_true / on_false).
                    if action_type == "condition" and isinstance(result, dict):
                        jump_to = result.get("jump_to")
                        if jump_to:
                            jump_idx = self._find_action_index(jump_to)
                            if jump_idx is not None:
                                idx = jump_idx
                                continue
                            else:
                                logger.warning(
                                    "Condition jump target '%s' not found; continuing sequentially.",
                                    jump_to,
                                )

                    idx += 1

        except asyncio.TimeoutError:
            self._status = "failed"
            self._results.append({
                "step": idx,
                "name": "GLOBAL_TIMEOUT",
                "action": "timeout",
                "result": {"error": f"Runbook exceeded max_duration of {self._max_duration}s"},
                "success": False,
            })
            logger.error(
                "Runbook %s exceeded global timeout of %ds.",
                self._runbook.name,
                self._max_duration,
            )

        # Finalize.
        self._finished_at = datetime.now(timezone.utc)
        if self._status == "running":
            self._status = "completed"

        self._current_action = None

        # Persist final state.
        await self._save_execution()

        # Audit: execution finished.
        await self._audit_logger.log(
            actor="automation-engine",
            action_type="runbook_finished",
            description=(
                f"Runbook '{self._runbook.name}' finished with status '{self._status}' "
                f"in {self.duration:.1f}s"
            ),
            runbook_name=self._runbook.name,
            execution_id=self._id,
            device_id=self._context.get("device_id"),
            details={
                "status": self._status,
                "duration_seconds": self.duration,
                "total_actions": len(self._results),
                "failed_actions": sum(1 for r in self._results if not r.get("success")),
            },
            result=self._status,
        )

        return self._build_summary()

    # ------------------------------------------------------------------
    # Template resolution
    # ------------------------------------------------------------------

    def _resolve_templates(self, value: Any) -> Any:
        """Recursively replace ``{{var}}`` placeholders in strings, dicts, and
        lists with values from ``self._context``.

        Supports dotted access: ``{{recheck.cpu_percent}}`` resolves to
        ``self._context["recheck"]["cpu_percent"]``.
        """
        if isinstance(value, str):
            return _TEMPLATE_RE.sub(
                lambda m: str(self._resolve_dotted(m.group(1))),
                value,
            )
        if isinstance(value, dict):
            return {k: self._resolve_templates(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_templates(item) for item in value]
        return value

    def _resolve_dotted(self, path: str) -> Any:
        """Walk a dotted path (e.g. ``"recheck.cpu_percent"``) through the
        context dict.  Returns the resolved value or the original placeholder
        string if not found."""
        parts = path.split(".")
        current: Any = self._context
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                # Value not found -- return original placeholder text.
                return "{{" + path + "}}"
        return current

    # ------------------------------------------------------------------
    # Action index lookup
    # ------------------------------------------------------------------

    def _find_action_index(self, name: str) -> int | None:
        """Return the index of the action with the given *name*, or ``None``."""
        for idx, action in enumerate(self._runbook.actions):
            if action.get("name") == name:
                return idx
        return None

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def cancel(self) -> None:
        """Mark the execution as cancelled."""
        self._status = "cancelled"
        self._finished_at = datetime.now(timezone.utc)
        await self._save_execution()

        await self._audit_logger.log(
            actor="automation-engine",
            action_type="runbook_cancelled",
            description=f"Runbook '{self._runbook.name}' execution cancelled",
            runbook_name=self._runbook.name,
            execution_id=self._id,
            device_id=self._context.get("device_id"),
            result="cancelled",
        )

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    async def _save_execution(self) -> None:
        """Upsert the execution record into the database."""
        import json

        await self._ensure_table()

        data = {
            "id": self._id,
            "runbook_name": self._runbook.name,
            "status": self._status,
            "trigger_type": self._runbook.trigger_type,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "finished_at": self._finished_at.isoformat() if self._finished_at else None,
            "duration_seconds": self.duration,
            "action_count": len(self._results),
            "results": json.dumps(self._results, default=str),
            "error": None,
        }

        # Extract error from last failed step if applicable.
        failed_steps = [r for r in self._results if not r.get("success")]
        if failed_steps:
            last_err = failed_steps[-1].get("result", {})
            if isinstance(last_err, dict):
                data["error"] = last_err.get("error")

        try:
            await self._db.execute(
                """
                INSERT OR REPLACE INTO runbook_executions
                    (id, runbook_name, status, trigger_type, started_at,
                     finished_at, duration_seconds, action_count, results, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["id"],
                    data["runbook_name"],
                    data["status"],
                    data["trigger_type"],
                    data["started_at"],
                    data["finished_at"],
                    data["duration_seconds"],
                    data["action_count"],
                    data["results"],
                    data["error"],
                ),
            )
        except Exception as exc:
            logger.error("Failed to save execution %s: %s", self._id, exc)

    def _build_summary(self) -> dict[str, Any]:
        """Build and return the execution summary dict."""
        return {
            "execution_id": self._id,
            "runbook_name": self._runbook.name,
            "status": self._status,
            "dry_run": self._dry_run,
            "trigger_type": self._runbook.trigger_type,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "finished_at": self._finished_at.isoformat() if self._finished_at else None,
            "duration_seconds": self.duration,
            "total_actions": len(self._results),
            "successful_actions": sum(1 for r in self._results if r.get("success")),
            "failed_actions": sum(1 for r in self._results if not r.get("success")),
            "results": self._results,
        }
