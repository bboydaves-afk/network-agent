"""Email alert notification channel using SMTP."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from core.exceptions import AlertError

logger = logging.getLogger(__name__)


class EmailChannel:
    """Sends alert notifications as HTML emails via SMTP.

    The actual SMTP call is blocking (``smtplib``), so it is executed in the
    default thread-pool executor to keep the event loop responsive.
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        from_address: str = "",
        use_tls: bool = True,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.from_address = from_address
        self.use_tls = use_tls

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(self, to: str, subject: str, body: str) -> None:
        """Send an email with *subject* and *body* (HTML) to *to*.

        Parameters
        ----------
        to:
            Recipient email address.
        subject:
            Email subject line.
        body:
            HTML body content.
        """
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._send_sync, to, subject, body)
            logger.info("Email sent to %s: %s", to, subject)
        except Exception as exc:
            logger.error("Failed to send email to %s: %s", to, exc)
            raise AlertError(f"Email delivery failed: {exc}") from exc

    async def send_alert(self, alert: dict[str, Any], event: str = "fired") -> None:
        """Format and send an alert-specific email.

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
        message = alert.get("message", "")
        alert_id = alert.get("id", "")
        rule_name = alert.get("rule_name", "")
        created_at = alert.get("created_at", "")

        if event == "resolved":
            subject = f"[RESOLVED] {rule_name or metric_name} on {device_id}"
            banner_color = "#27ae60"
            status_text = "RESOLVED"
        elif severity == "critical":
            subject = f"[CRITICAL] {rule_name or metric_name} on {device_id}"
            banner_color = "#e74c3c"
            status_text = "CRITICAL"
        else:
            subject = f"[WARNING] {rule_name or metric_name} on {device_id}"
            banner_color = "#f39c12"
            status_text = "WARNING"

        html_body = f"""\
<html>
<body style="font-family: Arial, sans-serif; margin: 0; padding: 0;">
<div style="background-color: {banner_color}; color: white; padding: 16px 24px;">
    <h2 style="margin: 0;">Network Agent Alert - {status_text}</h2>
</div>
<div style="padding: 24px;">
    <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
        <tr>
            <td style="padding: 8px; font-weight: bold; border-bottom: 1px solid #eee;">Alert ID</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{alert_id}</td>
        </tr>
        <tr>
            <td style="padding: 8px; font-weight: bold; border-bottom: 1px solid #eee;">Rule</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{rule_name}</td>
        </tr>
        <tr>
            <td style="padding: 8px; font-weight: bold; border-bottom: 1px solid #eee;">Device</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{device_id}</td>
        </tr>
        <tr>
            <td style="padding: 8px; font-weight: bold; border-bottom: 1px solid #eee;">Metric</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{metric_name} = {metric_value}</td>
        </tr>
        <tr>
            <td style="padding: 8px; font-weight: bold; border-bottom: 1px solid #eee;">Threshold</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{threshold}</td>
        </tr>
        <tr>
            <td style="padding: 8px; font-weight: bold; border-bottom: 1px solid #eee;">Severity</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{severity}</td>
        </tr>
        <tr>
            <td style="padding: 8px; font-weight: bold; border-bottom: 1px solid #eee;">Time</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{created_at}</td>
        </tr>
    </table>
    <p style="margin-top: 16px; color: #555;">{message}</p>
</div>
<div style="background-color: #f5f5f5; padding: 12px 24px; font-size: 12px; color: #888;">
    This alert was generated by Network Agent.
</div>
</body>
</html>
"""
        # Use from_address as the default recipient if none configured elsewhere.
        to = self.from_address
        await self.send(to, subject, html_body)

    # ------------------------------------------------------------------
    # Synchronous SMTP helper
    # ------------------------------------------------------------------

    def _send_sync(self, to: str, subject: str, html_body: str) -> None:
        """Blocking SMTP send -- called from an executor."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_address
        msg["To"] = to

        # Attach HTML part.
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        # Plain-text fallback (strip tags for simplicity).
        import re
        plain = re.sub(r"<[^>]+>", "", html_body)
        plain = re.sub(r"\s+", " ", plain).strip()
        msg.attach(MIMEText(plain, "plain", "utf-8"))

        if self.use_tls:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)

        try:
            if self.smtp_user and self.smtp_password:
                server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.from_address, [to], msg.as_string())
        finally:
            server.quit()
