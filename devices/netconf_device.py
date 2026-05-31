"""NETCONF-based device driver using ncclient."""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Optional

from ncclient import manager
from ncclient.transport.errors import AuthenticationError, SSHError
from ncclient.operations.errors import TimeoutExpiredError
from ncclient.operations.rpc import RPCError

from core.exceptions import (
    DeviceAuthenticationError,
    DeviceCommandError,
    DeviceConnectionError,
    DeviceTimeoutError,
)
from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.base import BaseDevice

logger = logging.getLogger(__name__)

_NC_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="netconf")

# ---------------------------------------------------------------------------
# XML Namespace helpers
# ---------------------------------------------------------------------------
NS_IETF_INTERFACES = "urn:ietf:params:xml:ns:yang:ietf-interfaces"
NS_IETF_IP = "urn:ietf:params:xml:ns:yang:ietf-ip"
NS_IETF_SYSTEM = "urn:ietf:params:xml:ns:yang:ietf-system"
NS_NETCONF_BASE = "urn:ietf:params:xml:ns:netconf:base:1.0"

# Cisco IOS-XE / IOS-XR specific
NS_CISCO_IOS_XE_NATIVE = "http://cisco.com/ns/yang/Cisco-IOS-XE-native"
NS_CISCO_IOS_XE_INTF = "http://cisco.com/ns/yang/Cisco-IOS-XE-interfaces-oper"
NS_CISCO_PROCESS_CPU = "http://cisco.com/ns/yang/Cisco-IOS-XE-process-cpu-oper"
NS_CISCO_PLATFORM = "http://cisco.com/ns/yang/Cisco-IOS-XE-platform-software-oper"
NS_CISCO_MEMORY = "http://cisco.com/ns/yang/Cisco-IOS-XE-memory-oper"

# Juniper
NS_JUNOS_CONF = "http://xml.juniper.net/xnm/1.1/xnm"
NS_JUNOS_RPC = "http://xml.juniper.net/junos/*/junos-rpc"


