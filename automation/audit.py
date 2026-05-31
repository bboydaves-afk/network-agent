"""Audit logger -- records every autonomous action to the database."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import uuid4

from core.database import Database

logger = logging.getLogger(__name__)

# SQL to create the audit_log table if it does not exist.
_CREATE_AUDIT_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    action_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    runbook_name TEXT,
    execution_id TEXT,
    device_id TEXT,
    details TEXT DEFAULT '{}',
    result TEXT NOT NULL DEFAULT 'success',
    before_state TEXT,
    after_state TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_execution
    ON audit_log(execution_id);
CREATE INDEX IF NOT EXISTS idx_audit_device
    ON audit_log(device_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp
    ON audit_log(timestamp);
"""


class AuditLogger:
    """Append-only audit trail for every automation action.

    Each entry captures who did what, when, on which device, with full
    before/after state snapshots when available.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._initialized = False

    async def _ensure_table(self) -> None:
        """Create the audit_log table on first use."""
        if self._initialized:
            return
        try:
            db = self._db._ensure_connected()
            await db.executescript(_CREATE_AUDIT_TABLE)
            await db.commit()
            self._initialized = True
        except Exception as exc:
            logger.error("Failed to create audit_log table: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def log(
        self,
        actor: str,
        action_type: str,
        description: str,
        runbook_name: str | None = None,
        execution_id: str | None = None,
        device_id: str | None = None,
        details: dict[str, Any] | None = None,
        result: str = "success",
        before_state: dict[str, Any] | str | None = None,
        after_state: dict[str, Any] | str | None = None,
    ) -> str:
        """Create an audit entry and return its unique ID.

        Parameters
        ----------
        actor:
            Identifier for who/what triggered the action (e.g.
            ``"automation-engine"``, ``"user:admin"``).
        action_type:
            Category of the action (e.g. ``"runbook_started"``,
            ``"action_executed"``, ``"escalation_fired"``).
        description:
            Human-readable summary of what happened.
        runbook_name:
            Name of the associated runbook, if applicable.
        execution_id:
            Execution session ID, if applicable.
        device_id:
            Target device ID, if applicable.
        details:
            Arbitrary structured metadata (serialized as JSON).
        result:
            Outcome indicator: ``"success"``, ``"failure"``, ``"skipped"``, etc.
        before_state:
            Snapshot of the relevant state *before* the action.
        after_state:
            Snapshot of the relevant state *after* the action.

        Returns
        -------
        str
            The unique audit entry ID.
        """
        await self._ensure_table()

        entry_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        details_json = json.dumps(details) if details else "{}"
        before_json = (
            json.dumps(before_state)
            if isinstance(before_state, dict)
            else (before_state or None)
        )
        after_json = (
            json.dumps(after_state)
            if isinstance(after_state, dict)
            else (after_state or None)
        )

        await self._db.execute(
            """
            INSERT INTO audit_log
                (id, timestamp, actor, action_type, description,
                 runbook_name, execution_id, device_id,
                 details, result, before_state, after_state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                now,
                actor,
                action_type,
                description,
                runbook_name,
                execution_id,
                device_id,
                details_json,
                result,
                before_json,
                after_json,
            ),
        )
        logger.debug(
            "Audit: [%s] %s -- %s (result=%s)", action_type, actor, description, result
        )
        return entry_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_log(
        self,
        device_id: str | None = None,
        actor: str | None = None,
        execution_id: str | None = None,
        action_type: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query audit entries with optional filters.

        All filters are combined with AND logic.  Results are returned newest
        first.
        """
        await self._ensure_table()

        query = "SELECT * FROM audit_log"
        conditions: list[str] = []
        params: list[Any] = []

        if device_id is not None:
            conditions.append("device_id = ?")
            params.append(device_id)
        if actor is not None:
            conditions.append("actor = ?")
            params.append(actor)
        if execution_id is not None:
            conditions.append("execution_id = ?")
            params.append(execution_id)
        if action_type is not None:
            conditions.append("action_type = ?")
            params.append(action_type)
        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = await self._db.fetch_all(query, params)
        return [self._deserialize_row(row) for row in rows]

    async def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        """Retrieve a single audit entry by its ID."""
        await self._ensure_table()

        row = await self._db.fetch_one(
            "SELECT * FROM audit_log WHERE id = ?", (entry_id,)
        )
        if row is None:
            return None
        return self._deserialize_row(row)

    async def get_execution_audit(
        self, execution_id: str
    ) -> list[dict[str, Any]]:
        """Return all audit entries for a given execution, oldest first."""
        await self._ensure_table()

        rows = await self._db.fetch_all(
            "SELECT * FROM audit_log WHERE execution_id = ? ORDER BY timestamp ASC",
            (execution_id,),
        )
        return [self._deserialize_row(row) for row in rows]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def cleanup_old_entries(self, days: int = 90) -> int:
        """Delete audit entries older than *days* days.

        Returns the number of deleted entries.
        """
        await self._ensure_table()

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        cursor = await self._db.execute(
            "DELETE FROM audit_log WHERE timestamp < ?", (cutoff,)
        )
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info("Cleaned up %d audit entries older than %d days.", deleted, days)
        return deleted

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deserialize_row(row: dict[str, Any]) -> dict[str, Any]:
        """Parse JSON fields in an audit row back into Python objects."""
        result = dict(row)
        for json_field in ("details", "before_state", "after_state"):
            val = result.get(json_field)
            if isinstance(val, str):
                try:
                    result[json_field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass  # Leave as string
        return result
