"""Tests for the device layer: registry, base class, SSH driver, credentials,
and Pydantic model validation.

Run with:
    python -m unittest tests.test_devices -v
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.models import (
    Device,
    DeviceCreate,
    DeviceUpdate,
    DeviceFacts,
    DeviceHealth,
    InterfaceInfo,
    ConfigBackup,
    Credential,
    CredentialCreate,
    Metric,
    AlertRule,
    AlertRuleCreate,
    Alert,
    DeviceStatus,
    DeviceProtocol,
    BackupType,
    AlertCondition,
    AlertStatus,
)
from core.exceptions import (
    DeviceAuthenticationError,
    DeviceConnectionError,
    DeviceTimeoutError,
    DeviceCommandError,
    NetworkAgentError,
)
from devices.base import BaseDevice
from devices.registry import (
    _DEVICE_REGISTRY,
    register_device,
    get_device_class,
    list_device_types,
)
from devices.ssh_device import SSHDevice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _ConcreteDevice(BaseDevice):
    """Minimal concrete device for testing the ABC."""

    async def connect(self):
        self._connected = True

    async def disconnect(self):
        self._connected = False

    async def get_config(self, config_type="running"):
        return "hostname TestRouter"

    async def send_command(self, command, timeout=30):
        return f"output of {command}"

    async def send_config(self, commands):
        return "config applied"

    async def get_facts(self):
        return DeviceFacts(hostname="TestRouter", vendor="test", model="T-1000")

    async def get_interfaces(self):
        return [
            InterfaceInfo(name="GigabitEthernet0/0", status="up")
        ]

    async def get_health(self):
        return DeviceHealth(cpu_percent=12.5, memory_percent=45.0, uptime_seconds=86400)


# ============================================================================
# Test: Device Registry
# ============================================================================

class TestDeviceRegistry(unittest.TestCase):
    """Test the global device-type registry."""

    def setUp(self):
        # Snapshot and restore the registry after each test
        self._original = dict(_DEVICE_REGISTRY)

    def tearDown(self):
        _DEVICE_REGISTRY.clear()
        _DEVICE_REGISTRY.update(self._original)

    def test_register_and_get(self):
        """register_device decorator stores the class; get_device_class retrieves it."""

        @register_device("test_device")
        class TestDevice(_ConcreteDevice):
            pass

        cls = get_device_class("test_device")
        self.assertIs(cls, TestDevice)

    def test_get_unknown_raises(self):
        """get_device_class raises KeyError for an unregistered type."""
        with self.assertRaises(KeyError) as ctx:
            get_device_class("nonexistent_vendor_xyz")
        self.assertIn("nonexistent_vendor_xyz", str(ctx.exception))

    def test_list_device_types(self):
        """list_device_types returns sorted registered type strings."""

        @register_device("zzz_type")
        class Z(_ConcreteDevice):
            pass

        @register_device("aaa_type")
        class A(_ConcreteDevice):
            pass

        types = list_device_types()
        # Our new entries should be present and sorted
        self.assertIn("aaa_type", types)
        self.assertIn("zzz_type", types)
        idx_a = types.index("aaa_type")
        idx_z = types.index("zzz_type")
        self.assertLess(idx_a, idx_z)

    def test_register_overwrites_with_warning(self):
        """Registering the same type twice overwrites the first."""

        @register_device("dupe_type")
        class First(_ConcreteDevice):
            pass

        @register_device("dupe_type")
        class Second(_ConcreteDevice):
            pass

        cls = get_device_class("dupe_type")
        self.assertIs(cls, Second)

    def test_registered_type_attribute(self):
        """The decorator stores the type string on the class itself."""

        @register_device("attr_test")
        class AttrDev(_ConcreteDevice):
            pass

        self.assertEqual(AttrDev._registered_type, "attr_test")


# ============================================================================
# Test: BaseDevice ABC
# ============================================================================

class TestBaseDeviceABC(unittest.TestCase):
    """Verify BaseDevice cannot be instantiated directly."""

    def test_cannot_instantiate_abc(self):
        """BaseDevice is abstract and should raise TypeError on instantiation."""
        with self.assertRaises(TypeError):
            BaseDevice(host="10.0.0.1")

    def test_concrete_device_works(self):
        """A fully-implemented subclass can be instantiated."""
        dev = _ConcreteDevice(host="10.0.0.1", username="admin", password="secret")
        self.assertEqual(dev.host, "10.0.0.1")
        self.assertEqual(dev.username, "admin")
        self.assertFalse(dev.is_connected)

    def test_default_port(self):
        """Default port should be 22."""
        dev = _ConcreteDevice(host="10.0.0.1")
        self.assertEqual(dev.port, 22)

    def test_repr(self):
        """__repr__ includes class name and host."""
        dev = _ConcreteDevice(host="192.168.1.1")
        self.assertIn("_ConcreteDevice", repr(dev))
        self.assertIn("192.168.1.1", repr(dev))

    def test_context_manager(self):
        """async with should call connect/disconnect."""
        dev = _ConcreteDevice(host="10.0.0.1")

        async def _test():
            async with dev as d:
                self.assertTrue(d.is_connected)
            self.assertFalse(dev.is_connected)

        _run(_test())

    def test_get_facts(self):
        """Concrete get_facts returns a DeviceFacts instance."""
        dev = _ConcreteDevice(host="10.0.0.1")
        facts = _run(dev.get_facts())
        self.assertIsInstance(facts, DeviceFacts)
        self.assertEqual(facts.hostname, "TestRouter")

    def test_get_health(self):
        """Concrete get_health returns a DeviceHealth instance."""
        dev = _ConcreteDevice(host="10.0.0.1")
        health = _run(dev.get_health())
        self.assertIsInstance(health, DeviceHealth)
        self.assertAlmostEqual(health.cpu_percent, 12.5)

    def test_get_interfaces(self):
        """Concrete get_interfaces returns a list of InterfaceInfo."""
        dev = _ConcreteDevice(host="10.0.0.1")
        ifaces = _run(dev.get_interfaces())
        self.assertIsInstance(ifaces, list)
        self.assertEqual(len(ifaces), 1)
        self.assertEqual(ifaces[0].name, "GigabitEthernet0/0")


# ============================================================================
# Test: SSHDevice with mocked Netmiko
# ============================================================================

class TestSSHDeviceMocked(unittest.TestCase):
    """Test SSHDevice with Netmiko's ConnectHandler mocked out."""

    def _make_device(self, **kwargs):
        defaults = {
            "host": "10.0.0.1",
            "username": "admin",
            "password": "pass123",
            "port": 22,
            "device_type": "cisco_ios",
        }
        defaults.update(kwargs)
        dev = SSHDevice(**defaults)
        return dev

    @patch("devices.ssh_device.ConnectHandler")
    def test_connect_success(self, mock_handler_cls):
        """connect() should create a ConnectHandler and set _connected."""
        mock_conn = MagicMock()
        mock_handler_cls.return_value = mock_conn

        dev = self._make_device()

        _run(dev.connect())

        self.assertTrue(dev.is_connected)
        mock_handler_cls.assert_called_once()
        call_kwargs = mock_handler_cls.call_args[1]
        self.assertEqual(call_kwargs["host"], "10.0.0.1")
        self.assertEqual(call_kwargs["username"], "admin")

    @patch("devices.ssh_device.ConnectHandler")
    def test_disconnect(self, mock_handler_cls):
        """disconnect() should call disconnect on the Netmiko connection."""
        mock_conn = MagicMock()
        mock_handler_cls.return_value = mock_conn

        dev = self._make_device()
        _run(dev.connect())
        self.assertTrue(dev.is_connected)

        _run(dev.disconnect())
        self.assertFalse(dev.is_connected)
        mock_conn.disconnect.assert_called_once()

    @patch("devices.ssh_device.ConnectHandler")
    def test_send_command(self, mock_handler_cls):
        """send_command() should delegate to Netmiko send_command."""
        mock_conn = MagicMock()
        mock_conn.send_command.return_value = "Switch#show version\nCisco IOS ..."
        mock_handler_cls.return_value = mock_conn

        dev = self._make_device()
        _run(dev.connect())

        output = _run(dev.send_command("show version"))
        self.assertIn("Cisco IOS", output)
        mock_conn.send_command.assert_called_once()

    @patch("devices.ssh_device.ConnectHandler")
    def test_send_config(self, mock_handler_cls):
        """send_config() should delegate to Netmiko send_config_set."""
        mock_conn = MagicMock()
        mock_conn.send_config_set.return_value = "config applied"
        mock_handler_cls.return_value = mock_conn

        dev = self._make_device()
        _run(dev.connect())

        output = _run(dev.send_config(["interface Gi0/0", "shutdown"]))
        self.assertEqual(output, "config applied")
        mock_conn.send_config_set.assert_called_once_with(
            ["interface Gi0/0", "shutdown"]
        )

    @patch("devices.ssh_device.ConnectHandler")
    def test_get_config(self, mock_handler_cls):
        """get_config() should call 'show running-config' by default."""
        mock_conn = MagicMock()
        mock_conn.send_command.return_value = "hostname Router1\n!\ninterface Gi0/0"
        mock_handler_cls.return_value = mock_conn

        dev = self._make_device()
        _run(dev.connect())

        config = _run(dev.get_config("running"))
        self.assertIn("hostname Router1", config)

    @patch("devices.ssh_device.ConnectHandler")
    def test_connect_auth_failure(self, mock_handler_cls):
        """connect() wraps NetmikoAuthenticationException."""
        from netmiko.exceptions import NetmikoAuthenticationException

        mock_handler_cls.side_effect = NetmikoAuthenticationException("bad creds")

        dev = self._make_device()
        with self.assertRaises(DeviceAuthenticationError):
            _run(dev.connect())

    @patch("devices.ssh_device.ConnectHandler")
    def test_connect_timeout(self, mock_handler_cls):
        """connect() wraps NetmikoTimeoutException."""
        from netmiko.exceptions import NetmikoTimeoutException

        mock_handler_cls.side_effect = NetmikoTimeoutException("timed out")

        dev = self._make_device()
        with self.assertRaises(DeviceTimeoutError):
            _run(dev.connect())

    @patch("devices.ssh_device.ConnectHandler")
    def test_connect_generic_failure(self, mock_handler_cls):
        """connect() wraps unexpected exceptions as DeviceConnectionError."""
        mock_handler_cls.side_effect = OSError("connection refused")

        dev = self._make_device()
        with self.assertRaises(DeviceConnectionError):
            _run(dev.connect())

    @patch("devices.ssh_device.ConnectHandler")
    def test_send_command_without_connect_raises(self, mock_handler_cls):
        """send_command() before connect() should raise DeviceConnectionError."""
        dev = self._make_device()
        with self.assertRaises(DeviceConnectionError):
            _run(dev.send_command("show version"))

    @patch("devices.ssh_device.ConnectHandler")
    def test_enable_mode_with_secret(self, mock_handler_cls):
        """When enable_secret is provided, connect() attempts to enter enable mode."""
        mock_conn = MagicMock()
        mock_handler_cls.return_value = mock_conn

        dev = self._make_device(enable_secret="en_pass")
        _run(dev.connect())

        mock_conn.enable.assert_called_once()

    @patch("devices.ssh_device.ConnectHandler")
    def test_build_netmiko_params_with_ssh_key(self, mock_handler_cls):
        """SSH key path should be included in Netmiko params."""
        mock_conn = MagicMock()
        mock_handler_cls.return_value = mock_conn

        dev = self._make_device(ssh_key_path="/path/to/key.pem")
        _run(dev.connect())

        call_kwargs = mock_handler_cls.call_args[1]
        self.assertTrue(call_kwargs["use_keys"])
        self.assertEqual(call_kwargs["key_file"], "/path/to/key.pem")


