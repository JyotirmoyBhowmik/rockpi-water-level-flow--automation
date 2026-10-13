"""
API Integration Tests for FastAPI REST & SSE Endpoints.
Verifies endpoints with Bearer token authentication and pure logic handling.
"""

from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient
from app.api.server import create_app
from app.config import system_config
from app.core.security import security_manager
from app.engine.failsafes import failsafe_engine


@pytest.fixture
def auth_credentials(tmp_path: Path):
    """Sets up an isolated token auth file and returns raw token and auth headers."""
    auth_file = tmp_path / "auth.json"
    security_manager.auth_file_path = auth_file
    system_config.security.auth_token_file = str(auth_file)
    system_config.setup_complete = True

    raw_token, _ = security_manager.get_or_create_token()
    if not raw_token:
        raw_token = security_manager.rotate_token()

    headers = {"Authorization": f"Bearer {raw_token}"}
    return raw_token, headers


@pytest.mark.asyncio
async def test_health_check_endpoint():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/healthz")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert "uptime_seconds" in data
        assert "version" in data


@pytest.mark.asyncio
async def test_unauthorized_access(auth_credentials):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Without auth header -> 401
        res = await client.get("/api/events")
        assert res.status_code == 401
        assert res.json()["error_code"] == "UNAUTHORIZED"

        # With bad token -> 401
        res_bad = await client.get("/api/events", headers={"Authorization": "Bearer bad-token"})
        assert res_bad.status_code == 401


@pytest.mark.asyncio
async def test_telemetry_live_empty_initial_state(auth_credentials):
    _, headers = auth_credentials
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/telemetry/live", headers=headers)
        assert res.status_code == 200
        data = res.json()
        # Initial unconfigured state must not fabricate values
        assert data["head_end"]["tank_low_float"] is None
        assert data["head_end"]["interlock_out"] is None
        assert data["head_end"]["interlock_mid"] is None
        assert data["garden"]["reservoir_wet"] is None
        assert data["motor"]["state"] in ("OFF", "SAFE-NO-HARDWARE")


@pytest.mark.asyncio
async def test_zero_seeded_templates(auth_credentials):
    _, headers = auth_credentials
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Part 0.1 Ground Rule: tables start empty, zero seeded templates
        res = await client.get("/api/rules/templates", headers=headers)
        assert res.status_code == 200
        templates = res.json()
        assert len(templates) == 0


@pytest.mark.asyncio
async def test_rules_crud_lifecycle(auth_credentials):
    _, headers = auth_credentials
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rule_payload = {
            "name": "Integration Test Rule",
            "enabled": True,
            "combinator": "AND",
            "conditions": [
                {"source": "zone_1.moisture", "operator": "<", "value": 30.0}
            ],
            "actions": [
                {"target": "pump.zone_1", "duration_seconds": 10}
            ],
        }
        # Create rule
        res_create = await client.post("/api/rules", json=rule_payload, headers=headers)
        assert res_create.status_code == 201
        created = res_create.json()
        rule_id = created["id"]
        assert created["name"] == "Integration Test Rule"

        # List rules
        res_list = await client.get("/api/rules", headers=headers)
        assert res_list.status_code == 200
        rules = res_list.json()
        assert any(r["id"] == rule_id for r in rules)

        # Toggle rule
        res_toggle = await client.post(f"/api/rules/{rule_id}/toggle", headers=headers)
        assert res_toggle.status_code == 200
        assert res_toggle.json()["enabled"] is False

        # Delete rule
        res_del = await client.delete(f"/api/rules/{rule_id}", headers=headers)
        assert res_del.status_code == 200
        assert res_del.json()["status"] == "success"


@pytest.mark.asyncio
async def test_emergency_stop_and_reset_endpoints(auth_credentials):
    _, headers = auth_credentials
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Emergency stop dispatches cleanly
        res_stop = await client.post("/api/control/emergency-stop", headers=headers)
        assert res_stop.status_code == 200
        assert res_stop.json()["status"] == "success"

        # Motor lockout reset endpoint
        failsafe_engine.manual_motor_lockout = True
        failsafe_engine.motor_cooldown_until = 0.0  # Cooldown expired
        res_reset = await client.post("/api/motor/reset", headers=headers)
        assert res_reset.status_code == 200
        assert res_reset.json()["status"] == "success"
        assert failsafe_engine.manual_motor_lockout is False


@pytest.mark.asyncio
async def test_config_endpoints_and_secret_preservation(auth_credentials, tmp_path):
    _, headers = auth_credentials
    from pydantic import SecretStr

    # Configure sample secrets
    system_config.telegram.bot_token = "123456:SECRET_BOT_TOKEN"
    system_config.garden_gateway.shared_key = SecretStr("abcdef0123456789abcdef0123456789")
    system_config.ntfy.access_token = SecretStr("tk_secret_access_token")
    system_config.mqtt.password = SecretStr("mqtt_secret_password")

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. GET /api/config redacts all secrets
        res = await client.get("/api/config", headers=headers)
        assert res.status_code == 200
        cfg_data = res.json()
        assert cfg_data["telegram"]["bot_token"] == "********"
        assert cfg_data["garden_gateway"]["shared_key"] == "********"
        assert cfg_data["ntfy"]["access_token"] == "********"
        assert cfg_data["mqtt"]["password"] == "********"

        # 2. Modify non-secret fields and post back with masked asterisks
        cfg_data["motor"]["max_runtime_s"] = 850
        cfg_data["head_end"]["debounce_ms"] = 150
        res_post = await client.post("/api/config", json=cfg_data, headers=headers)
        assert res_post.status_code == 200
        assert res_post.json()["status"] == "success"

        # 3. Confirm secrets were NOT overwritten with asterisks
        assert system_config.telegram.bot_token == "123456:SECRET_BOT_TOKEN"
        assert system_config.garden_gateway.shared_key.get_secret_value() == "abcdef0123456789abcdef0123456789"
        assert system_config.ntfy.access_token.get_secret_value() == "tk_secret_access_token"
        assert system_config.mqtt.password.get_secret_value() == "mqtt_secret_password"
        assert system_config.motor.max_runtime_s == 850
        assert system_config.head_end.debounce_ms == 150

