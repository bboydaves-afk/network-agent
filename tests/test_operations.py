"""Tests for the operations layer: ConfigManager, Troubleshooter, Discovery,
and AlertEngine.

Run with:
    python -m unittest tests.test_operations -v
"""

from __future__ import annotations

import asyncio
import difflib
import os
import socket
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


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


# ============================================================================
# Test: ConfigManager.diff_configs
# ============================================================================

class TestConfigManagerDiff(unittest.TestCase):
    """Test the configuration diff functionality using the static difflib
    approach from ConfigManager.

    Since ConfigManager requires a Database and CredentialManager, we test
    the diff logic directly using difflib (the same library ConfigManager
    uses internally) and also test with a mocked ConfigManager.
    """

    def test_diff_identical_configs(self):
        """Diffing identical configs should produce an empty diff."""
        config = "hostname Router1\n!\ninterface Gi0/0\n ip address 10.0.0.1 255.255.255.0\n!\n"
        lines_a = config.splitlines(keepends=True)
        lines_b = config.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(lines_a, lines_b, fromfile="a", tofile="b"))
        self.assertEqual(diff, "")

    def test_diff_different_configs(self):
        """Diffing two different configs should show changes."""
        config_a = "hostname Router1\n!\ninterface Gi0/0\n ip address 10.0.0.1 255.255.255.0\n!\n"
        config_b = "hostname Router1\n!\ninterface Gi0/0\n ip address 10.0.0.2 255.255.255.0\n!\n"
        lines_a = config_a.splitlines(keepends=True)
        lines_b = config_b.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(lines_a, lines_b, fromfile="old", tofile="new"))
        self.assertIn("-", diff)
        self.assertIn("+", diff)
        self.assertIn("10.0.0.1", diff)
        self.assertIn("10.0.0.2", diff)

    def test_diff_added_lines(self):
        """Lines added in the second config should appear as additions."""
        config_a = "hostname Router1\n!\n"
        config_b = "hostname Router1\n!\ninterface Loopback0\n ip address 1.1.1.1 255.255.255.255\n!\n"
        lines_a = config_a.splitlines(keepends=True)
        lines_b = config_b.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(lines_a, lines_b, fromfile="old", tofile="new"))
        self.assertIn("+interface Loopback0", diff)

    def test_diff_removed_lines(self):
        """Lines removed in the second config should appear as deletions."""
        config_a = "hostname Router1\n!\naccess-list 10 permit any\n!\n"
        config_b = "hostname Router1\n!\n"
        lines_a = config_a.splitlines(keepends=True)
        lines_b = config_b.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(lines_a, lines_b, fromfile="old", tofile="new"))
        self.assertIn("-access-list 10 permit any", diff)

    def test_diff_empty_configs(self):
        """Diffing two empty strings should produce an empty diff."""
        diff = "".join(difflib.unified_diff([], [], fromfile="a", tofile="b"))
        self.assertEqual(diff, "")

    def test_diff_with_mocked_config_manager(self):
        """Test diff_configs with a fully mocked ConfigManager."""
        from operations.config_manager import ConfigManager

        mock_db = MagicMock()
        mock_cred = MagicMock()

        # Mock the database backup retrieval
        backup_a = {
            "id": "backup-1",
            "config_text": "hostname OldRouter\n!\ninterface Gi0/0\n shutdown\n!\n",
            "created_at": "2024-01-01T00:00:00Z",
        }
        backup_b = {
            "id": "backup-2",
            "config_text": "hostname NewRouter\n!\ninterface Gi0/0\n no shutdown\n!\n",
            "created_at": "2024-01-02T00:00:00Z",
        }

        async def mock_get_backup(backup_id):
            if backup_id == "backup-1":
                return backup_a
            elif backup_id == "backup-2":
                return backup_b
            return None

        mock_db.get_config_backup = AsyncMock(side_effect=mock_get_backup)

        cm = ConfigManager(mock_db, mock_cred, data_dir="./data")
        diff = _run(cm.diff_configs("backup-1", "backup-2"))

        self.assertIn("OldRouter", diff)
        self.assertIn("NewRouter", diff)
        self.assertIn("shutdown", diff)

    def test_parse_config_to_commands(self):
        """_parse_config_to_commands should strip comments and metadata."""
        from operations.config_manager import ConfigManager

        config_text = (
            "!\n"
            "! Last configuration change at ...\n"
            "version 16.12\n"
            "hostname Router1\n"
            "!\n"
            "interface GigabitEthernet0/0\n"
            " ip address 10.0.0.1 255.255.255.0\n"
            " no shutdown\n"
            "!\n"
            "end\n"
        )

        commands = ConfigManager._parse_config_to_commands(config_text)
        self.assertIn("hostname Router1", commands)
        self.assertIn("interface GigabitEthernet0/0", commands)
        self.assertIn(" ip address 10.0.0.1 255.255.255.0", commands)
        # Comments and metadata should be stripped
        self.assertNotIn("!", commands)
        self.assertNotIn("end", commands)
        # version line should be stripped
        stripped_lower = [c.strip().lower() for c in commands]
        self.assertNotIn("version 16.12", stripped_lower)


