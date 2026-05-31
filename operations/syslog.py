"""Lightweight UDP syslog receiver (RFC 3164/5424)."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

FACILITY_NAMES = {
    0: "kern", 1: "user", 2: "mail", 3: "daemon", 4: "auth",
    5: "syslog", 6: "lpr", 7: "news", 8: "uucp", 9: "cron",
    10: "authpriv", 11: "ftp", 16: "local0", 17: "local1",
    18: "local2", 19: "local3", 20: "local4", 21: "local5",
    22: "local6", 23: "local7",
}

SEVERITY_NAMES = {
    0: "emergency", 1: "alert", 2: "critical", 3: "error",
    4: "warning", 5: "notice", 6: "info", 7: "debug",
}


class SyslogProtocol(asyncio.DatagramProtocol):
    """UDP datagram handler for syslog messages."""

    def __init__(self, receiver: SyslogReceiver) -> None:
        self._receiver = receiver

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        asyncio.create_task(self._receiver.process_message(data, addr))


class SyslogReceiver:
    """Receive and store syslog messages."""

    def __init__(
        self, db, alert_engine=None, websocket_manager=None,
        bind: str = "0.0.0.0", port: int = 514,
    ) -> None:
        self._db = db
        self._alert_engine = alert_engine
        self._ws_manager = websocket_manager
        self._bind = bind
        self._port = port
        self._transport = None
        self._protocol = None
        self._running = False

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: SyslogProtocol(self),
            local_addr=(self._bind, self._port),
        )
        self._running = True
        logger.info("Syslog receiver started on %s:%d", self._bind, self._port)

    async def stop(self) -> None:
        if self._transport:
            self._transport.close()
        self._running = False
        logger.info("Syslog receiver stopped")

    async def process_message(self, data: bytes, addr: tuple) -> None:
        try:
            raw = data.decode("utf-8", errors="replace").strip()
        except Exception:
            return

        parsed = self._parse_rfc3164(raw)
        if not parsed.get("message"):
            return

        # Try to match sender IP to a device
        sender_ip = addr[0]
        device_id = await self._match_to_device(parsed.get("hostname", ""), sender_ip)

        msg_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        await self._db.execute(
            """INSERT INTO syslog_messages
               (id, device_id, timestamp, facility, severity, hostname, app_name, message, raw, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, device_id, parsed.get("timestamp", now),
             parsed.get("facility"), parsed.get("severity"),
             parsed.get("hostname", sender_ip), parsed.get("app_name"),
             parsed["message"], raw, now),
        )

        # Broadcast to WebSocket
        if self._ws_manager:
            try:
                await self._ws_manager.broadcast("syslog", {
                    "type": "syslog_message",
                    "id": msg_id, "device_id": device_id,
                    "timestamp": parsed.get("timestamp", now),
                    "severity": parsed.get("severity"),
                    "hostname": parsed.get("hostname", sender_ip),
                    "facility": parsed.get("facility"),
                    "message": parsed["message"],
                })
            except Exception:
                pass

    def _parse_rfc3164(self, raw: str) -> dict[str, Any]:
        """Parse RFC 3164 syslog message: <PRI>TIMESTAMP HOSTNAME MSG."""
        result: dict[str, Any] = {"message": raw}

        # Extract PRI
        match = re.match(r"<(\d+)>(.*)", raw)
        if match:
            pri = int(match.group(1))
            result["facility"] = pri >> 3
            result["severity"] = pri & 0x07
            remainder = match.group(2).strip()
        else:
            remainder = raw

        # Try to extract timestamp and hostname
        # Format: "Mon DD HH:MM:SS hostname msg" or ISO timestamp
        ts_match = re.match(
            r"(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(\S+)\s+(.*)", remainder
        )
        if ts_match:
            result["timestamp"] = ts_match.group(1)
            result["hostname"] = ts_match.group(2)
            result["message"] = ts_match.group(3)
        else:
            # Try hostname msg format
            parts = remainder.split(None, 1)
            if len(parts) >= 2:
                result["hostname"] = parts[0]
                result["message"] = parts[1]

        # Extract app_name from message (e.g., "sshd[1234]: ...")
        app_match = re.match(r"(\S+?)(?:\[\d+\])?:\s*(.*)", result.get("message", ""))
        if app_match:
            result["app_name"] = app_match.group(1)

        return result

    async def _match_to_device(self, hostname: str, ip: str) -> str | None:
        """Try to find a device matching the sender."""
        if ip:
            device = await self._db.fetch_one(
                "SELECT id FROM devices WHERE ip_address = ?", (ip,)
            )
            if device:
                return device["id"]
        if hostname:
            device = await self._db.fetch_one(
                "SELECT id FROM devices WHERE hostname = ? OR hostname LIKE ?",
                (hostname, hostname.split(".")[0] + "%"),
            )
            if device:
                return device["id"]
        return None

    async def search(
        self, query: str | None = None, severity: int | None = None,
        device_id: str | None = None, since: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM syslog_messages WHERE 1=1"
        params: list[Any] = []
        if query:
            sql += " AND message LIKE ?"
            params.append(f"%{query}%")
        if severity is not None:
            sql += " AND severity = ?"
            params.append(severity)
        if device_id:
            sql += " AND device_id = ?"
            params.append(device_id)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return await self._db.fetch_all(sql, params)

    async def get_stats(self, hours: int = 24) -> dict[str, Any]:
        since = (datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=hours)).isoformat()
        total = await self._db.fetch_one(
            "SELECT COUNT(*) as cnt FROM syslog_messages WHERE created_at >= ?", (since,)
        )
        by_severity = await self._db.fetch_all(
            """SELECT severity, COUNT(*) as cnt FROM syslog_messages
               WHERE created_at >= ? GROUP BY severity ORDER BY severity""",
            (since,),
        )
        by_device = await self._db.fetch_all(
            """SELECT device_id, hostname, COUNT(*) as cnt FROM syslog_messages
               WHERE created_at >= ? GROUP BY device_id ORDER BY cnt DESC LIMIT 20""",
            (since,),
        )
        return {
            "hours": hours,
            "total": total["cnt"] if total else 0,
            "by_severity": [{"severity": r["severity"], "name": SEVERITY_NAMES.get(r["severity"], ""), "count": r["cnt"]} for r in by_severity],
            "by_device": by_device,
        }
