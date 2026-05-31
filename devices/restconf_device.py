"""RESTCONF-based device driver using httpx."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

import httpx

from core.exceptions import (
    DeviceAuthenticationError,
    DeviceCommandError,
    DeviceConnectionError,
    DeviceTimeoutError,
)
from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.base import BaseDevice

logger = logging.getLogger(__name__)

# Common RESTCONF media types
RESTCONF_JSON = "application/yang-data+json"
RESTCONF_XML = "application/yang-data+xml"
RESTCONF_PATCH_JSON = "application/yang-patch+json"

# Default RESTCONF root paths (varies by vendor)
DEFAULT_RESTCONF_ROOT = "/restconf"
CISCO_RESTCONF_ROOT = "/restconf"
JUNIPER_RESTCONF_ROOT = "/rests"


class RESTCONFDevice(BaseDevice):
    """Device driver for RESTCONF (RFC 8040) using httpx.

    Parameters
    ----------
    host : str
        Target device IP/hostname.
    port : int
        HTTPS port (default 443).
    restconf_root : str
        Base URL path for RESTCONF (default ``"/restconf"``).
    verify_ssl : bool
        Verify TLS certificates (default False for lab devices).
    scheme : str
        ``"https"`` or ``"http"`` (default ``"https"``).
    """

    def __init__(
        self,
        host: str,
        port: int = 443,
        username: str = "",
        password: str = "",
        timeout: int = 30,
        restconf_root: str = DEFAULT_RESTCONF_ROOT,
        verify_ssl: bool = False,
        scheme: str = "https",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            host=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            **kwargs,
        )
        self.restconf_root = restconf_root.rstrip("/")
        self.verify_ssl = verify_ssl
        self.scheme = scheme
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        """Construct the base URL for RESTCONF calls."""
        return f"{self.scheme}://{self.host}:{self.port}{self.restconf_root}"

    def _url(self, path: str) -> str:
        """Build a full URL for a RESTCONF path (relative to root)."""
        if path.startswith("http"):
            return path
        path = path.lstrip("/")
        return f"{self.base_url}/{path}"

    # ------------------------------------------------------------------
    # Low-level HTTP
    # ------------------------------------------------------------------

    async def _make_request(
        self,
        method: str,
        path: str,
        *,
        data: Any = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> httpx.Response:
        """Execute an authenticated HTTP request with error handling.

        Returns the ``httpx.Response`` object.

        Raises
        ------
        DeviceAuthenticationError
            On 401/403.
        DeviceTimeoutError
            On request timeout.
        DeviceCommandError
            On other HTTP errors.
        """
        if self._client is None:
            raise DeviceConnectionError(
                device_id=self.host,
                message="RESTCONF client not initialised -- call connect() first",
            )

        default_headers = {
            "Accept": RESTCONF_JSON,
            "Content-Type": RESTCONF_JSON,
        }
        if headers:
            default_headers.update(headers)

        url = self._url(path)
        req_timeout = timeout or self.timeout

        try:
            response = await self._client.request(
                method=method.upper(),
                url=url,
                content=json.dumps(data) if data and not isinstance(data, str) else data,
                headers=default_headers,
                params=params,
                timeout=req_timeout,
            )
        except httpx.TimeoutException as exc:
            raise DeviceTimeoutError(
                device_id=self.host,
                message=f"RESTCONF {method.upper()} {path} timed out",
                timeout_seconds=req_timeout,
            ) from exc
        except httpx.ConnectError as exc:
            raise DeviceConnectionError(
                device_id=self.host,
                message=f"RESTCONF connection error: {exc}",
            ) from exc

        if response.status_code in (401, 403):
            raise DeviceAuthenticationError(
                device_id=self.host,
                message=f"RESTCONF auth failure (HTTP {response.status_code})",
            )

        if response.status_code >= 400:
            body = response.text[:500]
            raise DeviceCommandError(
                device_id=self.host,
                message=(
                    f"RESTCONF {method.upper()} {path} returned HTTP "
                    f"{response.status_code}: {body}"
                ),
                command=f"{method.upper()} {path}",
            )

        return response

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Validate RESTCONF accessibility."""
        if self._connected and self._client:
            return
        self._client = httpx.AsyncClient(
            auth=(self.username, self.password) if self.username else None,
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        # Probe the RESTCONF root to verify reachability
        try:
            resp = await self._make_request("GET", "/data")
            self._connected = True
            logger.info(
                "RESTCONF connected to %s (HTTP %s)", self.host, resp.status_code
            )
        except DeviceAuthenticationError:
            await self._close_client()
            raise
        except Exception as exc:
            await self._close_client()
            raise DeviceConnectionError(
                device_id=self.host,
                message=f"RESTCONF connectivity check failed: {exc}",
            ) from exc

    async def disconnect(self) -> None:
        """Close the HTTP client."""
        await self._close_client()
        logger.info("RESTCONF disconnected from %s", self.host)

    async def _close_client(self) -> None:
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
            self._connected = False

    # ------------------------------------------------------------------
    # BaseDevice interface
    # ------------------------------------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        """Retrieve config via RESTCONF GET.

        ``config_type`` is mapped to the RESTCONF datastore:
        - ``"running"`` -> ``/data``
        - ``"startup"`` -> ``/data`` (most vendors unify them)
        """
        path = "/data"
        resp = await self._make_request("GET", path)
        return resp.text

    async def send_command(self, command: str, timeout: int = 30) -> str:
        """Execute a RESTCONF GET for *command* treated as a path.

        Example: ``send_command("data/ietf-interfaces:interfaces")``
        """
        resp = await self._make_request("GET", command, timeout=timeout)
        return resp.text

    async def send_config(self, commands: list[str]) -> str:
        """Push config via RESTCONF PATCH.

        Each item in *commands* should be a JSON string representing the
        payload, or a ``"METHOD path json"`` triple (e.g.
        ``"PATCH /data/ietf-interfaces:interfaces {...}"``) for full control.
        """
        results: list[str] = []
        for cmd in commands:
            method, path, payload = self._parse_config_command(cmd)
            resp = await self._make_request(method, path, data=payload)
            results.append(f"HTTP {resp.status_code}")
        return "\n".join(results)

    @staticmethod
    def _parse_config_command(cmd: str) -> tuple[str, str, Any]:
        """Parse a config command string.

        Accepted formats:
        - ``"PATCH /some/path {json}"``
        - ``"PUT /some/path {json}"``
        - Plain JSON (defaults to PATCH /data)
        """
        cmd = cmd.strip()
        m = re.match(r"^(PATCH|PUT|POST|DELETE)\s+(\S+)\s*(.*)", cmd, re.DOTALL)
        if m:
            method = m.group(1)
            path = m.group(2)
            body = m.group(3).strip()
            try:
                payload = json.loads(body) if body else None
            except json.JSONDecodeError:
                payload = body
            return method, path, payload
        # Default: treat entire command as JSON payload with PATCH
        try:
            payload = json.loads(cmd)
        except json.JSONDecodeError:
            payload = cmd
        return "PATCH", "/data", payload

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        """Retrieve device facts via RESTCONF."""
        hostname = self.host
        vendor = ""
        model = ""
        os_version = ""
        serial = ""

        # Try IETF system model
        try:
            resp = await self._make_request(
                "GET", "data/ietf-system:system"
            )
            data = resp.json()
            sys_data = data.get("ietf-system:system", data)
            hostname = sys_data.get("hostname", hostname)
            vendor = sys_data.get("contact", "")
            location = sys_data.get("location", "")
        except Exception:
            pass

        # Try Cisco native model
        try:
            resp = await self._make_request(
                "GET", "data/Cisco-IOS-XE-native:native/version"
            )
            data = resp.json()
            os_version = str(
                data.get("Cisco-IOS-XE-native:version", "")
            )
            if os_version:
                vendor = "Cisco"
        except Exception:
            pass

        # Try device info endpoint
        try:
            resp = await self._make_request(
                "GET", "data/Cisco-IOS-XE-device-hardware-oper:device-hardware-data"
            )
            data = resp.json()
            hw = data.get("Cisco-IOS-XE-device-hardware-oper:device-hardware-data", {})
            hw_info = hw.get("device-hardware", {})
            if isinstance(hw_info, dict):
                model = hw_info.get("device-model", "")
                serial = hw_info.get("device-serial", "")
        except Exception:
            pass

        return DeviceFacts(
            hostname=hostname,
            vendor=vendor,
            model=model,
            serial_number=serial,
            os_version=os_version,
        )

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    async def get_interfaces(self) -> list[InterfaceInfo]:
        """Retrieve interfaces via IETF interfaces YANG model."""
        try:
            resp = await self._make_request(
                "GET", "data/ietf-interfaces:interfaces"
            )
        except Exception as exc:
            logger.warning("Failed to get interfaces via RESTCONF for %s: %s", self.host, exc)
            return []

        data = resp.json()
        iface_list = (
            data.get("ietf-interfaces:interfaces", {}).get("interface", [])
        )
        if not iface_list:
            # Try alternate key
            iface_list = data.get("interface", [])

        interfaces: list[InterfaceInfo] = []
        for iface in iface_list:
            name = iface.get("name", "")
            enabled = iface.get("enabled", True)
            oper_status = iface.get("oper-status", "unknown")
            description = iface.get("description", "")
            mtu = iface.get("mtu", 0)
            speed = str(iface.get("speed", ""))
            phys_addr = iface.get("phys-address", "")

            # IP address
            ip_address = ""
            subnet_mask = ""
            ipv4 = iface.get("ietf-ip:ipv4", {})
            addrs = ipv4.get("address", [])
            if addrs and isinstance(addrs, list):
                ip_address = addrs[0].get("ip", "")
                subnet_mask = str(addrs[0].get("netmask", addrs[0].get("prefix-length", "")))

            # Statistics
            stats = iface.get("statistics", {})
            in_octets = int(stats.get("in-octets", 0))
            out_octets = int(stats.get("out-octets", 0))
            in_errors = int(stats.get("in-errors", 0))
            out_errors = int(stats.get("out-errors", 0))
            in_discards = int(stats.get("in-discards", 0))
            out_discards = int(stats.get("out-discards", 0))

            if not enabled:
                status = "administratively down"
            elif oper_status in ("up", "if-oper-status-ready"):
                status = "up"
            else:
                status = "down"
            proto = "up" if oper_status in ("up", "if-oper-status-ready") else "down"

            interfaces.append(
                InterfaceInfo(
                    name=name,
                    status=status,
                    protocol_status=proto,
                    ip_address=ip_address,
                    subnet_mask=subnet_mask,
                    speed=speed,
                    mtu=int(mtu) if mtu else 0,
                    mac_address=phys_addr,
                    description=description,
                    in_octets=in_octets,
                    out_octets=out_octets,
                    in_errors=in_errors,
                    out_errors=out_errors,
                    in_discards=in_discards,
                    out_discards=out_discards,
                )
            )

        return interfaces

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def get_health(self) -> DeviceHealth:
        """Retrieve health metrics via vendor-specific RESTCONF endpoints."""
        cpu_pct = 0.0
        mem_used = 0
        mem_total = 0

        # Cisco IOS-XE CPU
        try:
            resp = await self._make_request(
                "GET",
                "data/Cisco-IOS-XE-process-cpu-oper:cpu-usage/cpu-utilization",
            )
            data = resp.json()
            util = data.get("Cisco-IOS-XE-process-cpu-oper:cpu-utilization", data)
            cpu_pct = float(util.get("five-seconds", 0))
        except Exception:
            pass

        # Cisco IOS-XE memory
        try:
            resp = await self._make_request(
                "GET",
                "data/Cisco-IOS-XE-memory-oper:memory-statistics/memory-statistic",
            )
            data = resp.json()
            stats = data.get(
                "Cisco-IOS-XE-memory-oper:memory-statistic",
                data.get("memory-statistic", []),
            )
            if isinstance(stats, list):
                for entry in stats:
                    if entry.get("name", "").lower() == "processor":
                        mem_used = int(entry.get("used-memory", 0))
                        free = int(entry.get("free-memory", 0))
                        mem_total = mem_used + free
                        break
        except Exception:
            pass

        mem_pct = (mem_used / mem_total * 100.0) if mem_total else 0.0

        return DeviceHealth(
            cpu_percent=round(cpu_pct, 2),
            memory_used_bytes=mem_used,
            memory_total_bytes=mem_total,
            memory_percent=round(mem_pct, 2),
        )