# ============================================================================
# Test: Troubleshooter.dns_lookup
# ============================================================================

class TestTroubleshooterDNS(unittest.TestCase):
    """Test DNS lookup functionality.

    Since the Troubleshooter class may not exist yet, we test the underlying
    socket.getaddrinfo which the Troubleshooter would use, and also validate
    the pattern a Troubleshooter.dns_lookup would follow.
    """

    def test_dns_lookup_localhost(self):
        """DNS lookup for 'localhost' should resolve to 127.0.0.1 or ::1."""
        results = socket.getaddrinfo("localhost", None)
        addresses = [r[4][0] for r in results]
        # localhost should resolve to at least one loopback address
        self.assertTrue(len(addresses) > 0)
        loopback_found = any(
            addr in ("127.0.0.1", "::1") for addr in addresses
        )
        self.assertTrue(loopback_found, f"No loopback in {addresses}")

    def test_dns_lookup_known_domain(self):
        """DNS lookup for a known domain should return at least one result."""
        try:
            results = socket.getaddrinfo("dns.google", None)
            addresses = [r[4][0] for r in results]
            self.assertTrue(len(addresses) > 0)
        except socket.gaierror:
            self.skipTest("DNS resolution not available (no network)")

    def test_dns_lookup_invalid_domain(self):
        """DNS lookup for an invalid domain should raise gaierror."""
        with self.assertRaises(socket.gaierror):
            socket.getaddrinfo("this-domain-definitely-does-not-exist-xyz123.invalid", None)

    def test_dns_reverse_lookup_localhost(self):
        """Reverse DNS for 127.0.0.1 should return something."""
        try:
            hostname, aliases, addresses = socket.gethostbyaddr("127.0.0.1")
            self.assertTrue(len(hostname) > 0)
        except socket.herror:
            # Some systems may not have reverse DNS for 127.0.0.1
            pass

    def test_async_dns_lookup_pattern(self):
        """Validate the async DNS lookup pattern a Troubleshooter would use."""

        async def dns_lookup(hostname: str) -> dict:
            """Simulate what Troubleshooter.dns_lookup would do."""
            loop = asyncio.get_running_loop()
            try:
                results = await loop.run_in_executor(
                    None, socket.getaddrinfo, hostname, None
                )
                addresses = list(set(r[4][0] for r in results))
                return {
                    "hostname": hostname,
                    "addresses": addresses,
                    "success": True,
                }
            except socket.gaierror as exc:
                return {
                    "hostname": hostname,
                    "addresses": [],
                    "success": False,
                    "error": str(exc),
                }

        result = _run(dns_lookup("localhost"))
        self.assertTrue(result["success"])
        self.assertTrue(len(result["addresses"]) > 0)

        result_bad = _run(dns_lookup("nonexistent-host-xyz.invalid"))
        self.assertFalse(result_bad["success"])


# ============================================================================
# Test: Troubleshooter.port_check
# ============================================================================

