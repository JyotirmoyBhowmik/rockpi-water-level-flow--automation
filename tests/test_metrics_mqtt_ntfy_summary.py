"""
Comprehensive Test Suite for:
  1. Prometheus /metrics endpoint (hand-written text format, Bearer token security)
  2. ntfy.sh notification client (bounded queue, priority mapping, rate limit retries)
  3. MQTT publisher with Home Assistant discovery & failsafe-gated remote commands
  4. Daily summary notification (24h operational recap, SQLite aggregation, dispatch)
  5. Historical telemetry API & UI charting integration
"""

import asyncio
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.api.metrics import generate_prometheus_metrics, _format_metric
from app.api.server import app
from app.alerts.ntfy_client import NtfyNotifier, PRIORITY_MAP, TAGS_MAP
from app.alerts.mqtt_client import MQTTClientService
from app.config import system_config
from app.core.security import security_manager
from app.core.state_manager import state_manager
from app.engine.daily_summary import daily_summary_service, format_duration_human
from app.engine.failsafes import failsafe_engine


# ==============================================================================
# 1. PROMETHEUS METRICS TESTS
# ==============================================================================

def test_format_metric_helper():
    """Validates low-level metric line formatting with Prometheus 0.0.4 syntax."""
    res = _format_metric("test_gauge", "gauge", "A test gauge", 42.5, labels={"zone": "zone_1"})
    assert "# HELP test_gauge A test gauge\n" in res
    assert "# TYPE test_gauge gauge\n" in res
    assert 'test_gauge{zone="zone_1"} 42.5\n' in res

    # Boolean formatting
    res_bool = _format_metric("test_bool", "gauge", "Boolean flag", True)
    assert "test_bool 1\n" in res_bool

    # None handling (should return empty string)
    assert _format_metric("test_none", "gauge", "None val", None) == ""


def test_generate_prometheus_metrics_content():
    """Validates complete hand-written Prometheus metrics output contains expected metrics."""
    metrics_text = generate_prometheus_metrics()
    assert isinstance(metrics_text, str)
    assert metrics_text.endswith("\n")

    # Essential system metrics
    assert "smart_water_uptime_seconds" in metrics_text
    assert "smart_water_system_clock_valid" in metrics_text
    assert "smart_water_hardware_ready" in metrics_text
    assert "smart_water_setup_complete" in metrics_text

    # Motor and head-end metrics
    assert "smart_water_motor_running" in metrics_text
    assert "smart_water_motor_current_run_seconds" in metrics_text
    assert "smart_water_head_end_configured" in metrics_text
    assert "smart_water_head_end_stale" in metrics_text

    # Garden & Zone metrics
    assert "smart_water_garden_gateway_connected" in metrics_text
    assert "smart_water_garden_node_online" in metrics_text
    assert "smart_water_zone_pump_running" in metrics_text
    assert 'zone="zone_1"' in metrics_text

    # Power, level, and weather metrics
    assert "smart_water_power_meter_available" in metrics_text
    assert "smart_water_level_sensor_available" in metrics_text
    assert "smart_water_weather_skip_active" in metrics_text
    assert "smart_water_active_alarms_total" in metrics_text


def test_metrics_endpoint_unauthorized():
    """Asserts /metrics endpoint strictly requires Bearer token authentication."""
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"
    data = resp.json()
    assert data["error_code"] == "UNAUTHORIZED"


def test_metrics_endpoint_authorized():
    """Asserts /metrics endpoint returns Prometheus 0.0.4 text with valid Bearer token."""
    client = TestClient(app)
    token = security_manager.rotate_token()

    resp = client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "version=0.0.4" in resp.headers["content-type"]
    assert "# HELP smart_water_uptime_seconds" in resp.text


# ==============================================================================
# 2. NTFY.SH ALERTING CLIENT TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_ntfy_notifier_disabled_by_default():
    """Asserts ntfy notifier does not connect when disabled in config."""
    notifier = NtfyNotifier()
    system_config.ntfy.enabled = False
    await notifier.start()
    assert not notifier._running
    assert notifier._queue is None
    await notifier.stop()


