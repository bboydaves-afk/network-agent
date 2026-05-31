"""Async SQLite database manager for Network Agent."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from core.exceptions import DatabaseError

logger = logging.getLogger(__name__)


class Database:
    """Async SQLite database wrapper with domain-specific CRUD helpers."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Open the database connection and create all tables if they do not exist."""
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(self.db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA foreign_keys=ON")
            await self._create_tables()
            logger.info("Database initialized at %s", self.db_path)
        except Exception as exc:
            raise DatabaseError(f"Failed to initialize database: {exc}") from exc

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_tables(self) -> None:
        """Create all schema tables."""
        assert self._db is not None
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                hostname TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                device_type TEXT NOT NULL,
                protocol TEXT NOT NULL DEFAULT 'ssh',
                port INTEGER NOT NULL DEFAULT 22,
                credential_id TEXT,
                location TEXT,
                model TEXT,
                serial_number TEXT,
                os_version TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                last_seen TEXT,
                created_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS device_tags (
                device_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (device_id, tag),
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS credentials (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL,
                password TEXT,
                ssh_key_path TEXT,
                snmp_community TEXT,
                enable_secret TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS config_backups (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                config_text TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                backup_type TEXT NOT NULL DEFAULT 'running',
                file_path TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS config_deploys (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                config_diff TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                applied_by TEXT,
                applied_at TEXT,
                rollback_to TEXT,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS metrics (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                interface TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_metrics_device_time
                ON metrics(device_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_metrics_name
                ON metrics(metric_name);

            CREATE TABLE IF NOT EXISTS alert_rules (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                device_filter TEXT,
                metric_name TEXT NOT NULL,
                condition TEXT NOT NULL,
                threshold REAL NOT NULL,
                duration_seconds INTEGER NOT NULL DEFAULT 0,
                channel TEXT NOT NULL DEFAULT 'slack',
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                rule_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                metric_value REAL NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'firing',
                fired_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY (rule_id) REFERENCES alert_rules(id) ON DELETE CASCADE,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                action_type TEXT NOT NULL,
                runbook_name TEXT,
                execution_id TEXT,
                device_id TEXT,
                description TEXT NOT NULL,
                details TEXT DEFAULT '{}',
                result TEXT,
                before_state TEXT,
                after_state TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_device ON audit_log(device_id);
            CREATE INDEX IF NOT EXISTS idx_audit_execution ON audit_log(execution_id);

            CREATE TABLE IF NOT EXISTS runbook_executions (
                id TEXT PRIMARY KEY,
                runbook_name TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                trigger_details TEXT DEFAULT '{}',
                device_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                dry_run INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                duration_seconds REAL,
                context TEXT DEFAULT '{}',
                results TEXT DEFAULT '[]',
                error_message TEXT,
                escalation_level INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_exec_status ON runbook_executions(status);
            CREATE INDEX IF NOT EXISTS idx_exec_runbook ON runbook_executions(runbook_name);
            CREATE INDEX IF NOT EXISTS idx_exec_started ON runbook_executions(started_at);

            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                job_type TEXT NOT NULL,
                cron_expression TEXT NOT NULL,
                timezone TEXT NOT NULL DEFAULT 'UTC',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run TEXT,
                next_run TEXT,
                run_count INTEGER NOT NULL DEFAULT 0,
                last_result TEXT,
                created_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS sites (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                location TEXT,
                region TEXT,
                description TEXT,
                contact TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sites_name ON sites(name);
            CREATE INDEX IF NOT EXISTS idx_sites_region ON sites(region);

            CREATE TABLE IF NOT EXISTS topology_neighbors (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                local_interface TEXT NOT NULL,
                neighbor_device_id TEXT,
                neighbor_hostname TEXT,
                neighbor_ip TEXT,
                neighbor_port TEXT,
                neighbor_platform TEXT,
                protocol TEXT NOT NULL DEFAULT 'cdp',
                discovered_at TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS topology_snapshots (
                id TEXT PRIMARY KEY,
                name TEXT,
                snapshot_data TEXT NOT NULL,
                device_count INTEGER DEFAULT 0,
                link_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS firmware_catalog (
                id TEXT PRIMARY KEY,
                vendor TEXT NOT NULL,
                model_pattern TEXT,
                version TEXT NOT NULL,
                release_date TEXT,
                eol_date TEXT,
                eos_date TEXT,
                cve_list TEXT DEFAULT '[]',
                download_url TEXT,
                is_recommended INTEGER DEFAULT 0,
                notes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subnets (
                id TEXT PRIMARY KEY,
                network TEXT NOT NULL,
                prefix_length INTEGER NOT NULL,
                vlan_id INTEGER,
                name TEXT,
                site_id TEXT,
                description TEXT,
                gateway TEXT,
                dns_servers TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS ip_addresses (
                id TEXT PRIMARY KEY,
                subnet_id TEXT NOT NULL,
                address TEXT NOT NULL,
                hostname TEXT,
                mac_address TEXT,
                device_id TEXT,
                interface TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                last_seen TEXT,
                notes TEXT,
                FOREIGN KEY (subnet_id) REFERENCES subnets(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS syslog_messages (
                id TEXT PRIMARY KEY,
                device_id TEXT,
                timestamp TEXT NOT NULL,
                facility INTEGER,
                severity INTEGER,
                hostname TEXT,
                app_name TEXT,
                message TEXT NOT NULL,
                raw TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_syslog_timestamp ON syslog_messages(timestamp);
            CREATE INDEX IF NOT EXISTS idx_syslog_severity ON syslog_messages(severity);
            CREATE INDEX IF NOT EXISTS idx_syslog_device ON syslog_messages(device_id);

            CREATE TABLE IF NOT EXISTS compliance_results (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                ruleset_name TEXT NOT NULL,
                total_checks INTEGER DEFAULT 0,
                passed INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                score REAL DEFAULT 0.0,
                details TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS change_requests (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                title TEXT NOT NULL,
                config_commands TEXT NOT NULL,
                config_diff TEXT,
                requested_by TEXT NOT NULL,
                approved_by TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                priority TEXT NOT NULL DEFAULT 'normal',
                notes TEXT,
                created_at TEXT NOT NULL,
                approved_at TEXT,
                applied_at TEXT,
                rejected_at TEXT,
                deploy_id TEXT,
                scheduled_at TEXT,
                maintenance_window_start TEXT,
                maintenance_window_end TEXT,
                rollback_plan TEXT,
                pre_check_result TEXT,
                post_check_result TEXT,
                rollback_status TEXT,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_changes_status ON change_requests(status);
            CREATE INDEX IF NOT EXISTS idx_changes_device ON change_requests(device_id);

            CREATE TABLE IF NOT EXISTS change_approvals (
                id TEXT PRIMARY KEY,
                change_request_id TEXT NOT NULL,
                approver TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                notes TEXT,
                decided_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (change_request_id) REFERENCES change_requests(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_approvals_cr ON change_approvals(change_request_id);

            CREATE TABLE IF NOT EXISTS change_rollbacks (
                id TEXT PRIMARY KEY,
                change_request_id TEXT NOT NULL,
                rollback_commands TEXT NOT NULL,
                executed_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT,
                executed_by TEXT,
                FOREIGN KEY (change_request_id) REFERENCES change_requests(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_rollbacks_cr ON change_rollbacks(change_request_id);

            CREATE TABLE IF NOT EXISTS credential_rotations (
                id TEXT PRIMARY KEY,
                credential_id TEXT NOT NULL,
                old_password_hash TEXT,
                new_password_hash TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                devices_total INTEGER DEFAULT 0,
                devices_updated INTEGER DEFAULT 0,
                devices_failed INTEGER DEFAULT 0,
                failure_details TEXT DEFAULT '[]',
                started_at TEXT,
                completed_at TEXT,
                initiated_by TEXT,
                FOREIGN KEY (credential_id) REFERENCES credentials(id) ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS firewall_rules (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                policy_id TEXT,
                name TEXT NOT NULL,
                source_zone TEXT,
                dest_zone TEXT,
                source_addresses TEXT DEFAULT '[]',
                dest_addresses TEXT DEFAULT '[]',
                services TEXT DEFAULT '[]',
                action TEXT NOT NULL DEFAULT 'deny',
                enabled INTEGER DEFAULT 1,
                log_enabled INTEGER DEFAULT 0,
                position INTEGER DEFAULT 0,
                comment TEXT,
                synced_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS nat_rules (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                name TEXT NOT NULL,
                nat_type TEXT NOT NULL,
                source_zone TEXT,
                dest_zone TEXT,
                original_source TEXT,
                original_dest TEXT,
                original_service TEXT,
                translated_source TEXT,
                translated_dest TEXT,
                translated_service TEXT,
                enabled INTEGER DEFAULT 1,
                comment TEXT,
                synced_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS firewall_zones (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                name TEXT NOT NULL,
                interfaces TEXT DEFAULT '[]',
                security_level INTEGER DEFAULT 0,
                description TEXT,
                synced_at TEXT,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS firewall_objects (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                name TEXT NOT NULL,
                object_type TEXT NOT NULL,
                value TEXT,
                members TEXT DEFAULT '[]',
                description TEXT,
                synced_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_metrics_interface ON metrics(interface);

            CREATE TABLE IF NOT EXISTS vlans (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                vlan_id INTEGER NOT NULL,
                name TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                description TEXT,
                synced_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_vlans_device ON vlans(device_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_vlans_device_vlan
                ON vlans(device_id, vlan_id);

            CREATE TABLE IF NOT EXISTS vlan_interfaces (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                vlan_id INTEGER NOT NULL,
                interface_name TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'access',
                created_at TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_vlan_if_device ON vlan_interfaces(device_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_vlan_if_unique
                ON vlan_interfaces(device_id, vlan_id, interface_name);

            CREATE TABLE IF NOT EXISTS routes (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                destination TEXT NOT NULL,
                prefix_length INTEGER NOT NULL,
                next_hop TEXT,
                metric INTEGER DEFAULT 0,
                protocol TEXT NOT NULL DEFAULT 'static',
                admin_distance INTEGER DEFAULT 1,
                interface TEXT,
                vrf TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                synced_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_routes_device ON routes(device_id);
            CREATE INDEX IF NOT EXISTS idx_routes_protocol ON routes(protocol);

            CREATE TABLE IF NOT EXISTS ospf_config (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL UNIQUE,
                process_id INTEGER DEFAULT 1,
                router_id TEXT,
                status TEXT NOT NULL DEFAULT 'disabled',
                created_at TEXT NOT NULL,
                synced_at TEXT,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ospf_neighbors (
                id TEXT PRIMARY KEY,
                ospf_config_id TEXT NOT NULL,
                neighbor_id TEXT,
                neighbor_ip TEXT NOT NULL,
                state TEXT NOT NULL,
                interface TEXT,
                area TEXT DEFAULT '0',
                priority INTEGER DEFAULT 1,
                dead_time TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (ospf_config_id) REFERENCES ospf_config(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ospf_areas (
                id TEXT PRIMARY KEY,
                ospf_config_id TEXT NOT NULL,
                area_id TEXT NOT NULL DEFAULT '0',
                area_type TEXT NOT NULL DEFAULT 'normal',
                networks TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (ospf_config_id) REFERENCES ospf_config(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_ospf_neighbors_config
                ON ospf_neighbors(ospf_config_id);
            CREATE INDEX IF NOT EXISTS idx_ospf_areas_config
                ON ospf_areas(ospf_config_id);

            CREATE TABLE IF NOT EXISTS access_lists (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                name TEXT NOT NULL,
                acl_type TEXT NOT NULL DEFAULT 'extended',
                direction TEXT,
                description TEXT,
                synced_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_acls_device ON access_lists(device_id);

            CREATE TABLE IF NOT EXISTS acl_entries (
                id TEXT PRIMARY KEY,
                acl_id TEXT NOT NULL,
                sequence INTEGER DEFAULT 0,
                action TEXT NOT NULL DEFAULT 'deny',
                protocol TEXT,
                source TEXT,
                source_wildcard TEXT,
                destination TEXT,
                dest_wildcard TEXT,
                source_port TEXT,
                dest_port TEXT,
                log_enabled INTEGER DEFAULT 0,
                hit_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (acl_id) REFERENCES access_lists(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_acl_entries_acl ON acl_entries(acl_id);

            CREATE TABLE IF NOT EXISTS acl_bindings (
                id TEXT PRIMARY KEY,
                acl_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                interface TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'in',
                created_at TEXT NOT NULL,
                FOREIGN KEY (acl_id) REFERENCES access_lists(id) ON DELETE CASCADE,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_acl_bindings_acl ON acl_bindings(acl_id);
            CREATE INDEX IF NOT EXISTS idx_acl_bindings_device ON acl_bindings(device_id);
            """
        )
        await self._db.commit()

        # Migration: add site_id column to devices if not present
        try:
            await self._db.execute("ALTER TABLE devices ADD COLUMN site_id TEXT REFERENCES sites(id) ON DELETE SET NULL")
            await self._db.commit()
        except Exception:
            pass  # Column already exists

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> aiosqlite.Connection:
        if self._db is None:
            raise DatabaseError("Database is not initialized. Call initialize() first.")
        return self._db

    async def execute(self, query: str, params: tuple[Any, ...] | list[Any] = ()) -> aiosqlite.Cursor:
        """Execute a single SQL statement and return the cursor."""
        db = self._ensure_connected()
        try:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor
        except Exception as exc:
            raise DatabaseError(f"Execute failed: {exc}") from exc

    async def fetch_one(self, query: str, params: tuple[Any, ...] | list[Any] = ()) -> dict[str, Any] | None:
        """Fetch a single row as a dict, or None."""
        db = self._ensure_connected()
        try:
            cursor = await db.execute(query, params)
            row = await cursor.fetchone()
            if row is None:
                return None
            return dict(row)
        except Exception as exc:
            raise DatabaseError(f"fetch_one failed: {exc}") from exc

    async def fetch_all(self, query: str, params: tuple[Any, ...] | list[Any] = ()) -> list[dict[str, Any]]:
        """Fetch all rows as a list of dicts."""
        db = self._ensure_connected()
        try:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            raise DatabaseError(f"fetch_all failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Device CRUD
    # ------------------------------------------------------------------

    async def add_device(
        self,
        id: str,
        hostname: str,
        ip_address: str,
        device_type: str,
        protocol: str = "ssh",
        port: int = 22,
        credential_id: str | None = None,
        location: str | None = None,
        model: str | None = None,
        serial_number: str | None = None,
        os_version: str | None = None,
        status: str = "active",
        created_at: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Insert a new device and return its id."""
        meta_json = json.dumps(metadata or {})
        await self.execute(
            """
            INSERT INTO devices
                (id, hostname, ip_address, device_type, protocol, port,
                 credential_id, location, model, serial_number, os_version,
                 status, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id, hostname, ip_address, device_type, protocol, port,
                credential_id, location, model, serial_number, os_version,
                status, created_at, meta_json,
            ),
        )
        return id

    async def get_device(self, device_id: str) -> dict[str, Any] | None:
        """Retrieve a single device by id."""
        row = await self.fetch_one("SELECT * FROM devices WHERE id = ?", (device_id,))
        if row and row.get("metadata"):
            try:
                row["metadata"] = json.loads(row["metadata"])
            except (json.JSONDecodeError, TypeError):
                row["metadata"] = {}
        return row

    async def list_devices(
        self,
        status: str | None = None,
        device_type: str | None = None,
        tag: str | None = None,
        site_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List devices with optional filters."""
        query = "SELECT d.* FROM devices d"
        params: list[Any] = []
        conditions: list[str] = []

        if tag:
            query += " INNER JOIN device_tags dt ON d.id = dt.device_id"
            conditions.append("dt.tag = ?")
            params.append(tag)
        if status:
            conditions.append("d.status = ?")
            params.append(status)
        if device_type:
            conditions.append("d.device_type = ?")
            params.append(device_type)
        if site_id:
            conditions.append("d.site_id = ?")
            params.append(site_id)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY d.hostname"
        rows = await self.fetch_all(query, params)
        for row in rows:
            if row.get("metadata"):
                try:
                    row["metadata"] = json.loads(row["metadata"])
                except (json.JSONDecodeError, TypeError):
                    row["metadata"] = {}
        return rows

    async def update_device(self, device_id: str, **fields: Any) -> bool:
        """Update arbitrary fields on a device. Returns True if a row was updated."""
        if not fields:
            return False
        if "metadata" in fields and isinstance(fields["metadata"], dict):
            fields["metadata"] = json.dumps(fields["metadata"])
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [device_id]
        cursor = await self.execute(
            f"UPDATE devices SET {set_clause} WHERE id = ?",
            values,
        )
        return cursor.rowcount > 0

    async def delete_device(self, device_id: str) -> bool:
        """Delete a device by id. Returns True if deleted."""
        cursor = await self.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    async def add_tag(self, device_id: str, tag: str) -> None:
        """Add a tag to a device (idempotent)."""
        await self.execute(
            "INSERT OR IGNORE INTO device_tags (device_id, tag) VALUES (?, ?)",
            (device_id, tag),
        )

    async def remove_tag(self, device_id: str, tag: str) -> None:
        """Remove a tag from a device."""
        await self.execute(
            "DELETE FROM device_tags WHERE device_id = ? AND tag = ?",
            (device_id, tag),
        )

    async def get_device_tags(self, device_id: str) -> list[str]:
        """Return all tags for a device."""
        rows = await self.fetch_all(
            "SELECT tag FROM device_tags WHERE device_id = ? ORDER BY tag",
            (device_id,),
        )
        return [r["tag"] for r in rows]

    # ------------------------------------------------------------------
    # Config backups
    # ------------------------------------------------------------------

    async def add_config_backup(
        self,
        id: str,
        device_id: str,
        config_text: str,
        config_hash: str,
        backup_type: str = "running",
        file_path: str | None = None,
        created_at: str = "",
    ) -> str:
        """Store a configuration backup and return its id."""
        await self.execute(
            """
            INSERT INTO config_backups
                (id, device_id, config_text, config_hash, backup_type, file_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (id, device_id, config_text, config_hash, backup_type, file_path, created_at),
        )
        return id

    async def get_config_backups(
        self,
        device_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List config backups for a device, newest first."""
        return await self.fetch_all(
            "SELECT * FROM config_backups WHERE device_id = ? ORDER BY created_at DESC LIMIT ?",
            (device_id, limit),
        )

    async def get_config_backup(self, backup_id: str) -> dict[str, Any] | None:
        """Retrieve a single config backup by id."""
        return await self.fetch_one("SELECT * FROM config_backups WHERE id = ?", (backup_id,))

    # ------------------------------------------------------------------
    # Config deploys
    # ------------------------------------------------------------------

    async def add_config_deploy(
        self,
        id: str,
        device_id: str,
        config_diff: str,
        status: str = "pending",
        applied_by: str | None = None,
        applied_at: str | None = None,
        rollback_to: str | None = None,
    ) -> str:
        """Record a config deployment."""
        await self.execute(
            """
            INSERT INTO config_deploys
                (id, device_id, config_diff, status, applied_by, applied_at, rollback_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (id, device_id, config_diff, status, applied_by, applied_at, rollback_to),
        )
        return id

    async def update_deploy_status(self, deploy_id: str, status: str, applied_at: str | None = None) -> bool:
        """Update the status (and optionally applied_at) of a deployment."""
        if applied_at:
            cursor = await self.execute(
                "UPDATE config_deploys SET status = ?, applied_at = ? WHERE id = ?",
                (status, applied_at, deploy_id),
            )
        else:
            cursor = await self.execute(
                "UPDATE config_deploys SET status = ? WHERE id = ?",
                (status, deploy_id),
            )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    async def add_metric(
        self,
        id: str,
        device_id: str,
        metric_name: str,
        metric_value: float,
        interface: str | None = None,
        timestamp: str = "",
    ) -> str:
        """Insert a metric data point."""
        await self.execute(
            """
            INSERT INTO metrics (id, device_id, metric_name, metric_value, interface, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (id, device_id, metric_name, metric_value, interface, timestamp),
        )
        return id

    async def get_metrics(
        self,
        device_id: str,
        metric_name: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query metrics with optional filters and time range."""
        query = "SELECT * FROM metrics WHERE device_id = ?"
        params: list[Any] = [device_id]

        if metric_name:
            query += " AND metric_name = ?"
            params.append(metric_name)
        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return await self.fetch_all(query, params)

    async def cleanup_old_metrics(self, before_timestamp: str) -> int:
        """Delete metrics older than the given timestamp. Returns count deleted."""
        cursor = await self.execute(
            "DELETE FROM metrics WHERE timestamp < ?",
            (before_timestamp,),
        )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Alert rules
    # ------------------------------------------------------------------

    async def add_alert_rule(
        self,
        id: str,
        name: str,
        metric_name: str,
        condition: str,
        threshold: float,
        device_filter: str | None = None,
        duration_seconds: int = 0,
        channel: str = "slack",
        enabled: bool = True,
    ) -> str:
        """Create a new alert rule."""
        await self.execute(
            """
            INSERT INTO alert_rules
                (id, name, device_filter, metric_name, condition, threshold,
                 duration_seconds, channel, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, name, device_filter, metric_name, condition, threshold,
             duration_seconds, channel, int(enabled)),
        )
        return id

    async def get_alert_rules(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        """List alert rules."""
        query = "SELECT * FROM alert_rules"
        params: list[Any] = []
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY name"
        rows = await self.fetch_all(query, params)
        for row in rows:
            row["enabled"] = bool(row["enabled"])
        return rows

    async def update_alert_rule(self, rule_id: str, **fields: Any) -> bool:
        """Update fields on an alert rule."""
        if not fields:
            return False
        if "enabled" in fields:
            fields["enabled"] = int(fields["enabled"])
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [rule_id]
        cursor = await self.execute(
            f"UPDATE alert_rules SET {set_clause} WHERE id = ?",
            values,
        )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    async def add_alert(
        self,
        id: str,
        rule_id: str,
        device_id: str,
        metric_value: float,
        message: str,
        status: str = "firing",
        fired_at: str = "",
        resolved_at: str | None = None,
    ) -> str:
        """Record a fired alert."""
        await self.execute(
            """
            INSERT INTO alerts
                (id, rule_id, device_id, metric_value, message, status, fired_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, rule_id, device_id, metric_value, message, status, fired_at, resolved_at),
        )
        return id

    async def get_alerts(
        self,
        status: str | None = None,
        device_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List alerts with optional filters."""
        query = "SELECT * FROM alerts"
        params: list[Any] = []
        conditions: list[str] = []

        if status:
            conditions.append("status = ?")
            params.append(status)
        if device_id:
            conditions.append("device_id = ?")
            params.append(device_id)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY fired_at DESC LIMIT ?"
        params.append(limit)
        return await self.fetch_all(query, params)

    async def update_alert_status(self, alert_id: str, status: str, resolved_at: str | None = None) -> bool:
        """Update the status of an alert."""
        if resolved_at:
            cursor = await self.execute(
                "UPDATE alerts SET status = ?, resolved_at = ? WHERE id = ?",
                (status, resolved_at, alert_id),
            )
        else:
            cursor = await self.execute(
                "UPDATE alerts SET status = ? WHERE id = ?",
                (status, alert_id),
            )
        return cursor.rowcount > 0

    async def get_active_alerts(self) -> list[dict[str, Any]]:
        """Return all alerts with status 'firing'."""
        return await self.get_alerts(status="firing")

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    async def add_credential(
        self,
        id: str,
        name: str,
        username: str,
        password: str | None = None,
        ssh_key_path: str | None = None,
        snmp_community: str | None = None,
        enable_secret: str | None = None,
        created_at: str = "",
    ) -> str:
        """Store an (already encrypted) credential."""
        await self.execute(
            """
            INSERT INTO credentials
                (id, name, username, password, ssh_key_path, snmp_community, enable_secret, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, name, username, password, ssh_key_path, snmp_community, enable_secret, created_at),
        )
        return id

    async def get_credential(self, credential_id: str) -> dict[str, Any] | None:
        """Retrieve a single credential row by id."""
        return await self.fetch_one("SELECT * FROM credentials WHERE id = ?", (credential_id,))

    async def list_credentials(self) -> list[dict[str, Any]]:
        """List all credentials (fields are still encrypted)."""
        return await self.fetch_all("SELECT * FROM credentials ORDER BY name")

    async def delete_credential(self, credential_id: str) -> bool:
        """Delete a credential. Returns True if deleted."""
        cursor = await self.execute("DELETE FROM credentials WHERE id = ?", (credential_id,))
        return cursor.rowcount > 0

    # ---- Audit Log ----

    async def add_audit_entry(self, id: str, timestamp: str, actor: str,
                              action_type: str, description: str,
                              runbook_name: str = None, execution_id: str = None,
                              device_id: str = None, details: str = "{}",
                              result: str = None, before_state: str = None,
                              after_state: str = None) -> str:
        await self.execute(
            """INSERT INTO audit_log (id, timestamp, actor, action_type, description,
               runbook_name, execution_id, device_id, details, result, before_state, after_state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (id, timestamp, actor, action_type, description,
             runbook_name, execution_id, device_id, details, result,
             before_state, after_state)
        )
        return id

    async def get_audit_log(self, device_id: str = None, actor: str = None,
                            execution_id: str = None, action_type: str = None,
                            since: str = None, limit: int = 100) -> list[dict]:
        query = "SELECT * FROM audit_log WHERE 1=1"
        params = []
        if device_id:
            query += " AND device_id = ?"
            params.append(device_id)
        if actor:
            query += " AND actor = ?"
            params.append(actor)
        if execution_id:
            query += " AND execution_id = ?"
            params.append(execution_id)
        if action_type:
            query += " AND action_type = ?"
            params.append(action_type)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return await self.fetch_all(query, tuple(params))

    async def get_audit_entry(self, entry_id: str) -> dict | None:
        return await self.fetch_one("SELECT * FROM audit_log WHERE id = ?", (entry_id,))

    # ---- Runbook Executions ----

    async def add_runbook_execution(self, id: str, runbook_name: str,
                                     trigger_type: str, status: str = "pending",
                                     trigger_details: str = "{}",
                                     device_id: str = None, dry_run: bool = False,
                                     started_at: str = None, context: str = "{}") -> str:
        await self.execute(
            """INSERT INTO runbook_executions (id, runbook_name, trigger_type, trigger_details,
               device_id, status, dry_run, started_at, context)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (id, runbook_name, trigger_type, trigger_details,
             device_id, status, 1 if dry_run else 0, started_at, context)
        )
        return id

    async def update_runbook_execution(self, exec_id: str, **fields) -> bool:
        if not fields:
            return False
        set_clauses = []
        params = []
        for key, value in fields.items():
            set_clauses.append(f"{key} = ?")
            params.append(value)
        params.append(exec_id)
        query = f"UPDATE runbook_executions SET {', '.join(set_clauses)} WHERE id = ?"
        cursor = await self.execute(query, tuple(params))
        return cursor.rowcount > 0

    async def get_runbook_execution(self, exec_id: str) -> dict | None:
        return await self.fetch_one(
            "SELECT * FROM runbook_executions WHERE id = ?", (exec_id,)
        )

    async def list_runbook_executions(self, status: str = None,
                                       runbook_name: str = None,
                                       limit: int = 50) -> list[dict]:
        query = "SELECT * FROM runbook_executions WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if runbook_name:
            query += " AND runbook_name = ?"
            params.append(runbook_name)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        return await self.fetch_all(query, tuple(params))

    # ---- Scheduled Jobs ----

    async def add_scheduled_job(self, id: str, name: str, job_type: str,
                                 cron_expression: str, timezone: str = "UTC",
                                 enabled: bool = True, created_at: str = "",
                                 metadata: str = "{}") -> str:
        await self.execute(
            """INSERT OR REPLACE INTO scheduled_jobs
               (id, name, job_type, cron_expression, timezone, enabled, created_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (id, name, job_type, cron_expression, timezone,
             1 if enabled else 0, created_at, metadata)
        )
        return id

    async def update_scheduled_job(self, job_id: str, **fields) -> bool:
        if not fields:
            return False
        set_clauses = []
        params = []
        for key, value in fields.items():
            set_clauses.append(f"{key} = ?")
            params.append(value)
        params.append(job_id)
        query = f"UPDATE scheduled_jobs SET {', '.join(set_clauses)} WHERE id = ?"
        cursor = await self.execute(query, tuple(params))
        return cursor.rowcount > 0

    async def list_scheduled_jobs(self) -> list[dict]:
        return await self.fetch_all("SELECT * FROM scheduled_jobs ORDER BY name", ())

    async def delete_scheduled_job(self, job_id: str) -> bool:
        cursor = await self.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Firewall Rules
    # ------------------------------------------------------------------

    async def add_firewall_rule(self, rule_data: dict) -> str:
        """Insert a firewall rule and return its id."""
        json_fields = ['source_addresses', 'dest_addresses', 'services']
        for f in json_fields:
            if f in rule_data and isinstance(rule_data[f], list):
                rule_data[f] = json.dumps(rule_data[f])
        if 'enabled' in rule_data:
            rule_data['enabled'] = int(rule_data['enabled'])
        if 'log_enabled' in rule_data:
            rule_data['log_enabled'] = int(rule_data['log_enabled'])
        await self.execute(
            """
            INSERT INTO firewall_rules
                (id, device_id, policy_id, name, source_zone, dest_zone,
                 source_addresses, dest_addresses, services, action,
                 enabled, log_enabled, position, comment, synced_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule_data['id'], rule_data['device_id'], rule_data.get('policy_id'),
                rule_data['name'], rule_data.get('source_zone'), rule_data.get('dest_zone'),
                rule_data.get('source_addresses', '[]'), rule_data.get('dest_addresses', '[]'),
                rule_data.get('services', '[]'), rule_data.get('action', 'deny'),
                rule_data.get('enabled', 1), rule_data.get('log_enabled', 0),
                rule_data.get('position', 0), rule_data.get('comment'),
                rule_data.get('synced_at'), rule_data['created_at'],
            ),
        )
        return rule_data['id']

    def _parse_firewall_rule(self, row: dict) -> dict:
        """Deserialize JSON list fields on a firewall rule row."""
        for f in ('source_addresses', 'dest_addresses', 'services'):
            if row.get(f):
                try:
                    row[f] = json.loads(row[f])
                except (json.JSONDecodeError, TypeError):
                    row[f] = []
            else:
                row[f] = []
        row['enabled'] = bool(row.get('enabled', 0))
        row['log_enabled'] = bool(row.get('log_enabled', 0))
        return row

    async def get_firewall_rule(self, rule_id: str) -> dict | None:
        """Retrieve a single firewall rule by id."""
        row = await self.fetch_one("SELECT * FROM firewall_rules WHERE id = ?", (rule_id,))
        if row:
            row = self._parse_firewall_rule(row)
        return row

    async def get_firewall_rules_by_device(self, device_id: str) -> list[dict]:
        """List all firewall rules for a device, ordered by position."""
        rows = await self.fetch_all(
            "SELECT * FROM firewall_rules WHERE device_id = ? ORDER BY position",
            (device_id,),
        )
        return [self._parse_firewall_rule(r) for r in rows]

    async def update_firewall_rule(self, rule_id: str, updates: dict) -> bool:
        """Update arbitrary fields on a firewall rule. Returns True if a row was updated."""
        if not updates:
            return False
        json_fields = ['source_addresses', 'dest_addresses', 'services']
        for f in json_fields:
            if f in updates and isinstance(updates[f], list):
                updates[f] = json.dumps(updates[f])
        if 'enabled' in updates:
            updates['enabled'] = int(updates['enabled'])
        if 'log_enabled' in updates:
            updates['log_enabled'] = int(updates['log_enabled'])
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [rule_id]
        cursor = await self.execute(
            f"UPDATE firewall_rules SET {set_clause} WHERE id = ?",
            values,
        )
        return cursor.rowcount > 0

    async def delete_firewall_rule(self, rule_id: str) -> bool:
        """Delete a firewall rule by id. Returns True if deleted."""
        cursor = await self.execute("DELETE FROM firewall_rules WHERE id = ?", (rule_id,))
        return cursor.rowcount > 0

    async def clear_firewall_rules_for_device(self, device_id: str) -> int:
        """Delete all firewall rules for a device. Returns count deleted."""
        cursor = await self.execute(
            "DELETE FROM firewall_rules WHERE device_id = ?", (device_id,)
        )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # NAT Rules
    # ------------------------------------------------------------------

    async def add_nat_rule(self, rule_data: dict) -> str:
        """Insert a NAT rule and return its id."""
        if 'enabled' in rule_data:
            rule_data['enabled'] = int(rule_data['enabled'])
        await self.execute(
            """
            INSERT INTO nat_rules
                (id, device_id, name, nat_type, source_zone, dest_zone,
                 original_source, original_dest, original_service,
                 translated_source, translated_dest, translated_service,
                 enabled, comment, synced_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule_data['id'], rule_data['device_id'], rule_data['name'],
                rule_data['nat_type'], rule_data.get('source_zone'),
                rule_data.get('dest_zone'), rule_data.get('original_source'),
                rule_data.get('original_dest'), rule_data.get('original_service'),
                rule_data.get('translated_source'), rule_data.get('translated_dest'),
                rule_data.get('translated_service'), rule_data.get('enabled', 1),
                rule_data.get('comment'), rule_data.get('synced_at'),
                rule_data['created_at'],
            ),
        )
        return rule_data['id']

    def _parse_nat_rule(self, row: dict) -> dict:
        """Deserialize boolean fields on a NAT rule row."""
        row['enabled'] = bool(row.get('enabled', 0))
        return row

    async def get_nat_rule(self, rule_id: str) -> dict | None:
        """Retrieve a single NAT rule by id."""
        row = await self.fetch_one("SELECT * FROM nat_rules WHERE id = ?", (rule_id,))
        if row:
            row = self._parse_nat_rule(row)
        return row

    async def get_nat_rules_by_device(self, device_id: str) -> list[dict]:
        """List all NAT rules for a device."""
        rows = await self.fetch_all(
            "SELECT * FROM nat_rules WHERE device_id = ? ORDER BY name",
            (device_id,),
        )
        return [self._parse_nat_rule(r) for r in rows]

    async def delete_nat_rule(self, rule_id: str) -> bool:
        """Delete a NAT rule by id. Returns True if deleted."""
        cursor = await self.execute("DELETE FROM nat_rules WHERE id = ?", (rule_id,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Firewall Zones
    # ------------------------------------------------------------------

    async def add_firewall_zone(self, zone_data: dict) -> str:
        """Insert a firewall zone and return its id."""
        if 'interfaces' in zone_data and isinstance(zone_data['interfaces'], list):
            zone_data['interfaces'] = json.dumps(zone_data['interfaces'])
        await self.execute(
            """
            INSERT INTO firewall_zones
                (id, device_id, name, interfaces, security_level, description, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                zone_data['id'], zone_data['device_id'], zone_data['name'],
                zone_data.get('interfaces', '[]'), zone_data.get('security_level', 0),
                zone_data.get('description'), zone_data.get('synced_at'),
            ),
        )
        return zone_data['id']

    def _parse_firewall_zone(self, row: dict) -> dict:
        """Deserialize JSON list fields on a firewall zone row."""
        if row.get('interfaces'):
            try:
                row['interfaces'] = json.loads(row['interfaces'])
            except (json.JSONDecodeError, TypeError):
                row['interfaces'] = []
        else:
            row['interfaces'] = []
        return row

    async def get_firewall_zone(self, zone_id: str) -> dict | None:
        """Retrieve a single firewall zone by id."""
        row = await self.fetch_one("SELECT * FROM firewall_zones WHERE id = ?", (zone_id,))
        if row:
            row = self._parse_firewall_zone(row)
        return row

    async def get_firewall_zones_by_device(self, device_id: str) -> list[dict]:
        """List all firewall zones for a device."""
        rows = await self.fetch_all(
            "SELECT * FROM firewall_zones WHERE device_id = ? ORDER BY name",
            (device_id,),
        )
        return [self._parse_firewall_zone(r) for r in rows]

    async def delete_firewall_zone(self, zone_id: str) -> bool:
        """Delete a firewall zone by id. Returns True if deleted."""
        cursor = await self.execute("DELETE FROM firewall_zones WHERE id = ?", (zone_id,))
        return cursor.rowcount > 0

    async def clear_firewall_zones_for_device(self, device_id: str) -> int:
        """Delete all firewall zones for a device. Returns count deleted."""
        cursor = await self.execute(
            "DELETE FROM firewall_zones WHERE device_id = ?", (device_id,)
        )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Firewall Objects
    # ------------------------------------------------------------------

    async def add_firewall_object(self, obj_data: dict) -> str:
        """Insert a firewall object and return its id."""
        if 'members' in obj_data and isinstance(obj_data['members'], list):
            obj_data['members'] = json.dumps(obj_data['members'])
        await self.execute(
            """
            INSERT INTO firewall_objects
                (id, device_id, name, object_type, value, members,
                 description, synced_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obj_data['id'], obj_data['device_id'], obj_data['name'],
                obj_data['object_type'], obj_data.get('value'),
                obj_data.get('members', '[]'), obj_data.get('description'),
                obj_data.get('synced_at'), obj_data['created_at'],
            ),
        )
        return obj_data['id']

    def _parse_firewall_object(self, row: dict) -> dict:
        """Deserialize JSON list fields on a firewall object row."""
        if row.get('members'):
            try:
                row['members'] = json.loads(row['members'])
            except (json.JSONDecodeError, TypeError):
                row['members'] = []
        else:
            row['members'] = []
        return row

    async def get_firewall_object(self, obj_id: str) -> dict | None:
        """Retrieve a single firewall object by id."""
        row = await self.fetch_one("SELECT * FROM firewall_objects WHERE id = ?", (obj_id,))
        if row:
            row = self._parse_firewall_object(row)
        return row

    async def get_firewall_objects_by_device(self, device_id: str) -> list[dict]:
        """List all firewall objects for a device."""
        rows = await self.fetch_all(
            "SELECT * FROM firewall_objects WHERE device_id = ? ORDER BY name",
            (device_id,),
        )
        return [self._parse_firewall_object(r) for r in rows]

    async def delete_firewall_object(self, obj_id: str) -> bool:
        """Delete a firewall object by id. Returns True if deleted."""
        cursor = await self.execute("DELETE FROM firewall_objects WHERE id = ?", (obj_id,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # VLANs
    # ------------------------------------------------------------------

    async def add_vlan(
        self,
        id: str,
        device_id: str,
        vlan_id: int,
        name: str | None = None,
        status: str = "active",
        description: str | None = None,
        synced_at: str | None = None,
        created_at: str = "",
    ) -> str:
        """Insert a VLAN record and return its id."""
        await self.execute(
            """
            INSERT OR REPLACE INTO vlans
                (id, device_id, vlan_id, name, status, description, synced_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, device_id, vlan_id, name, status, description, synced_at, created_at),
        )
        return id

    async def get_vlan(self, vlan_db_id: str) -> dict[str, Any] | None:
        """Retrieve a single VLAN by its database id."""
        return await self.fetch_one("SELECT * FROM vlans WHERE id = ?", (vlan_db_id,))

    async def get_vlans_by_device(self, device_id: str) -> list[dict[str, Any]]:
        """List all VLANs for a device, ordered by VLAN ID."""
        return await self.fetch_all(
            "SELECT * FROM vlans WHERE device_id = ? ORDER BY vlan_id",
            (device_id,),
        )

    async def get_vlan_by_number(self, device_id: str, vlan_id: int) -> dict[str, Any] | None:
        """Retrieve a specific VLAN by device and VLAN number."""
        return await self.fetch_one(
            "SELECT * FROM vlans WHERE device_id = ? AND vlan_id = ?",
            (device_id, vlan_id),
        )

    async def delete_vlan(self, device_id: str, vlan_id: int) -> bool:
        """Delete a VLAN by device and VLAN number. Returns True if deleted."""
        cursor = await self.execute(
            "DELETE FROM vlans WHERE device_id = ? AND vlan_id = ?",
            (device_id, vlan_id),
        )
        # Also remove associated interface assignments
        await self.execute(
            "DELETE FROM vlan_interfaces WHERE device_id = ? AND vlan_id = ?",
            (device_id, vlan_id),
        )
        return cursor.rowcount > 0

    async def clear_vlans_for_device(self, device_id: str) -> int:
        """Delete all VLANs for a device. Returns count deleted."""
        await self.execute(
            "DELETE FROM vlan_interfaces WHERE device_id = ?", (device_id,)
        )
        cursor = await self.execute(
            "DELETE FROM vlans WHERE device_id = ?", (device_id,)
        )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # VLAN Interface Assignments
    # ------------------------------------------------------------------

    async def add_vlan_interface(
        self,
        id: str,
        device_id: str,
        vlan_id: int,
        interface_name: str,
        mode: str = "access",
        created_at: str = "",
    ) -> str:
        """Assign an interface to a VLAN. Mode: access, trunk, tagged, untagged."""
        await self.execute(
            """
            INSERT OR REPLACE INTO vlan_interfaces
                (id, device_id, vlan_id, interface_name, mode, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (id, device_id, vlan_id, interface_name, mode, created_at),
        )
        return id

    async def get_vlan_interfaces(self, device_id: str, vlan_id: int | None = None) -> list[dict[str, Any]]:
        """List interface assignments. Optionally filter by VLAN number."""
        if vlan_id is not None:
            return await self.fetch_all(
                "SELECT * FROM vlan_interfaces WHERE device_id = ? AND vlan_id = ? ORDER BY interface_name",
                (device_id, vlan_id),
            )
        return await self.fetch_all(
            "SELECT * FROM vlan_interfaces WHERE device_id = ? ORDER BY vlan_id, interface_name",
            (device_id,),
        )

    async def delete_vlan_interface(self, device_id: str, vlan_id: int, interface_name: str) -> bool:
        """Remove an interface from a VLAN. Returns True if deleted."""
        cursor = await self.execute(
            "DELETE FROM vlan_interfaces WHERE device_id = ? AND vlan_id = ? AND interface_name = ?",
            (device_id, vlan_id, interface_name),
        )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    async def add_route(
        self,
        id: str,
        device_id: str,
        destination: str,
        prefix_length: int,
        next_hop: str | None = None,
        metric: int = 0,
        protocol: str = "static",
        admin_distance: int = 1,
        interface: str | None = None,
        vrf: str | None = None,
        status: str = "active",
        synced_at: str | None = None,
        created_at: str = "",
    ) -> str:
        """Insert a route record."""
        await self.execute(
            """
            INSERT OR REPLACE INTO routes
                (id, device_id, destination, prefix_length, next_hop, metric,
                 protocol, admin_distance, interface, vrf, status, synced_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, device_id, destination, prefix_length, next_hop, metric,
             protocol, admin_distance, interface, vrf, status, synced_at, created_at),
        )
        return id

    async def get_routes_by_device(self, device_id: str, protocol: str | None = None) -> list[dict[str, Any]]:
        """List routes for a device, optionally filtered by protocol."""
        if protocol:
            return await self.fetch_all(
                "SELECT * FROM routes WHERE device_id = ? AND protocol = ? ORDER BY destination",
                (device_id, protocol),
            )
        return await self.fetch_all(
            "SELECT * FROM routes WHERE device_id = ? ORDER BY destination",
            (device_id,),
        )

    async def delete_route(self, device_id: str, destination: str, prefix_length: int, next_hop: str | None = None) -> bool:
        """Delete a specific route."""
        if next_hop:
            cursor = await self.execute(
                "DELETE FROM routes WHERE device_id = ? AND destination = ? AND prefix_length = ? AND next_hop = ?",
                (device_id, destination, prefix_length, next_hop),
            )
        else:
            cursor = await self.execute(
                "DELETE FROM routes WHERE device_id = ? AND destination = ? AND prefix_length = ?",
                (device_id, destination, prefix_length),
            )
        return cursor.rowcount > 0

    async def clear_routes_for_device(self, device_id: str) -> int:
        """Delete all routes for a device."""
        cursor = await self.execute("DELETE FROM routes WHERE device_id = ?", (device_id,))
        return cursor.rowcount

    # ------------------------------------------------------------------
    # OSPF Config
    # ------------------------------------------------------------------

    async def add_ospf_config(
        self,
        id: str,
        device_id: str,
        process_id: int = 1,
        router_id: str | None = None,
        status: str = "disabled",
        created_at: str = "",
        synced_at: str | None = None,
    ) -> str:
        """Insert or replace OSPF config for a device."""
        await self.execute(
            """
            INSERT OR REPLACE INTO ospf_config
                (id, device_id, process_id, router_id, status, created_at, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (id, device_id, process_id, router_id, status, created_at, synced_at),
        )
        return id

    async def get_ospf_config(self, device_id: str) -> dict[str, Any] | None:
        """Get OSPF config for a device."""
        return await self.fetch_one(
            "SELECT * FROM ospf_config WHERE device_id = ?", (device_id,)
        )

    async def delete_ospf_config(self, device_id: str) -> bool:
        """Delete OSPF config and related data for a device."""
        config = await self.get_ospf_config(device_id)
        if config:
            await self.execute("DELETE FROM ospf_neighbors WHERE ospf_config_id = ?", (config["id"],))
            await self.execute("DELETE FROM ospf_areas WHERE ospf_config_id = ?", (config["id"],))
        cursor = await self.execute("DELETE FROM ospf_config WHERE device_id = ?", (device_id,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # OSPF Neighbors
    # ------------------------------------------------------------------

    async def add_ospf_neighbor(
        self,
        id: str,
        ospf_config_id: str,
        neighbor_ip: str,
        state: str,
        neighbor_id: str | None = None,
        interface: str | None = None,
        area: str = "0",
        priority: int = 1,
        dead_time: str | None = None,
        created_at: str = "",
    ) -> str:
        """Insert an OSPF neighbor."""
        await self.execute(
            """
            INSERT INTO ospf_neighbors
                (id, ospf_config_id, neighbor_id, neighbor_ip, state,
                 interface, area, priority, dead_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, ospf_config_id, neighbor_id, neighbor_ip, state,
             interface, area, priority, dead_time, created_at),
        )
        return id

    async def get_ospf_neighbors(self, ospf_config_id: str) -> list[dict[str, Any]]:
        """List OSPF neighbors for a config."""
        return await self.fetch_all(
            "SELECT * FROM ospf_neighbors WHERE ospf_config_id = ? ORDER BY neighbor_ip",
            (ospf_config_id,),
        )

    async def clear_ospf_neighbors(self, ospf_config_id: str) -> int:
        """Clear all OSPF neighbors for a config."""
        cursor = await self.execute(
            "DELETE FROM ospf_neighbors WHERE ospf_config_id = ?", (ospf_config_id,)
        )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # OSPF Areas
    # ------------------------------------------------------------------

    async def add_ospf_area(
        self,
        id: str,
        ospf_config_id: str,
        area_id: str = "0",
        area_type: str = "normal",
        networks: str = "[]",
        created_at: str = "",
    ) -> str:
        """Insert an OSPF area."""
        await self.execute(
            """
            INSERT OR REPLACE INTO ospf_areas
                (id, ospf_config_id, area_id, area_type, networks, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (id, ospf_config_id, area_id, area_type, networks, created_at),
        )
        return id

    async def get_ospf_areas(self, ospf_config_id: str) -> list[dict[str, Any]]:
        """List OSPF areas for a config."""
        rows = await self.fetch_all(
            "SELECT * FROM ospf_areas WHERE ospf_config_id = ? ORDER BY area_id",
            (ospf_config_id,),
        )
        for row in rows:
            if row.get("networks"):
                try:
                    row["networks"] = json.loads(row["networks"])
                except (json.JSONDecodeError, TypeError):
                    row["networks"] = []
        return rows

    async def clear_ospf_areas(self, ospf_config_id: str) -> int:
        """Clear all OSPF areas for a config."""
        cursor = await self.execute(
            "DELETE FROM ospf_areas WHERE ospf_config_id = ?", (ospf_config_id,)
        )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Access Lists (ACLs)
    # ------------------------------------------------------------------

    async def add_access_list(
        self,
        id: str,
        device_id: str,
        name: str,
        acl_type: str = "extended",
        direction: str | None = None,
        description: str | None = None,
        synced_at: str | None = None,
        created_at: str = "",
    ) -> str:
        """Insert an access list and return its id."""
        await self.execute(
            """
            INSERT OR REPLACE INTO access_lists
                (id, device_id, name, acl_type, direction, description, synced_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, device_id, name, acl_type, direction, description, synced_at, created_at),
        )
        return id

    async def get_access_lists_by_device(self, device_id: str) -> list[dict[str, Any]]:
        """List all ACLs for a device."""
        return await self.fetch_all(
            "SELECT * FROM access_lists WHERE device_id = ? ORDER BY name",
            (device_id,),
        )

    async def get_access_list(self, acl_id: str) -> dict[str, Any] | None:
        """Get a single ACL by id."""
        return await self.fetch_one("SELECT * FROM access_lists WHERE id = ?", (acl_id,))

    async def get_access_list_by_name(self, device_id: str, name: str) -> dict[str, Any] | None:
        """Get an ACL by device and name."""
        return await self.fetch_one(
            "SELECT * FROM access_lists WHERE device_id = ? AND name = ?",
            (device_id, name),
        )

    async def delete_access_list(self, acl_id: str) -> bool:
        """Delete an ACL and its entries/bindings."""
        await self.execute("DELETE FROM acl_entries WHERE acl_id = ?", (acl_id,))
        await self.execute("DELETE FROM acl_bindings WHERE acl_id = ?", (acl_id,))
        cursor = await self.execute("DELETE FROM access_lists WHERE id = ?", (acl_id,))
        return cursor.rowcount > 0

    async def clear_acls_for_device(self, device_id: str) -> int:
        """Delete all ACLs for a device."""
        acls = await self.get_access_lists_by_device(device_id)
        for acl in acls:
            await self.execute("DELETE FROM acl_entries WHERE acl_id = ?", (acl["id"],))
            await self.execute("DELETE FROM acl_bindings WHERE acl_id = ?", (acl["id"],))
        cursor = await self.execute("DELETE FROM access_lists WHERE device_id = ?", (device_id,))
        return cursor.rowcount

    # ------------------------------------------------------------------
    # ACL Entries
    # ------------------------------------------------------------------

    async def add_acl_entry(
        self,
        id: str,
        acl_id: str,
        sequence: int = 0,
        action: str = "deny",
        protocol: str | None = None,
        source: str | None = None,
        source_wildcard: str | None = None,
        destination: str | None = None,
        dest_wildcard: str | None = None,
        source_port: str | None = None,
        dest_port: str | None = None,
        log_enabled: bool = False,
        hit_count: int = 0,
        created_at: str = "",
    ) -> str:
        """Insert an ACL entry."""
        await self.execute(
            """
            INSERT INTO acl_entries
                (id, acl_id, sequence, action, protocol, source, source_wildcard,
                 destination, dest_wildcard, source_port, dest_port, log_enabled,
                 hit_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, acl_id, sequence, action, protocol, source, source_wildcard,
             destination, dest_wildcard, source_port, dest_port,
             int(log_enabled), hit_count, created_at),
        )
        return id

    async def get_acl_entries(self, acl_id: str) -> list[dict[str, Any]]:
        """List all entries for an ACL, ordered by sequence."""
        rows = await self.fetch_all(
            "SELECT * FROM acl_entries WHERE acl_id = ? ORDER BY sequence",
            (acl_id,),
        )
        for row in rows:
            row["log_enabled"] = bool(row.get("log_enabled", 0))
        return rows

    async def delete_acl_entry(self, entry_id: str) -> bool:
        """Delete an ACL entry."""
        cursor = await self.execute("DELETE FROM acl_entries WHERE id = ?", (entry_id,))
        return cursor.rowcount > 0

    async def delete_acl_entry_by_sequence(self, acl_id: str, sequence: int) -> bool:
        """Delete an ACL entry by ACL and sequence number."""
        cursor = await self.execute(
            "DELETE FROM acl_entries WHERE acl_id = ? AND sequence = ?",
            (acl_id, sequence),
        )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # ACL Bindings
    # ------------------------------------------------------------------

    async def add_acl_binding(
        self,
        id: str,
        acl_id: str,
        device_id: str,
        interface: str,
        direction: str = "in",
        created_at: str = "",
    ) -> str:
        """Bind an ACL to an interface."""
        await self.execute(
            """
            INSERT OR REPLACE INTO acl_bindings
                (id, acl_id, device_id, interface, direction, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (id, acl_id, device_id, interface, direction, created_at),
        )
        return id

    async def get_acl_bindings(self, acl_id: str) -> list[dict[str, Any]]:
        """List all bindings for an ACL."""
        return await self.fetch_all(
            "SELECT * FROM acl_bindings WHERE acl_id = ? ORDER BY interface",
            (acl_id,),
        )

    async def get_acl_bindings_by_device(self, device_id: str) -> list[dict[str, Any]]:
        """List all ACL bindings for a device."""
        return await self.fetch_all(
            "SELECT * FROM acl_bindings WHERE device_id = ? ORDER BY interface",
            (device_id,),
        )

    async def delete_acl_binding(self, binding_id: str) -> bool:
        """Remove an ACL binding."""
        cursor = await self.execute("DELETE FROM acl_bindings WHERE id = ?", (binding_id,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Change Approvals
    # ------------------------------------------------------------------

    async def add_change_approval(
        self, approval_id: str, change_request_id: str, approver: str,
        status: str = "pending", notes: str | None = None, decided_at: str | None = None,
        created_at: str = "",
    ) -> dict[str, Any]:
        """Insert a change approval record."""
        await self.execute(
            """INSERT INTO change_approvals
               (id, change_request_id, approver, status, notes, decided_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, change_request_id, approver, status, notes, decided_at, created_at),
        )
        return await self.fetch_one("SELECT * FROM change_approvals WHERE id = ?", (approval_id,))

    async def get_change_approvals(self, change_request_id: str) -> list[dict[str, Any]]:
        """Get all approvals for a change request."""
        return await self.fetch_all(
            "SELECT * FROM change_approvals WHERE change_request_id = ? ORDER BY created_at",
            (change_request_id,),
        )

    async def update_change_approval(
        self, approval_id: str, status: str, notes: str | None = None, decided_at: str = "",
    ) -> bool:
        """Update an approval record."""
        cursor = await self.execute(
            "UPDATE change_approvals SET status = ?, notes = COALESCE(?, notes), decided_at = ? WHERE id = ?",
            (status, notes, decided_at, approval_id),
        )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Change Rollbacks
    # ------------------------------------------------------------------

    async def add_change_rollback(
        self, rollback_id: str, change_request_id: str, rollback_commands: str,
        status: str = "pending", executed_by: str | None = None,
    ) -> dict[str, Any]:
        """Insert a change rollback record."""
        await self.execute(
            """INSERT INTO change_rollbacks
               (id, change_request_id, rollback_commands, status, executed_by)
               VALUES (?, ?, ?, ?, ?)""",
            (rollback_id, change_request_id, rollback_commands, status, executed_by),
        )
        return await self.fetch_one("SELECT * FROM change_rollbacks WHERE id = ?", (rollback_id,))

    async def get_change_rollbacks(self, change_request_id: str) -> list[dict[str, Any]]:
        """Get all rollbacks for a change request."""
        return await self.fetch_all(
            "SELECT * FROM change_rollbacks WHERE change_request_id = ? ORDER BY executed_at DESC",
            (change_request_id,),
        )

    async def update_change_rollback(
        self, rollback_id: str, status: str, result: str | None = None, executed_at: str = "",
    ) -> bool:
        """Update a rollback record."""
        cursor = await self.execute(
            "UPDATE change_rollbacks SET status = ?, result = ?, executed_at = ? WHERE id = ?",
            (status, result, executed_at, rollback_id),
        )
        return cursor.rowcount > 0
