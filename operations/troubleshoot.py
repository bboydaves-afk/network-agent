"""Network diagnostics and troubleshooting toolkit."""

from __future__ import annotations

import asyncio
import logging
import platform
import re
import socket
from datetime import datetime, timezone
from typing import Any

from core.database import Database
from core.credentials import CredentialManager
from core.exceptions import DeviceConnectionError, MonitoringError
from devices.registry import get_device_class

logger = logging.getLogger(__name__)


class Troubleshooter:
    """Provides a set of diagnostic operations that can be executed either
    from the local machine or through a remote network device."""

    def __init__(
        self,
        db: Database,
        credential_manager: CredentialManager,
    ) -> None:
        self._db = db
        self._cred_mgr = credential_manager

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_device_and_connect(self, device_id: str):
        """Look up, instantiate, and connect to a device. Returns
        ``(device_record, device_instance)``."""
        device_record = await self._db.get_device(device_id)
        if device_record is None:
            raise MonitoringError(device_id, f"Device {device_id!r} not found.")

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
        try:
            await device.connect()
        except Exception as exc:
            raise DeviceConnectionError(
                device_id,
                f"Cannot connect to {device_record['host']}: {exc}",
            ) from exc
        return device_record, device

    # ------------------------------------------------------------------
    # Ping
    # ------------------------------------------------------------------

    async def ping_test(
        self,
        target: str,
        count: int = 4,
        source_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a ping test.

        If *source_device_id* is given the ping is executed **from** that
        device; otherwise it runs locally via ``subprocess``.

        Returns
        -------
        dict
            ``target``, ``source``, ``packet_loss``, ``min_rtt``,
            ``avg_rtt``, ``max_rtt``, ``raw_output``.
        """
        if source_device_id:
            return await self._ping_from_device(target, count, source_device_id)
        return await self._ping_local(target, count)

    async def _ping_local(self, target: str, count: int) -> dict[str, Any]:
        is_windows = platform.system().lower() == "windows"
        if is_windows:
            cmd = ["ping", "-n", str(count), target]
        else:
            cmd = ["ping", "-c", str(count), target]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=count * 5 + 10)
            raw_output = stdout.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            return {
                "target": target,
                "source": "local",
                "packet_loss": 100.0,
                "min_rtt": None,
                "avg_rtt": None,
                "max_rtt": None,
                "raw_output": "Ping timed out.",
            }
        except OSError as exc:
            return {
                "target": target,
                "source": "local",
                "packet_loss": 100.0,
                "min_rtt": None,
                "avg_rtt": None,
                "max_rtt": None,
                "raw_output": f"OS error: {exc}",
            }

        return self._parse_ping_output(raw_output, target, "local")

    async def _ping_from_device(
        self, target: str, count: int, device_id: str
    ) -> dict[str, Any]:
        device_record, device = await self._get_device_and_connect(device_id)
        try:
            result = await device.ping(target, count=count)
            raw_output = result.get("raw_output", "")
        finally:
            await device.disconnect()

        return self._parse_ping_output(raw_output, target, device_id)

    @staticmethod
    def _parse_ping_output(
        raw: str, target: str, source: str
    ) -> dict[str, Any]:
        """Best-effort parsing of ping output across platforms / vendors."""
        result: dict[str, Any] = {
            "target": target,
            "source": source,
            "packet_loss": 100.0,
            "min_rtt": None,
            "avg_rtt": None,
            "max_rtt": None,
            "raw_output": raw,
        }

        # Packet loss  (e.g. "0% packet loss", "50% loss", "(0% loss)")
        loss_match = re.search(r"(\d+(?:\.\d+)?)%\s*(?:packet\s+)?loss", raw, re.IGNORECASE)
        if loss_match:
            result["packet_loss"] = float(loss_match.group(1))

        # RTT line -- Linux: rtt min/avg/max/mdev = 0.123/0.456/0.789/0.012 ms
        rtt_match = re.search(
            r"(?:rtt|round-trip)\s+min/avg/max(?:/\w+)?\s*=\s*"
            r"([\d.]+)/([\d.]+)/([\d.]+)",
            raw,
            re.IGNORECASE,
        )
        if rtt_match:
            result["min_rtt"] = float(rtt_match.group(1))
            result["avg_rtt"] = float(rtt_match.group(2))
            result["max_rtt"] = float(rtt_match.group(3))
        else:
            # Windows: Minimum = 1ms, Maximum = 3ms, Average = 2ms
            win_match = re.search(
                r"Minimum\s*=\s*(\d+)\s*ms.*Maximum\s*=\s*(\d+)\s*ms.*Average\s*=\s*(\d+)\s*ms",
                raw,
                re.IGNORECASE | re.DOTALL,
            )
            if win_match:
                result["min_rtt"] = float(win_match.group(1))
                result["max_rtt"] = float(win_match.group(2))
                result["avg_rtt"] = float(win_match.group(3))

        return result

    # ------------------------------------------------------------------
    # Traceroute
    # ------------------------------------------------------------------

    async def traceroute_test(
        self,
        target: str,
        source_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a traceroute.

        Returns
        -------
        dict
            ``target``, ``source``, ``hops`` (list of dicts), ``raw_output``.
        """
        if source_device_id:
            return await self._traceroute_from_device(target, source_device_id)
        return await self._traceroute_local(target)

    async def _traceroute_local(self, target: str) -> dict[str, Any]:
        is_windows = platform.system().lower() == "windows"
        cmd = ["tracert", target] if is_windows else ["traceroute", "-m", "30", target]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            raw_output = stdout.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            return {
                "target": target,
                "source": "local",
                "hops": [],
                "raw_output": "Traceroute timed out.",
            }
        except OSError as exc:
            return {
                "target": target,
                "source": "local",
                "hops": [],
                "raw_output": f"OS error: {exc}",
            }

        return {
            "target": target,
            "source": "local",
            "hops": self._parse_traceroute(raw_output),
            "raw_output": raw_output,
        }

    async def _traceroute_from_device(
        self, target: str, device_id: str
    ) -> dict[str, Any]:
        device_record, device = await self._get_device_and_connect(device_id)
        try:
            result = await device.traceroute(target)
            raw_output = result.get("raw_output", "")
        finally:
            await device.disconnect()

        return {
            "target": target,
            "source": device_id,
            "hops": self._parse_traceroute(raw_output),
            "raw_output": raw_output,
        }

    @staticmethod
    def _parse_traceroute(raw: str) -> list[dict[str, Any]]:
        """Best-effort parsing of traceroute output into structured hops."""
        hops: list[dict[str, Any]] = []
        # Match lines like: " 1  10.0.0.1  2.345 ms  1.234 ms  1.567 ms"
        hop_pattern = re.compile(
            r"^\s*(\d+)\s+"        # hop number
            r"(?:"
            r"(\S+)"               # hostname or IP
            r"\s+\(?([\d.]+)\)?"   # IP in parens (optional)
            r"|(\*)"               # or a timeout star
            r")"
            r"(.*)",               # rest of line (RTT values)
        )
        rtt_pattern = re.compile(r"([\d.]+)\s*ms")

        for line in raw.splitlines():
            m = hop_pattern.match(line)
            if not m:
                # Simpler pattern: just hop_num and IP
                simple = re.match(r"^\s*(\d+)\s+([\d.]+)\s+(.*)", line)
                if simple:
                    hop_num = int(simple.group(1))
                    ip = simple.group(2)
                    rest = simple.group(3)
                    rtts = [float(x) for x in rtt_pattern.findall(rest)]
                    hops.append({
                        "hop": hop_num,
                        "ip": ip,
                        "hostname": ip,
                        "rtt_ms": rtts if rtts else None,
                    })
                continue

            hop_num = int(m.group(1))
            if m.group(4) == "*":
                hops.append({"hop": hop_num, "ip": "*", "hostname": "*", "rtt_ms": None})
                continue

            hostname = m.group(2) or ""
            ip = m.group(3) or hostname
            rest = m.group(5) or ""
            rtts = [float(x) for x in rtt_pattern.findall(rest)]

            hops.append({
                "hop": hop_num,
                "ip": ip,
                "hostname": hostname,
                "rtt_ms": rtts if rtts else None,
            })

        return hops

    # ------------------------------------------------------------------
    # Port check
    # ------------------------------------------------------------------

    async def port_check(
        self, target: str, port: int, timeout: int = 5
    ) -> dict[str, Any]:
        """Test TCP connectivity to *target*:*port*.

        Returns
        -------
        dict
            ``target``, ``port``, ``open`` (bool), ``response_time_ms``.
        """
        loop = asyncio.get_running_loop()
        start = loop.time()
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(target, port),
                timeout=timeout,
            )
            elapsed = (loop.time() - start) * 1000.0
            writer.close()
            await writer.wait_closed()
            return {
                "target": target,
                "port": port,
                "open": True,
                "response_time_ms": round(elapsed, 2),
            }
        except (asyncio.TimeoutError, OSError, ConnectionRefusedError):
            elapsed = (loop.time() - start) * 1000.0
            return {
                "target": target,
                "port": port,
                "open": False,
                "response_time_ms": round(elapsed, 2),
            }

    # ------------------------------------------------------------------
    # Interface error check
    # ------------------------------------------------------------------

    async def check_interface_errors(
        self, device_id: str
    ) -> list[dict[str, Any]]:
        """Connect to a device, pull interface counters, and return only
        interfaces that have errors.

        Returns
        -------
        list[dict]
            Each dict: ``name``, ``in_errors``, ``out_errors``,
            ``in_discards``, ``out_discards``, ``total_errors``,
            ``status``.
        """
        device_record, device = await self._get_device_and_connect(device_id)
        try:
            interfaces = await device.get_interfaces()
        finally:
            await device.disconnect()

        error_ifaces: list[dict[str, Any]] = []
        for iface in interfaces:
            total = iface.in_errors + iface.out_errors + iface.in_discards + iface.out_discards
            if total > 0:
                error_ifaces.append({
                    "name": iface.name,
                    "status": iface.status,
                    "in_errors": iface.in_errors,
                    "out_errors": iface.out_errors,
                    "in_discards": iface.in_discards,
                    "out_discards": iface.out_discards,
                    "total_errors": total,
                })

        error_ifaces.sort(key=lambda x: x["total_errors"], reverse=True)
        return error_ifaces

    # ------------------------------------------------------------------
    # Comprehensive health check
    # ------------------------------------------------------------------

    async def check_device_health(self, device_id: str) -> dict[str, Any]:
        """Compile a comprehensive health report for a single device.

        Collects facts, health metrics, and interfaces, then flags any
        warnings where thresholds are exceeded.

        Returns
        -------
        dict
            ``device_id``, ``facts``, ``health``, ``interfaces``,
            ``warnings`` (list of strings), ``overall_status``.
        """
        device_record, device = await self._get_device_and_connect(device_id)
        warnings: list[str] = []
        try:
            facts = await device.get_facts()
            health = await device.get_health()
            interfaces = await device.get_interfaces()
        finally:
            await device.disconnect()

        facts_dict = facts.to_dict()
        health_dict = health.to_dict()
        iface_list = [iface.to_dict() for iface in interfaces]

        # Threshold checks.
        if health.cpu_percent > 90:
            warnings.append(
                f"CRITICAL: CPU usage at {health.cpu_percent:.1f}% (threshold: 90%)"
            )
        elif health.cpu_percent > 80:
            warnings.append(
                f"WARNING: CPU usage at {health.cpu_percent:.1f}% (threshold: 80%)"
            )

        if health.memory_percent > 90:
            warnings.append(
                f"CRITICAL: Memory usage at {health.memory_percent:.1f}% (threshold: 90%)"
            )
        elif health.memory_percent > 85:
            warnings.append(
                f"WARNING: Memory usage at {health.memory_percent:.1f}% (threshold: 85%)"
            )

        if health.temperature_celsius is not None:
            if health.temperature_celsius > 75:
                warnings.append(
                    f"CRITICAL: Temperature at {health.temperature_celsius:.1f}C (threshold: 75C)"
                )
            elif health.temperature_celsius > 60:
                warnings.append(
                    f"WARNING: Temperature at {health.temperature_celsius:.1f}C (threshold: 60C)"
                )

        if health.disk_percent > 90:
            warnings.append(
                f"CRITICAL: Disk usage at {health.disk_percent:.1f}% (threshold: 90%)"
            )
        elif health.disk_percent > 80:
            warnings.append(
                f"WARNING: Disk usage at {health.disk_percent:.1f}% (threshold: 80%)"
            )

        # Interface checks.
        down_interfaces: list[str] = []
        error_interfaces: list[str] = []
        for iface in interfaces:
            if iface.status == "down" and iface.protocol_status == "down":
                down_interfaces.append(iface.name)
            total_errs = iface.in_errors + iface.out_errors
            if total_errs > 0:
                error_interfaces.append(f"{iface.name} ({total_errs} errors)")

        if down_interfaces:
            warnings.append(
                f"INFO: {len(down_interfaces)} interface(s) down: "
                + ", ".join(down_interfaces[:10])
            )
        if error_interfaces:
            warnings.append(
                f"WARNING: Interface errors on: "
                + ", ".join(error_interfaces[:10])
            )

        # Overall status.
        if any(w.startswith("CRITICAL") for w in warnings):
            overall = "critical"
        elif any(w.startswith("WARNING") for w in warnings):
            overall = "warning"
        else:
            overall = "healthy"

        return {
            "device_id": device_id,
            "hostname": facts.hostname,
            "facts": facts_dict,
            "health": health_dict,
            "interfaces": iface_list,
            "interface_count": len(iface_list),
            "warnings": warnings,
            "overall_status": overall,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # DNS lookup
    # ------------------------------------------------------------------

    async def dns_lookup(self, hostname: str) -> dict[str, Any]:
        """Resolve a hostname to IP addresses using :func:`socket.getaddrinfo`.

        Returns
        -------
        dict
            ``hostname``, ``ips`` (list of unique IPs), ``resolved`` (bool).
        """
        loop = asyncio.get_running_loop()

        try:
            infos = await loop.run_in_executor(
                None,
                lambda: socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM),
            )
            ips = sorted({info[4][0] for info in infos})
            return {"hostname": hostname, "ips": ips, "resolved": True}
        except socket.gaierror as exc:
            logger.warning("DNS lookup for %s failed: %s", hostname, exc)
            return {"hostname": hostname, "ips": [], "resolved": False}

    # ------------------------------------------------------------------
    # Bandwidth test
    # ------------------------------------------------------------------

    async def bandwidth_test(
        self,
        device_id: str,
        interface: str,
        duration: int = 10,
    ) -> dict[str, Any]:
        """Measure bandwidth utilisation on *interface* over *duration*
        seconds by taking two interface counter readings and computing the
        delta.

        Returns
        -------
        dict
            ``device_id``, ``interface``, ``duration_seconds``,
            ``bps_in``, ``bps_out``, ``utilization_in_percent``,
            ``utilization_out_percent``, ``speed``.
        """
        device_record, device = await self._get_device_and_connect(device_id)
        try:
            # First reading.
            ifaces_t1 = await device.get_interfaces()
            t1 = asyncio.get_running_loop().time()

            await asyncio.sleep(duration)

            # Second reading.
            ifaces_t2 = await device.get_interfaces()
            t2 = asyncio.get_running_loop().time()
        finally:
            await device.disconnect()

        elapsed = t2 - t1
        if elapsed <= 0:
            elapsed = duration  # Fallback.

        iface_t1 = self._find_interface(ifaces_t1, interface)
        iface_t2 = self._find_interface(ifaces_t2, interface)

        if iface_t1 is None or iface_t2 is None:
            raise MonitoringError(
                device_id,
                f"Interface {interface!r} not found on device {device_id!r}.",
            )

        delta_in_bytes = max(iface_t2.in_octets - iface_t1.in_octets, 0)
        delta_out_bytes = max(iface_t2.out_octets - iface_t1.out_octets, 0)

        bps_in = (delta_in_bytes * 8) / elapsed
        bps_out = (delta_out_bytes * 8) / elapsed

        # Parse interface speed for utilisation calculation.
        speed_bps = self._parse_speed(iface_t1.speed)
        if speed_bps > 0:
            util_in = (bps_in / speed_bps) * 100.0
            util_out = (bps_out / speed_bps) * 100.0
        else:
            util_in = 0.0
            util_out = 0.0

        return {
            "device_id": device_id,
            "interface": interface,
            "duration_seconds": round(elapsed, 2),
            "bps_in": round(bps_in, 2),
            "bps_out": round(bps_out, 2),
            "utilization_in_percent": round(util_in, 2),
            "utilization_out_percent": round(util_out, 2),
            "speed": iface_t1.speed,
            "speed_bps": speed_bps,
        }

    @staticmethod
    def _find_interface(interfaces, name: str):
        """Find an interface by name (case-insensitive)."""
        name_lower = name.lower()
        for iface in interfaces:
            if iface.name.lower() == name_lower:
                return iface
        return None

    @staticmethod
    def _parse_speed(speed_str: str) -> float:
        """Convert a speed string like ``"1000Mbps"`` or ``"10Gbps"`` to bits
        per second."""
        if not speed_str:
            return 0.0
        m = re.match(r"([\d.]+)\s*(Gbps|Mbps|Kbps|bps)", speed_str, re.IGNORECASE)
        if not m:
            # Try plain numeric (assume bps).
            try:
                return float(speed_str)
            except ValueError:
                return 0.0
        value = float(m.group(1))
        unit = m.group(2).lower()
        multipliers = {"gbps": 1e9, "mbps": 1e6, "kbps": 1e3, "bps": 1.0}
        return value * multipliers.get(unit, 1.0)