def _strip_ns(tag: str) -> str:
    """Remove XML namespace prefix from a tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_text(element: ET.Element, path: str, default: str = "") -> str:
    """Find text in an element, ignoring namespace prefixes."""
    # Try direct path first
    node = element.find(path)
    if node is not None and node.text:
        return node.text.strip()
    # Search children by local name
    parts = path.strip("./").split("/")
    current = element
    for part in parts:
        found = False
        for child in current:
            if _strip_ns(child.tag) == part:
                current = child
                found = True
                break
        if not found:
            return default
    return (current.text or default).strip() if current is not None else default


class NETCONFDevice(BaseDevice):
    """Device driver for NETCONF (RFC 6241) using ncclient.

    Parameters
    ----------
    host : str
        Target device IP/hostname.
    port : int
        NETCONF port (default 830).
    hostkey_verify : bool
        Whether to verify the SSH host key (default False for lab use).
    device_handler : str
        ncclient device handler name (``"default"``, ``"iosxe"``,
        ``"junos"``, ``"iosxr"``, etc.).
    """

    def __init__(
        self,
        host: str,
        port: int = 830,
        username: str = "",
        password: str = "",
        ssh_key_path: str = "",
        timeout: int = 30,
        hostkey_verify: bool = False,
        device_handler: str = "default",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            host=host,
            port=port,
            username=username,
            password=password,
            ssh_key_path=ssh_key_path,
            timeout=timeout,
            **kwargs,
        )
        self.hostkey_verify = hostkey_verify
        self.device_handler = device_handler
        self._manager: Any = None  # ncclient manager instance

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    async def _run_sync(self, func, *args, **kwargs):
        loop = self._get_loop()
        return await loop.run_in_executor(
            _NC_EXECUTOR, partial(func, *args, **kwargs)
        )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open a NETCONF session."""
        if self._connected and self._manager:
            return
        try:
            connect_kwargs: dict[str, Any] = {
                "host": self.host,
                "port": self.port,
                "username": self.username,
                "password": self.password,
                "hostkey_verify": self.hostkey_verify,
                "device_params": {"name": self.device_handler},
                "timeout": self.timeout,
                "allow_agent": False,
                "look_for_keys": bool(self.ssh_key_path),
            }
            if self.ssh_key_path:
                connect_kwargs["key_filename"] = self.ssh_key_path

            self._manager = await self._run_sync(manager.connect, **connect_kwargs)
            self._connected = True
            logger.info(
                "NETCONF connected to %s (session-id=%s)",
                self.host,
                self._manager.session_id,
            )
        except AuthenticationError as exc:
            raise DeviceAuthenticationError(
                device_id=self.host,
                message=f"NETCONF authentication failed: {exc}",
            ) from exc
        except SSHError as exc:
            raise DeviceConnectionError(
                device_id=self.host,
                message=f"NETCONF SSH error: {exc}",
            ) from exc
        except Exception as exc:
            raise DeviceConnectionError(
                device_id=self.host,
                message=f"NETCONF connection failed: {exc}",
            ) from exc

    async def disconnect(self) -> None:
        """Close the NETCONF session."""
        if self._manager:
            try:
                await self._run_sync(self._manager.close_session)
            except Exception:
                logger.debug("Ignoring error closing NETCONF session for %s", self.host)
            finally:
                self._manager = None
                self._connected = False
                logger.info("NETCONF disconnected from %s", self.host)

    # ------------------------------------------------------------------
    # Configuration operations
    # ------------------------------------------------------------------

    async def get_config(self, config_type: str = "running") -> str:
        """Retrieve config from the specified datastore.

        Parameters
        ----------
        config_type:
            ``"running"``, ``"startup"``, or ``"candidate"``.
        """
        self._require_connection()
        try:
            reply = await self._run_sync(
                self._manager.get_config, source=config_type
            )
            return reply.xml
        except TimeoutExpiredError as exc:
            raise DeviceTimeoutError(
                device_id=self.host,
                message=f"NETCONF get_config timed out for source={config_type}",
                timeout_seconds=self.timeout,
            ) from exc
        except RPCError as exc:
            raise DeviceCommandError(
                device_id=self.host,
                message=f"NETCONF RPC error: {exc}",
                command=f"get-config source={config_type}",
            ) from exc

    async def send_command(self, command: str, timeout: int = 30) -> str:
        """Execute an RPC ``<get>`` with an optional filter.

        If *command* looks like XML it is used as a subtree filter;
        otherwise it is wrapped in an XPath filter.
        """
        self._require_connection()
        try:
            if command.strip().startswith("<"):
                reply = await self._run_sync(
                    self._manager.get,
                    ("subtree", command),
                )
            else:
                reply = await self._run_sync(self._manager.get)
            return reply.xml
        except TimeoutExpiredError as exc:
            raise DeviceTimeoutError(
                device_id=self.host,
                message=f"NETCONF get timed out",
                timeout_seconds=timeout,
            ) from exc
        except RPCError as exc:
            raise DeviceCommandError(
                device_id=self.host,
                message=f"NETCONF RPC error: {exc}",
                command=command[:200],
            ) from exc

    async def send_config(self, commands: list[str]) -> str:
        """Push configuration XML to the device.

        Each item in *commands* should be valid XML for ``edit-config``.
        If a candidate datastore is supported, the driver uses
        edit-config + commit; otherwise it targets ``running`` directly.
        """
        self._require_connection()
        results: list[str] = []
        target = "candidate" if ":candidate" in (self._manager.server_capabilities or []) else "running"

        # Check capabilities properly
        has_candidate = any(
            "candidate" in cap for cap in (self._manager.server_capabilities or [])
        )
        target = "candidate" if has_candidate else "running"

        try:
            for config_xml in commands:
                reply = await self._run_sync(
                    self._manager.edit_config,
                    target=target,
                    config=config_xml,
                )
                results.append(reply.xml)

            if has_candidate:
                commit_reply = await self._run_sync(self._manager.commit)
                results.append(commit_reply.xml)

            return "\n".join(results)
        except TimeoutExpiredError as exc:
            # Attempt discard if candidate was used
            if has_candidate:
                try:
                    await self._run_sync(self._manager.discard_changes)
                except Exception:
                    pass
            raise DeviceTimeoutError(
                device_id=self.host,
                message="NETCONF edit_config timed out",
                timeout_seconds=self.timeout,
            ) from exc
        except RPCError as exc:
            if has_candidate:
                try:
                    await self._run_sync(self._manager.discard_changes)
                except Exception:
                    pass
            raise DeviceCommandError(
                device_id=self.host,
                message=f"NETCONF edit_config RPC error: {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        """Parse system information from NETCONF.

        Tries IETF system model first, then falls back to vendor-specific.
        """
        self._require_connection()
        # IETF system filter
        sys_filter = f"""
        <system xmlns="{NS_IETF_SYSTEM}">
            <hostname/>
            <contact/>
            <location/>
        </system>
        """
        try:
            reply = await self._run_sync(
                self._manager.get, ("subtree", sys_filter)
            )
            root = ET.fromstring(reply.xml)
            hostname = _find_text(root, ".//hostname") or self.host
        except Exception:
            hostname = self.host

        # Try a broader get for version info (vendor-specific)
        try:
            full_reply = await self._run_sync(self._manager.get_config, source="running")
            full_xml = full_reply.xml
        except Exception:
            full_xml = ""

        # Extract from capabilities
        os_version = ""
        vendor = ""
        model = ""
        for cap in self._manager.server_capabilities or []:
            cap_str = str(cap)
            if "cisco" in cap_str.lower():
                vendor = "Cisco"
            elif "juniper" in cap_str.lower():
                vendor = "Juniper"
            # Some capabilities embed version info
            ver_m = re.search(r"revision=(\d{4}-\d{2}-\d{2})", cap_str)
            if ver_m and not os_version:
                os_version = ver_m.group(1)

        return DeviceFacts(
            hostname=hostname,
            vendor=vendor,
            model=model,
            os_version=os_version,
        )

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    async def get_interfaces(self) -> list[InterfaceInfo]:
        """Retrieve interface information using IETF interfaces YANG model."""
        self._require_connection()
        intf_filter = f"""
        <interfaces xmlns="{NS_IETF_INTERFACES}"/>
        """
        try:
            reply = await self._run_sync(
                self._manager.get, ("subtree", intf_filter)
            )
        except Exception as exc:
            logger.warning("Failed to get interfaces via NETCONF for %s: %s", self.host, exc)
            return []

        root = ET.fromstring(reply.xml)
        interfaces: list[InterfaceInfo] = []

        # Walk all <interface> elements regardless of namespace
        for iface_elem in root.iter():
            if _strip_ns(iface_elem.tag) != "interface":
                continue
            # Only process direct children of <interfaces>
            name = _find_text(iface_elem, "name")
            if not name:
                continue

            enabled = _find_text(iface_elem, "enabled", "true")
            oper_status = _find_text(iface_elem, "oper-status", "unknown")
            admin_status = _find_text(iface_elem, "admin-status", "unknown")
            description = _find_text(iface_elem, "description")
            mtu_str = _find_text(iface_elem, "mtu", "0")
            speed_str = _find_text(iface_elem, "speed", "0")
            phys_addr = _find_text(iface_elem, "phys-address")

            # IP address (ipv4 sub-element)
            ip_address = ""
            prefix_len = ""
            for child in iface_elem.iter():
                tag = _strip_ns(child.tag)
                if tag == "ip":
                    ip_address = _find_text(child, "ip") or _find_text(child, "address")
                elif tag == "address" and not ip_address:
                    ip_address = _find_text(child, "ip")
                    prefix_len = _find_text(child, "prefix-length")

            # Statistics
            in_octets = 0
            out_octets = 0
            in_errors = 0
            out_errors = 0
            in_discards = 0
            out_discards = 0
            for child in iface_elem.iter():
                tag = _strip_ns(child.tag)
                if tag == "statistics":
                    in_octets = int(_find_text(child, "in-octets", "0"))
                    out_octets = int(_find_text(child, "out-octets", "0"))
                    in_errors = int(_find_text(child, "in-errors", "0"))
                    out_errors = int(_find_text(child, "out-errors", "0"))
                    in_discards = int(_find_text(child, "in-discards", "0"))
                    out_discards = int(_find_text(child, "out-discards", "0"))

            # Map status
            if enabled.lower() == "false" or admin_status == "down":
                status = "administratively down"
            elif oper_status in ("up", "1"):
                status = "up"
            else:
                status = "down"

            proto = "up" if oper_status in ("up", "1") else "down"

            try:
                mtu_val = int(mtu_str)
            except ValueError:
                mtu_val = 0

            interfaces.append(
                InterfaceInfo(
                    name=name,
                    status=status,
                    protocol_status=proto,
                    ip_address=ip_address,
                    subnet_mask=prefix_len,
                    speed=speed_str,
                    mtu=mtu_val,
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
        """Attempt to retrieve health via NETCONF (best-effort, vendor-specific)."""
        # Many vendors do not expose CPU/memory via standard YANG models.
        # Attempt Cisco IOS-XE specific model.
        cpu_pct = 0.0
        mem_used = 0
        mem_total = 0

        try:
            cpu_filter = f"""
            <cpu-usage xmlns="{NS_CISCO_PROCESS_CPU}"/>
            """
            reply = await self._run_sync(
                self._manager.get, ("subtree", cpu_filter)
            )
            root = ET.fromstring(reply.xml)
            cpu_pct = float(_find_text(root, ".//five-seconds", "0"))
        except Exception:
            pass

        try:
            mem_filter = f"""
            <memory-statistics xmlns="{NS_CISCO_MEMORY}"/>
            """
            reply = await self._run_sync(
                self._manager.get, ("subtree", mem_filter)
            )
            root = ET.fromstring(reply.xml)
            mem_used = int(_find_text(root, ".//used-memory", "0"))
            free_mem = int(_find_text(root, ".//free-memory", "0"))
            mem_total = mem_used + free_mem
        except Exception:
            pass

        mem_pct = (mem_used / mem_total * 100.0) if mem_total else 0.0

        return DeviceHealth(
            cpu_percent=round(cpu_pct, 2),
            memory_used_bytes=mem_used,
            memory_total_bytes=mem_total,
            memory_percent=round(mem_pct, 2),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _require_connection(self) -> None:
        if not self._connected or self._manager is None:
            raise DeviceConnectionError(
                device_id=self.host,
                message="Not connected -- call connect() first",
            )