@pytest.mark.asyncio
async def test_ntfy_priority_and_tag_mapping():
    """Asserts critical alarms map to urgent priority and appropriate tags."""
    assert PRIORITY_MAP["LEAK_DETECTED"] == 5
    assert PRIORITY_MAP["WIRING_FAULT"] == 5
    assert PRIORITY_MAP["SLOW_FILL_RATE"] == 4
    assert PRIORITY_MAP["DAILY_SUMMARY"] == 2

    assert "rotating_light" in TAGS_MAP["LEAK_DETECTED"]
    assert "zap" in TAGS_MAP["CONTACTOR_MISMATCH"]
    assert "chart_with_upwards_trend" in TAGS_MAP["DAILY_SUMMARY"]


@pytest.mark.asyncio
async def test_ntfy_delivery_worker_success(monkeypatch):
    """Asserts ntfy delivery worker sends HTTP POST with proper headers and body."""
    notifier = NtfyNotifier()
    system_config.ntfy.enabled = True
    system_config.ntfy.server_url = "https://ntfy.sh"
    system_config.ntfy.topic = "test-smart-water-topic"

    posted_requests = []

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            self.is_closed = False

        async def post(self, url, content=None, headers=None, **kwargs):
            posted_requests.append({"url": url, "content": content.decode("utf-8"), "headers": headers})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            return mock_resp

        async def aclose(self):
            self.is_closed = True

    notifier._client = MockAsyncClient()  # type: ignore[assignment]
    await notifier.start()

    await notifier.send_alert("LEAK_DETECTED", "Water leak on floor sensor IN4")
    # Allow background delivery loop to process queue
    await asyncio.sleep(0.1)
    await notifier.stop()

    assert len(posted_requests) == 1
    req = posted_requests[0]
    assert req["url"] == "https://ntfy.sh/test-smart-water-topic"
    assert "Water leak on floor sensor IN4" in req["content"]
    assert req["headers"]["Priority"] == "5"
    assert "Title" in req["headers"]
    assert "LEAK_DETECTED" in req["headers"]["Title"]
    assert "rotating_light" in req["headers"]["Tags"]


@pytest.mark.asyncio
async def test_ntfy_rate_limiting_and_retry(monkeypatch):
    """Asserts ntfy delivery worker handles 429 rate limit with backoff retry."""
    notifier = NtfyNotifier()
    system_config.ntfy.enabled = True
    system_config.ntfy.topic = "test-topic-retry"

    attempts = 0

    class MockRetryClient:
        def __init__(self, *args, **kwargs):
            self.is_closed = False

        async def post(self, url, content=None, headers=None, **kwargs):
            nonlocal attempts
            attempts += 1
            mock_resp = MagicMock()
            if attempts == 1:
                mock_resp.status_code = 429
                mock_resp.headers = {"Retry-After": "0.05"}
                return mock_resp
            mock_resp.status_code = 200
            return mock_resp

        async def aclose(self):
            self.is_closed = True

    notifier._client = MockRetryClient()  # type: ignore[assignment]
    await notifier.start()

    await notifier.send_alert("SLOW_FILL_RATE", "Filling slower than 5 L/min")
    await asyncio.sleep(0.2)
    await notifier.stop()

    assert attempts >= 2


# ==============================================================================
# 3. MQTT PUBLISHER & DISCOVERY & COMMAND TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_mqtt_disabled_by_default():
    """Asserts MQTT client does not connect when disabled in config."""
    client_service = MQTTClientService()
    system_config.mqtt.enabled = False
    await client_service.start()
    assert not client_service._running
    assert client_service._client is None
    await client_service.stop()


