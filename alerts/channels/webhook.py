"""Generic webhook alert notification channel with retry logic."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from core.exceptions import AlertError

logger = logging.getLogger(__name__)

# Retry configuration.
_MAX_RETRIES = 3
_BACKOFF_BASE = 2  # seconds; exponential: 2, 4, 8


class WebhookChannel:
    """Sends alert payloads as JSON to an arbitrary webhook URL.

    Features:
    * Configurable headers (e.g. for API keys or auth tokens).
    * Automatic retry with exponential back-off (up to 3 attempts).
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> None:
        if not url:
            raise ValueError("Webhook URL must not be empty.")
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(self, payload: dict[str, Any]) -> None:
        """POST *payload* as JSON to the webhook URL.

        Retries up to ``_MAX_RETRIES`` times with exponential back-off on
        transient failures (network errors or 5xx responses).

        Parameters
        ----------
        payload:
            Arbitrary JSON-serialisable dictionary.

        Raises
        ------
        AlertError
            If all retry attempts fail.
        """
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        self.url,
                        json=payload,
                        headers=self.headers,
                    )

                if resp.status_code < 300:
                    logger.info(
                        "Webhook delivered to %s (attempt %d, HTTP %d).",
                        self.url,
                        attempt,
                        resp.status_code,
                    )
                    return

                # Treat 5xx as transient, 4xx as permanent.
                if 500 <= resp.status_code < 600:
                    logger.warning(
                        "Webhook %s returned %d on attempt %d; will retry.",
                        self.url,
                        resp.status_code,
                        attempt,
                    )
                    last_exc = AlertError(
                        f"Webhook returned HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                else:
                    # 4xx -- no point retrying.
                    raise AlertError(
                        f"Webhook returned HTTP {resp.status_code}: {resp.text[:200]}"
                    )

            except httpx.HTTPError as exc:
                logger.warning(
                    "Webhook %s failed on attempt %d: %s",
                    self.url,
                    attempt,
                    exc,
                )
                last_exc = AlertError(f"Webhook request failed: {exc}")
            except AlertError:
                raise  # 4xx -- propagate immediately.

            # Exponential back-off before next attempt.
            if attempt < _MAX_RETRIES:
                wait = _BACKOFF_BASE ** attempt
                logger.debug("Retrying webhook in %d seconds...", wait)
                await asyncio.sleep(wait)

        # All retries exhausted.
        logger.error(
            "Webhook delivery to %s failed after %d attempts.", self.url, _MAX_RETRIES
        )
        raise last_exc or AlertError("Webhook delivery failed after retries.")

    async def send_alert(self, alert: dict[str, Any], event: str = "fired") -> None:
        """Build a standardised payload from an alert dict and send it.

        Parameters
        ----------
        alert:
            The alert record dictionary.
        event:
            ``"fired"`` or ``"resolved"``.
        """
        payload = {
            "event": event,
            "alert_id": alert.get("id", ""),
            "rule_name": alert.get("rule_name", ""),
            "device_id": alert.get("device_id", ""),
            "metric_name": alert.get("metric_name", ""),
            "metric_value": alert.get("metric_value"),
            "threshold": alert.get("threshold"),
            "condition": alert.get("condition", ""),
            "severity": alert.get("severity", "warning"),
            "status": "resolved" if event == "resolved" else alert.get("status", "active"),
            "message": alert.get("message", ""),
            "created_at": alert.get("created_at", ""),
            "resolved_at": alert.get("resolved_at"),
        }
        await self.send(payload)
