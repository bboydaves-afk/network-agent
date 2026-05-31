"""Health monitoring engine -- periodic device polling and metrics collection."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import uuid4

from core.database import Database
from core.credentials import CredentialManager
from core.exceptions import DeviceConnectionError, MonitoringError
from devices.registry import get_device_class

logger = logging.getLogger(__name__)

# Maximum number of devices polled at the same time.
_DEFAULT_CONCURRENCY = 10


class MonitoringEngine:
    """Polls network devices for health data and stores metrics in the DB.

    Features
    --------
    * Per-device polling that captures CPU, memory, and interface counters.
    * Concurrent polling of all devices with a configurable semaphore.
    * An infinite polling loop with proper cancellation support.
    * Convenience helpers for querying metrics and building a dashboard.
    """

    def __init__(
        self,
        db: Database,
        credential_manager: CredentialManager,
        poll_interval: int = 60,
        max_concurrent: int = _DEFAULT_CONCURRENCY,
        alert_engine=None,
    ) -> None:
        self._db = db
        self._cred_mgr = credential_manager
        self.poll_interval = poll_interval
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._alert_engine = alert_engine

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _instantiate_device(self, device_record: dict[str, Any]):
        """Create and connect a device driver from a DB record."""
        creds = await self._cred_mgr.get_credentials(
            device_record.get("credential_id", "")
        )
        device_cls = get_device_class(device_record["device_type"])
        device = device_cls(
            host=device_record["host"],
            username=creds.get("username", ""),
            password=creds.get("password", ""),
            port=device_record.get("port", 22),
            device_type=device_record["device_type"],
            enable_secret=creds.get("enable_secret", ""),
            ssh_key_path=creds.get("ssh_key_path", ""),
            timeout=device_record.get("timeout", 30),
        )
        await device.connect()
        return device

    async def _store_metric(
        self,
        device_id: str,
        metric_name: str,
        metric_value: float,
        timestamp: str | None = None,
    ) -> None:
        """Store a single metric data-point in the DB."""
        metric = {
            "id": str(uuid4()),
            "device_id": device_id,
            "metric_name": metric_name,
            "metric_value": metric_value,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        }
        await self._db.add_metric(metric)

    # ------------------------------------------------------------------
    # Public API -- single device
    # ------------------------------------------------------------------

    async def poll_device(self, device_id: str) -> dict[str, Any]:
        """Poll a single device for health and interface data.

        Returns
        -------
        dict
            Collected metrics keyed by metric name.  Also includes a
            ``"device_status"`` field (``"online"`` or ``"degraded"``).
        """
        device_record = await self._db.get_device(device_id)
        if device_record is None:
            raise MonitoringError(device_id, f"Device {device_id!r} not found.")

        now = datetime.now(timezone.utc).isoformat()
        metrics: dict[str, Any] = {"device_id": device_id, "timestamp": now}

        try:
            device = await self._instantiate_device(device_record)
        except Exception as exc:
            logger.error("Poll failed for %s: cannot connect -- %s", device_id, exc)
            metrics["device_status"] = "offline"
            await self._store_metric(device_id, "device_reachable", 0.0, now)
            return metrics

        try:
            # --- Health ---
            health = await device.get_health()
            health_dict = health.to_dict()

            await self._store_metric(device_id, "cpu_percent", health.cpu_percent, now)
            await self._store_metric(device_id, "memory_percent", health.memory_percent, now)

            if health.temperature_celsius is not None:
                await self._store_metric(
                    device_id, "temperature_celsius", health.temperature_celsius, now
                )

            metrics["cpu_percent"] = health.cpu_percent
            metrics["memory_percent"] = health.memory_percent
            metrics["temperature_celsius"] = health.temperature_celsius

            # --- Interfaces ---
            interfaces = await device.get_interfaces()
            total_in_errors = 0
            total_out_errors = 0
            total_in_octets = 0
            total_out_octets = 0

            interface_details: list[dict[str, Any]] = []
            for iface in interfaces:
                iface_dict = iface.to_dict()
                interface_details.append(iface_dict)
                total_in_errors += iface.in_errors
                total_out_errors += iface.out_errors
                total_in_octets += iface.in_octets
                total_out_octets += iface.out_octets

            await self._store_metric(device_id, "interface_in_errors", float(total_in_errors), now)
            await self._store_metric(device_id, "interface_out_errors", float(total_out_errors), now)
            await self._store_metric(device_id, "interface_in_octets", float(total_in_octets), now)
            await self._store_metric(device_id, "interface_out_octets", float(total_out_octets), now)

            metrics["interface_in_errors"] = total_in_errors
            metrics["interface_out_errors"] = total_out_errors
            metrics["interfaces"] = interface_details

            # Reachable.
            await self._store_metric(device_id, "device_reachable", 1.0, now)

            # Determine status.
            if health.cpu_percent > 90 or health.memory_percent > 90 or total_in_errors + total_out_errors > 100:
                device_status = "degraded"
            else:
                device_status = "online"

            metrics["device_status"] = device_status
            logger.info(
                "Polled device %s: CPU=%.1f%%, MEM=%.1f%%, status=%s",
                device_id,
                health.cpu_percent,
                health.memory_percent,
                device_status,
            )

            # Evaluate alert rules against collected metrics
            if self._alert_engine:
                try:
                    await self._alert_engine.evaluate_rules(device_id, metrics)
                except Exception:
                    logger.exception("Alert evaluation failed for device %s", device_id)
        except Exception as exc:
            logger.exception("Error polling device %s: %s", device_id, exc)
            metrics["device_status"] = "degraded"
            await self._store_metric(device_id, "device_reachable", 0.5, now)
        finally:
            try:
                await device.disconnect()
            except Exception:
                logger.debug("Error disconnecting from %s.", device_id)

        return metrics

    # ------------------------------------------------------------------
    # Public API -- all devices
    # ------------------------------------------------------------------

    async def poll_all_devices(self) -> dict[str, Any]:
        """Poll every device in the database concurrently.

        Uses an ``asyncio.Semaphore`` to cap the number of simultaneous
        device connections.

        Returns
        -------
        dict
            ``{ "results": [...], "total": int, "online": int, "offline": int }``
        """
        devices = await self._db.list_devices()
        if not devices:
            logger.warning("No devices found in database for polling.")
            return {"results": [], "total": 0, "online": 0, "offline": 0}

        async def _limited_poll(dev: dict[str, Any]) -> dict[str, Any]:
            async with self._semaphore:
                return await self._safe_poll(dev["id"])

        tasks = [_limited_poll(d) for d in devices]
        results = await asyncio.gather(*tasks)

        online = sum(1 for r in results if r.get("device_status") == "online")
        offline = sum(1 for r in results if r.get("device_status") == "offline")

        return {
            "results": list(results),
            "total": len(devices),
            "online": online,
            "offline": len(devices) - online,
        }

    async def _safe_poll(self, device_id: str) -> dict[str, Any]:
        """Wrapper that never raises so ``gather`` continues on failure."""
        try:
            return await self.poll_device(device_id)
        except Exception:
            logger.exception("Unhandled error polling device %s.", device_id)
            return {"device_id": device_id, "device_status": "offline"}

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def start_polling_loop(self) -> None:
        """Run an infinite polling loop.

        The loop can be cancelled by calling :meth:`stop_polling_loop` or by
        cancelling the task returned by ``asyncio.create_task(engine.start_polling_loop())``.
        """
        self._running = True
        logger.info(
            "Starting monitoring loop (interval=%ds).", self.poll_interval
        )
        try:
            while self._running:
                try:
                    summary = await self.poll_all_devices()
                    logger.info(
                        "Poll cycle complete: %d devices, %d online, %d offline.",
                        summary["total"],
                        summary["online"],
                        summary["offline"],
                    )
                except Exception:
                    logger.exception("Error during poll cycle.")

                # Clean up old metrics periodically (every cycle is fine).
                try:
                    await self._db.cleanup_old_metrics()
                except Exception:
                    logger.debug("Metric cleanup skipped or failed.")

                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("Monitoring loop cancelled.")
        finally:
            self._running = False

    def stop_polling_loop(self) -> None:
        """Signal the polling loop to stop after the current cycle."""
        self._running = False
        logger.info("Monitoring loop stop requested.")

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def get_device_metrics(
        self,
        device_id: str,
        metric_name: str | None = None,
        hours: int = 24,
    ) -> list[dict[str, Any]]:
        """Retrieve stored metrics for a device within the given time window.

        Parameters
        ----------
        device_id:
            Device to query.
        metric_name:
            Optional filter on a specific metric (e.g. ``"cpu_percent"``).
        hours:
            How many hours of history to return (default 24).
        """
        since = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()
        metrics = await self._db.get_metrics(
            device_id=device_id,
            metric_name=metric_name,
            since=since,
        )
        return metrics

    async def get_dashboard_summary(self) -> dict[str, Any]:
        """Build a high-level dashboard summary across all devices.

        Returns
        -------
        dict
            Keys: ``total_devices``, ``online``, ``offline``, ``avg_cpu``,
            ``avg_memory``, ``top_cpu_devices``, ``recent_alerts``,
            ``interface_error_count``.
        """
        devices = await self._db.list_devices()
        total_devices = len(devices)
        if total_devices == 0:
            return {
                "total_devices": 0,
                "online": 0,
                "offline": 0,
                "avg_cpu": 0.0,
                "avg_memory": 0.0,
                "top_cpu_devices": [],
                "recent_alerts": 0,
                "interface_error_count": 0,
            }

        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        cpu_values: list[tuple[str, float]] = []
        mem_values: list[float] = []
        total_errors = 0
        online_count = 0

        for dev in devices:
            dev_id = dev["id"]
            # Latest CPU metric.
            cpu_metrics = await self._db.get_metrics(
                device_id=dev_id, metric_name="cpu_percent", since=since
            )
            if cpu_metrics:
                latest_cpu = cpu_metrics[-1].get("metric_value", 0.0)
                cpu_values.append((dev_id, latest_cpu))

            # Latest memory metric.
            mem_metrics = await self._db.get_metrics(
                device_id=dev_id, metric_name="memory_percent", since=since
            )
            if mem_metrics:
                mem_values.append(mem_metrics[-1].get("metric_value", 0.0))

            # Reachable?
            reach_metrics = await self._db.get_metrics(
                device_id=dev_id, metric_name="device_reachable", since=since
            )
            if reach_metrics and reach_metrics[-1].get("metric_value", 0) >= 1.0:
                online_count += 1

            # Interface errors.
            err_metrics = await self._db.get_metrics(
                device_id=dev_id, metric_name="interface_in_errors", since=since
            )
            if err_metrics:
                total_errors += int(err_metrics[-1].get("metric_value", 0))
            err_metrics_out = await self._db.get_metrics(
                device_id=dev_id, metric_name="interface_out_errors", since=since
            )
            if err_metrics_out:
                total_errors += int(err_metrics_out[-1].get("metric_value", 0))

        avg_cpu = sum(v for _, v in cpu_values) / len(cpu_values) if cpu_values else 0.0
        avg_memory = sum(mem_values) / len(mem_values) if mem_values else 0.0

        # Top 5 CPU consumers.
        cpu_values.sort(key=lambda x: x[1], reverse=True)
        top_cpu = [
            {"device_id": did, "cpu_percent": val}
            for did, val in cpu_values[:5]
        ]

        # Recent alerts (last 24 h).
        try:
            alerts = await self._db.get_alerts(status="active")
            recent_alerts = len(alerts)
        except Exception:
            recent_alerts = 0

        return {
            "total_devices": total_devices,
            "online": online_count,
            "offline": total_devices - online_count,
            "avg_cpu": round(avg_cpu, 2),
            "avg_memory": round(avg_memory, 2),
            "top_cpu_devices": top_cpu,
            "recent_alerts": recent_alerts,
            "interface_error_count": total_errors,
        }
