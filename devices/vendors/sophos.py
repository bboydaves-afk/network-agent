"""Sophos UTM (SG series) device implementation.

Hybrid driver: REST API on port 4444 (primary) + SSH (fallback).
Tested against Sophos UTM 9.x firmware on SG 230 hardware.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

import httpx
from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
    ReadTimeout,
)

from core.exceptions import (
    DeviceAuthenticationError,
    DeviceCommandError,
    DeviceConnectionError,
    DeviceTimeoutError,
)
from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.base import BaseDevice
from devices.registry import register_device

logger = logging.getLogger(__name__)

# Shared thread pool for blocking SSH operations.
_SSH_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sophos-ssh")

# ---------------------------------------------------------------------------
# Regex patterns for Linux CLI output parsing
# ---------------------------------------------------------------------------

# ifconfig header
_RE_IFCONFIG_HEADER = re.compile(r"^(\S+):\s+flags=", re.MULTILINE)
_RE_IFCONFIG_STATUS = re.compile(r"status:\s+(\w+)", re.IGNORECASE)
_RE_IFCONFIG_INET = re.compile(
    r"inet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+(0x[0-9a-fA-F]+|[\d\.]+)",
    re.IGNORECASE,
)
_RE_IFCONFIG_ETHER = re.compile(r"ether\s+([0-9a-fA-F:]+)", re.IGNORECASE)
_RE_IFCONFIG_MTU = re.compile(r"mtu\s+(\d+)", re.IGNORECASE)
_RE_IFCONFIG_MEDIA = re.compile(r"media:\s+(.+)", re.IGNORECASE)
_RE_IFCONFIG_IN_LINE = re.compile(
    r"^\s+(?:RX|input).*?(\d+)\s+packets\s+(\d+)\s+bytes.*?(\d+)\s+errors",
    re.MULTILINE | re.IGNORECASE,
)
_RE_IFCONFIG_OUT_LINE = re.compile(
    r"^\s+(?:TX|output).*?(\d+)\s+packets\s+(\d+)\s+bytes.*?(\d+)\s+errors",
    re.MULTILINE | re.IGNORECASE,
)
_RE_IFCONFIG_RX_BYTES = re.compile(
    r"(?:RX|input).*?(\d+)\s+bytes", re.IGNORECASE | re.DOTALL
)
_RE_IFCONFIG_TX_BYTES = re.compile(
    r"(?:TX|output).*?(\d+)\s+bytes", re.IGNORECASE | re.DOTALL
)

# CPU from top
_RE_CPU = re.compile(
    r"Cpu.*?:\s*([\d\.]+)\s*%?\s*us.*?([\d\.]+)\s*%?\s*sy.*?([\d\.]+)\s*%?\s*id",
    re.IGNORECASE,
)
_RE_CPU_ALT = re.compile(
    r"CPU:\s+([\d\.]+)%\s+user,\s+([\d\.]+)%\s+nice,\s+([\d\.]+)%\s+system.*?([\d\.]+)%\s+idle",
    re.IGNORECASE,
)

# Memory from free
_RE_MEM = re.compile(r"Mem:\s+(\d+)\s+(\d+)\s+(\d+)", re.IGNORECASE)

# Disk from df
_RE_DF = re.compile(
    r"^(/dev/\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)%\s+(/\S*)",
    re.MULTILINE,
)

# Ping result
_RE_PING_RESULT = re.compile(
    r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received",
    re.IGNORECASE,
)
_RE_PING_RTT = re.compile(
    r"min/avg/max/(?:std-dev|mdev)\s+=\s+([\d\.]+)/([\d\.]+)/([\d\.]+)",
    re.IGNORECASE,
)

# ip -o addr show
_RE_IP_ADDR = re.compile(
    r"(\d+):\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)"
)


def _hex_netmask_to_dotted(hex_mask: str) -> str:
    """Convert ``0xffffff00`` to ``255.255.255.0``."""
    if hex_mask.startswith("0x"):
        val = int(hex_mask, 16)
        return ".".join(str((val >> (8 * i)) & 0xFF) for i in range(3, -1, -1))
    return hex_mask


def _parse_linux_uptime(uptime_str: str) -> int:
    """Parse Linux uptime string to seconds."""
    total = 0
    for m in re.finditer(
        r"(\d+)\s+(day|hour|min|minute|sec|second)s?",
        uptime_str,
        re.IGNORECASE,
    ):
        val = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "day":
            total += val * 86400
        elif unit == "hour":
            total += val * 3600
        elif unit in ("min", "minute"):
            total += val * 60
        elif unit in ("sec", "second"):
            total += val
    # Handle "HH:MM" format
    if total == 0:
        hm = re.search(r"(\d+):(\d+)", uptime_str)
        if hm:
            total = int(hm.group(1)) * 3600 + int(hm.group(2)) * 60
    return total


@register_device("sophos")
class SophosDevice(BaseDevice):
    """Sophos UTM device driver (SG series, UTM 9.x firmware).

    Uses the Sophos UTM REST API (port 4444) as the primary interface,
    with SSH (Netmiko device_type='linux') as fallback.

    Parameters
    ----------
    host : str
        Device IP or hostname.
    username : str
        WebAdmin / root username (used for both API and SSH).
    password : str
        WebAdmin / root password (used for both API and SSH).
    port : int
        SSH port (default 22). The API port is always 4444.
    api_port : int
        REST API port (default 4444).
    api_token : str
        Optional API token. If provided, used instead of username/password
        for API auth.
    verify_ssl : bool
        Verify TLS certificate for API calls (default False).
    ssh_enabled : bool
        Whether to establish SSH as fallback (default True).
    timeout : int
        Connection/command timeout in seconds (default 30).
    """

    def __init__(
        self,
        host: str,
        username: str = "",
        password: str = "",
        port: int = 22,
        api_port: int = 4444,
        api_token: str = "",
        verify_ssl: bool = False,
        ssh_enabled: bool = True,
        timeout: int = 30,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            host=host,
            username=username,
            password=password,
            port=port,
            timeout=timeout,
            **kwargs,
        )
        self.api_port = api_port
        self.api_token = api_token or self._extra.get("api_token", "")
        self.verify_ssl = verify_ssl
        self.ssh_enabled = ssh_enabled

        self._http_client: httpx.AsyncClient | None = None
        self._net_connect: Any = None
        self._api_available: bool = False
        self._ssh_available: bool = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _api_base_url(self) -> str:
        return f"https://{self.host}:{self.api_port}/api"

    def _api_auth(self) -> tuple[str, str] | None:
        if self.api_token:
            return (self.api_token, "")
        elif self.username:
            return (self.username, self.password)
        return None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    async def _run_sync(self, func, *args, **kwargs):
        """Run a blocking callable in the thread-pool executor."""
        loop = self._get_loop()
        return await loop.run_in_executor(
            _SSH_EXECUTOR, partial(func, *args, **kwargs)
        )

    # ------------------------------------------------------------------
    # API request
    # ------------------------------------------------------------------

    async def _api_request(
        self,
        method: str,
        path: str,
        *,
        data: Any = None,
        timeout: int | None = None,
    ) -> httpx.Response:
        """Execute an authenticated request against the UTM REST API.

        *path* is relative to ``/api/`` (e.g. ``"status/"``).
        """
        if self._http_client is None:
            raise DeviceConnectionError(
                device_id=self.host,
                message="API client not initialised -- call connect() first",
            )

        path = path.lstrip("/")
        url = f"{self._api_base_url}/{path}"
        req_timeout = timeout or self.timeout

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        try:
            response = await self._http_client.request(
                method=method.upper(),
                url=url,
                content=json.dumps(data) if data and not isinstance(data, str) else data,
                headers=headers,
                timeout=req_timeout,
            )
        except httpx.TimeoutException as exc:
            raise DeviceTimeoutError(
                device_id=self.host,
                message=f"API {method.upper()} {path} timed out",
                timeout_seconds=req_timeout,
            ) from exc
        except httpx.ConnectError as exc:
            raise DeviceConnectionError(
                device_id=self.host,
                message=f"API connection error: {exc}",
            ) from exc

        if response.status_code in (401, 403):
            raise DeviceAuthenticationError(
                device_id=self.host,
                message=f"API auth failure (HTTP {response.status_code})",
            )

        if response.status_code >= 400:
            body = response.text[:500]
            raise DeviceCommandError(
                device_id=self.host,
                message=f"API {method.upper()} {path} returned HTTP {response.status_code}: {body}",
                command=f"{method.upper()} {path}",
            )

        return response

    # ------------------------------------------------------------------
    # SSH command
    # ------------------------------------------------------------------

    async def _ssh_command(self, command: str, timeout: int = 30) -> str:
        """Send a command via SSH and return output."""
        if not self._ssh_available or not self._net_connect:
            raise DeviceCommandError(
                device_id=self.host,
                message="SSH not available for this device",
            )
        try:
            output: str = await self._run_sync(
                self._net_connect.send_command,
                command,
                read_timeout=timeout,
            )
            return output
        except ReadTimeout as exc:
            raise DeviceTimeoutError(
                device_id=self.host,
                message=f"SSH command timed out: {command!r}",
                timeout_seconds=timeout,
            ) from exc
        except Exception as exc:
            raise DeviceCommandError(
                device_id=self.host,
                message=f"SSH command failed: {exc}",
                command=command,
            ) from exc

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open API and/or SSH sessions. At least one must succeed."""
        if self._connected:
            return

        errors: list[str] = []

        # 1. Try REST API
        try:
            self._http_client = httpx.AsyncClient(
                auth=self._api_auth(),
                verify=self.verify_ssl,
                timeout=self.timeout,
            )
            await self._api_request("GET", "status/")
            self._api_available = True
            logger.info("Sophos UTM API connected to %s:%d", self.host, self.api_port)
        except Exception as exc:
            errors.append(f"API: {exc}")
            self._api_available = False
            if self._http_client:
                try:
                    await self._http_client.aclose()
                except Exception:
                    pass
                self._http_client = None

        # 2. Try SSH (if enabled)
        if self.ssh_enabled:
            try:
                params = {
                    "device_type": "linux",
                    "host": self.host,
                    "port": self.port,
                    "username": self.username,
                    "password": self.password,
                    "timeout": self.timeout,
                    "conn_timeout": self.timeout,
                    "banner_timeout": self.timeout,
                    "auth_timeout": self.timeout,
                }
                if self.ssh_key_path:
                    params["use_keys"] = True
                    params["key_file"] = self.ssh_key_path
                self._net_connect = await self._run_sync(ConnectHandler, **params)
                self._ssh_available = True
                logger.info("Sophos UTM SSH connected to %s:%d", self.host, self.port)
            except NetmikoAuthenticationException as exc:
                errors.append(f"SSH auth: {exc}")
                self._ssh_available = False
            except NetmikoTimeoutException as exc:
                errors.append(f"SSH timeout: {exc}")
                self._ssh_available = False
            except Exception as exc:
                errors.append(f"SSH: {exc}")
                self._ssh_available = False

        # Must have at least one working channel
        if not self._api_available and not self._ssh_available:
            raise DeviceConnectionError(
                device_id=self.host,
                message=f"Failed to connect via API and SSH: {'; '.join(errors)}",
            )

        self._connected = True

    async def disconnect(self) -> None:
        """Close API and SSH sessions."""
        if self._http_client:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None
            self._api_available = False

        if self._net_connect:
            try:
                await self._run_sync(self._net_connect.disconnect)
            except Exception:
                pass
            self._net_connect = None
            self._ssh_available = False

        self._connected = False
        logger.info("Sophos UTM disconnected from %s", self.host)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        """Retrieve configuration via REST API (JSON config tree)."""
        if self._api_available:
            try:
                resp = await self._api_request("GET", "nodes/", timeout=60)
                return resp.text
            except Exception:
                pass

        # SSH fallback
        if self._ssh_available:
            return await self._ssh_command("cc get_objects_list", timeout=60)

        raise DeviceCommandError(
            device_id=self.host,
            message="No channel available to retrieve config",
        )

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def send_command(self, command: str, timeout: int = 30) -> str:
        """Execute a command. API-style commands are routed to the API."""
        command = command.strip()
        m = re.match(r"^(GET|POST|PUT|PATCH|DELETE)\s+(/api/\S+)", command)
        if m and self._api_available:
            path = m.group(2).replace("/api/", "", 1)
            resp = await self._api_request(m.group(1), path, timeout=timeout)
            return resp.text

        if self._ssh_available:
            return await self._ssh_command(command, timeout=timeout)

        raise DeviceCommandError(
            device_id=self.host,
            message="No channel available to execute command",
            command=command,
        )

    async def send_config(self, commands: list[str]) -> str:
        """Push configuration commands.

        Accepts:
        - ``"POST /api/path {json}"`` -- REST API call
        - ``"PUT /api/path {json}"``  -- REST API call
        - ``"DELETE /api/path"``      -- REST API call
        - Any other string            -- SSH shell command
        """
        results: list[str] = []
        for cmd in commands:
            cmd = cmd.strip()
            m = re.match(
                r"^(GET|POST|PUT|PATCH|DELETE)\s+(/api/\S+)\s*(.*)",
                cmd,
                re.DOTALL,
            )
            if m and self._api_available:
                method = m.group(1)
                path = m.group(2).replace("/api/", "", 1)
                body = m.group(3).strip()
                payload = None
                if body:
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError:
                        payload = body
                resp = await self._api_request(method, path, data=payload)
                results.append(f"HTTP {resp.status_code}: {resp.text[:200]}")
            elif self._ssh_available:
                output = await self._ssh_command(cmd, timeout=30)
                results.append(output)
            else:
                results.append(f"ERROR: No channel available for: {cmd}")
        return "\n".join(results)

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        hostname = self.host
        vendor = "Sophos"
        model = ""
        serial = ""
        os_version = ""
        uptime_str = ""
        uptime_seconds = 0

        # Try API
        if self._api_available:
            try:
                resp = await self._api_request("GET", "status/")
                data = resp.json()
                result = data.get("result", data) if isinstance(data, dict) else {}
                hostname = result.get("hostname", hostname)
                os_version = result.get("version", "")
                serial = result.get("serial", "")
                model = result.get("model", "")
            except Exception:
                pass

        # SSH supplement
        if self._ssh_available:
            if not os_version:
                try:
                    ver_output = await self._ssh_command(
                        "cat /etc/version 2>/dev/null || echo unknown"
                    )
                    ver = ver_output.strip().splitlines()[0] if ver_output.strip() else ""
                    if ver and ver != "unknown":
                        os_version = ver
                except Exception:
                    pass

            if not model:
                try:
                    product_output = await self._ssh_command(
                        "awk -F= '/PRODUCT/ {print $2}' /etc/corporate_info 2>/dev/null || true"
                    )
                    if product_output.strip():
                        model = product_output.strip()
                except Exception:
                    pass

            try:
                uptime_raw = await self._ssh_command("cat /proc/uptime")
                parts = uptime_raw.strip().split()
                if parts:
                    uptime_seconds = int(float(parts[0]))
                    days = uptime_seconds // 86400
                    hours = (uptime_seconds % 86400) // 3600
                    minutes = (uptime_seconds % 3600) // 60
                    uptime_str = f"{days} days, {hours} hours, {minutes} minutes"
            except Exception:
                pass

            # Fallback uptime from uptime command
            if not uptime_str:
                try:
                    uptime_output = await self._ssh_command("uptime")
                    up_m = re.search(r"up\s+(.+?),\s+\d+\s+user", uptime_output)
                    if up_m:
                        uptime_str = up_m.group(1).strip()
                        if not uptime_seconds:
                            uptime_seconds = _parse_linux_uptime(uptime_str)
                except Exception:
                    pass

        return DeviceFacts(
            hostname=hostname,
            vendor=vendor,
            model=model,
            serial_number=serial,
            os_version=os_version,
            uptime=uptime_str,
            uptime_seconds=uptime_seconds,
        )

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    async def get_interfaces(self) -> list[InterfaceInfo]:
        interfaces: list[InterfaceInfo] = []

        # SSH: ip -o addr show (fast, structured)
        if self._ssh_available:
            try:
                output = await self._ssh_command("ip -o addr show", timeout=15)
                seen: set[str] = set()
                for line in output.splitlines():
                    m = _RE_IP_ADDR.match(line)
                    if m:
                        name = m.group(2)
                        if name in seen:
                            continue
                        seen.add(name)
                        interfaces.append(
                            InterfaceInfo(
                                name=name,
                                status="up",
                                protocol_status="up",
                                ip_address=m.group(3),
                            )
                        )
            except Exception:
                pass

            # Supplement with ifconfig for MAC/MTU/counters
            if not interfaces:
                try:
                    ifconfig_output = await self._ssh_command("ifconfig -a", timeout=15)
                    interfaces = self._parse_ifconfig(ifconfig_output)
                except Exception:
                    pass

        # API fallback
        if not interfaces and self._api_available:
            try:
                resp = await self._api_request(
                    "GET", "objects/network/interface_network/"
                )
                data = resp.json()
                items = data if isinstance(data, list) else data.get("objects", [])
                for iface in items:
                    interfaces.append(
                        InterfaceInfo(
                            name=iface.get("name", ""),
                            status="up" if iface.get("status") else "down",
                            ip_address=iface.get("address", ""),
                        )
                    )
            except Exception:
                pass

        return interfaces

    # ------------------------------------------------------------------
    # Health (SSH only)
    # ------------------------------------------------------------------

    async def get_health(self) -> DeviceHealth:
        cpu_percent = 0.0
        mem_total = 0
        mem_used = 0
        disk_total = 0
        disk_used = 0

        if not self._ssh_available:
            return DeviceHealth(cpu_percent=0.0, memory_percent=0.0)

        # CPU from top
        try:
            top_output = await self._ssh_command("top -bn1 | head -5", timeout=15)
            cpu_m = _RE_CPU.search(top_output)
            if cpu_m:
                idle = float(cpu_m.group(3))
                cpu_percent = 100.0 - idle
            else:
                cpu_alt_m = _RE_CPU_ALT.search(top_output)
                if cpu_alt_m:
                    idle = float(cpu_alt_m.group(4))
                    cpu_percent = 100.0 - idle
        except Exception:
            pass

        # Memory from free -b
        try:
            free_output = await self._ssh_command("free -b")
            mem_m = _RE_MEM.search(free_output)
            if mem_m:
                mem_total = int(mem_m.group(1))
                mem_used = int(mem_m.group(2))
        except Exception:
            pass

        mem_pct = (mem_used / mem_total * 100.0) if mem_total else 0.0

        # Disk from df
        try:
            df_output = await self._ssh_command("df -k /")
            for m in _RE_DF.finditer(df_output):
                disk_total = int(m.group(2)) * 1024
                disk_used = int(m.group(3)) * 1024
                break
            if not disk_total:
                for line in df_output.splitlines():
                    parts = line.split()
                    if len(parts) >= 4 and parts[0].startswith("/"):
                        disk_total = int(parts[1]) * 1024
                        disk_used = int(parts[2]) * 1024
                        break
        except Exception:
            pass

        disk_pct = (disk_used / disk_total * 100.0) if disk_total else 0.0

        return DeviceHealth(
            cpu_percent=round(cpu_percent, 2),
            memory_used_bytes=mem_used,
            memory_total_bytes=mem_total,
            memory_percent=round(mem_pct, 2),
            disk_used_bytes=disk_used,
            disk_total_bytes=disk_total,
            disk_percent=round(disk_pct, 2),
        )

    # ------------------------------------------------------------------
    # Firewall-specific (REST API)
    # ------------------------------------------------------------------

    async def get_policies(self) -> list[dict[str, Any]]:
        """Retrieve firewall packet filter rules via REST API."""
        if self._api_available:
            try:
                resp = await self._api_request(
                    "GET", "objects/packetfilter/packetfilter/", timeout=30
                )
                data = resp.json()
                items = data if isinstance(data, list) else data.get("objects", [])
                rules: list[dict[str, Any]] = []
                for item in items:
                    rules.append({
                        "_ref": item.get("_ref", ""),
                        "name": item.get("name", ""),
                        "action": item.get("action", ""),
                        "status": item.get("status", False),
                        "sources": item.get("sources", []),
                        "destinations": item.get("destinations", []),
                        "services": item.get("services", []),
                        "group": item.get("group", ""),
                        "comment": item.get("comment", ""),
                        "log": item.get("log", False),
                        "position": item.get("position", 0),
                    })
                return rules
            except Exception:
                logger.warning("Failed to get policies via API for %s", self.host)

        # SSH fallback via iptables
        if self._ssh_available:
            try:
                output = await self._ssh_command(
                    "iptables -L -n --line-numbers 2>/dev/null || true", timeout=15
                )
                return [{"raw": output}] if output.strip() else []
            except Exception:
                pass

        return []

    async def get_nat_rules(self) -> list[dict[str, Any]]:
        """Retrieve NAT rules via REST API."""
        if not self._api_available:
            return []
        try:
            resp = await self._api_request(
                "GET", "objects/packetfilter/nat/", timeout=30
            )
            data = resp.json()
            return data if isinstance(data, list) else data.get("objects", [])
        except Exception:
            return []

    async def get_zones(self) -> list[dict[str, Any]]:
        """Retrieve interface networks (Sophos equivalent of zones)."""
        if not self._api_available:
            return []
        try:
            resp = await self._api_request(
                "GET", "objects/network/interface_network/", timeout=30
            )
            data = resp.json()
            return data if isinstance(data, list) else data.get("objects", [])
        except Exception:
            return []

    async def get_ha_status(self) -> dict[str, Any]:
        """Retrieve HA cluster status (Sophos-specific)."""
        if not self._api_available:
            return {}
        try:
            resp = await self._api_request("GET", "status/ha")
            return resp.json()
        except Exception:
            return {}

    async def get_license_info(self) -> dict[str, Any]:
        """Retrieve licensing status (Sophos-specific)."""
        if not self._api_available:
            return {}
        try:
            resp = await self._api_request("GET", "nodes/licensing.status")
            return resp.json()
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Ping / Traceroute (SSH)
    # ------------------------------------------------------------------

    async def ping(self, target: str, count: int = 4) -> dict[str, Any]:
        output = await self._ssh_command(
            f"ping -c {count} {target}", timeout=30 + count * 2
        )
        result: dict[str, Any] = {"raw_output": output, "success": False}

        m = _RE_PING_RESULT.search(output)
        if m:
            sent = int(m.group(1))
            recv = int(m.group(2))
            result["sent"] = sent
            result["received"] = recv
            result["success_rate"] = int((recv / sent * 100) if sent else 0)
            result["success"] = recv > 0

        rtt_m = _RE_PING_RTT.search(output)
        if rtt_m:
            result["rtt_min"] = float(rtt_m.group(1))
            result["rtt_avg"] = float(rtt_m.group(2))
            result["rtt_max"] = float(rtt_m.group(3))

        return result

    async def traceroute(self, target: str) -> dict[str, Any]:
        output = await self._ssh_command(f"traceroute {target}", timeout=120)
        hops: list[dict[str, Any]] = []
        for line in output.splitlines():
            m = re.match(r"^\s*(\d+)\s+([\d\.\*]+)", line)
            if m:
                hop_num = int(m.group(1))
                addr = m.group(2)
                hops.append({
                    "hop": hop_num,
                    "address": addr if addr != "*" else None,
                })
        return {"raw_output": output, "hops": hops}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_ifconfig(output: str) -> list[InterfaceInfo]:
        """Parse ``ifconfig -a`` output into InterfaceInfo list."""
        blocks = SophosDevice._split_ifconfig_blocks(output)
        interfaces: list[InterfaceInfo] = []

        for name, block in blocks.items():
            # Status
            status_m = _RE_IFCONFIG_STATUS.search(block)
            if status_m:
                raw_status = status_m.group(1).lower()
                status = "up" if raw_status == "active" else "down"
                proto = status
            elif "<UP," in block or ",UP>" in block or ",UP," in block:
                status = "up"
                proto = "up"
            else:
                status = "down"
                proto = "down"

            # IP
            ip_m = _RE_IFCONFIG_INET.search(block)
            ip_address = ip_m.group(1) if ip_m else ""
            subnet_mask = ""
            if ip_m:
                raw_mask = ip_m.group(2)
                subnet_mask = (
                    _hex_netmask_to_dotted(raw_mask)
                    if raw_mask.startswith("0x")
                    else raw_mask
                )

            # MAC
            mac_m = _RE_IFCONFIG_ETHER.search(block)
            mac = mac_m.group(1).lower() if mac_m else ""

            # MTU
            mtu_m = _RE_IFCONFIG_MTU.search(block)
            mtu = int(mtu_m.group(1)) if mtu_m else 0

            # Speed
            media_m = _RE_IFCONFIG_MEDIA.search(block)
            speed = media_m.group(1).strip() if media_m else ""

            # Counters
            in_octets = 0
            out_octets = 0
            in_errors = 0
            out_errors = 0

            in_line_m = _RE_IFCONFIG_IN_LINE.search(block)
            if in_line_m:
                in_octets = int(in_line_m.group(2))
                in_errors = int(in_line_m.group(3))

            out_line_m = _RE_IFCONFIG_OUT_LINE.search(block)
            if out_line_m:
                out_octets = int(out_line_m.group(2))
                out_errors = int(out_line_m.group(3))

            if not in_octets:
                rx_m = _RE_IFCONFIG_RX_BYTES.search(block)
                if rx_m:
                    in_octets = int(rx_m.group(1))
            if not out_octets:
                tx_m = _RE_IFCONFIG_TX_BYTES.search(block)
                if tx_m:
                    out_octets = int(tx_m.group(1))

            interfaces.append(
                InterfaceInfo(
                    name=name,
                    status=status,
                    protocol_status=proto,
                    ip_address=ip_address,
                    subnet_mask=subnet_mask,
                    speed=speed,
                    mtu=mtu,
                    mac_address=mac,
                    in_octets=in_octets,
                    out_octets=out_octets,
                    in_errors=in_errors,
                    out_errors=out_errors,
                )
            )

        return interfaces

    @staticmethod
    def _split_ifconfig_blocks(output: str) -> dict[str, str]:
        """Split ``ifconfig -a`` output into per-interface blocks."""
        blocks: dict[str, str] = {}
        current_name: str | None = None
        current_lines: list[str] = []

        for line in output.splitlines():
            m = _RE_IFCONFIG_HEADER.match(line)
            if m:
                if current_name is not None:
                    blocks[current_name] = "\n".join(current_lines)
                current_name = m.group(1)
                current_lines = [line]
            elif current_name is not None:
                current_lines.append(line)

        if current_name is not None:
            blocks[current_name] = "\n".join(current_lines)
        return blocks
