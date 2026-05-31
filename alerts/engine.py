"""Alert evaluation engine -- monitors metrics against rules and fires alerts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from core.database import Database
from core.exceptions import AlertError

logger = logging.getLogger(__name__)


class AlertEngine:
    """Evaluates alert rules against incoming device metrics, manages alert
    lifecycle (fire / acknowledge / resolve), and dispatches notifications
    through configured channels.

    The engine keeps an in-memory cache of active alerts keyed by a composite
    ``(rule_id, device_id)`` tuple so that it can avoid firing duplicate alerts
    for the same condition.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        # Keyed by "{rule_id}:{device_id}" for fast lookup.
        self._active_alerts: dict[str, dict[str, Any]] = {}
        # Notification channels registered at runtime.
        self._channels: list[Any] = []
        self._automation_callbacks: list = []

    # ------------------------------------------------------------------
    # Channel management
    # ------------------------------------------------------------------

    def register_channel(self, channel: Any) -> None:
        """Add a notification channel (e.g. email, Slack, webhook)."""
        self._channels.append(channel)
        logger.info("Registered alert channel: %s", type(channel).__name__)

    def register_automation_callback(self, callback) -> None:
        """Register a callback invoked on alert fire/resolve.

        Callback signature: async callback(alert: dict, event: str)
        where event is 'fired' or 'resolved'.
        """
        self._automation_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Rule evaluation
    # ------------------------------------------------------------------

    async def evaluate_rules(
        self, device_id: str, metrics: dict[str, Any]
    ) -> None:
        """Check all enabled alert rules against the supplied *metrics* dict
        for a given device.

        For each rule whose condition is met and that is not already active,
        :meth:`fire_alert` is called.  For rules that were previously active
        but are no longer triggered, :meth:`resolve_alert` is called.

        Parameters
        ----------
        device_id:
            The device these metrics belong to.
        metrics:
            A mapping of metric names to their current values (e.g.
            ``{"cpu_percent": 92.5, "memory_percent": 70.0}``).
        """
        try:
            rules = await self._db.get_alert_rules()
        except Exception as exc:
            logger.error("Failed to load alert rules: %s", exc)
            return

        if not rules:
            return

        for rule in rules:
            if not rule.get("enabled", True):
                continue

            # Scope filtering -- a rule can target a specific device_id or a
            # tag.  If neither is set the rule is global.
            rule_device_id = rule.get("device_id")
            rule_tag = rule.get("tag")
            if rule_device_id and rule_device_id != device_id:
                continue
            if rule_tag and rule_tag not in metrics.get("tags", ""):
                continue

            metric_name = rule.get("metric_name", "")
            if metric_name not in metrics:
                continue

            metric_value = metrics[metric_name]
            if not isinstance(metric_value, (int, float)):
                continue

            threshold = float(rule.get("threshold", 0))
            condition = rule.get("condition", "gt")
            triggered = self._check_condition(metric_value, condition, threshold)

            alert_key = f"{rule['id']}:{device_id}"

            if triggered and alert_key not in self._active_alerts:
                await self.fire_alert(rule, device_id, metric_value)
            elif not triggered and alert_key in self._active_alerts:
                alert = self._active_alerts[alert_key]
                await self.resolve_alert(alert["id"])

    @staticmethod
    def _check_condition(
        value: float, condition: str, threshold: float
    ) -> bool:
        """Evaluate a condition string against a value and threshold."""
        ops = {
            "gt": value > threshold,
            "gte": value >= threshold,
            "lt": value < threshold,
            "lte": value <= threshold,
            "eq": value == threshold,
            "ne": value != threshold,
        }
        return ops.get(condition, False)

    # ------------------------------------------------------------------
    # Alert lifecycle
    # ------------------------------------------------------------------

    async def fire_alert(
        self,
        rule: dict[str, Any],
        device_id: str,
        metric_value: float,
    ) -> dict[str, Any]:
        """Create a new alert, persist it, and send notifications.

        Returns the alert record.
        """
        alert_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        severity = rule.get("severity", "warning")
        metric_name = rule.get("metric_name", "unknown")
        threshold = rule.get("threshold", 0)
        condition = rule.get("condition", "gt")

        message = (
            f"Alert on device {device_id}: {metric_name} = {metric_value} "
            f"({condition} {threshold}) -- severity: {severity}"
        )

        alert: dict[str, Any] = {
            "id": alert_id,
            "rule_id": rule["id"],
            "rule_name": rule.get("name", ""),
            "device_id": device_id,
            "metric_name": metric_name,
            "metric_value": metric_value,
            "threshold": threshold,
            "condition": condition,
            "severity": severity,
            "status": "active",
            "message": message,
            "created_at": now,
            "resolved_at": None,
        }

        try:
            await self._db.add_alert(alert)
        except Exception as exc:
            logger.error("Failed to persist alert: %s", exc)
            raise AlertError(f"Cannot persist alert: {exc}") from exc

        alert_key = f"{rule['id']}:{device_id}"
        self._active_alerts[alert_key] = alert

        logger.warning("ALERT FIRED: %s", message)

        # Dispatch notifications.
        await self._notify(alert, event="fired")

        # Notify automation callbacks
        for cb in self._automation_callbacks:
            try:
                await cb(alert, "fired")
            except Exception:
                logger.exception("Automation callback error on fire_alert")

        return alert

    async def resolve_alert(self, alert_id: str) -> None:
        """Mark an alert as resolved in the DB and notify channels."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self._db.update_alert_status(alert_id, "resolved")
        except Exception as exc:
            logger.error("Failed to resolve alert %s: %s", alert_id, exc)
            raise AlertError(f"Cannot resolve alert {alert_id}: {exc}") from exc

        # Remove from in-memory cache.
        resolved_alert: dict[str, Any] | None = None
        key_to_remove: str | None = None
        for key, alert in self._active_alerts.items():
            if alert["id"] == alert_id:
                resolved_alert = alert
                key_to_remove = key
                break

        if key_to_remove:
            del self._active_alerts[key_to_remove]

        if resolved_alert:
            resolved_alert["status"] = "resolved"
            resolved_alert["resolved_at"] = now
            logger.info("ALERT RESOLVED: %s", resolved_alert.get("message", alert_id))
            await self._notify(resolved_alert, event="resolved")

            # Notify automation callbacks
            for cb in self._automation_callbacks:
                try:
                    await cb(resolved_alert, "resolved")
                except Exception:
                    logger.exception("Automation callback error on resolve_alert")
        else:
            logger.info("Alert %s resolved (not in memory cache).", alert_id)

    async def acknowledge_alert(self, alert_id: str) -> None:
        """Mark an alert as acknowledged."""
        try:
            await self._db.update_alert_status(alert_id, "acknowledged")
        except Exception as exc:
            logger.error("Failed to acknowledge alert %s: %s", alert_id, exc)
            raise AlertError(f"Cannot acknowledge alert {alert_id}: {exc}") from exc

        for alert in self._active_alerts.values():
            if alert["id"] == alert_id:
                alert["status"] = "acknowledged"
                break

        logger.info("Alert %s acknowledged.", alert_id)

    async def get_active_alerts(self) -> list[dict[str, Any]]:
        """Query the DB for all active or acknowledged alerts."""
        active = []
        try:
            for status in ("active", "acknowledged"):
                alerts = await self._db.get_alerts(status=status)
                active.extend(alerts)
        except Exception as exc:
            logger.error("Failed to query active alerts: %s", exc)
            raise AlertError(f"Cannot query active alerts: {exc}") from exc
        return active

    # ------------------------------------------------------------------
    # Notification dispatch
    # ------------------------------------------------------------------

    async def _notify(
        self, alert: dict[str, Any], event: str = "fired"
    ) -> None:
        """Send alert through all registered channels.

        Each channel is called independently; a failure in one does not block
        the others.
        """
        for channel in self._channels:
            try:
                channel_type = type(channel).__name__

                if hasattr(channel, "send_alert"):
                    # Generic method supported by all our channels.
                    await channel.send_alert(alert, event=event)
                elif hasattr(channel, "send"):
                    # Fallback: build a text payload.
                    severity = alert.get("severity", "warning")
                    message = alert.get("message", "")
                    if event == "resolved":
                        message = f"[RESOLVED] {message}"
                    await channel.send(message, severity=severity)
                else:
                    logger.warning(
                        "Channel %s has no send or send_alert method.", channel_type
                    )
            except Exception:
                logger.exception(
                    "Failed to send notification via %s.", type(channel).__name__
                )
