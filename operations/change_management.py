"""Change management with approval workflow for config deployments.

Extended to support:
- Scheduled maintenance windows
- Rollback plans with automatic rollback on failure
- Pre/post deployment health checks
- Multi-approver tracking
- Full change history audit trail
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class ChangeManager:
    """Approval gate for configuration changes with rollback and health checks."""

    def __init__(self, db, config_manager=None) -> None:
        self._db = db
        self._config_mgr = config_manager

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    async def _get_config_manager(self):
        """Return the config_manager, raising if unavailable."""
        if not self._config_mgr:
            raise RuntimeError("ConfigManager not available — cannot deploy changes")
        return self._config_mgr

    # ------------------------------------------------------------------
    # Create / Read / List
    # ------------------------------------------------------------------

    async def create_request(
        self,
        device_id: str,
        title: str,
        config_commands: str,
        requested_by: str = "admin",
        priority: str = "normal",
        notes: str | None = None,
        rollback_plan: str | None = None,
        scheduled_at: str | None = None,
        maintenance_window_start: str | None = None,
        maintenance_window_end: str | None = None,
    ) -> dict[str, Any]:
        """Create a new change request with optional rollback plan and scheduling."""
        req_id = str(uuid4())
        now = self._now()
        await self._db.execute(
            """INSERT INTO change_requests
               (id, device_id, title, config_commands, requested_by, status,
                priority, notes, rollback_plan, scheduled_at,
                maintenance_window_start, maintenance_window_end, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)""",
            (
                req_id, device_id, title, config_commands, requested_by,
                priority, notes, rollback_plan, scheduled_at,
                maintenance_window_start, maintenance_window_end, now,
            ),
        )
        return await self.get_request(req_id)

    async def get_request(self, request_id: str) -> dict[str, Any] | None:
        """Get a single change request with its approvals and rollbacks."""
        cr = await self._db.fetch_one(
            "SELECT * FROM change_requests WHERE id = ?", (request_id,)
        )
        if not cr:
            return None
        cr = dict(cr)
        cr["approvals"] = await self._db.get_change_approvals(request_id)
        cr["rollbacks"] = await self._db.get_change_rollbacks(request_id)
        return cr

    async def list_requests(
        self, status: str | None = None, device_id: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List change requests with optional filters."""
        query = "SELECT * FROM change_requests WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if device_id:
            query += " AND device_id = ?"
            params.append(device_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return await self._db.fetch_all(query, params)

    async def list_pending(self) -> list[dict[str, Any]]:
        """List all change requests awaiting approval."""
        return await self.list_requests(status="pending")

    async def get_change_history(self, device_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get full change history for a device — all statuses, with approvals and rollbacks."""
        changes = await self._db.fetch_all(
            "SELECT * FROM change_requests WHERE device_id = ? ORDER BY created_at DESC LIMIT ?",
            (device_id, limit),
        )
        result = []
        for cr in changes:
            cr = dict(cr)
            cr["approvals"] = await self._db.get_change_approvals(cr["id"])
            cr["rollbacks"] = await self._db.get_change_rollbacks(cr["id"])
            result.append(cr)
        return result

    # ------------------------------------------------------------------
    # Approve / Reject
    # ------------------------------------------------------------------

    async def approve_request(
        self, request_id: str, approved_by: str = "admin", notes: str | None = None,
    ) -> dict[str, Any]:
        """Approve a pending change request and record the approval."""
        now = self._now()
        # Record approval
        approval_id = str(uuid4())
        await self._db.add_change_approval(
            approval_id, request_id, approved_by, "approved", notes, now, now,
        )
        # Update main record
        await self._db.execute(
            """UPDATE change_requests
               SET status = 'approved', approved_by = ?, approved_at = ?,
                   notes = COALESCE(?, notes)
               WHERE id = ? AND status = 'pending'""",
            (approved_by, now, notes, request_id),
        )
        return await self.get_request(request_id)

    async def reject_request(
        self, request_id: str, rejected_by: str = "admin", reason: str | None = None,
    ) -> dict[str, Any]:
        """Reject a pending change request and record the rejection."""
        now = self._now()
        # Record rejection
        approval_id = str(uuid4())
        await self._db.add_change_approval(
            approval_id, request_id, rejected_by, "rejected", reason, now, now,
        )
        # Update main record
        await self._db.execute(
            """UPDATE change_requests
               SET status = 'rejected', approved_by = ?, rejected_at = ?,
                   notes = COALESCE(?, notes)
               WHERE id = ? AND status = 'pending'""",
            (rejected_by, now, reason, request_id),
        )
        return await self.get_request(request_id)

    # ------------------------------------------------------------------
    # Pre/Post Checks
    # ------------------------------------------------------------------

    async def _run_pre_checks(self, device_id: str) -> dict[str, Any]:
        """Run pre-deployment health checks — snapshot config + ping device."""
        checks = {"timestamp": self._now(), "passed": True, "checks": []}

        if not self._config_mgr:
            checks["checks"].append({"name": "config_manager", "status": "skip", "detail": "no config manager"})
            return checks

        # Check 1: device reachable
        try:
            from operations.troubleshoot import Troubleshooter
            ts = Troubleshooter(self._db, self._config_mgr._cred_manager if hasattr(self._config_mgr, "_cred_manager") else None)
            device = await self._db.get_device(device_id)
            if device:
                ping_result = await ts.ping(device["ip_address"])
                reachable = ping_result.get("reachable", False)
                checks["checks"].append({
                    "name": "device_reachable",
                    "status": "pass" if reachable else "fail",
                    "detail": f"Ping to {device['ip_address']}: {'OK' if reachable else 'FAILED'}",
                })
                if not reachable:
                    checks["passed"] = False
        except Exception as exc:
            checks["checks"].append({
                "name": "device_reachable", "status": "error",
                "detail": str(exc),
            })

        # Check 2: backup current config
        try:
            backup_result = await self._config_mgr.backup_config(device_id)
            checks["checks"].append({
                "name": "config_backup",
                "status": "pass",
                "detail": f"Backup created: {backup_result.get('id', 'ok')}",
            })
        except Exception as exc:
            checks["checks"].append({
                "name": "config_backup", "status": "warning",
                "detail": f"Backup failed (non-blocking): {exc}",
            })

        return checks

    async def _run_post_checks(self, device_id: str) -> dict[str, Any]:
        """Run post-deployment health checks — verify device still reachable."""
        checks = {"timestamp": self._now(), "passed": True, "checks": []}

        # Check 1: device still reachable after change
        try:
            from operations.troubleshoot import Troubleshooter
            ts = Troubleshooter(self._db, self._config_mgr._cred_manager if hasattr(self._config_mgr, "_cred_manager") else None)
            device = await self._db.get_device(device_id)
            if device:
                ping_result = await ts.ping(device["ip_address"])
                reachable = ping_result.get("reachable", False)
                checks["checks"].append({
                    "name": "post_deploy_reachable",
                    "status": "pass" if reachable else "fail",
                    "detail": f"Post-deploy ping to {device['ip_address']}: {'OK' if reachable else 'FAILED'}",
                })
                if not reachable:
                    checks["passed"] = False
        except Exception as exc:
            checks["checks"].append({
                "name": "post_deploy_reachable", "status": "error",
                "detail": str(exc),
            })
            checks["passed"] = False

        return checks

    # ------------------------------------------------------------------
    # Execute Change (the full pipeline)
    # ------------------------------------------------------------------

    async def execute_change(self, request_id: str) -> dict[str, Any]:
        """Execute an approved change request with the full pipeline:

        1. Validate status is 'approved'
        2. Run pre-deployment health checks
        3. Deploy configuration
        4. Run post-deployment health checks
        5. Auto-rollback if post-checks fail and rollback_plan exists
        """
        req = await self.get_request(request_id)
        if not req:
            return {"error": "Request not found"}
        if req["status"] != "approved":
            return {"error": f"Cannot execute request with status '{req['status']}'. Must be 'approved'."}

        now = self._now()
        result = {"change_id": request_id, "steps": []}

        # Step 1: Pre-checks
        pre_checks = await self._run_pre_checks(req["device_id"])
        await self._db.execute(
            "UPDATE change_requests SET pre_check_result = ? WHERE id = ?",
            (json.dumps(pre_checks), request_id),
        )
        result["steps"].append({"step": "pre_checks", "result": pre_checks})

        if not pre_checks["passed"]:
            await self._db.execute(
                "UPDATE change_requests SET status = 'failed', notes = ? WHERE id = ?",
                ("Pre-deployment checks failed — aborting", request_id),
            )
            result["status"] = "failed"
            result["error"] = "Pre-deployment checks failed"
            return result

        # Step 2: Deploy
        try:
            cm = await self._get_config_manager()
            commands = [c.strip() for c in req["config_commands"].split("\n") if c.strip()]
            deploy_result = await cm.deploy_config(req["device_id"], commands)
            deploy_id = deploy_result.get("id", "")

            result["steps"].append({"step": "deploy", "status": "success", "deploy_id": deploy_id})

            await self._db.execute(
                "UPDATE change_requests SET applied_at = ?, deploy_id = ? WHERE id = ?",
                (now, deploy_id, request_id),
            )
        except Exception as exc:
            await self._db.execute(
                "UPDATE change_requests SET status = 'failed', notes = ? WHERE id = ?",
                (f"Deploy failed: {exc}", request_id),
            )
            result["status"] = "failed"
            result["error"] = f"Deploy failed: {exc}"
            result["steps"].append({"step": "deploy", "status": "failed", "error": str(exc)})

            # Attempt rollback if we have a plan
            if req.get("rollback_plan"):
                rb_result = await self._execute_rollback(request_id, req["device_id"], req["rollback_plan"], "auto")
                result["steps"].append({"step": "auto_rollback", "result": rb_result})

            return result

        # Step 3: Post-checks
        post_checks = await self._run_post_checks(req["device_id"])
        await self._db.execute(
            "UPDATE change_requests SET post_check_result = ? WHERE id = ?",
            (json.dumps(post_checks), request_id),
        )
        result["steps"].append({"step": "post_checks", "result": post_checks})

        if not post_checks["passed"]:
            # Post-checks failed — auto-rollback if plan exists
            if req.get("rollback_plan"):
                logger.warning("Post-checks failed for %s, auto-rolling back", request_id)
                rb_result = await self._execute_rollback(request_id, req["device_id"], req["rollback_plan"], "auto")
                result["steps"].append({"step": "auto_rollback", "result": rb_result})

                await self._db.execute(
                    "UPDATE change_requests SET status = 'rolled_back', rollback_status = 'auto', notes = ? WHERE id = ?",
                    ("Post-checks failed — automatic rollback executed", request_id),
                )
                result["status"] = "rolled_back"
            else:
                await self._db.execute(
                    "UPDATE change_requests SET status = 'failed', notes = ? WHERE id = ?",
                    ("Post-checks failed — no rollback plan available", request_id),
                )
                result["status"] = "failed"
                result["error"] = "Post-checks failed and no rollback plan defined"
        else:
            # Success!
            await self._db.execute(
                "UPDATE change_requests SET status = 'applied' WHERE id = ?",
                (request_id,),
            )
            result["status"] = "applied"

        return result

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    async def _execute_rollback(
        self, request_id: str, device_id: str, rollback_commands: str, executed_by: str,
    ) -> dict[str, Any]:
        """Execute rollback commands and record the result."""
        rollback_id = str(uuid4())
        await self._db.add_change_rollback(rollback_id, request_id, rollback_commands, "executing", executed_by)

        try:
            cm = await self._get_config_manager()
            commands = [c.strip() for c in rollback_commands.split("\n") if c.strip()]
            deploy_result = await cm.deploy_config(device_id, commands)

            await self._db.update_change_rollback(
                rollback_id, "completed", json.dumps(deploy_result), self._now(),
            )
            return {"status": "completed", "rollback_id": rollback_id}
        except Exception as exc:
            await self._db.update_change_rollback(
                rollback_id, "failed", str(exc), self._now(),
            )
            return {"status": "failed", "rollback_id": rollback_id, "error": str(exc)}

    async def rollback_change(self, request_id: str, executed_by: str = "admin") -> dict[str, Any]:
        """Manually rollback an applied change request.

        Uses the stored rollback_plan, or returns an error if none exists.
        """
        req = await self.get_request(request_id)
        if not req:
            return {"error": "Request not found"}
        if req["status"] not in ("applied", "failed"):
            return {"error": f"Cannot rollback request with status '{req['status']}'. Must be 'applied' or 'failed'."}
        if not req.get("rollback_plan"):
            return {"error": "No rollback plan defined for this change request"}

        result = await self._execute_rollback(request_id, req["device_id"], req["rollback_plan"], executed_by)

        if result["status"] == "completed":
            await self._db.execute(
                "UPDATE change_requests SET status = 'rolled_back', rollback_status = 'manual' WHERE id = ?",
                (request_id,),
            )

        return result

    # ------------------------------------------------------------------
    # Legacy apply (kept for backward compat)
    # ------------------------------------------------------------------

    async def apply_request(self, request_id: str) -> dict[str, Any]:
        """Apply an approved change (simple deploy without pre/post checks).

        For production use, prefer ``execute_change()`` which runs the full pipeline.
        """
        req = await self.get_request(request_id)
        if not req:
            return {"error": "Request not found"}
        if req["status"] != "approved":
            return {"error": f"Cannot apply request with status '{req['status']}'"}

        now = self._now()
        try:
            if self._config_mgr:
                commands = [c.strip() for c in req["config_commands"].split("\n") if c.strip()]
                result = await self._config_mgr.deploy_config(req["device_id"], commands)
                deploy_id = result.get("id", "")
            else:
                deploy_id = ""

            await self._db.execute(
                "UPDATE change_requests SET status = 'applied', applied_at = ?, deploy_id = ? WHERE id = ?",
                (now, deploy_id, request_id),
            )
        except Exception as exc:
            await self._db.execute(
                "UPDATE change_requests SET status = 'failed', notes = ? WHERE id = ?",
                (str(exc), request_id),
            )
            return {"error": str(exc)}

        return await self.get_request(request_id)

    async def get_pending_count(self) -> int:
        """Get the count of pending change requests."""
        result = await self._db.fetch_one(
            "SELECT COUNT(*) as cnt FROM change_requests WHERE status = 'pending'"
        )
        return result["cnt"] if result else 0