# ============================================================================
# Test: Credential encryption round-trip
# ============================================================================

class TestCredentialEncryption(unittest.TestCase):
    """Test that credentials can be encrypted and decrypted correctly
    using the Fernet symmetric encryption pattern used by CredentialManager.
    """

    def test_fernet_round_trip(self):
        """Fernet encrypt/decrypt produces the original plaintext."""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        fernet = Fernet(key)

        original = "SuperSecretPassword123!"
        encrypted = fernet.encrypt(original.encode("utf-8"))
        decrypted = fernet.decrypt(encrypted).decode("utf-8")

        self.assertEqual(decrypted, original)

    def test_fernet_different_keys_fail(self):
        """Decrypting with a different key should fail."""
        from cryptography.fernet import Fernet, InvalidToken

        key1 = Fernet.generate_key()
        key2 = Fernet.generate_key()

        fernet1 = Fernet(key1)
        fernet2 = Fernet(key2)

        encrypted = fernet1.encrypt(b"my secret")
        with self.assertRaises(InvalidToken):
            fernet2.decrypt(encrypted)

    def test_fernet_empty_string(self):
        """Empty strings should encrypt and decrypt correctly."""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        fernet = Fernet(key)

        encrypted = fernet.encrypt(b"")
        decrypted = fernet.decrypt(encrypted)
        self.assertEqual(decrypted, b"")

    def test_fernet_unicode_round_trip(self):
        """Unicode credentials (e.g. non-ASCII passwords) round-trip correctly."""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        fernet = Fernet(key)

        original = "p@$$w0rd-\u00e9\u00e8\u00ea-\u4e2d\u6587"
        encrypted = fernet.encrypt(original.encode("utf-8"))
        decrypted = fernet.decrypt(encrypted).decode("utf-8")

        self.assertEqual(decrypted, original)

    def test_fernet_key_generation(self):
        """Generated keys should be valid base64-encoded 32-byte strings."""
        from cryptography.fernet import Fernet
        import base64

        key = Fernet.generate_key()
        # Fernet keys are URL-safe base64 encoded
        decoded = base64.urlsafe_b64decode(key)
        self.assertEqual(len(decoded), 32)

    def test_credential_manager_init_with_key(self):
        """CredentialManager should accept a valid Fernet key without error."""
        from cryptography.fernet import Fernet
        from core.credentials import CredentialManager

        key = Fernet.generate_key().decode()
        mock_db = MagicMock()
        # Should not raise
        cm = CredentialManager(mock_db, encryption_key=key)
        self.assertIsNotNone(cm)

    def test_credential_manager_init_without_key(self):
        """CredentialManager without a key should generate one (with warning)."""
        from core.credentials import CredentialManager

        mock_db = MagicMock()
        # Should not raise; generates a key internally
        cm = CredentialManager(mock_db, encryption_key=None)
        self.assertIsNotNone(cm)


