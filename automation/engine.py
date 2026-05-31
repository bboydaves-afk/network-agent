"""Core automation orchestrator.

Subscribes to AlertEngine events, matches alerts to runbooks,
dispatches RunbookExecutor instances, manages cooldowns and concurrency.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import yaml

from automation.actions import ActionRegistry
from automation.audit import AuditLogger
from automation.exceptions import (
    AutomationError,
    RunbookCooldownError,
    RunbookError,
    RunbookExecutionError,
)
from automation.executor import RunbookExecutor
from automation.runbook import (
    Runbook,
    _parse_runbook_dict,
    load_runbook,
    load_runbooks_from_dir,
    save_runbook,
    validate_runbook,
)

logger = logging.getLogger(__name__)


class AutomationEngine:
    """Central orchestrator for runbook-based automation.

    Responsibilities
    ----------------
    - Load and manage the on-disk runbook library.
    - Subscribe to ``AlertEngine`` fire/resolve events and match them against
      alert-triggered runbooks.
    - Enforce per-device and global cooldowns so noisy alerts do not cause
      runbook storms.
    - Enforce global and per-runbook concurrency limits.
    - Dispatch ``RunbookExecutor`` instances as background ``asyncio.Task``s.
    - Provide a synchronous query API over in-flight and historical executions.
    """

    def __init__(
        self,
        db,
        alert_engine,
        config_manager,
        monitor,
        troubleshooter,
        discovery,
        credential_manager,
        audit_logger: AuditLogger,
        runbook_dir: str = "./data/runbooks",
        dry_run: bool = False,
        max_global_executions: int = 10,
    ):
        self._db = db
        self._alert_engine = alert_engine
        self._config_manager = config_manager
        self._monitor = monitor
        self._troubleshooter = troubleshooter
        self._discovery = discovery
        self._cred_mgr = credential_manager
        self._audit = audit_logger
        self._runbook_dir = Path(runbook_dir)
        self._dry_run = dry_run
        self._max_global = max_global_executions

        # Runbook library keyed by name.
        self._runbooks: dict[str, Runbook] = {}

        # In-flight executor instances keyed by execution ID.
        self._executions: dict[str, RunbookExecutor] = {}

        # Background tasks driving each executor, keyed by execution ID.
        self._execution_tasks: dict[str, asyncio.Task] = {}

        # Cooldown timestamps (monotonic) keyed by "name:scope:key".
        self._cooldowns: dict[str, float] = {}

        # Per-device asyncio locks to serialize runbooks targeting the same
        # device, preventing conflicting concurrent operations.
        self._device_locks: dict[str, asyncio.Lock] = {}

        # Global concurrency bookkeeping.
        self._active_count: int = 0
        self._lock = asyncio.Lock()

        # Action registry shared by all executors.
        self._action_registry = ActionRegistry(
            db=db,
            config_manager=config_manager,
            monitor=monitor,
            troubleshooter=troubleshooter,
            discovery=discovery,
            credential_manager=credential_manager,
            alert_engine=alert_engine,
            audit_logger=audit_logger,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Load runbooks from disk and register with the alert engine."""
        count = await self.reload_runbooks()
        self._alert_engine.register_automation_callback(self._on_alert)
        logger.info("AutomationEngine started with %d runbooks", count)
        await self._audit.log(
            actor="automation",
            action_type="engine_started",
            description=f"Automation engine started with {count} runbooks loaded",
        )

    async def stop(self) -> None:
        """Cancel all in-flight executions and clean up state."""
        for exec_id, task in list(self._execution_tasks.items()):
            if not task.done():
                task.cancel()
                # Allow the cancellation to propagate so the executor can
                # finalize its audit record.
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._execution_tasks.clear()
        self._executions.clear()
        self._active_count = 0
        logger.info("AutomationEngine stopped")
        await self._audit.log(
            actor="automation",
            action_type="engine_stopped",
            description="Automation engine stopped",
        )

    # ------------------------------------------------------------------ #
    # Runbook management                                                   #
    # ------------------------------------------------------------------ #

    async def reload_runbooks(self) -> int:
        """(Re-)load all runbooks from the configured directory.

        Returns the number of successfully loaded runbooks.
        """
        self._runbook_dir.mkdir(parents=True, exist_ok=True)
        loaded = load_runbooks_from_dir(str(self._runbook_dir))
        self._runbooks = loaded
        logger.info("Loaded %d runbooks from %s", len(loaded), self._runbook_dir)
        return len(loaded)

    def get_runbook(self, name: str) -> Runbook | None:
        """Return a loaded runbook by name, or ``None``."""
        return self._runbooks.get(name)

    def list_runbooks(self) -> list[Runbook]:
        """Return a list of all loaded runbooks."""
        return list(self._runbooks.values())

    async def add_runbook(self, yaml_content: str) -> Runbook:
        """Parse, validate, persist, and register a new runbook from YAML.

        Raises :class:`RunbookError` if validation fails.
        """
        data = yaml.safe_load(yaml_content)
        if not isinstance(data, dict):
            raise RunbookError(message="Runbook YAML must be a mapping")

        errors = validate_runbook(data)
        if errors:
            raise RunbookError(message=f"Validation errors: {'; '.join(errors)}")

        rb = _parse_runbook_dict(data)
        saved_path = save_runbook(rb, str(self._runbook_dir))
        rb.file_path = saved_path
        self._runbooks[rb.name] = rb

        logger.info("Added runbook: %s", rb.name)
        await self._audit.log(
            actor="automation",
            action_type="runbook_added",
            description=f"Runbook '{rb.name}' added",
            runbook_name=rb.name,
        )
        return rb

    async def update_runbook(self, name: str, yaml_content: str) -> Runbook:
        """Replace an existing runbook's definition with new YAML content.

        Raises :class:`RunbookError` if the runbook does not exist or
        validation fails.
        """
        if name not in self._runbooks:
            raise RunbookError(runbook_name=name, message="Runbook not found")

        data = yaml.safe_load(yaml_content)
        if not isinstance(data, dict):
            raise RunbookError(
                runbook_name=name,
                message="Runbook YAML must be a mapping",
            )

        errors = validate_runbook(data)
        if errors:
            raise RunbookError(
                runbook_name=name,
                message=f"Validation errors: {'; '.join(errors)}",
            )

        # If the YAML defines a different name, remove the old entry.
        old_rb = self._runbooks.pop(name, None)
        if old_rb and old_rb.file_path:
            old_path = Path(old_rb.file_path)
            if old_path.exists() and data.get("name", name) != name:
                old_path.unlink()

        rb = _parse_runbook_dict(data)
        saved_path = save_runbook(rb, str(self._runbook_dir))
        rb.file_path = saved_path
        self._runbooks[rb.name] = rb

        logger.info("Updated runbook: %s", rb.name)
        await self._audit.log(
            actor="automation",
            action_type="runbook_updated",
            description=f"Runbook '{rb.name}' updated",
            runbook_name=rb.name,
        )
        return rb

    async def delete_runbook(self, name: str) -> bool:
        """Remove a runbook from memory and disk.

        Returns ``True`` if the runbook was found and removed.
        """
        rb = self._runbooks.pop(name, None)
        if not rb:
            return False
        if rb.file_path:
            path = Path(rb.file_path)
            if path.exists():
                path.unlink()

        logger.info("Deleted runbook: %s", name)
        await self._audit.log(
            actor="automation",
            action_type="runbook_deleted",
            description=f"Runbook '{name}' deleted",
            runbook_name=name,
        )
        return True

    async def enable_runbook(self, name: str) -> None:
        """Enable a runbook so it can be triggered."""
        rb = self._runbooks.get(name)
        if not rb:
            raise RunbookError(runbook_name=name, message="Runbook not found")
        rb.enabled = True
        save_runbook(rb, str(self._runbook_dir))
        await self._audit.log(
            actor="automation",
            action_type="runbook_enabled",
            description=f"Runbook '{name}' enabled",
            runbook_name=name,
        )

    async def disable_runbook(self, name: str) -> None:
        """Disable a runbook so it will not be triggered."""
        rb = self._runbooks.get(name)
        if not rb:
            raise RunbookError(runbook_name=name, message="Runbook not found")
        rb.enabled = False
        save_runbook(rb, str(self._runbook_dir))
        await self._audit.log(
            actor="automation",
            action_type="runbook_disabled",
            description=f"Runbook '{name}' disabled",
            runbook_name=name,
        )

    # ------------------------------------------------------------------ #
    # Alert callback                                                       #
    # ------------------------------------------------------------------ #

    async def _on_alert(self, alert: dict, event: str) -> None:
        """Callback registered with ``AlertEngine``.

        Only processes ``"fired"`` events.  For each matching
        alert-triggered runbook, a dispatch is attempted.  Cooldown and
        concurrency violations are silently suppressed.
        """
        if event != "fired":
            return

        for runbook in self._runbooks.values():
            if not runbook.enabled or runbook.trigger_type != "alert":
                continue
            if not self._matches_alert(runbook, alert):
                continue

            context = await self._build_alert_context(alert)
            try:
                await self._dispatch(runbook, context)
            except RunbookCooldownError as exc:
                logger.debug(
                    "Runbook %s suppressed (cooldown): %s",
                    runbook.name,
                    exc,
                )
            except AutomationError as exc:
                logger.debug(
                    "Runbook %s suppressed: %s",
                    runbook.name,
                    exc,
                )

    def _matches_alert(self, runbook: Runbook, alert: dict) -> bool:
        """Return ``True`` if *alert* satisfies the runbook's
        ``trigger_alert_match`` criteria.

        When a match field is absent or empty in the runbook definition
        the corresponding alert attribute is treated as a wildcard.
        """
        match = runbook.trigger_alert_match
        if not match:
            # No match criteria -- matches every alert.
            return True

        # --- rule_id exact match ---
        if match.get("rule_id"):
            if alert.get("rule_id") != match["rule_id"]:
                return False

        # --- rule_name glob/fnmatch ---
        if match.get("rule_name_pattern"):
            rule_name = alert.get("rule_name", "")
            if not fnmatch.fnmatch(rule_name, match["rule_name_pattern"]):
                return False

        # --- severity whitelist ---
        if match.get("severity"):
            allowed = match["severity"]
            if isinstance(allowed, str):
                allowed = [allowed]
            if alert.get("severity") not in allowed:
                return False

        # --- metric_name exact match ---
        if match.get("metric_name"):
            if alert.get("metric_name") != match["metric_name"]:
                return False

        # --- device_filter glob ---
        device_filter = match.get("device_filter")
        if device_filter and device_filter != "*":
            device_id = alert.get("device_id", "")
            if not fnmatch.fnmatch(str(device_id), device_filter):
                return False

        return True

    # ------------------------------------------------------------------ #
    # Dispatch and execution                                               #
    # ------------------------------------------------------------------ #

    async def _dispatch(
        self,
        runbook: Runbook,
        context: dict,
    ) -> str | None:
        """Perform cooldown/concurrency checks, create a ``RunbookExecutor``,
        and launch it as a background task.

        Returns the execution ID on success, or ``None`` if the dispatch
        is silently suppressed by a concurrency limit.

        Raises
        ------
        RunbookCooldownError
            If the runbook is in cooldown for the target device.
        """
        device_id = context.get("device_id", "")

        # -- Cooldown --
        if not self._check_cooldown(runbook, device_id):
            remaining = self._cooldown_remaining(runbook, device_id)
            raise RunbookCooldownError(
                runbook_name=runbook.name,
                message=f"Cooldown active for device {device_id}",
                remaining_seconds=remaining,
            )

        # -- Concurrency limits --
        async with self._lock:
            # Global limit
            if self._active_count >= self._max_global:
                logger.warning(
                    "Global execution limit reached (%d), skipping %s",
                    self._max_global,
                    runbook.name,
                )
                return None

            # Per-runbook limit
            max_concurrent = runbook.limits.get("max_concurrent", 1)
            current = sum(
                1
                for ex in self._executions.values()
                if ex._runbook.name == runbook.name and ex.status == "running"
            )
            if current >= max_concurrent:
                logger.debug(
                    "Runbook %s at max concurrent (%d), skipping",
                    runbook.name,
                    max_concurrent,
                )
                return None

            self._active_count += 1

        # -- Create executor --
        exec_id = str(uuid4())
        max_duration = runbook.limits.get("max_duration_seconds", 600)

        executor = RunbookExecutor(
            execution_id=exec_id,
            runbook=runbook,
            context=context,
            action_registry=self._action_registry,
            audit_logger=self._audit,
            db=self._db,
            dry_run=self._dry_run,
            max_duration=max_duration,
        )

        self._executions[exec_id] = executor
        self._set_cooldown(runbook, device_id)

        # -- Launch background task --
        task = asyncio.create_task(
            self._run_executor(exec_id, executor, runbook, device_id)
        )
        self._execution_tasks[exec_id] = task

        logger.info(
            "Dispatched runbook %s (exec=%s, device=%s, dry_run=%s)",
            runbook.name,
            exec_id,
            device_id,
            self._dry_run,
        )
        await self._audit.log(
            actor="automation",
            action_type="runbook_dispatched",
            description=(
                f"Dispatched runbook '{runbook.name}' for device {device_id}"
            ),
            runbook_name=runbook.name,
            execution_id=exec_id,
            device_id=device_id if device_id else None,
        )

        return exec_id

    async def _run_executor(
        self,
        exec_id: str,
        executor: RunbookExecutor,
        runbook: Runbook,
        device_id: str,
    ) -> None:
        """Run a single executor inside a background task.

        Acquires a per-device lock so that two runbooks targeting the same
        device never overlap, then delegates to ``executor.execute()``.
        """
        # Obtain (or create) a per-device lock if a device is targeted.
        lock: asyncio.Lock | None = None
        if device_id:
            if device_id not in self._device_locks:
                self._device_locks[device_id] = asyncio.Lock()
            lock = self._device_locks[device_id]

        try:
            if lock:
                async with lock:
                    result = await executor.execute()
            else:
                result = await executor.execute()

            logger.info(
                "Runbook %s completed: %s (exec=%s)",
                runbook.name,
                result.get("status"),
                exec_id,
            )
        except asyncio.CancelledError:
            logger.info(
                "Runbook %s cancelled (exec=%s)", runbook.name, exec_id
            )
            try:
                await executor.cancel()
            except Exception:
                pass
        except Exception:
            logger.exception(
                "Runbook %s failed unexpectedly (exec=%s)",
                runbook.name,
                exec_id,
            )
        finally:
            async with self._lock:
                self._active_count = max(0, self._active_count - 1)
            self._execution_tasks.pop(exec_id, None)

    async def execute_runbook(
        self,
        name: str,
        context: dict | None = None,
        dry_run: bool = False,
    ) -> str:
        """Manually trigger a runbook by name.

        Parameters
        ----------
        name:
            Runbook name.
        context:
            Optional context dict merged into the execution context.
        dry_run:
            If ``True``, override the engine-level dry_run flag for this
            single execution.

        Returns
        -------
        str
            The execution ID.

        Raises
        ------
        RunbookError
            If the runbook is not found.
        RunbookExecutionError
            If the dispatch is suppressed by limits or cooldown.
        """
        runbook = self._runbooks.get(name)
        if not runbook:
            raise RunbookError(runbook_name=name, message="Runbook not found")

        ctx = dict(context or {})
        ctx.setdefault("trigger", "manual")
        ctx.setdefault("trigger_type", "manual")

        # Temporarily override dry_run for this single dispatch if requested.
        saved_dry_run = self._dry_run
        if dry_run:
            self._dry_run = True

        try:
            exec_id = await self._dispatch(runbook, ctx)
            if not exec_id:
                raise RunbookExecutionError(
                    runbook_name=name,
                    message="Dispatch suppressed (concurrency limit reached)",
                )
            return exec_id
        finally:
            self._dry_run = saved_dry_run

    async def cancel_execution(self, exec_id: str) -> bool:
        """Cancel an in-flight execution by its ID.

        Returns ``True`` if the execution was found and cancellation was
        initiated.
        """
        task = self._execution_tasks.get(exec_id)
        executor = self._executions.get(exec_id)

        if not task or task.done():
            # If we have an executor but no running task, try to cancel it
            # directly (it may be stuck waiting for a lock).
            if executor and executor.status == "running":
                await executor.cancel()
                return True
            return False

        task.cancel()
        logger.info("Cancellation requested for execution %s", exec_id)
        await self._audit.log(
            actor="automation",
            action_type="execution_cancelled",
            description=f"Execution {exec_id} cancellation requested",
            execution_id=exec_id,
            runbook_name=executor._runbook.name if executor else None,
        )
        return True

    # ------------------------------------------------------------------ #
    # Cooldown management                                                  #
    # ------------------------------------------------------------------ #

    def _check_cooldown(self, runbook: Runbook, device_id: str) -> bool:
        """Return ``True`` if the runbook may execute now (no active cooldown).

        Checks both per-device and global cooldown windows.
        """
        now = time.monotonic()
        cooldown_cfg = runbook.cooldown

        # Per-device cooldown
        per_device = cooldown_cfg.get("per_device", 0)
        if per_device > 0 and device_id:
            key = f"{runbook.name}:device:{device_id}"
            last = self._cooldowns.get(key, 0)
            if (now - last) < per_device:
                return False

        # Per-rule cooldown
        per_rule = cooldown_cfg.get("per_rule", 0)
        if per_rule > 0:
            key = f"{runbook.name}:rule"
            last = self._cooldowns.get(key, 0)
            if (now - last) < per_rule:
                return False

        # Global cooldown (note: the dataclass stores this as ``global_``
        # to avoid shadowing the Python builtin).
        global_cd = cooldown_cfg.get("global_", cooldown_cfg.get("global", 0))
        if global_cd > 0:
            key = f"{runbook.name}:global"
            last = self._cooldowns.get(key, 0)
            if (now - last) < global_cd:
                return False

        return True

    def _cooldown_remaining(self, runbook: Runbook, device_id: str) -> float:
        """Return the number of seconds remaining on the longest active
        cooldown window for the given runbook + device.  Returns 0.0 if no
        cooldown is active.
        """
        now = time.monotonic()
        cooldown_cfg = runbook.cooldown
        remaining = 0.0

        per_device = cooldown_cfg.get("per_device", 0)
        if per_device > 0 and device_id:
            key = f"{runbook.name}:device:{device_id}"
            last = self._cooldowns.get(key, 0)
            r = per_device - (now - last)
            if r > remaining:
                remaining = r

        per_rule = cooldown_cfg.get("per_rule", 0)
        if per_rule > 0:
            key = f"{runbook.name}:rule"
            last = self._cooldowns.get(key, 0)
            r = per_rule - (now - last)
            if r > remaining:
                remaining = r

        global_cd = cooldown_cfg.get("global_", cooldown_cfg.get("global", 0))
        if global_cd > 0:
            key = f"{runbook.name}:global"
            last = self._cooldowns.get(key, 0)
            r = global_cd - (now - last)
            if r > remaining:
                remaining = r

        return max(remaining, 0.0)

    def _set_cooldown(self, runbook: Runbook, device_id: str) -> None:
        """Record the current monotonic timestamp against all applicable
        cooldown keys for the given runbook and device.
        """
        now = time.monotonic()
        if device_id:
            self._cooldowns[f"{runbook.name}:device:{device_id}"] = now
        self._cooldowns[f"{runbook.name}:rule"] = now
        self._cooldowns[f"{runbook.name}:global"] = now

    def clear_cooldowns(self, runbook_name: str | None = None) -> int:
        """Clear cooldown state.

        If *runbook_name* is given, only cooldowns for that runbook are
        cleared.  Otherwise all cooldowns are cleared.

        Returns the number of cooldown entries removed.
        """
        if runbook_name is None:
            count = len(self._cooldowns)
            self._cooldowns.clear()
            return count

        prefix = f"{runbook_name}:"
        keys_to_remove = [k for k in self._cooldowns if k.startswith(prefix)]
        for k in keys_to_remove:
            del self._cooldowns[k]
        return len(keys_to_remove)

    # ------------------------------------------------------------------ #
    # Context building                                                     #
    # ------------------------------------------------------------------ #

    async def _build_alert_context(self, alert: dict) -> dict:
        """Construct the execution context dict from an incoming alert.

        Enriches the context with device metadata from the database when
        available.
        """
        context: dict[str, Any] = {
            "device_id": alert.get("device_id", ""),
            "device_ip": "",
            "device_hostname": "",
            "metric_name": alert.get("metric_name", ""),
            "metric_value": alert.get("metric_value", ""),
            "threshold": alert.get("threshold", ""),
            "severity": alert.get("severity", ""),
            "alert_id": alert.get("id", ""),
            "rule_id": alert.get("rule_id", ""),
            "rule_name": alert.get("rule_name", ""),
            "message": alert.get("message", ""),
            "timestamp": alert.get(
                "created_at", datetime.now(timezone.utc).isoformat()
            ),
            "trigger": "alert",
            "trigger_type": "alert",
        }

        # Enrich with device record from the database.
        device_id = alert.get("device_id")
        if device_id:
            try:
                device = await self._db.get_device(device_id)
                if device:
                    context["device_ip"] = device.get("ip_address", "")
                    context["device_hostname"] = device.get("hostname", "")
            except Exception:
                logger.debug(
                    "Failed to look up device %s for alert context",
                    device_id,
                )

        return context

    # ------------------------------------------------------------------ #
    # Query API                                                            #
    # ------------------------------------------------------------------ #

    def get_execution(self, exec_id: str) -> dict | None:
        """Return a snapshot dict for an in-memory execution, or ``None``.

        For historical executions that have already been garbage-collected
        from memory, callers should fall back to
        ``db.get_runbook_execution(exec_id)``.
        """
        executor = self._executions.get(exec_id)
        if not executor:
            return None

        return {
            "id": exec_id,
            "runbook_name": executor._runbook.name,
            "status": executor.status,
            "started_at": (
                executor._started_at.isoformat()
                if executor._started_at
                else None
            ),
            "finished_at": (
                executor._finished_at.isoformat()
                if executor._finished_at
                else None
            ),
            "duration": executor.duration,
            "results": executor.results,
            "dry_run": executor._dry_run,
        }

    async def get_execution_full(self, exec_id: str) -> dict | None:
        """Return execution details, checking in-memory state first and
        falling back to the database.
        """
        # Try in-memory first (contains richer live data).
        result = self.get_execution(exec_id)
        if result:
            return result

        # Fall back to database.
        row = await self._db.get_runbook_execution(exec_id)
        if row:
            return dict(row)
        return None

    async def list_executions(
        self,
        limit: int = 50,
        status: str | None = None,
        runbook_name: str | None = None,
    ) -> list[dict]:
        """List execution records from the database.

        Parameters
        ----------
        limit:
            Maximum number of records to return.
        status:
            Optional filter by execution status.
        runbook_name:
            Optional filter by runbook name.
        """
        return await self._db.list_runbook_executions(
            status=status,
            runbook_name=runbook_name,
            limit=limit,
        )

    def get_active_executions(self) -> list[dict]:
        """Return snapshot dicts for all currently in-flight executions."""
        results: list[dict] = []
        for exec_id, executor in self._executions.items():
            if executor.status == "running":
                results.append(
                    {
                        "id": exec_id,
                        "runbook_name": executor._runbook.name,
                        "status": executor.status,
                        "started_at": (
                            executor._started_at.isoformat()
                            if executor._started_at
                            else None
                        ),
                        "duration": executor.duration,
                        "dry_run": executor._dry_run,
                    }
                )
        return results

    # ------------------------------------------------------------------ #
    # Status / statistics                                                  #
    # ------------------------------------------------------------------ #

    def get_status(self) -> dict[str, Any]:
        """Return a summary of the engine's current state."""
        return {
            "runbooks_loaded": len(self._runbooks),
            "runbooks_enabled": sum(
                1 for rb in self._runbooks.values() if rb.enabled
            ),
            "active_executions": self._active_count,
            "max_global_executions": self._max_global,
            "total_tracked_executions": len(self._executions),
            "cooldown_entries": len(self._cooldowns),
            "device_locks": len(self._device_locks),
            "dry_run": self._dry_run,
            "runbook_dir": str(self._runbook_dir),
        }
