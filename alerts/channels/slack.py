"""Slack webhook alert notification channel."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.exceptions import AlertError

logger = logging.getLogger(__name__)

# Slack attachment colours mapped to severity levels.
_SEVERITY_COLORS: dict[str, str] = {
    "critical": "#e74c3c",   # Red
    "warning": "#f39c12",    # Orange/yellow
    "info": "#3498db",       # Blue
    "resolved": "#27ae60",   # Green
}


class SlackChannel:
    """Sends alert notifications to a Slack channel via an incoming webhook.

    Uses ``httpx`` for async HTTP requests.
    """

    def __init__(self, webhook_url: str) -> None:
        if not webhook_url:
            raise ValueError("Slack webhook_url must not be empty.")
        self.webhook_url = webhook_url

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(self, message: str, severity: str = "warning") -> None:
        """Post a simple text message to the Slack webhook.

        Parameters
        ----------
        message:
            The message body.
        severity:
            One of ``"critical"``, ``"warning"``, ``"info"``, ``"resolved"``.
            Controls the sidebar colour on the Slack attachment.
        """
        color = _SEVERITY_COLORS.get(severity, _SEVERITY_COLORS["warning"])

        payload = {
            "attachments": [
                {
                    "color": color,
                    "text": message,
                    "footer": "Network Agent",
                    "ts": None,  # Slack will use current time.
                }
            ]
        }

        await self._post(payload)

    async def send_alert(self, alert: dict[str, Any], event: str = "fired") -> None:
        """Format and send a rich Slack message for an alert event.

        Parameters
        ----------
        alert:
            The alert record dict.
        event:
            ``"fired"`` or ``"resolved"``.
        """
        severity = alert.get("severity", "warning")
        device_id = alert.get("device_id", "unknown")
        metric_name = alert.get("metric_name", "")
        metric_value = alert.get("metric_value", "")
        threshold = alert.get("threshold", "")
        condition = alert.get("condition", "")
        rule_name = alert.get("rule_name", "")
        message = alert.get("message", "")
        alert_id = alert.get("id", "")[:8]

        if event == "resolved":
            color = _SEVERITY_COLORS["resolved"]
            title = f"Resolved: {rule_name or metric_name}"
            emoji = ":white_check_mark:"
        elif severity == "critical":
            color = _SEVERITY_COLORS["critical"]
            title = f"Critical Alert: {rule_name or metric_name}"
            emoji = ":rotating_light:"
        else:
            color = _SEVERITY_COLORS.get(severity, _SEVERITY_COLORS["warning"])
            title = f"Warning: {rule_name or metric_name}"
            emoji = ":warning:"

        fields = [
            {"title": "Device", "value": device_id, "short": True},
            {"title": "Severity", "value": severity.upper(), "short": True},
            {"title": "Metric", "value": f"{metric_name} = {metric_value}", "short": True},
            {"title": "Threshold", "value": f"{condition} {threshold}", "short": True},
        ]

        payload = {
            "text": f"{emoji} *{title}*",
            "attachments": [
                {
                    "color": color,
                    "title": title,
                    "text": message,
                    "fields": fields,
                    "footer": f"Network Agent | Alert ID: {alert_id}",
                }
            ],
        }

        await self._post(payload)
        logger.info("Slack notification sent for alert on device %s.", device_id)

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    async def _post(self, payload: dict[str, Any]) -> None:
        """POST *payload* as JSON to the Slack webhook URL."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(self.webhook_url, json=payload)
                if resp.status_code != 200:
                    logger.error(
                        "Slack webhook returned %d: %s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    raise AlertError(
                        f"Slack webhook returned HTTP {resp.status_code}"
                    )
        except httpx.HTTPError as exc:
            logger.error("Slack webhook request failed: %s", exc)
            raise AlertError(f"Slack notification failed: {exc}") from exc