# ============================================================================
# Test: Pydantic model validation
# ============================================================================

class TestPydanticModels(unittest.TestCase):
    """Test the Pydantic v2 data models for validation behaviour."""

    # --- Device ---

    def test_device_create_valid(self):
        """DeviceCreate with valid data should succeed."""
        dc = DeviceCreate(
            hostname="router-1",
            ip_address="10.0.0.1",
            device_type="cisco_ios",
        )
        self.assertEqual(dc.hostname, "router-1")
        self.assertEqual(dc.ip_address, "10.0.0.1")
        self.assertEqual(dc.port, 22)  # default

    def test_device_create_requires_hostname(self):
        """DeviceCreate without hostname should fail validation."""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            DeviceCreate(
                ip_address="10.0.0.1",
                device_type="cisco_ios",
            )

    def test_device_create_requires_ip(self):
        """DeviceCreate without ip_address should fail validation."""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            DeviceCreate(
                hostname="router-1",
                device_type="cisco_ios",
            )

    def test_device_create_invalid_port(self):
        """DeviceCreate with port out of range should fail validation."""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            DeviceCreate(
                hostname="router-1",
                ip_address="10.0.0.1",
                device_type="cisco_ios",
                port=99999,
            )

    def test_device_update_all_optional(self):
        """DeviceUpdate with no fields should succeed (all optional)."""
        du = DeviceUpdate()
        self.assertIsNone(du.hostname)
        self.assertIsNone(du.ip_address)

    def test_device_model_full(self):
        """Device model with all required fields should succeed."""
        d = Device(
            hostname="switch-1",
            ip_address="192.168.1.10",
            device_type="arista_eos",
        )
        self.assertEqual(d.hostname, "switch-1")
        self.assertIsNotNone(d.id)  # auto-generated
        self.assertIsNotNone(d.created_at)

    def test_device_model_dump(self):
        """Device.model_dump() should include all fields."""
        d = Device(
            hostname="fw-1",
            ip_address="10.0.0.254",
            device_type="fortinet",
        )
        dumped = d.model_dump()
        self.assertIn("hostname", dumped)
        self.assertIn("id", dumped)
        self.assertIn("created_at", dumped)
        self.assertEqual(dumped["hostname"], "fw-1")

    # --- DeviceFacts ---

    def test_device_facts_creation(self):
        """DeviceFacts requires hostname."""
        facts = DeviceFacts(hostname="Router1", vendor="Cisco", model="ISR4451")
        self.assertEqual(facts.hostname, "Router1")
        self.assertEqual(facts.vendor, "Cisco")

    def test_device_facts_optional_fields(self):
        """DeviceFacts optional fields default to None."""
        facts = DeviceFacts(hostname="MinimalRouter")
        self.assertIsNone(facts.serial_number)
        self.assertIsNone(facts.os_version)
        self.assertIsNone(facts.uptime)

    # --- DeviceHealth ---

    def test_device_health_creation(self):
        """DeviceHealth requires cpu_percent, memory_percent, uptime_seconds."""
        health = DeviceHealth(cpu_percent=75.3, memory_percent=50.0, uptime_seconds=86400)
        self.assertAlmostEqual(health.cpu_percent, 75.3)
        self.assertAlmostEqual(health.memory_percent, 50.0)
        self.assertEqual(health.uptime_seconds, 86400)

    def test_device_health_validation_range(self):
        """DeviceHealth cpu_percent must be 0-100."""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            DeviceHealth(cpu_percent=150.0, memory_percent=50.0, uptime_seconds=0)

    # --- InterfaceInfo ---

    def test_interface_info_creation(self):
        """InterfaceInfo requires name."""
        iface = InterfaceInfo(name="GigabitEthernet0/1", status="up")
        self.assertEqual(iface.name, "GigabitEthernet0/1")
        self.assertEqual(iface.status, "up")
        self.assertEqual(iface.in_errors, 0)  # default

    def test_interface_info_counters(self):
        """InterfaceInfo counter fields should work correctly."""
        iface = InterfaceInfo(
            name="Eth0",
            in_octets=1_000_000,
            out_octets=2_000_000,
            in_errors=42,
            out_errors=7,
        )
        self.assertEqual(iface.in_octets, 1_000_000)
        self.assertEqual(iface.in_errors, 42)

    # --- ConfigBackup ---

    def test_config_backup_creation(self):
        """ConfigBackup should accept required fields."""
        backup = ConfigBackup(
            device_id="dev-1",
            config_text="hostname Router1\n!\n",
            config_hash="abc123",
        )
        self.assertEqual(backup.device_id, "dev-1")
        self.assertIn("hostname Router1", backup.config_text)
        self.assertIsNotNone(backup.id)

    # --- Credential ---

    def test_credential_create(self):
        """CredentialCreate should accept name and username."""
        cred = CredentialCreate(
            name="lab-creds",
            username="admin",
            password="secretpass",
        )
        self.assertEqual(cred.name, "lab-creds")
        self.assertEqual(cred.username, "admin")

    def test_credential_create_requires_name(self):
        """CredentialCreate without name should fail."""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            CredentialCreate(username="admin")

    # --- Metric ---

    def test_metric_creation(self):
        """Metric should accept device_id, metric_name, metric_value."""
        m = Metric(
            device_id="dev-1",
            metric_name="cpu_percent",
            metric_value=85.5,
        )
        self.assertEqual(m.device_id, "dev-1")
        self.assertAlmostEqual(m.metric_value, 85.5)

    # --- AlertRule ---

    def test_alert_rule_creation(self):
        """AlertRule should accept valid parameters."""
        rule = AlertRule(
            name="High CPU",
            metric_name="cpu_percent",
            condition=AlertCondition.GREATER_THAN,
            threshold=90.0,
        )
        self.assertEqual(rule.name, "High CPU")
        self.assertEqual(rule.threshold, 90.0)
        self.assertTrue(rule.enabled)  # default

    def test_alert_rule_create(self):
        """AlertRuleCreate should accept valid parameters."""
        rule = AlertRuleCreate(
            name="High Memory",
            metric_name="memory_percent",
            condition="gt",
            threshold=85.0,
        )
        self.assertEqual(rule.name, "High Memory")

    # --- Alert ---

    def test_alert_creation(self):
        """Alert should accept required fields."""
        alert = Alert(
            rule_id="rule-1",
            device_id="dev-1",
            metric_value=95.0,
            message="CPU is at 95%",
        )
        self.assertEqual(alert.rule_id, "rule-1")
        self.assertAlmostEqual(alert.metric_value, 95.0)
        self.assertEqual(alert.status, "firing")  # default

    # --- Enums ---

    def test_device_status_enum(self):
        """DeviceStatus enum should have expected values."""
        self.assertEqual(DeviceStatus.ACTIVE, "active")
        self.assertEqual(DeviceStatus.INACTIVE, "inactive")
        self.assertEqual(DeviceStatus.UNREACHABLE, "unreachable")
        self.assertEqual(DeviceStatus.MAINTENANCE, "maintenance")

    def test_device_protocol_enum(self):
        """DeviceProtocol enum should include common protocols."""
        self.assertEqual(DeviceProtocol.SSH, "ssh")
        self.assertEqual(DeviceProtocol.NETCONF, "netconf")
        self.assertEqual(DeviceProtocol.SNMP, "snmp")

    def test_alert_condition_enum(self):
        """AlertCondition enum should include comparison operators."""
        self.assertEqual(AlertCondition.GREATER_THAN, "gt")
        self.assertEqual(AlertCondition.LESS_THAN, "lt")
        self.assertEqual(AlertCondition.EQUAL, "eq")