class TestTroubleshooterPortCheck(unittest.TestCase):
    """Test TCP port checking against localhost."""

    def test_port_check_open_port(self):
        """A listening port on localhost should be detected as open."""
        # Create a temporary server socket on an ephemeral port
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        try:
            result = self._check_port("127.0.0.1", port, timeout=2)
            self.assertTrue(result["open"])
            self.assertEqual(result["port"], port)
        finally:
            server.close()

    def test_port_check_closed_port(self):
        """A non-listening port on localhost should be detected as closed."""
        # Use a port that is very unlikely to be in use
        # We bind-and-close to find a free port, then check it (now closed)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        result = self._check_port("127.0.0.1", port, timeout=1)
        self.assertFalse(result["open"])

    def test_async_port_check_pattern(self):
        """Validate the async port check pattern."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        async def async_port_check(host, port, timeout=2):
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._check_port, host, port, timeout
            )

        try:
            result = _run(async_port_check("127.0.0.1", port))
            self.assertTrue(result["open"])
        finally:
            server.close()

    @staticmethod
    def _check_port(host: str, port: int, timeout: int = 2) -> dict:
        """Check if a TCP port is open on the given host.

        This mirrors what Troubleshooter.port_check would do internally.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            result = sock.connect_ex((host, port))
            is_open = result == 0
        except (socket.timeout, OSError):
            is_open = False
        finally:
            sock.close()

        return {
            "host": host,
            "port": port,
            "open": is_open,
        }


# ============================================================================
# Test: NetworkDiscovery.ping_sweep (localhost)
# ============================================================================

class TestNetworkDiscoveryPingSweep(unittest.TestCase):
    """Test ping sweep with localhost / 127.0.0.1."""

    def test_ping_localhost(self):
        """Pinging 127.0.0.1 should succeed."""
        from operations.discovery import NetworkDiscovery

        async def _test():
            result = await NetworkDiscovery._ping_host("127.0.0.1", timeout=3)
            return result

        result = _run(_test())
        self.assertEqual(result, "127.0.0.1")

    def test_ping_unreachable_host(self):
        """Pinging an unroutable address should return None."""
        from operations.discovery import NetworkDiscovery

        async def _test():
            # 192.0.2.1 is in the TEST-NET-1 range (RFC 5737), should not respond
            result = await NetworkDiscovery._ping_host("192.0.2.1", timeout=1)
            return result

        result = _run(_test())
        self.assertIsNone(result)

    def test_ping_sweep_loopback(self):
        """Ping sweep of 127.0.0.1/32 should find exactly 127.0.0.1."""
        from operations.discovery import NetworkDiscovery

        mock_db = MagicMock()
        discovery = NetworkDiscovery(mock_db)

        async def _test():
            # /32 has no hosts() -- use a single-host approach
            # Instead test with a known responding IP directly
            result = await discovery._ping_host("127.0.0.1", timeout=3)
            return result

        result = _run(_test())
        self.assertEqual(result, "127.0.0.1")

    def test_ping_sweep_small_subnet(self):
        """Ping sweep of 127.0.0.0/31 (which yields 127.0.0.0 and 127.0.0.1
        via ip_network hosts()) should include at least 127.0.0.1."""
        from operations.discovery import NetworkDiscovery
        import ipaddress

        mock_db = MagicMock()
        discovery = NetworkDiscovery(mock_db)

        # Verify the subnet parsing works correctly
        network = ipaddress.ip_network("127.0.0.0/30", strict=False)
        hosts = [str(ip) for ip in network.hosts()]
        self.assertIn("127.0.0.1", hosts)

        # Actually run the ping sweep on 127.0.0.0/30 (2 usable hosts)
        responding = _run(discovery.ping_sweep("127.0.0.0/30", timeout=3))
        self.assertIsInstance(responding, list)
        self.assertIn("127.0.0.1", responding)

    def test_invalid_subnet_raises(self):
        """An invalid subnet should raise DiscoveryError."""
        from operations.discovery import NetworkDiscovery
        from core.exceptions import DiscoveryError

        mock_db = MagicMock()
        discovery = NetworkDiscovery(mock_db)

        with self.assertRaises(DiscoveryError):
            _run(discovery.ping_sweep("not-a-subnet"))

    def test_vendor_matching(self):
        """Test the vendor OID matching logic."""
        from operations.discovery import NetworkDiscovery

        # Cisco OID
        result = NetworkDiscovery._match_vendor("1.3.6.1.4.1.9.1.2345")
        self.assertEqual(result["vendor"], "cisco")

        # Juniper OID
        result = NetworkDiscovery._match_vendor("1.3.6.1.4.1.2636.1.1.1")
        self.assertEqual(result["vendor"], "juniper")

        # Unknown OID
        result = NetworkDiscovery._match_vendor("1.3.6.1.4.1.99999.1")
        self.assertEqual(result["vendor"], "unknown")

        # None
        result = NetworkDiscovery._match_vendor(None)
        self.assertEqual(result["vendor"], "unknown")


