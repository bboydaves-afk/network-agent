"""Scheduled job manager using APScheduler.

Wraps APScheduler's AsyncIOScheduler to provide:
- Automatic registration of runbook-defined scheduled jobs (trigger.type == "schedule")
- Built-in recurring jobs from config.yaml (nightly backup, metric cleanup, etc.)
- A public API for listing, inspecting, running, pausing, and resuming jobs
- Full audit logging of every scheduled action
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
)

logger = logging.getLogger(__name__)


class SchedulerManager:
    """Wraps APScheduler's AsyncIOScheduler for scheduled network operations.

    Responsibilities
    ----------------
    - Load runbook-defined scheduled jobs (trigger.type == "schedule")
    - Load config-defined built-in jobs (nightly backup, metric cleanup, etc.)
    - Provide API for listing, running, pausing, resuming jobs
    - Emit audit log entries for every scheduled execution
    """

    def __init__(
        self,
        automation_engine,       # AutomationEngine
        config_manager,          # ConfigManager
        discovery,               # NetworkDiscovery
        db,                      # Database
        audit_logger,            # AuditLogger
        alert_engine=None,       # AlertEngine (optional)
        monitor=None,            # MonitoringEngine (optional)
        config: dict | None = None,
    ) -> None:
        self._automation = automation_engine
        self._config_manager = config_manager
        self._discovery = discovery
        self._db = db
        self._audit = audit_logger
        self._alert_engine = alert_engine
        self._monitor = monitor
        self._config = config or {}

        self._scheduler = AsyncIOScheduler(
            timezone=self._config.get("timezone", "UTC"),
        )
        # job_id -> metadata dict
        self._jobs: dict[str, dict[str, Any]] = {}
        # Track execution history (last N per job)
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._max_history = 50

        # Listen for APScheduler lifecycle events
        self._scheduler.add_listener(self._on_job_executed, EVENT_JOB_EXECUTED)
        self._scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)
        self._scheduler.add_listener(self._on_job_missed, EVENT_JOB_MISSED)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Register all jobs and start the scheduler."""
        self._register_builtin_jobs()
        self._register_runbook_schedules()
        self._scheduler.start()
        logger.info("Scheduler started with %d jobs", len(self._jobs))

    async def stop(self) -> None:
        """Shut down the scheduler gracefully."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    # ------------------------------------------------------------------
    # APScheduler event listeners
    # ------------------------------------------------------------------

    def _on_job_executed(self, event: JobExecutionEvent) -> None:
        """Record successful job execution in history."""
        self._record_history(event.job_id, "success", None)

    def _on_job_error(self, event: JobExecutionEvent) -> None:
        """Record failed job execution in history."""
        error_msg = str(event.exception) if event.exception else "Unknown error"
        self._record_history(event.job_id, "error", error_msg)

    def _on_job_missed(self, event: JobExecutionEvent) -> None:
        """Record missed job execution in history."""
        self._record_history(event.job_id, "missed", "Job missed its scheduled time")

    def _record_history(
        self, job_id: str, status: str, error: str | None
    ) -> None:
        """Append an execution record to the job's history buffer."""
        if job_id not in self._history:
            self._history[job_id] = []
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "error": error,
        }
        self._history[job_id].append(record)
        # Trim to max history length
        if len(self._history[job_id]) > self._max_history:
            self._history[job_id] = self._history[job_id][-self._max_history:]

    # ------------------------------------------------------------------
    # Built-in job registration
    # ------------------------------------------------------------------

    def _register_builtin_jobs(self) -> None:
        """Register built-in scheduled jobs from the scheduler config block."""
        jobs_config = self._config.get("jobs", {})

        # -- Nightly config backup --
        nightly = jobs_config.get("nightly_backup", {})
        if nightly.get("enabled", False):
            cron = nightly.get("cron", "0 2 * * *")
            tz = self._config.get("timezone", "UTC")
            job = self._scheduler.add_job(
                self._job_nightly_backup,
                CronTrigger.from_crontab(cron, timezone=tz),
                id="builtin-nightly-backup",
                replace_existing=True,
                name="Nightly Config Backup",
            )
            self._jobs[job.id] = {
                "type": "builtin",
                "name": "Nightly Config Backup",
                "description": nightly.get(
                    "description", "Backup all device configurations nightly"
                ),
                "cron": cron,
                "enabled": True,
            }

        # -- Metric cleanup --
        cleanup = jobs_config.get("metric_cleanup", {})
        if cleanup.get("enabled", False):
            cron = cleanup.get("cron", "0 3 * * *")
            retention = cleanup.get("retention_days", 30)
            tz = self._config.get("timezone", "UTC")
            job = self._scheduler.add_job(
                self._job_metric_cleanup,
                CronTrigger.from_crontab(cron, timezone=tz),
                id="builtin-metric-cleanup",
                replace_existing=True,
                name="Metric Cleanup",
                kwargs={"retention_days": retention},
            )
            self._jobs[job.id] = {
                "type": "builtin",
                "name": "Metric Cleanup",
                "description": cleanup.get(
                    "description",
                    f"Delete metrics older than {retention} days",
                ),
                "cron": cron,
                "enabled": True,
                "params": {"retention_days": retention},
            }

        # -- Weekly compliance check --
        compliance = jobs_config.get("weekly_compliance", {})
        if compliance.get("enabled", False):
            cron = compliance.get("cron", "0 4 * * 6")
            tz = self._config.get("timezone", "UTC")
            job = self._scheduler.add_job(
                self._job_weekly_compliance,
                CronTrigger.from_crontab(cron, timezone=tz),
                id="builtin-weekly-compliance",
                replace_existing=True,
                name="Weekly Compliance Check",
            )
            self._jobs[job.id] = {
                "type": "builtin",
                "name": "Weekly Compliance Check",
                "description": compliance.get(
                    "description",
                    "Compare running configs against latest backups for drift detection",
                ),
                "cron": cron,
                "enabled": True,
            }

        # -- Periodic network discovery --
        disc = jobs_config.get("periodic_discovery", {})
        if disc.get("enabled", False):
            cron = disc.get("cron", "0 6 * * 1")
            params = disc.get("params", {})
            tz = self._config.get("timezone", "UTC")
            job = self._scheduler.add_job(
                self._job_periodic_discovery,
                CronTrigger.from_crontab(cron, timezone=tz),
                id="builtin-periodic-discovery",
                replace_existing=True,
                name="Periodic Network Discovery",
                kwargs={
                    "subnet": params.get("subnet", ""),
                    "community": params.get("community", "public"),
                },
            )
            self._jobs[job.id] = {
                "type": "builtin",
                "name": "Periodic Network Discovery",
                "description": disc.get(
                    "description", "Scan network for new devices"
                ),
                "cron": cron,
                "enabled": True,
                "params": params,
            }

        # -- Periodic monitoring poll --
        polling = jobs_config.get("periodic_poll", {})
        if polling.get("enabled", False) and self._monitor is not None:
            cron = polling.get("cron", "*/5 * * * *")
            tz = self._config.get("timezone", "UTC")
            job = self._scheduler.add_job(
                self._job_periodic_poll,
                CronTrigger.from_crontab(cron, timezone=tz),
                id="builtin-periodic-poll",
                replace_existing=True,
                name="Periodic Device Poll",
            )
            self._jobs[job.id] = {
                "type": "builtin",
                "name": "Periodic Device Poll",
                "description": polling.get(
                    "description", "Poll all devices for health metrics"
                ),
                "cron": cron,
                "enabled": True,
            }

        # -- Alert rule evaluation --
        alert_eval = jobs_config.get("alert_evaluation", {})
        if alert_eval.get("enabled", False) and self._alert_engine is not None:
            cron = alert_eval.get("cron", "*/2 * * * *")
            tz = self._config.get("timezone", "UTC")
            job = self._scheduler.add_job(
                self._job_alert_evaluation,
                CronTrigger.from_crontab(cron, timezone=tz),
                id="builtin-alert-evaluation",
                replace_existing=True,
                name="Alert Rule Evaluation",
            )
            self._jobs[job.id] = {
                "type": "builtin",
                "name": "Alert Rule Evaluation",
                "description": alert_eval.get(
                    "description", "Evaluate alert rules against recent metrics"
                ),
                "cron": cron,
                "enabled": True,
            }

    # ------------------------------------------------------------------
    # Runbook schedule registration
    # ------------------------------------------------------------------

    def _register_runbook_schedules(self) -> None:
        """Register APScheduler jobs for all runbooks with trigger.type == 'schedule'."""
        for runbook in self._automation.list_runbooks():
            if (
                runbook.trigger_type == "schedule"
                and runbook.enabled
                and runbook.trigger_schedule_cron
            ):
                self._add_runbook_job(runbook)

    def _add_runbook_job(self, runbook) -> None:
        """Add a single runbook as a scheduled APScheduler job."""
        job_id = f"runbook-{runbook.name}"
        tz = runbook.trigger_schedule_timezone or self._config.get("timezone", "UTC")

        try:
            trigger = CronTrigger.from_crontab(runbook.trigger_schedule_cron, timezone=tz)
            job = self._scheduler.add_job(
                self._run_scheduled_runbook,
                trigger=trigger,
                args=[runbook.name],
                id=job_id,
                replace_existing=True,
                name=f"Runbook: {runbook.name}",
            )
            self._jobs[job_id] = {
                "type": "runbook",
                "name": runbook.name,
                "description": runbook.description or "",
                "cron": runbook.trigger_schedule_cron,
                "timezone": tz,
                "enabled": runbook.enabled,
            }
            logger.info(
                "Registered scheduled runbook: %s (%s TZ=%s)",
                runbook.name,
                runbook.trigger_schedule_cron,
                tz,
            )
        except Exception as e:
            logger.error(
                "Failed to register runbook schedule %s: %s", runbook.name, e
            )

    # ------------------------------------------------------------------
    # Built-in job handlers
    # ------------------------------------------------------------------

    async def _job_nightly_backup(self) -> None:
        """Backup all device configurations."""
        logger.info("Starting nightly config backup...")
        try:
            results = await self._config_manager.backup_all()
            count = len(results) if results else 0

            # Count successes vs failures
            success_count = sum(1 for r in results if r is not None) if results else 0
            fail_count = count - success_count

            description = (
                f"Nightly config backup completed: {success_count} devices backed up"
            )
            if fail_count > 0:
                description += f", {fail_count} failed"

            await self._audit.log(
                actor="scheduler",
                action_type="nightly_backup",
                description=description,
                result="success" if fail_count == 0 else "partial",
                details={
                    "total": count,
                    "success": success_count,
                    "failed": fail_count,
                },
            )
            logger.info("Nightly backup completed: %d devices", success_count)
        except Exception as e:
            await self._audit.log(
                actor="scheduler",
                action_type="nightly_backup",
                description=f"Nightly config backup failed: {e}",
                result="failure",
            )
            logger.exception("Nightly backup failed")

    async def _job_metric_cleanup(self, retention_days: int = 30) -> None:
        """Clean up metrics older than the retention period."""
        logger.info("Starting metric cleanup (retention=%d days)...", retention_days)
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=retention_days)
            ).isoformat()
            deleted = await self._db.cleanup_old_metrics(cutoff)
            await self._audit.log(
                actor="scheduler",
                action_type="metric_cleanup",
                description=(
                    f"Cleaned up {deleted} metric records older than {retention_days} days"
                ),
                result="success",
                details={"deleted": deleted, "retention_days": retention_days},
            )
            logger.info("Metric cleanup: deleted %s records", deleted)
        except Exception as e:
            await self._audit.log(
                actor="scheduler",
                action_type="metric_cleanup",
                description=f"Metric cleanup failed: {e}",
                result="failure",
            )
            logger.exception("Metric cleanup failed")

    async def _job_weekly_compliance(self) -> None:
        """Compare current running configs against the latest backups to detect drift.

        For each device that has at least one previous backup, a fresh backup is
        taken and compared against the prior version.  Devices whose config has
        changed since the last backup are flagged as drifted.
        """
        logger.info("Starting weekly compliance check...")
        try:
            devices = await self._db.list_devices()
            checked = 0
            drifted: list[dict[str, Any]] = []
            errors: list[str] = []

            for dev in devices:
                device_id = dev["id"]
                try:
                    # Get the most recent backup before this run
                    prior_backups = await self._db.get_config_backups(device_id, limit=1)
                    if not prior_backups:
                        logger.debug(
                            "Compliance: no prior backup for %s, skipping drift check",
                            device_id,
                        )
                        continue

                    prior_hash = prior_backups[0].get("config_hash", "")

                    # Take a fresh backup for comparison
                    try:
                        new_backup = await self._config_manager.backup_config(
                            device_id, backup_type="compliance"
                        )
                    except Exception as backup_err:
                        errors.append(f"{device_id}: backup failed ({backup_err})")
                        continue

                    new_hash = new_backup.get("config_hash", "")

                    checked += 1
                    if new_hash != prior_hash:
                        drifted.append(
                            {
                                "device_id": device_id,
                                "hostname": dev.get("hostname", ""),
                                "prior_backup_id": prior_backups[0].get("id"),
                                "new_backup_id": new_backup.get("id"),
                            }
                        )
                except Exception as dev_err:
                    errors.append(f"{device_id}: {dev_err}")

            description = (
                f"Weekly compliance check: {checked} devices checked, "
                f"{len(drifted)} drifted, {len(errors)} errors"
            )
            await self._audit.log(
                actor="scheduler",
                action_type="weekly_compliance",
                description=description,
                result="success" if not errors else "partial",
                details={
                    "devices_checked": checked,
                    "devices_drifted": len(drifted),
                    "drifted_devices": drifted,
                    "errors": errors,
                },
            )
            logger.info(description)

            if drifted:
                logger.warning(
                    "Config drift detected on %d device(s): %s",
                    len(drifted),
                    ", ".join(d["device_id"] for d in drifted),
                )
        except Exception as e:
            await self._audit.log(
                actor="scheduler",
                action_type="weekly_compliance",
                description=f"Weekly compliance check failed: {e}",
                result="failure",
            )
            logger.exception("Weekly compliance check failed")

    async def _job_periodic_discovery(
        self, subnet: str = "", community: str = "public"
    ) -> None:
        """Scan a subnet for new network devices."""
        if not subnet:
            logger.warning("Periodic discovery skipped: no subnet configured")
            return

        logger.info("Starting periodic discovery of %s...", subnet)
        try:
            results = await self._discovery.scan_subnet(subnet, community)
            found = len(results) if results else 0
            await self._audit.log(
                actor="scheduler",
                action_type="periodic_discovery",
                description=f"Network scan of {subnet}: found {found} devices",
                result="success",
                details={
                    "subnet": subnet,
                    "community": community,
                    "found": found,
                },
            )
            logger.info(
                "Periodic discovery of %s: found %d devices", subnet, found
            )
        except Exception as e:
            await self._audit.log(
                actor="scheduler",
                action_type="periodic_discovery",
                description=f"Network discovery of {subnet} failed: {e}",
                result="failure",
                details={"subnet": subnet},
            )
            logger.exception("Periodic discovery of %s failed", subnet)

    async def _job_periodic_poll(self) -> None:
        """Poll all devices for health metrics."""
        if self._monitor is None:
            logger.warning("Periodic poll skipped: no monitor engine configured")
            return

        logger.info("Starting periodic device poll...")
        try:
            summary = await self._monitor.poll_all_devices()
            total = summary.get("total", 0)
            online = summary.get("online", 0)
            offline = summary.get("offline", 0)
            await self._audit.log(
                actor="scheduler",
                action_type="periodic_poll",
                description=(
                    f"Device poll completed: {total} total, {online} online, "
                    f"{offline} offline"
                ),
                result="success",
                details=summary,
            )
            logger.info(
                "Periodic poll: %d total, %d online, %d offline",
                total,
                online,
                offline,
            )
        except Exception as e:
            await self._audit.log(
                actor="scheduler",
                action_type="periodic_poll",
                description=f"Periodic device poll failed: {e}",
                result="failure",
            )
            logger.exception("Periodic device poll failed")

    async def _job_alert_evaluation(self) -> None:
        """Evaluate alert rules against the latest metrics for all devices."""
        if self._alert_engine is None:
            logger.warning("Alert evaluation skipped: no alert engine configured")
            return

        logger.info("Starting alert rule evaluation...")
        try:
            devices = await self._db.list_devices()
            evaluated = 0
            alerts_fired = 0

            for dev in devices:
                device_id = dev["id"]
                try:
                    # Gather latest metrics for the device
                    now_iso = datetime.now(timezone.utc).isoformat()
                    one_hour_ago = (
                        datetime.now(timezone.utc) - timedelta(hours=1)
                    ).isoformat()

                    metrics: dict[str, Any] = {"device_id": device_id}

                    # Fetch latest value for standard metric names
                    for metric_name in (
                        "cpu_percent",
                        "memory_percent",
                        "interface_in_errors",
                        "interface_out_errors",
                        "device_reachable",
                        "temperature_celsius",
                    ):
                        rows = await self._db.get_metrics(
                            device_id=device_id,
                            metric_name=metric_name,
                            start_time=one_hour_ago,
                            limit=1,
                        )
                        if rows:
                            metrics[metric_name] = rows[0].get("metric_value", 0.0)

                    if len(metrics) > 1:  # has at least one real metric beyond device_id
                        await self._alert_engine.evaluate_rules(device_id, metrics)
                        evaluated += 1
                except Exception as dev_err:
                    logger.debug(
                        "Alert evaluation error for %s: %s", device_id, dev_err
                    )

            logger.info("Alert evaluation completed for %d devices", evaluated)
        except Exception as e:
            logger.exception("Alert evaluation failed: %s", e)

    # ------------------------------------------------------------------
    # Scheduled runbook execution
    # ------------------------------------------------------------------

    async def _run_scheduled_runbook(self, runbook_name: str) -> None:
        """Execute a runbook triggered by its schedule."""
        logger.info("Executing scheduled runbook: %s", runbook_name)
        try:
            exec_id = await self._automation.execute_runbook(
                runbook_name,
                context={
                    "trigger": "schedule",
                    "trigger_type": "schedule",
                    "scheduled_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            await self._audit.log(
                actor="scheduler",
                action_type="runbook_scheduled",
                description=(
                    f"Scheduled runbook '{runbook_name}' executed successfully"
                ),
                runbook_name=runbook_name,
                result="success",
                details={"execution_id": exec_id},
            )
            logger.info(
                "Scheduled runbook %s executed: exec_id=%s",
                runbook_name,
                exec_id,
            )
        except Exception as e:
            logger.exception(
                "Scheduled runbook %s failed: %s", runbook_name, e
            )
            await self._audit.log(
                actor="scheduler",
                action_type="runbook_scheduled",
                description=f"Scheduled runbook '{runbook_name}' failed: {e}",
                runbook_name=runbook_name,
                result="failure",
            )

    # ------------------------------------------------------------------
    # Public API -- job listing and inspection
    # ------------------------------------------------------------------

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return metadata for all registered jobs, including next run time."""
        result: list[dict[str, Any]] = []
        for job_id, meta in self._jobs.items():
            job = self._scheduler.get_job(job_id)
            entry: dict[str, Any] = {
                "id": job_id,
                **meta,
                "next_run": (
                    str(job.next_run_time) if job and job.next_run_time else None
                ),
                "running": False,
            }

            # Attach last execution info if available
            history = self._history.get(job_id, [])
            if history:
                entry["last_run"] = history[-1]
            else:
                entry["last_run"] = None

            result.append(entry)
        return result

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Get metadata for a specific job, or None if not found."""
        meta = self._jobs.get(job_id)
        if not meta:
            return None
        job = self._scheduler.get_job(job_id)
        info: dict[str, Any] = {
            "id": job_id,
            **meta,
            "next_run": (
                str(job.next_run_time) if job and job.next_run_time else None
            ),
        }
        info["history"] = self._history.get(job_id, [])
        return info

    def get_job_history(self, job_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return the execution history for a specific job."""
        history = self._history.get(job_id, [])
        return history[-limit:]

    # ------------------------------------------------------------------
    # Public API -- job control
    # ------------------------------------------------------------------

    async def run_job_now(self, job_id: str) -> None:
        """Trigger immediate execution of a scheduled job.

        The job function is invoked directly (not via APScheduler's internal
        queue) so the caller can await its completion.
        """
        job = self._scheduler.get_job(job_id)
        if not job:
            raise ValueError(f"Job '{job_id}' not found")

        logger.info("Manual trigger of job: %s", job_id)

        # Execute the job function with its configured args/kwargs
        if asyncio.iscoroutinefunction(job.func):
            await job.func(*job.args, **job.kwargs)
        else:
            job.func(*job.args, **job.kwargs)

        await self._audit.log(
            actor="scheduler",
            action_type="manual_job_run",
            description=f"Job '{job_id}' manually triggered",
            details={"job_id": job_id},
        )

    def pause_job(self, job_id: str) -> None:
        """Pause a scheduled job so it will not fire at its next scheduled time."""
        if job_id not in self._jobs:
            raise ValueError(f"Job '{job_id}' not found in registry")
        self._scheduler.pause_job(job_id)
        self._jobs[job_id]["enabled"] = False
        logger.info("Paused job: %s", job_id)

    def resume_job(self, job_id: str) -> None:
        """Resume a previously paused job."""
        if job_id not in self._jobs:
            raise ValueError(f"Job '{job_id}' not found in registry")
        self._scheduler.resume_job(job_id)
        self._jobs[job_id]["enabled"] = True
        logger.info("Resumed job: %s", job_id)

    def remove_job(self, job_id: str) -> None:
        """Remove a job entirely from the scheduler."""
        if job_id not in self._jobs:
            raise ValueError(f"Job '{job_id}' not found in registry")
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass  # Job may have already been removed from APScheduler
        del self._jobs[job_id]
        self._history.pop(job_id, None)
        logger.info("Removed job: %s", job_id)

    # ------------------------------------------------------------------
    # Public API -- runbook schedule management
    # ------------------------------------------------------------------

    async def reload_runbook_schedules(self) -> None:
        """Reload runbook-based schedules after runbooks are modified.

        Removes all existing runbook-* jobs and re-registers them from the
        current set of loaded runbooks.
        """
        removed = 0
        for job_id in list(self._jobs.keys()):
            if job_id.startswith("runbook-"):
                try:
                    self._scheduler.remove_job(job_id)
                except Exception:
                    pass
                del self._jobs[job_id]
                removed += 1

        self._register_runbook_schedules()
        logger.info(
            "Reloaded runbook schedules: removed %d old, registered %d new",
            removed,
            sum(1 for jid in self._jobs if jid.startswith("runbook-")),
        )

    def add_dynamic_job(
        self,
        job_id: str,
        cron: str,
        func,
        *,
        name: str = "",
        description: str = "",
        args: list | None = None,
        kwargs: dict | None = None,
    ) -> dict[str, Any]:
        """Add a custom scheduled job at runtime.

        Parameters
        ----------
        job_id : str
            Unique identifier for the job.
        cron : str
            Crontab expression (5-field).
        func : callable
            The async or sync function to execute.
        name : str
            Human-readable name.
        description : str
            Description of what the job does.
        args : list, optional
            Positional arguments forwarded to the function.
        kwargs : dict, optional
            Keyword arguments forwarded to the function.

        Returns
        -------
        dict
            The metadata dict stored for this job.
        """
        tz = self._config.get("timezone", "UTC")
        trigger = CronTrigger.from_crontab(cron, timezone=tz)

        job = self._scheduler.add_job(
            func,
            trigger=trigger,
            args=args or [],
            kwargs=kwargs or {},
            id=job_id,
            replace_existing=True,
            name=name or job_id,
        )

        meta: dict[str, Any] = {
            "type": "dynamic",
            "name": name or job_id,
            "description": description,
            "cron": cron,
            "enabled": True,
        }
        self._jobs[job_id] = meta
        logger.info("Added dynamic job: %s (%s)", job_id, cron)
        return meta

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the APScheduler instance is currently running."""
        return self._scheduler.running

    @property
    def job_count(self) -> int:
        """Number of registered jobs."""
        return len(self._jobs)