# ============================================================================
# Test: Exception hierarchy
# ============================================================================

class TestExceptions(unittest.TestCase):
    """Verify the custom exception hierarchy."""

    def test_device_connection_error_format(self):
        """DeviceConnectionError should include device_id in the message."""
        exc = DeviceConnectionError("router1", "SSH refused")
        self.assertIn("router1", str(exc))
        self.assertIn("SSH refused", str(exc))
        self.assertEqual(exc.device_id, "router1")

    def test_device_timeout_error_with_seconds(self):
        """DeviceTimeoutError should include timeout duration."""
        exc = DeviceTimeoutError("sw1", "Timed out", timeout_seconds=30)
        self.assertIn("30", str(exc))
        self.assertEqual(exc.timeout_seconds, 30)

    def test_device_command_error_with_command(self):
        """DeviceCommandError should include the failing command."""
        exc = DeviceCommandError("fw1", "Syntax error", command="show bgp")
        self.assertIn("show bgp", str(exc))
        self.assertEqual(exc.command, "show bgp")

    def test_exception_hierarchy(self):
        """All device exceptions should inherit from NetworkAgentError."""
        self.assertTrue(issubclass(DeviceConnectionError, NetworkAgentError))
        self.assertTrue(issubclass(DeviceAuthenticationError, NetworkAgentError))
        self.assertTrue(issubclass(DeviceTimeoutError, NetworkAgentError))
        self.assertTrue(issubclass(DeviceCommandError, NetworkAgentError))

    def test_exception_without_device_id(self):
        """Exceptions should work without a device_id."""
        exc = DeviceConnectionError(message="General failure")
        self.assertIn("General failure", str(exc))
        self.assertIsNone(exc.device_id)


if __name__ == "__main__":
    unittest.main()