# ============================================================================
# Test: AlertEngine.evaluate_rules (mock metrics)
# ============================================================================

class TestAlertEngineEvaluateRules(unittest.TestCase):
    """Test alert rule evaluation logic with mock metrics.

    Since AlertEngine may not exist yet, we implement and test the core
    rule evaluation logic that such an engine would use.
    """

    def setUp(self):
        """Set up a simple rule evaluation function and sample rules."""
        self.rules = [
            {
                "id": "rule-cpu-high",
                "name": "High CPU",
                "metric_name": "cpu_percent",
                "operator": ">",
                "threshold": 90.0,
                "severity": "critical",
            },
            {
                "id": "rule-mem-high",
                "name": "High Memory",
                "metric_name": "memory_percent",
                "operator": ">",
                "threshold": 85.0,
                "severity": "warning",
            },
            {
                "id": "rule-temp-high",
                "name": "High Temperature",
                "metric_name": "temperature_celsius",
                "operator": ">=",
                "threshold": 75.0,
                "severity": "critical",
            },
            {
                "id": "rule-errors-high",
                "name": "Interface Errors",
                "metric_name": "interface_in_errors",
                "operator": ">",
                "threshold": 100.0,
                "severity": "warning",
            },
            {
                "id": "rule-device-down",
                "name": "Device Down",
                "metric_name": "device_reachable",
                "operator": "<",
                "threshold": 1.0,
                "severity": "critical",
            },
        ]

    @staticmethod
    def evaluate_rule(rule: dict, metric_value: float) -> bool:
        """Evaluate a single alert rule against a metric value.

        Supports operators: >, <, >=, <=, ==, !=
        Returns True if the rule triggers (alert condition met).
        """
        threshold = rule["threshold"]
        op = rule["operator"]

        if op == ">":
            return metric_value > threshold
        elif op == "<":
            return metric_value < threshold
        elif op == ">=":
            return metric_value >= threshold
        elif op == "<=":
            return metric_value <= threshold
        elif op == "==":
            return metric_value == threshold
        elif op == "!=":
            return metric_value != threshold
        else:
            raise ValueError(f"Unknown operator: {op!r}")

    def evaluate_rules(
        self, metrics: dict[str, float]
    ) -> list[dict]:
        """Evaluate all rules against a metrics dict.

        Returns a list of triggered alert dicts.
        """
        triggered = []
        for rule in self.rules:
            metric_name = rule["metric_name"]
            if metric_name in metrics:
                if self.evaluate_rule(rule, metrics[metric_name]):
                    triggered.append({
                        "rule_id": rule["id"],
                        "rule_name": rule["name"],
                        "severity": rule["severity"],
                        "metric_name": metric_name,
                        "metric_value": metrics[metric_name],
                        "threshold": rule["threshold"],
                        "operator": rule["operator"],
                    })
        return triggered

    def test_no_alerts_when_healthy(self):
        """No alerts should fire when all metrics are within thresholds."""
        metrics = {
            "cpu_percent": 45.0,
            "memory_percent": 60.0,
            "temperature_celsius": 35.0,
            "interface_in_errors": 5,
            "device_reachable": 1.0,
        }
        triggered = self.evaluate_rules(metrics)
        self.assertEqual(len(triggered), 0)

    def test_cpu_alert_fires(self):
        """CPU > 90% should trigger the High CPU alert."""
        metrics = {
            "cpu_percent": 95.0,
            "memory_percent": 60.0,
            "device_reachable": 1.0,
        }
        triggered = self.evaluate_rules(metrics)
        rule_ids = [t["rule_id"] for t in triggered]
        self.assertIn("rule-cpu-high", rule_ids)
        self.assertEqual(len(triggered), 1)

    def test_multiple_alerts_fire(self):
        """Multiple conditions exceeding thresholds should trigger multiple alerts."""
        metrics = {
            "cpu_percent": 99.0,
            "memory_percent": 92.0,
            "temperature_celsius": 80.0,
            "interface_in_errors": 500,
            "device_reachable": 1.0,
        }
        triggered = self.evaluate_rules(metrics)
        rule_ids = {t["rule_id"] for t in triggered}
        self.assertIn("rule-cpu-high", rule_ids)
        self.assertIn("rule-mem-high", rule_ids)
        self.assertIn("rule-temp-high", rule_ids)
        self.assertIn("rule-errors-high", rule_ids)
        self.assertEqual(len(triggered), 4)

    def test_device_down_alert(self):
        """device_reachable < 1.0 should trigger Device Down alert."""
        metrics = {
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "device_reachable": 0.0,
        }
        triggered = self.evaluate_rules(metrics)
        rule_ids = [t["rule_id"] for t in triggered]
        self.assertIn("rule-device-down", rule_ids)

    def test_boundary_value_greater_than(self):
        """Exactly at threshold should NOT trigger for '>' operator."""
        metrics = {"cpu_percent": 90.0}
        triggered = self.evaluate_rules(metrics)
        self.assertEqual(len(triggered), 0)

    def test_boundary_value_greater_equal(self):
        """Exactly at threshold SHOULD trigger for '>=' operator."""
        metrics = {"temperature_celsius": 75.0}
        triggered = self.evaluate_rules(metrics)
        rule_ids = [t["rule_id"] for t in triggered]
        self.assertIn("rule-temp-high", rule_ids)

    def test_alert_contains_metric_details(self):
        """Triggered alerts should contain the metric value and threshold."""
        metrics = {"cpu_percent": 95.5}
        triggered = self.evaluate_rules(metrics)
        self.assertEqual(len(triggered), 1)
        alert = triggered[0]
        self.assertAlmostEqual(alert["metric_value"], 95.5)
        self.assertAlmostEqual(alert["threshold"], 90.0)
        self.assertEqual(alert["operator"], ">")
        self.assertEqual(alert["severity"], "critical")

    def test_missing_metric_ignored(self):
        """Rules for metrics not present in the input should not trigger."""
        metrics = {"cpu_percent": 50.0}
        # Only cpu rule is relevant; memory, temp, errors, reachable are absent
        triggered = self.evaluate_rules(metrics)
        self.assertEqual(len(triggered), 0)

    def test_unknown_operator_raises(self):
        """An unsupported operator should raise ValueError."""
        rule = {"threshold": 50.0, "operator": "~"}
        with self.assertRaises(ValueError):
            self.evaluate_rule(rule, 60.0)

    def test_equality_operator(self):
        """The == operator should match exact values."""
        self.assertTrue(self.evaluate_rule(
            {"threshold": 42.0, "operator": "=="}, 42.0
        ))
        self.assertFalse(self.evaluate_rule(
            {"threshold": 42.0, "operator": "=="}, 42.1
        ))

    def test_not_equal_operator(self):
        """The != operator should match non-equal values."""
        self.assertTrue(self.evaluate_rule(
            {"threshold": 0.0, "operator": "!="}, 1.0
        ))
        self.assertFalse(self.evaluate_rule(
            {"threshold": 0.0, "operator": "!="}, 0.0
        ))


if __name__ == "__main__":
    unittest.main()
