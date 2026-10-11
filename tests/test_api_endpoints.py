"""
API Integration Tests for FastAPI REST & SSE Endpoints.
Verifies compliance with Enterprise Production Engineering standards.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from app.api.server import app
from app.engine.failsafes import failsafe_engine


@pytest.mark.asyncio
async def test_health_check_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/healthz")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert "uptime_seconds" in data
        assert "version" in data


@pytest.mark.asyncio
async def test_telemetry_live_and_history():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Live state
        res_live = await client.get("/api/telemetry/live")
        assert res_live.status_code == 200
        live_data = res_live.json()
        assert "sensors" in live_data
        assert "actuators" in live_data
        assert "alarms" in live_data

        # History with pagination
        res_hist = await client.get("/api/telemetry/history?limit=10&offset=0")
        assert res_hist.status_code == 200
        hist_data = res_hist.json()
        assert hist_data["status"] == "success"
        assert "data" in hist_data

        # System events with pagination
        res_events = await client.get("/api/telemetry/events?limit=10&offset=0")
        assert res_events.status_code == 200
        assert res_events.json()["status"] == "success"


@pytest.mark.asyncio
async def test_rules_crud_and_templates():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Templates
        res_tpl = await client.get("/api/rules/templates")
        assert res_tpl.status_code == 200
        templates = res_tpl.json()
        assert len(templates) >= 4
        assert any(t["id"] == "tpl-morning-drip" for t in templates)

        # Create rule
        rule_payload = {
            "name": "Integration Test Rule",
            "enabled": True,
            "combinator": "AND",
            "conditions": [
                {"source": "sensor.zone_1_moisture", "operator": "<", "value": 30.0}
            ],
            "actions": [
                {"target": "pump.zone_1", "duration_seconds": 10}
            ],
        }
        res_create = await client.post("/api/rules", json=rule_payload)
        assert res_create.status_code == 201
        created = res_create.json()
        rule_id = created["id"]
        assert created["name"] == "Integration Test Rule"

        # Toggle rule
        res_toggle = await client.post(f"/api/rules/{rule_id}/toggle")
        assert res_toggle.status_code == 200
        assert res_toggle.json()["enabled"] is False

        # Delete rule
        res_del = await client.delete(f"/api/rules/{rule_id}")
        assert res_del.status_code == 200
        assert res_del.json()["status"] == "success"


@pytest.mark.asyncio
async def test_control_pump_and_safety_rejection():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Normal allowed start with cooldown bypass for manual testing
        cmd = {
            "target": "pump.zone_1",
            "state": True,
            "duration_seconds": 5,
            "ignore_cooldown": True,
        }
        res = await client.post("/api/control/pump", json=cmd)
        assert res.status_code == 200
        assert res.json()["status"] == "success"

        # Stop pump
        stop_cmd = {
            "target": "pump.zone_1",
            "state": False,
        }
        res_stop = await client.post("/api/control/pump", json=stop_cmd)
        assert res_stop.status_code == 200

        # 2. Blocked start when dry-run failsafe is engaged
        failsafe_engine.dry_run_lockout_active = True
        res_blocked = await client.post("/api/control/pump", json=cmd)
        assert res_blocked.status_code == 400
        assert "Dry-Run" in res_blocked.json()["detail"] or "Safety Lockout" in res_blocked.json()["detail"]

        failsafe_engine.dry_run_lockout_active = False


@pytest.mark.asyncio
async def test_emergency_stop_and_voice():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Emergency stop
        res_stop = await client.post("/api/control/emergency-stop")
        assert res_stop.status_code == 200
        assert res_stop.json()["status"] == "success"

        # Voice alert test
        res_voice = await client.post("/api/control/speak", json={"text": "Hello Rock Pi S", "priority": 1})
        assert res_voice.status_code == 200
        assert res_voice.json()["enqueued"] == "Hello Rock Pi S"

        # Reset lockout
        res_reset = await client.post("/api/control/reset-lockout")
        assert res_reset.status_code == 200
