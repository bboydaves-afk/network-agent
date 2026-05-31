"""Network discovery -- subnet scanning, device identification, and auto-add."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import platform
import struct
import socket
from typing import Any
from uuid import uuid4
from datetime import datetime, timezone

from core.database import Database
from core.exceptions import DiscoveryError

logger = logging.getLogger(__name__)

# Maximum concurrent scans to avoid socket exhaustion.
_DEFAULT_SCAN_CONCURRENCY = 50

# ---------------------------------------------------------------------------
# Vendor OID map -- maps SNMP sysObjectID enterprise prefixes to vendors.
# ---------------------------------------------------------------------------

VENDOR_OID_MAP: dict[str, dict[str, str]] = {
    "1.3.6.1.4.1.9": {"vendor": "cisco", "device_type": "cisco_ios"},
    "1.3.6.1.4.1.2636": {"vendor": "juniper", "device_type": "juniper_junos"},
    "1.3.6.1.4.1.2011": {"vendor": "huawei", "device_type": "huawei_vrp"},
    "1.3.6.1.4.1.6527": {"vendor": "nokia", "device_type": "nokia_sros"},
    "1.3.6.1.4.1.6486": {"vendor": "alcatel", "device_type": "alcatel_aos"},
    "1.3.6.1.4.1.30065": {"vendor": "arista", "device_type": "arista_eos"},
    "1.3.6.1.4.1.25506": {"vendor": "hp_comware", "device_type": "hp_comware"},
    "1.3.6.1.4.1.11": {"vendor": "hp", "device_type": "hp_procurve"},
    "1.3.6.1.4.1.12356": {"vendor": "fortinet", "device_type": "fortinet"},
    "1.3.6.1.4.1.2272": {"vendor": "nortel", "device_type": "nortel"},
    "1.3.6.1.4.1.3375": {"vendor": "f5", "device_type": "f5_tmsh"},
    "1.3.6.1.4.1.8072": {"vendor": "net-snmp", "device_type": "linux"},
    "1.3.6.1.4.1.14988": {"vendor": "mikrotik", "device_type": "mikrotik_routeros"},
    "1.3.6.1.4.1.4526": {"vendor": "netgear", "device_type": "netgear"},
    "1.3.6.1.4.1.1991": {"vendor": "brocade", "device_type": "brocade"},
    "1.3.6.1.4.1.3224": {"vendor": "juniper_screenos", "device_type": "juniper_screenos"},
    "1.3.6.1.4.1.52": {"vendor": "3com", "device_type": "generic"},
}

# Standard SNMP OIDs.
_OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
_OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
_OID_SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
_OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
_OID_SYS_CONTACT = "1.3.6.1.2.1.1.4.0"
_OID_SYS_LOCATION = "1.3.6.1.2.1.1.6.0"


class NetworkDiscovery:
    """Discovers devices on a network using SNMP and ICMP."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # SNMP helpers (pysnmp-based)
    # ------------------------------------------------------------------

    @staticmethod
    async def _snmp_get(
        ip: str,
        oid: str,
        community: str = "public",
        timeout: int = 2,
    ) -> str | None:
        """Perform a single SNMP GET and return the string value, or ``None``
        on timeout / error.

        Uses ``pysnmp`` which is synchronous, so we run it in the default
        executor to keep the event loop responsive.
        """

        def _do_snmp() -> str | None:
            try:
                from pysnmp.hlapi.v3arch import (
                    SnmpEngine,
                    CommunityData,
                    UdpTransportTarget,
                    ContextData,
                    ObjectType,
                    ObjectIdentity,
                    get_cmd as getCmd,
                )

                iterator = getCmd(
                    SnmpEngine(),
                    CommunityData(community),
                    UdpTransportTarget((ip, 161), timeout=timeout, retries=0),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
                error_indication, error_status, error_index, var_binds = next(iterator)
                if error_indication or error_status:
                    return None
                for _name, val in var_binds:
                    return str(val)
                return None
            except Exception:
                return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do_snmp)

    @staticmethod
    async def _snmp_get_multiple(
        ip: str,
        oids: list[str],
        community: str = "public",
        timeout: int = 2,
    ) -> dict[str, str | None]:
        """Fetch several OIDs in one SNMP GET request."""

        def _do_snmp_multi() -> dict[str, str | None]:
            results: dict[str, str | None] = {oid: None for oid in oids}
            try:
                from pysnmp.hlapi.v3arch import (
                    SnmpEngine,
                    CommunityData,
                    UdpTransportTarget,
                    ContextData,
                    ObjectType,
                    ObjectIdentity,
                    get_cmd as getCmd,
                )

                obj_types = [ObjectType(ObjectIdentity(oid)) for oid in oids]
                iterator = getCmd(
                    SnmpEngine(),
                    CommunityData(community),
                    UdpTransportTarget((ip, 161), timeout=timeout, retries=0),
                    ContextData(),
                    *obj_types,
                )
                error_indication, error_status, error_index, var_binds = next(iterator)
                if error_indication or error_status:
                    return results
                for name, val in var_binds:
                    results[str(name)] = str(val)
                return results
            except Exception:
                return results

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do_snmp_multi)

    # ------------------------------------------------------------------
    # Vendor matching
    # ------------------------------------------------------------------

    @staticmethod
    def _match_vendor(sys_object_id: str | None) -> dict[str, str]:
        """Match a sysObjectID against the :data:`VENDOR_OID_MAP`.

        Returns ``{"vendor": ..., "device_type": ...}`` or sensible defaults.
        """
        if not sys_object_id:
            return {"vendor": "unknown", "device_type": "generic"}

        # Walk from most specific to least specific OID prefix.
        best_match: dict[str, str] | None = None
        best_len = 0
        for prefix, info in VENDOR_OID_MAP.items():
            if sys_object_id.startswith(prefix) and len(prefix) > best_len:
                best_match = info
                best_len = len(prefix)

        return best_match or {"vendor": "unknown", "device_type": "generic"}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan_subnet(
        self,
        subnet: str,
        community: str = "public",
        timeout: int = 2,
    ) -> list[dict[str, Any]]:
        """Scan all hosts in a subnet using SNMP.

        Parameters
        ----------
        subnet:
            CIDR notation, e.g. ``"192.168.1.0/24"``.
        community:
            SNMPv2c community string.
        timeout:
            Per-host SNMP timeout in seconds.

        Returns
        -------
        list[dict]
            Each dict contains ``ip``, ``hostname``, ``device_type``,
            ``vendor``, ``description``.
        """
        try:
            network = ipaddress.ip_network(subnet, strict=False)
        except ValueError as exc:
            raise DiscoveryError(f"Invalid subnet {subnet!r}: {exc}") from exc

        hosts = [str(ip) for ip in network.hosts()]
        logger.info(
            "Starting SNMP scan of %s (%d hosts).", subnet, len(hosts)
        )

        semaphore = asyncio.Semaphore(_DEFAULT_SCAN_CONCURRENCY)
        discovered: list[dict[str, Any]] = []

        async def _probe(ip: str) -> dict[str, Any] | None:
            async with semaphore:
                return await self._snmp_probe(ip, community, timeout)

        tasks = [_probe(ip) for ip in hosts]
        results = await asyncio.gather(*tasks)

        for result in results:
            if result is not None:
                discovered.append(result)

        logger.info(
            "SNMP scan of %s complete: %d devices found.", subnet, len(discovered)
        )
        return discovered

    async def _snmp_probe(
        self, ip: str, community: str, timeout: int
    ) -> dict[str, Any] | None:
        """Probe a single IP with SNMP and return device info or None."""
        sys_descr = await self._snmp_get(ip, _OID_SYS_DESCR, community, timeout)
        if sys_descr is None:
            return None  # Host did not respond.

        sys_name = await self._snmp_get(ip, _OID_SYS_NAME, community, timeout)
        sys_object_id = await self._snmp_get(ip, _OID_SYS_OBJECT_ID, community, timeout)

        vendor_info = self._match_vendor(sys_object_id)

        return {
            "ip": ip,
            "hostname": sys_name or "",
            "device_type": vendor_info["device_type"],
            "vendor": vendor_info["vendor"],
            "description": sys_descr or "",
            "sys_object_id": sys_object_id or "",
        }

    async def ping_sweep(
        self, subnet: str, timeout: int = 1
    ) -> list[str]:
        """Ping every host in a subnet and return the list of responding IPs.

        Uses the system ``ping`` command (``-n`` on Windows, ``-c`` on
        Linux/macOS) run via ``asyncio.create_subprocess_exec``.
        """
        try:
            network = ipaddress.ip_network(subnet, strict=False)
        except ValueError as exc:
            raise DiscoveryError(f"Invalid subnet {subnet!r}: {exc}") from exc

        hosts = [str(ip) for ip in network.hosts()]
        logger.info("Starting ping sweep of %s (%d hosts).", subnet, len(hosts))

        semaphore = asyncio.Semaphore(_DEFAULT_SCAN_CONCURRENCY)
        responding: list[str] = []

        async def _ping(ip: str) -> str | None:
            async with semaphore:
                return await self._ping_host(ip, timeout)

        tasks = [_ping(ip) for ip in hosts]
        results = await asyncio.gather(*tasks)

        for ip in results:
            if ip is not None:
                responding.append(ip)

        logger.info(
            "Ping sweep of %s complete: %d/%d hosts responding.",
            subnet,
            len(responding),
            len(hosts),
        )
        return responding

    @staticmethod
    async def _ping_host(ip: str, timeout: int) -> str | None:
        """Ping a single host; return the IP if it responds, else ``None``."""
        is_windows = platform.system().lower() == "windows"
        if is_windows:
            cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), ip]
        else:
            cmd = ["ping", "-c", "1", "-W", str(timeout), ip]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=timeout + 5)
            return ip if proc.returncode == 0 else None
        except (asyncio.TimeoutError, OSError):
            return None

    async def identify_device(
        self, ip: str, community: str = "public"
    ) -> dict[str, Any]:
        """Query a single host via SNMP to determine vendor and device type.

        Returns
        -------
        dict
            ``ip``, ``hostname``, ``vendor``, ``device_type``, ``description``,
            ``sys_object_id``, ``uptime``, ``contact``, ``location``.
        """
        results = await self._snmp_get_multiple(
            ip,
            [
                _OID_SYS_DESCR,
                _OID_SYS_NAME,
                _OID_SYS_OBJECT_ID,
                _OID_SYS_UPTIME,
                _OID_SYS_CONTACT,
                _OID_SYS_LOCATION,
            ],
            community,
        )

        sys_descr = results.get(_OID_SYS_DESCR)
        sys_name = results.get(_OID_SYS_NAME)
        sys_object_id = results.get(_OID_SYS_OBJECT_ID)
        sys_uptime = results.get(_OID_SYS_UPTIME)
        sys_contact = results.get(_OID_SYS_CONTACT)
        sys_location = results.get(_OID_SYS_LOCATION)

        vendor_info = self._match_vendor(sys_object_id)

        return {
            "ip": ip,
            "hostname": sys_name or "",
            "vendor": vendor_info["vendor"],
            "device_type": vendor_info["device_type"],
            "description": sys_descr or "",
            "sys_object_id": sys_object_id or "",
            "uptime": sys_uptime or "",
            "contact": sys_contact or "",
            "location": sys_location or "",
        }

    async def auto_discover_and_add(
        self,
        subnet: str,
        community: str = "public",
        credential_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Scan a subnet and automatically add newly discovered devices to the
        database.

        Parameters
        ----------
        subnet:
            CIDR notation (e.g. ``"10.0.0.0/24"``).
        community:
            SNMPv2c community string for the scan.
        credential_id:
            Optional credential ID to associate with the new devices.

        Returns
        -------
        list[dict]
            The device records that were added to the database.
        """
        discovered = await self.scan_subnet(subnet, community)
        if not discovered:
            logger.info("No devices discovered in %s.", subnet)
            return []

        # Fetch existing devices to avoid duplicates.
        existing_devices = await self._db.list_devices()
        existing_hosts = {d["host"] for d in existing_devices}

        added: list[dict[str, Any]] = []
        for dev_info in discovered:
            if dev_info["ip"] in existing_hosts:
                logger.debug(
                    "Device %s already in database; skipping.", dev_info["ip"]
                )
                continue

            device_record = {
                "id": str(uuid4()),
                "host": dev_info["ip"],
                "hostname": dev_info.get("hostname", dev_info["ip"]),
                "device_type": dev_info["device_type"],
                "vendor": dev_info.get("vendor", "unknown"),
                "port": 22,
                "credential_id": credential_id or "",
                "description": dev_info.get("description", ""),
                "tags": "auto-discovered",
                "status": "online",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            try:
                await self._db.add_device(device_record)
                added.append(device_record)
                logger.info(
                    "Auto-added device %s (%s) as %s.",
                    dev_info["ip"],
                    dev_info.get("hostname", ""),
                    dev_info["device_type"],
                )
            except Exception:
                logger.exception(
                    "Failed to add discovered device %s.", dev_info["ip"]
                )

        logger.info(
            "Auto-discovery complete: %d new devices added from %s.",
            len(added),
            subnet,
        )
        return added
