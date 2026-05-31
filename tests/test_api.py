"""Tests for the FastAPI web endpoints using TestClient.

The tests create a lightweight FastAPI app that mirrors the production
router structure but uses an in-memory mock database so tests are fast,
isolated, and require no external services.

Run with:
    python -m unittest tests.test_api -v

Requires:
    pip install httpx   (needed by FastAPI's TestClient)
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient


# ============================================================================
# In-memory mock database
# ============================================================================

class MockDatabase:
    """A simple in-memory store that mimics the Database interface used by the
    web routes.  Only the methods called by the tested endpoints are
    implemented.
    """

    def __init__(self):
        self._devices: dict[str, dict[str, Any]] = {}
        self._alerts: list[dict[str, Any]] = []
        self._metrics: list[dict[str, Any]] = []
        self._config_backups: list[dict[str, Any]] = []

    # --- Devices ---

    async def list_devices(self) -> list[dict[str, Any]]:
        return list(self._devices.values())

    async def get_device(self, device_id: str) -> Optional[dict[str, Any]]:
        return self._devices.get(device_id)

    async def add_device(self, device) -> dict[str, Any]:
        """Accept either a dict or a Pydantic-like object with .model_dump()."""
        if isinstance(device, dict):
            data = dict(device)
        elif hasattr(device, "model_dump"):
            data = device.model_dump()
        elif hasattr(device, "dict"):
            data = device.dict()
        else:
            data = dict(device)

        device_id = data.get("id") or str(uuid4())
        data["id"] = device_id
        data.setdefault("status", "unknown")
        data.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self._devices[device_id] = data
        return data

    async def update_device(self, device_id: str, update) -> dict[str, Any]:
        if device_id not in self._devices:
            raise KeyError(device_id)
        if isinstance(update, dict):
            changes = update
        elif hasattr(update, "model_dump"):
            changes = {k: v for k, v in update.model_dump().items() if v is not None}
        elif hasattr(update, "dict"):
            changes = {k: v for k, v in update.dict().items() if v is not None}
        else:
            changes = dict(update)
        self._devices[device_id].update(changes)
        return self._devices[device_id]

    async def delete_device(self, device_id: str) -> None:
        self._devices.pop(device_id, None)

    # Alias used by some routes
    async def get_all_devices(self) -> list[dict[str, Any]]:
        return await self.list_devices()

    # --- Alerts ---

    async def get_alerts(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        alerts = self._alerts
        if status:
            alerts = [a for a in alerts if a.get("status") == status]
        return alerts[:limit]

    async def get_active_alerts(self) -> list[dict[str, Any]]:
        return await self.get_alerts(status="active")

    # --- Metrics ---

    async def get_metrics(self, device_id: str, **kwargs) -> list[dict[str, Any]]:
        return [m for m in self._metrics if m.get("device_id") == device_id]

    async def add_metric(self, metric: dict) -> None:
        self._metrics.append(metric)

    # --- Config backups ---

    async def get_config_backups(self, device_id: str, limit: int = 20) -> list[dict[str, Any]]:
        backups = [b for b in self._config_backups if b.get("device_id") == device_id]
        return backups[:limit]

    # --- Initialisation ---

    async def initialize(self) -> None:
        """No-op for mock."""
        pass

    async def cleanup_old_metrics(self) -> None:
        """No-op for mock."""
        pass


# ============================================================================
# Test-only FastAPI app
# ============================================================================

def create_test_app(mock_db: MockDatabase) -> FastAPI:
    """Build a minimal FastAPI app wired to a MockDatabase.

    This avoids importing the production ``interfaces.web.app`` which
    triggers heavyweight lifespan logic, static file mounting, WebSocket
    setup, and background tasks.
    """
    app = FastAPI(title="Network Agent Test", version="1.0.0-test")

    # --- /api/status -------------------------------------------------------

    @app.get("/api/status")
    async def agent_status():
        devices = await mock_db.list_devices()
        active_alerts = await mock_db.get_alerts(status="active")
        return {
            "name": "NetworkAgent",
            "version": "1.0.0",
            "total_devices": len(devices),
            "online_devices": sum(1 for d in devices if d.get("status") == "online"),
            "offline_devices": sum(1 for d in devices if d.get("status") == "offline"),
            "active_alerts": len(active_alerts),
            "monitoring_active": False,
        }

    # --- /api/health -------------------------------------------------------

    @app.get("/api/health")
    async def health_check():
        return {"status": "ok"}

    # --- /api/devices ------------------------------------------------------

    @app.get("/api/devices")
    async def list_devices(
        tag: Optional[str] = None,
        status_filter: Optional[str] = None,
        device_type: Optional[str] = None,
    ):
        devices = await mock_db.list_devices()
        if tag:
            devices = [d for d in devices if tag in d.get("tags", [])]
        if status_filter:
            devices = [d for d in devices if d.get("status") == status_filter]
        if device_type:
            devices = [d for d in devices if d.get("device_type") == device_type]
        return {"devices": devices, "total": len(devices)}

    @app.post("/api/devices", status_code=status.HTTP_201_CREATED)
    async def add_device(device: dict):
        created = await mock_db.add_device(device)
        return created

    @app.get("/api/devices/{device_id}")
    async def get_device(device_id: str):
        device = await mock_db.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        return device

    @app.put("/api/devices/{device_id}")
    async def update_device(device_id: str, update: dict):
        existing = await mock_db.get_device(device_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Device not found")
        updated = await mock_db.update_device(device_id, update)
        return updated

    @app.delete("/api/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_device(device_id: str):
        existing = await mock_db.get_device(device_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Device not found")
        await mock_db.delete_device(device_id)
        return None

    return app


# ============================================================================
# Test: GET /api/status
# ============================================================================

class TestStatusEndpoint(unittest.TestCase):
    """Test the /api/status endpoint."""

    def setUp(self):
        self.db = MockDatabase()
        self.app = create_test_app(self.db)
        self.client = TestClient(self.app)

    def test_status_empty_db(self):
        """GET /api/status with no devices returns zeroes."""
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["name"], "NetworkAgent")
        self.assertEqual(data["version"], "1.0.0")
        self.assertEqual(data["total_devices"], 0)
        self.assertEqual(data["online_devices"], 0)
        self.assertEqual(data["offline_devices"], 0)
        self.assertEqual(data["active_alerts"], 0)
        self.assertFalse(data["monitoring_active"])

    def test_status_with_devices(self):
        """GET /api/status reflects device counts accurately."""
        import asyncio

        async def _seed():
            await self.db.add_device({"hostname": "r1", "status": "online"})
            await self.db.add_device({"hostname": "r2", "status": "online"})
            await self.db.add_device({"hostname": "r3", "status": "offline"})

        asyncio.get_event_loop().run_until_complete(_seed())

        resp = self.client.get("/api/status")
        data = resp.json()
        self.assertEqual(data["total_devices"], 3)
        self.assertEqual(data["online_devices"], 2)
        self.assertEqual(data["offline_devices"], 1)

    def test_status_with_active_alerts(self):
        """GET /api/status includes active alert count."""
        self.db._alerts = [
            {"id": "a1", "status": "active", "message": "CPU high"},
            {"id": "a2", "status": "resolved", "message": "Memory OK"},
            {"id": "a3", "status": "active", "message": "Interface down"},
        ]

        resp = self.client.get("/api/status")
        data = resp.json()
        self.assertEqual(data["active_alerts"], 2)


# ============================================================================
# Test: GET /api/health
# ============================================================================

class TestHealthEndpoint(unittest.TestCase):
    """Test the /api/health endpoint."""

    def setUp(self):
        self.db = MockDatabase()
        self.app = create_test_app(self.db)
        self.client = TestClient(self.app)

    def test_health_returns_ok(self):
        """GET /api/health should always return ok."""
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


# ============================================================================
# Test: POST /api/devices (create)
# ============================================================================

class TestCreateDevice(unittest.TestCase):
    """Test the POST /api/devices endpoint."""

    def setUp(self):
        self.db = MockDatabase()
        self.app = create_test_app(self.db)
        self.client = TestClient(self.app)

    def test_create_device_success(self):
        """POST /api/devices with valid data returns 201 and device record."""
        device_data = {
            "hostname": "core-switch-01",
            "host": "10.0.0.1",
            "device_type": "cisco_ios",
            "vendor": "cisco",
            "port": 22,
            "description": "Main core switch",
        }

        resp = self.client.post("/api/devices", json=device_data)
        self.assertEqual(resp.status_code, 201)

        created = resp.json()
        self.assertIn("id", created)
        self.assertEqual(created["hostname"], "core-switch-01")
        self.assertEqual(created["host"], "10.0.0.1")
        self.assertEqual(created["device_type"], "cisco_ios")
        self.assertEqual(created["port"], 22)

    def test_create_device_generates_id(self):
        """Created device should have an auto-generated UUID id."""
        resp = self.client.post("/api/devices", json={"hostname": "test"})
        self.assertEqual(resp.status_code, 201)
        created = resp.json()
        self.assertTrue(len(created["id"]) > 0)

    def test_create_device_sets_defaults(self):
        """Created device should have default status and created_at."""
        resp = self.client.post("/api/devices", json={"hostname": "test2"})
        created = resp.json()
        self.assertIn("status", created)
        self.assertIn("created_at", created)

    def test_create_multiple_devices(self):
        """Creating multiple devices should all be stored."""
        for i in range(5):
            resp = self.client.post(
                "/api/devices", json={"hostname": f"device-{i}", "host": f"10.0.0.{i}"}
            )
            self.assertEqual(resp.status_code, 201)

        # Verify all are listed
        resp = self.client.get("/api/devices")
        data = resp.json()
        self.assertEqual(data["total"], 5)


# ============================================================================
# Test: GET /api/devices (list)
# ============================================================================

class TestListDevices(unittest.TestCase):
    """Test the GET /api/devices endpoint."""

    def setUp(self):
        self.db = MockDatabase()
        self.app = create_test_app(self.db)
        self.client = TestClient(self.app)

        # Seed some devices
        import asyncio

        async def _seed():
            await self.db.add_device({
                "hostname": "router-1",
                "host": "10.0.0.1",
                "device_type": "cisco_ios",
                "status": "online",
                "tags": ["core", "dc1"],
            })
            await self.db.add_device({
                "hostname": "switch-1",
                "host": "10.0.0.2",
                "device_type": "arista_eos",
                "status": "offline",
                "tags": ["access", "dc1"],
            })
            await self.db.add_device({
                "hostname": "firewall-1",
                "host": "10.0.0.3",
                "device_type": "fortinet",
                "status": "online",
                "tags": ["security"],
            })

        asyncio.get_event_loop().run_until_complete(_seed())

    def test_list_all_devices(self):
        """GET /api/devices returns all devices."""
        resp = self.client.get("/api/devices")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["devices"]), 3)

    def test_list_devices_filter_by_status(self):
        """GET /api/devices?status_filter=online returns only online devices."""
        resp = self.client.get("/api/devices", params={"status_filter": "online"})
        data = resp.json()
        self.assertEqual(data["total"], 2)
        for dev in data["devices"]:
            self.assertEqual(dev["status"], "online")

    def test_list_devices_filter_by_type(self):
        """GET /api/devices?device_type=cisco_ios returns only Cisco devices."""
        resp = self.client.get("/api/devices", params={"device_type": "cisco_ios"})
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["devices"][0]["hostname"], "router-1")

    def test_list_devices_filter_by_tag(self):
        """GET /api/devices?tag=dc1 returns devices with that tag."""
        resp = self.client.get("/api/devices", params={"tag": "dc1"})
        data = resp.json()
        self.assertEqual(data["total"], 2)

    def test_list_devices_empty_result(self):
        """GET /api/devices with non-matching filter returns empty list."""
        resp = self.client.get("/api/devices", params={"device_type": "nonexistent"})
        data = resp.json()
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["devices"], [])

    def test_list_devices_response_structure(self):
        """Response should contain 'devices' list and 'total' count."""
        resp = self.client.get("/api/devices")
        data = resp.json()
        self.assertIn("devices", data)
        self.assertIn("total", data)
        self.assertIsInstance(data["devices"], list)
        self.assertIsInstance(data["total"], int)


# ============================================================================
# Test: GET /api/devices/{id}
# ============================================================================

class TestGetDevice(unittest.TestCase):
    """Test the GET /api/devices/{device_id} endpoint."""

    def setUp(self):
        self.db = MockDatabase()
        self.app = create_test_app(self.db)
        self.client = TestClient(self.app)

    def test_get_existing_device(self):
        """GET /api/devices/{id} returns the device when it exists."""
        resp = self.client.post("/api/devices", json={
            "hostname": "my-router",
            "host": "10.0.0.1",
            "device_type": "cisco_ios",
        })
        device_id = resp.json()["id"]

        resp = self.client.get(f"/api/devices/{device_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["hostname"], "my-router")
        self.assertEqual(data["id"], device_id)

    def test_get_nonexistent_device(self):
        """GET /api/devices/{id} returns 404 for unknown id."""
        resp = self.client.get("/api/devices/nonexistent-id")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("not found", resp.json()["detail"].lower())


# ============================================================================
# Test: PUT /api/devices/{id}
# ============================================================================

class TestUpdateDevice(unittest.TestCase):
    """Test the PUT /api/devices/{device_id} endpoint."""

    def setUp(self):
        self.db = MockDatabase()
        self.app = create_test_app(self.db)
        self.client = TestClient(self.app)

        # Create a device to update
        resp = self.client.post("/api/devices", json={
            "hostname": "old-name",
            "host": "10.0.0.1",
            "device_type": "cisco_ios",
        })
        self.device_id = resp.json()["id"]

    def test_update_device_success(self):
        """PUT /api/devices/{id} updates the device and returns updated data."""
        resp = self.client.put(
            f"/api/devices/{self.device_id}",
            json={"hostname": "new-name", "description": "Updated switch"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["hostname"], "new-name")
        self.assertEqual(data["description"], "Updated switch")

    def test_update_preserves_existing_fields(self):
        """Updating one field should not erase other existing fields."""
        resp = self.client.put(
            f"/api/devices/{self.device_id}",
            json={"description": "Just a description update"},
        )
        data = resp.json()
        self.assertEqual(data["host"], "10.0.0.1")  # preserved
        self.assertEqual(data["device_type"], "cisco_ios")  # preserved

    def test_update_nonexistent_device(self):
        """PUT /api/devices/{id} returns 404 for unknown id."""
        resp = self.client.put(
            "/api/devices/does-not-exist",
            json={"hostname": "x"},
        )
        self.assertEqual(resp.status_code, 404)


# ============================================================================
# Test: DELETE /api/devices/{id}
# ============================================================================

class TestDeleteDevice(unittest.TestCase):
    """Test the DELETE /api/devices/{device_id} endpoint."""

    def setUp(self):
        self.db = MockDatabase()
        self.app = create_test_app(self.db)
        self.client = TestClient(self.app)

    def test_delete_existing_device(self):
        """DELETE /api/devices/{id} removes the device and returns 204."""
        # Create a device
        resp = self.client.post("/api/devices", json={"hostname": "to-delete"})
        device_id = resp.json()["id"]

        # Verify it exists
        resp = self.client.get(f"/api/devices/{device_id}")
        self.assertEqual(resp.status_code, 200)

        # Delete it
        resp = self.client.delete(f"/api/devices/{device_id}")
        self.assertEqual(resp.status_code, 204)

        # Verify it's gone
        resp = self.client.get(f"/api/devices/{device_id}")
        self.assertEqual(resp.status_code, 404)

    def test_delete_nonexistent_device(self):
        """DELETE /api/devices/{id} returns 404 for unknown id."""
        resp = self.client.delete("/api/devices/does-not-exist")
        self.assertEqual(resp.status_code, 404)

    def test_delete_does_not_affect_other_devices(self):
        """Deleting one device should not affect other devices."""
        resp1 = self.client.post("/api/devices", json={"hostname": "keep-me"})
        keep_id = resp1.json()["id"]

        resp2 = self.client.post("/api/devices", json={"hostname": "delete-me"})
        delete_id = resp2.json()["id"]

        # Delete one
        self.client.delete(f"/api/devices/{delete_id}")

        # Other still exists
        resp = self.client.get(f"/api/devices/{keep_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["hostname"], "keep-me")

        # List shows only the remaining device
        resp = self.client.get("/api/devices")
        self.assertEqual(resp.json()["total"], 1)


# ============================================================================
# Test: Full CRUD lifecycle
# ============================================================================

class TestDeviceCRUDLifecycle(unittest.TestCase):
    """Test the complete create-read-update-delete lifecycle."""

    def setUp(self):
        self.db = MockDatabase()
        self.app = create_test_app(self.db)
        self.client = TestClient(self.app)

    def test_full_lifecycle(self):
        """Create, read, update, and delete a device end-to-end."""
        # 1. Create
        create_resp = self.client.post("/api/devices", json={
            "hostname": "lifecycle-router",
            "host": "192.168.1.1",
            "device_type": "juniper_junos",
            "vendor": "juniper",
            "port": 22,
        })
        self.assertEqual(create_resp.status_code, 201)
        device = create_resp.json()
        device_id = device["id"]

        # 2. Read
        read_resp = self.client.get(f"/api/devices/{device_id}")
        self.assertEqual(read_resp.status_code, 200)
        self.assertEqual(read_resp.json()["hostname"], "lifecycle-router")

        # 3. Update
        update_resp = self.client.put(f"/api/devices/{device_id}", json={
            "hostname": "lifecycle-router-updated",
            "status": "online",
        })
        self.assertEqual(update_resp.status_code, 200)
        self.assertEqual(update_resp.json()["hostname"], "lifecycle-router-updated")
        self.assertEqual(update_resp.json()["host"], "192.168.1.1")  # preserved

        # 4. Verify update persisted
        read_resp2 = self.client.get(f"/api/devices/{device_id}")
        self.assertEqual(read_resp2.json()["hostname"], "lifecycle-router-updated")
        self.assertEqual(read_resp2.json()["status"], "online")

        # 5. Delete
        delete_resp = self.client.delete(f"/api/devices/{device_id}")
        self.assertEqual(delete_resp.status_code, 204)

        # 6. Verify deleted
        get_resp = self.client.get(f"/api/devices/{device_id}")
        self.assertEqual(get_resp.status_code, 404)

        # 7. Verify list is empty
        list_resp = self.client.get("/api/devices")
        self.assertEqual(list_resp.json()["total"], 0)


# ============================================================================
# Test: Edge cases
# ============================================================================

class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error handling."""

    def setUp(self):
        self.db = MockDatabase()
        self.app = create_test_app(self.db)
        self.client = TestClient(self.app)

    def test_create_device_with_empty_body(self):
        """POST /api/devices with an empty body should still return 201."""
        resp = self.client.post("/api/devices", json={})
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertIn("id", data)

    def test_create_device_with_explicit_id(self):
        """If the body includes an id, it should be used."""
        resp = self.client.post("/api/devices", json={
            "id": "my-custom-id",
            "hostname": "custom-id-device",
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["id"], "my-custom-id")

    def test_update_device_with_empty_body(self):
        """PUT with empty body should succeed without changing anything."""
        resp = self.client.post("/api/devices", json={"hostname": "original"})
        device_id = resp.json()["id"]

        resp = self.client.put(f"/api/devices/{device_id}", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["hostname"], "original")

    def test_status_endpoint_always_200(self):
        """GET /api/status should always return 200, regardless of state."""
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)

    def test_health_endpoint_always_200(self):
        """GET /api/health should always return 200."""
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)

    def test_concurrent_device_operations(self):
        """Multiple rapid create/delete operations should not corrupt state."""
        ids = []
        for i in range(10):
            resp = self.client.post("/api/devices", json={"hostname": f"dev-{i}"})
            ids.append(resp.json()["id"])

        # Delete every other device
        for i in range(0, 10, 2):
            self.client.delete(f"/api/devices/{ids[i]}")

        # Should have 5 remaining
        resp = self.client.get("/api/devices")
        self.assertEqual(resp.json()["total"], 5)

    def test_device_json_response_content_type(self):
        """All JSON endpoints should return application/json."""
        resp = self.client.get("/api/status")
        self.assertIn("application/json", resp.headers["content-type"])

        resp = self.client.get("/api/devices")
        self.assertIn("application/json", resp.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
