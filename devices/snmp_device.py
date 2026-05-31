"""SNMP-based device driver supporting SNMPv2c and SNMPv3."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from pysnmp.hlapi.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    bulk_cmd as bulkCmd,
    get_cmd as getCmd,
    next_cmd as nextCmd,
)
from pysnmp.proto.rfc1902 import Integer, OctetString

from core.exceptions import (
    DeviceCommandError,
    DeviceConnectionError,
    DeviceTimeoutError,
)
from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.base import BaseDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Standard OID constants
# ---------------------------------------------------------------------------

# System MIB (SNMPv2-MIB)
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
OID_SYS_CONTACT = "1.3.6.1.2.1.1.4.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
OID_SYS_LOCATION = "1.3.6.1.2.1.1.6.0"

# IF-MIB
OID_IF_NUMBER = "1.3.6.1.2.1.2.1.0"
OID_IF_TABLE = "1.3.6.1.2.1.2.2.1"
OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
OID_IF_TYPE = "1.3.6.1.2.1.2.2.1.3"
OID_IF_MTU = "1.3.6.1.2.1.2.2.1.4"
OID_IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
OID_IF_PHYS_ADDRESS = "1.3.6.1.2.1.2.2.1.6"
OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
OID_IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"
OID_IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
OID_IF_IN_DISCARDS = "1.3.6.1.2.1.2.2.1.13"
OID_IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"
OID_IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"
OID_IF_OUT_DISCARDS = "1.3.6.1.2.1.2.2.1.19"

# IP-MIB  (ifIndex -> IP)
OID_IP_ADDR_IF_INDEX = "1.3.6.1.2.1.4.20.1.2"
OID_IP_ADDR_ENTRY = "1.3.6.1.2.1.4.20.1.1"
OID_IP_ADDR_MASK = "1.3.6.1.2.1.4.20.1.3"

# HOST-RESOURCES-MIB
OID_HR_PROCESSOR_LOAD = "1.3.6.1.2.1.25.3.3.1.2"
OID_HR_STORAGE_DESCR = "1.3.6.1.2.1.25.2.3.1.3"
OID_HR_STORAGE_ALLOC_UNITS = "1.3.6.1.2.1.25.2.3.1.4"
OID_HR_STORAGE_SIZE = "1.3.6.1.2.1.25.2.3.1.5"
OID_HR_STORAGE_USED = "1.3.6.1.2.1.25.2.3.1.6"

# Interface status mapping
_IF_STATUS_MAP = {1: "up", 2: "down", 3: "testing"}


class SNMPDevice(BaseDevice):
    """Device driver that communicates entirely via SNMP.

    Supports both SNMPv2c (community string) and SNMPv3 (USM
    authentication / encryption).

    Parameters
    ----------
    host : str
        Target IP or hostname.
    port : int
        SNMP port (default 161).
    community : str
        SNMPv2c community string.  When set, v2c is used.
    snmp_version : str
        ``"2c"`` or ``"3"``.  Defaults to ``"2c"`` if *community* is set.
    snmp_user : str
        SNMPv3 USM username.
    auth_protocol : str
        ``"md5"`` | ``"sha"`` | ``"sha256"`` | ``"sha512"`` | ``"none"``.
    auth_key : str
        USM authentication passphrase.
    priv_protocol : str
        ``"des"`` | ``"aes128"`` | ``"aes192"`` | ``"aes256"`` | ``"none"``.
    priv_key : str
        USM privacy (encryption) passphrase.
    """

    def __init__(
        self,
        host: str,
        port: int = 161,
        community: str = "public",
        snmp_version: str = "",
        snmp_user: str = "",
        auth_protocol: str = "sha",
        auth_key: str = "",
        priv_protocol: str = "aes128",
        priv_key: str = "",
        timeout: int = 10,
        retries: int = 2,
        **kwargs: Any,
    ) -> None:
        super().__init__(host=host, port=port, timeout=timeout, **kwargs)
        self.community = community
        self.snmp_version = snmp_version or ("3" if snmp_user else "2c")
        self.snmp_user = snmp_user
        self.auth_protocol = auth_protocol
        self.auth_key = auth_key
        self.priv_protocol = priv_protocol
        self.priv_key = priv_key
        self.retries = retries

        self._engine = SnmpEngine()

    # ------------------------------------------------------------------
    # Auth / transport helpers
    # ------------------------------------------------------------------

    def _get_auth_data(self) -> CommunityData | UsmUserData:
        """Build the appropriate pysnmp auth object."""
        if self.snmp_version == "3":
            from pysnmp.hlapi.asyncio import (
                usmDESPrivProtocol,
                usmHMACMD5AuthProtocol,
                usmHMACSHAAuthProtocol,
                usmNoAuthProtocol,
                usmNoPrivProtocol,
            )

            # Optional: newer PySNMP may expose AES helpers; fall back gracefully.
            try:
                from pysnmp.hlapi.asyncio import usmAesCfb128Protocol
            except ImportError:
                usmAesCfb128Protocol = usmDESPrivProtocol
            try:
                from pysnmp.hlapi.asyncio import usmAesCfb192Protocol
            except ImportError:
                usmAesCfb192Protocol = usmDESPrivProtocol
            try:
                from pysnmp.hlapi.asyncio import usmAesCfb256Protocol
            except ImportError:
                usmAesCfb256Protocol = usmDESPrivProtocol
            try:
                from pysnmp.hlapi.asyncio import usmHMAC128SHA224AuthProtocol as sha224
            except ImportError:
                sha224 = usmHMACSHAAuthProtocol
            try:
                from pysnmp.hlapi.asyncio import usmHMAC256SHA384AuthProtocol as sha384
            except ImportError:
                sha384 = usmHMACSHAAuthProtocol

            auth_map = {
                "md5": usmHMACMD5AuthProtocol,
                "sha": usmHMACSHAAuthProtocol,
                "sha224": sha224,
                "sha256": sha224,  # closest standard
                "sha384": sha384,
                "sha512": sha384,
                "none": usmNoAuthProtocol,
            }
            priv_map = {
                "des": usmDESPrivProtocol,
                "aes128": usmAesCfb128Protocol,
                "aes192": usmAesCfb192Protocol,
                "aes256": usmAesCfb256Protocol,
                "none": usmNoPrivProtocol,
            }

            auth_proto = auth_map.get(self.auth_protocol.lower(), usmHMACSHAAuthProtocol)
            priv_proto = priv_map.get(self.priv_protocol.lower(), usmNoPrivProtocol)

            return UsmUserData(
                self.snmp_user,
                authKey=self.auth_key or None,
                privKey=self.priv_key or None,
                authProtocol=auth_proto,
                privProtocol=priv_proto,
            )
        # v2c
        return CommunityData(self.community, mpModel=1)

    def _get_transport(self) -> UdpTransportTarget:
        return UdpTransportTarget(
            (self.host, self.port),
            timeout=self.timeout,
            retries=self.retries,
        )

    # ------------------------------------------------------------------
    # Low-level SNMP operations
    # ------------------------------------------------------------------

    async def get_snmp(self, oid: str) -> Any:
        """Perform an SNMP GET for a single OID and return the value."""
        error_indication, error_status, error_index, var_binds = await getCmd(
            self._engine,
            self._get_auth_data(),
            self._get_transport(),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )
        if error_indication:
            raise DeviceCommandError(
                device_id=self.host,
                message=f"SNMP GET error: {error_indication}",
                command=oid,
            )
        if error_status:
            raise DeviceCommandError(
                device_id=self.host,
                message=(
                    f"SNMP GET error: {error_status.prettyPrint()} at "
                    f"{error_index and var_binds[int(error_index) - 1][0] or '?'}"
                ),
                command=oid,
            )
        if var_binds:
            _, value = var_binds[0]
            return value
        return None

    async def walk_snmp(self, oid: str) -> list[tuple[str, Any]]:
        """Perform an SNMP WALK (GETNEXT) and return a list of (oid, value)."""
        results: list[tuple[str, Any]] = []
        kwargs = dict(
            snmpEngine=self._engine,
            authData=self._get_auth_data(),
            transportTarget=self._get_transport(),
            contextData=ContextData(),
            varBinds=(ObjectType(ObjectIdentity(oid)),),
        )
        # Use nextCmd as an async generator
        async for error_indication, error_status, error_index, var_binds in nextCmd(
            self._engine,
            self._get_auth_data(),
            self._get_transport(),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        ):
            if error_indication:
                raise DeviceCommandError(
                    device_id=self.host,
                    message=f"SNMP WALK error: {error_indication}",
                    command=oid,
                )
            if error_status:
                raise DeviceCommandError(
                    device_id=self.host,
                    message=(
                        f"SNMP WALK error: {error_status.prettyPrint()} at "
                        f"{error_index and var_binds[int(error_index) - 1][0] or '?'}"
                    ),
                    command=oid,
                )
            for var_bind in var_binds:
                oid_str = str(var_bind[0])
                # Stop if we've walked past the requested subtree.
                if not oid_str.startswith(oid):
                    return results
                results.append((oid_str, var_bind[1]))

        return results

    # ------------------------------------------------------------------
    # BaseDevice interface
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Validate SNMP reachability by fetching sysDescr."""
        try:
            sys_descr = await self.get_snmp(OID_SYS_DESCR)
            if sys_descr is None:
                raise DeviceConnectionError(
                    device_id=self.host,
                    message="SNMP connect check returned no data for sysDescr",
                )
            self._connected = True
            logger.info(
                "SNMP connection validated for %s: %s",
                self.host,
                str(sys_descr)[:120],
            )
        except DeviceCommandError:
            raise
        except Exception as exc:
            raise DeviceConnectionError(
                device_id=self.host,
                message=f"SNMP connectivity check failed: {exc}",
            ) from exc

    async def disconnect(self) -> None:
        """SNMP is connectionless; mark as disconnected."""
        self._connected = False
        logger.info("SNMP session closed (logically) for %s", self.host)

    async def send_command(self, command: str, timeout: int = 30) -> str:
        """SNMP does not support arbitrary CLI commands.

        Interpret *command* as an OID for a GET request.
        """
        value = await self.get_snmp(command)
        return str(value) if value is not None else ""

    async def send_config(self, commands: list[str]) -> str:
        """SNMP SET is not implemented in this driver (read-only)."""
        raise DeviceCommandError(
            device_id=self.host,
            message="SNMP SET (config push) is not supported by this driver",
        )

    async def get_config(self, config_type: str = "running") -> str:
        """SNMP cannot retrieve full running config; return sysDescr instead."""
        sys_descr = await self.get_snmp(OID_SYS_DESCR)
        return str(sys_descr) if sys_descr else ""

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        """Gather device facts from standard MIB-II OIDs."""
        sys_name = await self.get_snmp(OID_SYS_NAME)
        sys_descr = await self.get_snmp(OID_SYS_DESCR)
        sys_uptime = await self.get_snmp(OID_SYS_UPTIME)
        sys_contact = await self.get_snmp(OID_SYS_CONTACT)
        sys_location = await self.get_snmp(OID_SYS_LOCATION)

        # sysUpTime is in hundredths of a second
        uptime_cs = int(sys_uptime) if sys_uptime is not None else 0
        uptime_seconds = uptime_cs // 100

        days = uptime_seconds // 86400
        hours = (uptime_seconds % 86400) // 3600
        minutes = (uptime_seconds % 3600) // 60
        secs = uptime_seconds % 60
        uptime_str = f"{days}d {hours}h {minutes}m {secs}s"

        descr = str(sys_descr) if sys_descr else ""
        vendor = ""
        model = ""
        os_version = ""
        # Attempt to extract vendor/model/version from sysDescr
        if "cisco" in descr.lower():
            vendor = "Cisco"
            ver_m = re.search(r"Version\s+([\S]+)", descr)
            if ver_m:
                os_version = ver_m.group(1).rstrip(",")
        elif "juniper" in descr.lower():
            vendor = "Juniper"
            ver_m = re.search(r"JUNOS\s+([\S]+)", descr, re.IGNORECASE)
            if ver_m:
                os_version = ver_m.group(1)
        elif "fortinet" in descr.lower() or "fortigate" in descr.lower():
            vendor = "Fortinet"

        # Try to get interface count
        try:
            if_num = await self.get_snmp(OID_IF_NUMBER)
            interface_count = int(if_num) if if_num else 0
        except Exception:
            interface_count = 0

        return DeviceFacts(
            hostname=str(sys_name) if sys_name else self.host,
            vendor=vendor,
            model=model,
            os_version=os_version,
            uptime=uptime_str,
            uptime_seconds=uptime_seconds,
            interface_count=interface_count,
            extra={
                "sys_descr": descr,
                "sys_contact": str(sys_contact) if sys_contact else "",
                "sys_location": str(sys_location) if sys_location else "",
            },
        )

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    async def get_interfaces(self) -> list[InterfaceInfo]:
        """Walk IF-MIB to gather interface information."""
        descr_walk = await self.walk_snmp(OID_IF_DESCR)
        oper_walk = await self.walk_snmp(OID_IF_OPER_STATUS)
        admin_walk = await self.walk_snmp(OID_IF_ADMIN_STATUS)
        speed_walk = await self.walk_snmp(OID_IF_SPEED)
        mtu_walk = await self.walk_snmp(OID_IF_MTU)
        phys_walk = await self.walk_snmp(OID_IF_PHYS_ADDRESS)
        in_oct_walk = await self.walk_snmp(OID_IF_IN_OCTETS)
        out_oct_walk = await self.walk_snmp(OID_IF_OUT_OCTETS)
        in_err_walk = await self.walk_snmp(OID_IF_IN_ERRORS)
        out_err_walk = await self.walk_snmp(OID_IF_OUT_ERRORS)
        in_disc_walk = await self.walk_snmp(OID_IF_IN_DISCARDS)
        out_disc_walk = await self.walk_snmp(OID_IF_OUT_DISCARDS)

        # Build IP address lookup: ifIndex -> (ip, mask)
        ip_idx_walk = await self.walk_snmp(OID_IP_ADDR_IF_INDEX)
        ip_entry_walk = await self.walk_snmp(OID_IP_ADDR_ENTRY)
        ip_mask_walk = await self.walk_snmp(OID_IP_ADDR_MASK)

        ip_map: dict[int, tuple[str, str]] = {}
        # ip_entry_walk items have OID like ...1.1.<ip>, value is the IP
        # ip_idx_walk items have OID like ...1.2.<ip>, value is ifIndex
        idx_by_ip: dict[str, int] = {}
        for oid_str, val in ip_idx_walk:
            ip_part = oid_str.replace(OID_IP_ADDR_IF_INDEX + ".", "", 1)
            idx_by_ip[ip_part] = int(val)
        mask_by_ip: dict[str, str] = {}
        for oid_str, val in ip_mask_walk:
            ip_part = oid_str.replace(OID_IP_ADDR_MASK + ".", "", 1)
            mask_by_ip[ip_part] = str(val)
        for ip_addr, if_index in idx_by_ip.items():
            mask = mask_by_ip.get(ip_addr, "")
            ip_map[if_index] = (ip_addr, mask)

        def _extract_index(oid_str: str, base_oid: str) -> int:
            suffix = oid_str[len(base_oid) :]  # e.g. ".1", ".2"
            return int(suffix.lstrip("."))

        def _to_dict(walk_result: list[tuple[str, Any]], base_oid: str) -> dict[int, Any]:
            result: dict[int, Any] = {}
            for oid_str, val in walk_result:
                try:
                    idx = _extract_index(oid_str, base_oid)
                    result[idx] = val
                except (ValueError, IndexError):
                    pass
            return result

        descr_d = _to_dict(descr_walk, OID_IF_DESCR)
        oper_d = _to_dict(oper_walk, OID_IF_OPER_STATUS)
        admin_d = _to_dict(admin_walk, OID_IF_ADMIN_STATUS)
        speed_d = _to_dict(speed_walk, OID_IF_SPEED)
        mtu_d = _to_dict(mtu_walk, OID_IF_MTU)
        phys_d = _to_dict(phys_walk, OID_IF_PHYS_ADDRESS)
        in_oct_d = _to_dict(in_oct_walk, OID_IF_IN_OCTETS)
        out_oct_d = _to_dict(out_oct_walk, OID_IF_OUT_OCTETS)
        in_err_d = _to_dict(in_err_walk, OID_IF_IN_ERRORS)
        out_err_d = _to_dict(out_err_walk, OID_IF_OUT_ERRORS)
        in_disc_d = _to_dict(in_disc_walk, OID_IF_IN_DISCARDS)
        out_disc_d = _to_dict(out_disc_walk, OID_IF_OUT_DISCARDS)

        interfaces: list[InterfaceInfo] = []
        for if_index in sorted(descr_d):
            name = str(descr_d.get(if_index, f"if{if_index}"))
            oper_val = int(oper_d.get(if_index, 2))
            admin_val = int(admin_d.get(if_index, 2))
            speed_bps = int(speed_d.get(if_index, 0))

            # Determine status string
            if admin_val != 1:
                status = "administratively down"
            else:
                status = _IF_STATUS_MAP.get(oper_val, "unknown")
            protocol_status = _IF_STATUS_MAP.get(oper_val, "unknown")

            # Convert speed
            if speed_bps >= 1_000_000_000:
                speed_str = f"{speed_bps // 1_000_000_000}Gbps"
            elif speed_bps >= 1_000_000:
                speed_str = f"{speed_bps // 1_000_000}Mbps"
            elif speed_bps > 0:
                speed_str = f"{speed_bps}bps"
            else:
                speed_str = ""

            # MAC address
            phys_raw = phys_d.get(if_index)
            if phys_raw and hasattr(phys_raw, "prettyPrint"):
                mac_hex = phys_raw.prettyPrint()
                # pysnmp renders OctetString as "0x001122334455" or similar
                mac_hex = mac_hex.replace("0x", "")
                if len(mac_hex) == 12:
                    mac_str = ":".join(mac_hex[i : i + 2] for i in range(0, 12, 2))
                else:
                    mac_str = mac_hex
            else:
                mac_str = ""

            ip_addr, subnet = ip_map.get(if_index, ("", ""))

            interfaces.append(
                InterfaceInfo(
                    name=name,
                    status=status,
                    protocol_status=protocol_status,
                    ip_address=ip_addr,
                    subnet_mask=subnet,
                    speed=speed_str,
                    mtu=int(mtu_d.get(if_index, 0)),
                    mac_address=mac_str,
                    in_octets=int(in_oct_d.get(if_index, 0)),
                    out_octets=int(out_oct_d.get(if_index, 0)),
                    in_errors=int(in_err_d.get(if_index, 0)),
                    out_errors=int(out_err_d.get(if_index, 0)),
                    in_discards=int(in_disc_d.get(if_index, 0)),
                    out_discards=int(out_disc_d.get(if_index, 0)),
                )
            )

        return interfaces

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def get_health(self) -> DeviceHealth:
        """Gather health data from HOST-RESOURCES-MIB."""
        # CPU -- hrProcessorLoad returns one value per CPU core
        cpu_walk = await self.walk_snmp(OID_HR_PROCESSOR_LOAD)
        if cpu_walk:
            loads = [int(v) for _, v in cpu_walk]
            avg_cpu = sum(loads) / len(loads) if loads else 0.0
        else:
            avg_cpu = 0.0

        # Storage (memory + disk)
        descr_walk = await self.walk_snmp(OID_HR_STORAGE_DESCR)
        alloc_walk = await self.walk_snmp(OID_HR_STORAGE_ALLOC_UNITS)
        size_walk = await self.walk_snmp(OID_HR_STORAGE_SIZE)
        used_walk = await self.walk_snmp(OID_HR_STORAGE_USED)

        def _idx(oid_s: str, base: str) -> int:
            return int(oid_s[len(base) :].lstrip("."))

        descr_map = {_idx(o, OID_HR_STORAGE_DESCR): str(v) for o, v in descr_walk}
        alloc_map = {_idx(o, OID_HR_STORAGE_ALLOC_UNITS): int(v) for o, v in alloc_walk}
        size_map = {_idx(o, OID_HR_STORAGE_SIZE): int(v) for o, v in size_walk}
        used_map = {_idx(o, OID_HR_STORAGE_USED): int(v) for o, v in used_walk}

        mem_total = mem_used = 0
        disk_total = disk_used = 0

        for idx, descr in descr_map.items():
            alloc = alloc_map.get(idx, 1)
            size = size_map.get(idx, 0) * alloc
            used = used_map.get(idx, 0) * alloc
            dl = descr.lower()
            if "real memory" in dl or "physical memory" in dl or "ram" in dl:
                mem_total = size
                mem_used = used
            elif "/" == descr.strip() or "c:\\" in dl or "/dev/" in dl:
                disk_total += size
                disk_used += used

        mem_pct = (mem_used / mem_total * 100.0) if mem_total else 0.0
        disk_pct = (disk_used / disk_total * 100.0) if disk_total else 0.0

        return DeviceHealth(
            cpu_percent=round(avg_cpu, 2),
            memory_used_bytes=mem_used,
            memory_total_bytes=mem_total,
            memory_percent=round(mem_pct, 2),
            disk_used_bytes=disk_used,
            disk_total_bytes=disk_total,
            disk_percent=round(disk_pct, 2),
        )