@pytest.mark.asyncio
async def test_mqtt_ha_discovery_publishing():
    """Asserts Home Assistant MQTT Discovery entities are properly formatted and published."""
    client_service = MQTTClientService()
    system_config.mqtt.enabled = True
    system_config.mqtt.ha_discovery_enabled = True

    published_messages = []

    mock_paho = MagicMock()
    def mock_publish(topic, payload=None, qos=0, retain=False):
        published_messages.append({"topic": topic, "payload": payload, "retain": retain})
        return MagicMock()
    mock_paho.publish = mock_publish

    client_service._client = mock_paho
    client_service._connected = True

    client_service._publish_ha_discovery()

    assert len(published_messages) > 10
    # Check binary sensor discovery
    motor_disco = next(m for m in published_messages if "binary_sensor/smart_water/motor_running" in m["topic"])
    assert motor_disco["retain"] is True
    data = json.loads(motor_disco["payload"])
    assert data["name"] == "Motor Running"
    assert data["device_class"] == "running"
    assert data["device"]["model"] == "Radxa ROCK Pi S"

    # Check switch discovery
    switch_disco = next(m for m in published_messages if "switch/smart_water/motor_switch" in m["topic"])
    switch_data = json.loads(switch_disco["payload"])
    assert switch_data["command_topic"] == "smart_water/cmd/motor/set"


@pytest.mark.asyncio
async def test_mqtt_unauthorized_command_rejected():
    """Asserts MQTT command without valid Bearer token is rejected when auth required."""
    client_service = MQTTClientService()
    system_config.mqtt.enabled = True
    system_config.mqtt.command_auth_required = True

    published_responses = []
    mock_paho = MagicMock()
    mock_paho.publish = lambda topic, payload=None, qos=0, retain=False: published_responses.append(
        {"topic": topic, "payload": json.loads(payload)}
    )
    client_service._client = mock_paho
    client_service._connected = True

    # Attempt to start motor without token
    await client_service._handle_command("smart_water/cmd/motor/set", json.dumps({"action": "START"}))

    assert len(published_responses) == 1
    resp = published_responses[0]["payload"]
    assert resp["success"] is False
    assert resp["error"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_mqtt_command_failsafe_blocked():
    """Asserts MQTT command is rejected if failsafe_engine blocks it."""
    client_service = MQTTClientService()
    system_config.mqtt.enabled = True
    system_config.mqtt.command_auth_required = True

    token = security_manager.rotate_token()

    published_responses = []
    mock_paho = MagicMock()
    mock_paho.publish = lambda topic, payload=None, qos=0, retain=False: published_responses.append(
        {"topic": topic, "payload": json.loads(payload)}
    )
    client_service._client = mock_paho
    client_service._connected = True

    # Mock failsafe_engine.can_start_motor to return False (e.g. Tank full)
    with patch.object(failsafe_engine, "can_start_motor", return_value=(False, "Tank full float reached")):
        await client_service._handle_command(
            "smart_water/cmd/motor/set",
            json.dumps({"action": "START", "token": token}),
        )

    assert len(published_responses) == 1
    resp = published_responses[0]["payload"]
    assert resp["success"] is False
    assert resp["error"] == "FAILSAFE_BLOCKED"
    assert "Tank full" in resp["message"]


@pytest.mark.asyncio
async def test_mqtt_estop_command():
    """Asserts MQTT estop command immediately triggers hardware and gateway output kill."""
    client_service = MQTTClientService()
    system_config.mqtt.enabled = True
    system_config.mqtt.command_auth_required = False

    published_responses = []
    mock_paho = MagicMock()
    mock_paho.publish = lambda topic, payload=None, qos=0, retain=False: published_responses.append(
        {"topic": topic, "payload": json.loads(payload)}
    )
    client_service._client = mock_paho
    client_service._connected = True

    with patch("app.hardware.gpio_driver.head_end_gpio.force_all_outputs_off", new_callable=AsyncMock) as mock_gpio_kill, \
         patch("app.hardware.gateway_driver.garden_gateway.send_stop_all", new_callable=AsyncMock) as mock_gw_kill:
        await client_service._handle_command("smart_water/cmd/estop", json.dumps({"action": "STOP"}))

        assert mock_gpio_kill.called
        assert mock_gw_kill.called

    assert len(published_responses) == 1
    assert published_responses[0]["payload"]["success"] is True


# ==============================================================================
# 4. DAILY SUMMARY SERVICE TESTS
# ==============================================================================

def test_format_duration_human():
    """Validates human-readable duration formatting."""
    assert format_duration_human(0) == "0s"
    assert format_duration_human(45) == "45s"
    assert format_duration_human(125) == "2m 5s"
    assert format_duration_human(3665) == "1h 1m 5s"


@pytest.mark.asyncio
async def test_daily_summary_aggregation_and_message():
    """Asserts daily summary compiles database records into clear text recap."""
    await state_manager.init_db()

    # Log mock run
    await state_manager.log_run("motor", datetime.now(timezone.utc).isoformat(), 90, "tank_replenished", 0.25)
    await state_manager.log_run("zone_1", datetime.now(timezone.utc).isoformat(), 15, "moisture_threshold", 0.0)

    summary_data = await daily_summary_service.get_summary_data(hours=24)
    assert summary_data["motor"]["starts"] >= 1
    assert summary_data["motor"]["runtime_s"] >= 90
    assert summary_data["zones"]["zone_1"]["runs"] >= 1

    formatted_msg = daily_summary_service.format_summary_message(summary_data)
    assert "📊 [SMART WATER DAILY RECAP]" in formatted_msg
    assert "Motor Operations:" in formatted_msg
    assert "Irrigation Per Zone:" in formatted_msg
    assert "Safety Alarms & Trips:" in formatted_msg


@pytest.mark.asyncio
async def test_daily_summary_dispatch():
    """Asserts dispatch_daily_summary forwards report to Telegram and ntfy."""
    with patch("app.alerts.telegram_client.telegram_notifier.send_alert", new_callable=AsyncMock) as mock_tg, \
         patch("app.alerts.ntfy_client.ntfy_notifier.send_alert", new_callable=AsyncMock) as mock_ntfy:
        await daily_summary_service.dispatch_daily_summary(hours=24)
        assert mock_tg.called
        assert mock_ntfy.called


def test_api_summary_daily_endpoints():
    """Asserts /api/summary/daily and /api/summary/daily/send endpoints work via REST."""
    client = TestClient(app)
    token = security_manager.rotate_token()
    headers = {"Authorization": f"Bearer {token}"}

    # 1. GET /api/summary/daily
    resp_get = client.get("/api/summary/daily?hours=24", headers=headers)
    assert resp_get.status_code == 200
    data = resp_get.json()
    assert data["status"] == "success"
    assert "motor" in data["data"]
    assert "message" in data

    # 2. POST /api/summary/daily/send
    with patch("app.alerts.telegram_client.telegram_notifier.send_alert", new_callable=AsyncMock), \
         patch("app.alerts.ntfy_client.ntfy_notifier.send_alert", new_callable=AsyncMock):
        resp_post = client.post("/api/summary/daily/send?hours=24", headers=headers)
        assert resp_post.status_code == 200
        assert resp_post.json()["status"] == "success"


# ==============================================================================
# 5. UI & HISTORICAL CHARTS INTEGRATION TESTS
# ==============================================================================

def test_api_history_pagination():
    """Asserts /api/history returns readings list under RAM limit."""
    client = TestClient(app)
    token = security_manager.rotate_token()
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/api/history?limit=50", headers=headers)
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "success"
    assert isinstance(res["data"], list)


def test_ui_contains_chart_containers_and_script():
    """Asserts index.html renders historical charts tab and container IDs."""
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "tab-history" in html
    assert "chart-soil-container" in html
    assert "chart-motor-container" in html
    assert "chart-level-container" in html
    assert "summary-motor-runtime" in html
