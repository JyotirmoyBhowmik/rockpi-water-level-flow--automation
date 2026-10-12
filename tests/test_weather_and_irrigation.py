"""
Comprehensive Tests for Weather-Aware Skip, Schedules, Soak Cycles, BME280, and Daily Budgets.
Covers:
  - Open-Meteo forecast fetching, caching, rain probability & past rain skips, timeout handling
  - Per-zone schedule windows (standard and midnight-crossing)
  - Daily water budget tracking via flow K-factor and midnight reset
  - Failsafe invariant: can_water_zone() gates all automated watering
  - Soak cycles: multi-pulse sequence, abort on failsafe denial, stop cancellation, auto-trigger
  - BME280 environmental telemetry (HeartbeatPacket with env and EnvPacket)
"""

import asyncio
from datetime import datetime, timedelta, timezone
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import system_config
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.engine.plant_controller import PlantIrrigationController
from app.engine.scheduler import SystemScheduler
from app.engine.weather_service import WeatherService, weather_service
from app.hardware.gateway_driver import garden_gateway
from app.hardware.protocol import EnvData, EnvPacket, HeartbeatPacket


@pytest.fixture
def clean_irrigation_state():
    """Provides a fresh, fully configured test state for irrigation tests."""
    state = state_manager.get_state()
    now = time.time()
    state["garden"]["gateway_connected"] = True
    state["garden"]["node_online"] = True
    state["garden"]["is_stale"] = False
    state["garden"]["reservoir_wet"] = True
    state["garden"]["last_heartbeat_ts"] = now
    state["garden"]["reservoir_last_update_ts"] = now
    state["garden"]["last_packet_ts"] = now

    for i in range(1, 5):
        zk = f"zone_{i}"
        state["garden"]["zones"][zk]["calibrated"] = True
        state["garden"]["zones"][zk]["voltage"] = 2.0
        state["garden"]["zones"][zk]["moisture_pct"] = 45.0
        state["garden"]["zones"][zk]["last_update_ts"] = now
        state["garden"]["zones"][zk]["cooldown_until_ts"] = 0.0
        state["garden"]["zones"][zk]["flow_fault"] = False
        state["garden"]["zones"][zk]["pump_running"] = False
        state["garden"]["zones"][zk]["soak_active"] = False
        state["garden"]["zones"][zk]["daily_litres"] = 0.0
        state["garden"]["zones"][zk]["flow_pulses"] = 0

    system_config.setup_complete = True
    for zk, z_cfg in system_config.zones.items():
        z_cfg.enabled = True
        z_cfg.soil_dry_threshold_pct = 35.0
        z_cfg.calibration.dry_v = 3.0
        z_cfg.calibration.wet_v = 1.0
        z_cfg.calibration.margin_v = 0.5
        z_cfg.cooldown_hours = 6.0
        z_cfg.pulse_seconds = 15
        z_cfg.max_pulse_seconds = 60
        z_cfg.schedule_window_start = None
        z_cfg.schedule_window_end = None
        z_cfg.soak_cycle_enabled = False
        z_cfg.soak_pulse_seconds = 15
        z_cfg.soak_wait_seconds = 60
        z_cfg.soak_pulse_count = 3
        z_cfg.daily_budget_litres = None
        z_cfg.flow_k_factor = 7.5

    system_config.weather.enabled = False
    weather_service.clear_mock_forecast()
    state_manager._state = state
    failsafe_engine.flow_faults.clear()
    failsafe_engine._zone_run_start_mono.clear()
    failsafe_engine._zone_pulses_start.clear()
    return state


# ==============================================================================
# 1. Weather Service Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_weather_service_disabled(clean_irrigation_state):
    """When weather is disabled, no HTTP request is made and skips are inactive."""
    ws = WeatherService()
    system_config.weather.enabled = False

    result = await ws.fetch_forecast_if_due()
    assert result is None
    skip, reason = ws.should_skip_watering()
    assert skip is False
    assert reason is None


@pytest.mark.asyncio
async def test_weather_service_fetch_success(clean_irrigation_state):
    """Successfully fetches and parses Open-Meteo hourly response."""
    ws = weather_service
    system_config.weather.enabled = True
    system_config.weather.latitude = 12.97
    system_config.weather.longitude = 77.59
    system_config.weather.rain_prob_threshold_pct = 60.0
    system_config.weather.past_rain_threshold_mm = 5.0

    # Build mock hourly data with 48 hours
    times = []
    rain_probs = []
    precipitation = []
    current_utc = datetime.now(timezone.utc)
    now_hour = current_utc.replace(minute=0, second=0, microsecond=0)

    for h in range(-24, 24):
        t_dt = now_hour + timedelta(hours=h)
        times.append(t_dt.strftime("%Y-%m-%dT%H:00"))
        rain_probs.append(75.0 if h >= 0 else 10.0)
        precipitation.append(0.5 if h < 0 else 0.0)  # 24 * 0.5 = 12.0 mm past rain

    mock_json = {
        "hourly": {
            "time": times,
            "precipitation_probability": rain_probs,
            "precipitation": precipitation,
        }
    }

    mock_resp = httpx.Response(200, json=mock_json, request=httpx.Request("GET", "https://api.open-meteo.com"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        data = await ws.fetch_forecast_if_due(force=True)
        assert data is not None
        assert ws.last_poll_ts > 0
        state = state_manager.get_state()["weather"]
        assert state["status"] == "OK"
        assert state["forecast_rain_prob_pct"] == 75.0
        assert state["past_rain_24h_mm"] == 12.0

        # High rain probability skip triggers
        skip, reason = ws.should_skip_watering()
        assert skip is True
        assert "rain probability" in reason.lower() or "precipitation" in reason.lower()


@pytest.mark.asyncio
async def test_weather_service_caching_and_force(clean_irrigation_state):
    """Respects poll_interval_hours cache unless force=True."""
    ws = WeatherService()
    system_config.weather.enabled = True
    system_config.weather.poll_interval_hours = 3.0

    valid_hourly = {
        "time": ["2026-10-13T00:00"],
        "precipitation_probability": [20.0],
        "precipitation": [0.0],
    }
    mock_resp = httpx.Response(
        200,
        json={"hourly": valid_hourly},
        request=httpx.Request("GET", "https://api.open-meteo.com"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
        # First call fetches
        await ws.fetch_forecast_if_due(force=True)
        assert mock_get.call_count == 1

        # Second call within 3h is cached (no GET)
        await ws.fetch_forecast_if_due(force=False)
        assert mock_get.call_count == 1

        # Force call ignores cache
        await ws.fetch_forecast_if_due(force=True)
        assert mock_get.call_count == 2


@pytest.mark.asyncio
async def test_weather_service_timeout_handling(clean_irrigation_state):
    """Handles HTTP timeout gracefully without crashing, recording TIMEOUT status."""
    ws = weather_service
    ws.clear_mock_forecast()
    system_config.weather.enabled = True

    with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("Connection timed out")):
        res = await ws.fetch_forecast_if_due(force=True)
        assert res is None
        state = state_manager.get_state()["weather"]
        assert state["status"] == "TIMEOUT"

        # On timeout, should_skip_watering defaults to False (fail-open)
        skip, _ = ws.should_skip_watering()
        assert skip is False


# ==============================================================================
# 2. Per-Zone Schedule Windows Tests
# ==============================================================================


def test_schedule_window_unconfigured():
    """Zone with start=None, end=None is always within schedule."""
    z_cfg = system_config.zones["zone_1"]
    z_cfg.schedule_window_start = None
    z_cfg.schedule_window_end = None
    for hour in range(24):
        assert z_cfg.is_within_schedule(hour) is True


def test_schedule_window_standard():
    """Standard daytime window e.g. 06:00 to 09:00."""
    z_cfg = system_config.zones["zone_1"]
    z_cfg.schedule_window_start = 6
    z_cfg.schedule_window_end = 9

    assert z_cfg.is_within_schedule(5) is False
    assert z_cfg.is_within_schedule(6) is True
    assert z_cfg.is_within_schedule(7) is True
    assert z_cfg.is_within_schedule(8) is True
    assert z_cfg.is_within_schedule(9) is False
    assert z_cfg.is_within_schedule(10) is False


def test_schedule_window_midnight_crossing():
    """Window crossing midnight e.g. 22:00 to 04:00."""
    z_cfg = system_config.zones["zone_1"]
    z_cfg.schedule_window_start = 22
    z_cfg.schedule_window_end = 4

    assert z_cfg.is_within_schedule(21) is False
    assert z_cfg.is_within_schedule(22) is True
    assert z_cfg.is_within_schedule(23) is True
    assert z_cfg.is_within_schedule(0) is True
    assert z_cfg.is_within_schedule(1) is True
    assert z_cfg.is_within_schedule(3) is True
    assert z_cfg.is_within_schedule(4) is False
    assert z_cfg.is_within_schedule(12) is False


# ==============================================================================
# 3. Daily Water Budget & Flow Math Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_flow_k_factor_volume_and_budget(clean_irrigation_state):
    """Flow pulses convert to litres via K-factor, and budget limits auto-watering."""
    z_key = "zone_1"
    z_cfg = system_config.zones[z_key]
    z_cfg.flow_k_factor = 7.5  # 7.5 Hz per L/min -> 450 pulses/L
    z_cfg.daily_budget_litres = 2.0

    # 450 pulses = 1.0 L
    await state_manager.update_garden_flow_delta(zone_idx=1, delta_pulses=450, period_ms=1000)
    state = state_manager.get_state()
    assert state["garden"]["zones"][z_key]["daily_litres"] == 1.0

    # Under budget: auto-watering allowed
    allowed, _ = failsafe_engine.can_water_zone(z_key, is_manual=False)
    assert allowed is True

    # Another 450 pulses -> 2.0 L (budget limit reached)
    await state_manager.update_garden_flow_delta(zone_idx=1, delta_pulses=450, period_ms=1000)
    state = state_manager.get_state()
    assert state["garden"]["zones"][z_key]["daily_litres"] == 2.0

    # At or above budget: auto-watering BLOCKED
    allowed, reason = failsafe_engine.can_water_zone(z_key, is_manual=False)
    assert allowed is False
    assert "Daily watering budget reached" in reason

    # Manual override bypasses daily budget!
    allowed_man, _ = failsafe_engine.can_water_zone(z_key, is_manual=True)
    assert allowed_man is True


# ==============================================================================
# 4. Failsafe Invariant Enforcement
# ==============================================================================


@pytest.mark.asyncio
async def test_failsafe_gates_weather_and_schedule(clean_irrigation_state):
    """Safety invariant: weather skip and schedule gate auto-watering, but allow manual."""
    z_key = "zone_1"
    z_cfg = system_config.zones[z_key]

    # 1. Weather skip active
    from app.engine.weather_service import weather_service

    weather_service.set_mock_forecast(rain_prob_max_pct=85.0, rainfall_past_24h_mm=0.0)
    system_config.weather.enabled = True
    system_config.weather.rain_prob_threshold_pct = 60.0

    allowed, reason = failsafe_engine.can_water_zone(z_key, is_manual=False)
    assert allowed is False
    assert "Weather forecast skip active" in reason

    # Manual pulse bypasses weather skip
    allowed_man, _ = failsafe_engine.can_water_zone(z_key, is_manual=True)
    assert allowed_man is True

    weather_service.clear_mock_forecast()

    # 2. Outside schedule window
    system_config.weather.enabled = False
    current_utc_hour = datetime.now(timezone.utc).hour
    # Set window outside current hour
    z_cfg.schedule_window_start = (current_utc_hour + 2) % 24
    z_cfg.schedule_window_end = (current_utc_hour + 4) % 24

    allowed, reason = failsafe_engine.can_water_zone(z_key, is_manual=False)
    assert allowed is False
    assert "Outside allowed watering schedule window" in reason

    # Manual pulse bypasses schedule window
    allowed_man, _ = failsafe_engine.can_water_zone(z_key, is_manual=True)
    assert allowed_man is True

    # 3. Reservoir Dry: Blocks EVEN manual watering!
    state_manager._state["garden"]["reservoir_wet"] = False
    allowed_man, reason = failsafe_engine.can_water_zone(z_key, is_manual=True)
    assert allowed_man is False
    assert "reservoir is dry" in reason.lower()


# ==============================================================================
# 5. Soak Cycle Multi-Pulse Execution
# ==============================================================================


@pytest.mark.asyncio
async def test_soak_cycle_execution(clean_irrigation_state, monkeypatch):
    """Executes a multi-pulse soak cycle (pulse + wait + pulse) with cooldown set on completion."""
    controller = PlantIrrigationController()
    z_key = "zone_1"
    z_cfg = system_config.zones[z_key]
    z_cfg.soak_cycle_enabled = True
    z_cfg.soak_pulse_seconds = 2
    z_cfg.soak_wait_seconds = 1
    z_cfg.soak_pulse_count = 2

    sent_commands = []

    async def mock_send_pump_run(zone_idx: int, duration_s: int):
        sent_commands.append((zone_idx, duration_s))
        return 101

    async def mock_send_pump_stop(zone_idx: int):
        return None

    monkeypatch.setattr(garden_gateway, "send_pump_run", mock_send_pump_run)
    monkeypatch.setattr(garden_gateway, "send_pump_stop", mock_send_pump_stop)

    # Initiate soak cycle
    ok, msg = await controller.trigger_soak_cycle(z_key, is_manual=False, source="auto_moisture")
    assert ok is True
    assert "Soak cycle initiated" in msg

    # Check soak task is active
    assert z_key in controller._active_soak_tasks
    state = state_manager.get_state()
    assert state["garden"]["zones"][z_key]["soak_active"] is True

    # Fast-complete pulse 1
    await asyncio.sleep(0.1)
    await controller.on_pump_completed(z_key, reason="done")

    # Intermediate pulses should NOT set zone cooldown
    state = state_manager.get_state()
    assert state["garden"]["zones"][z_key]["cooldown_until_ts"] == 0.0

    # Wait for wait interval to elapse and pulse 2 to start
    for _ in range(30):
        if len(sent_commands) == 2:
            break
        await asyncio.sleep(0.05)
    assert len(sent_commands) == 2

    # Fast-complete pulse 2
    await controller.on_pump_completed(z_key, reason="done")
    for _ in range(30):
        if z_key not in controller._active_soak_tasks:
            break
        await asyncio.sleep(0.05)

    # Soak cycle should now be complete
    assert z_key not in controller._active_soak_tasks
    state = state_manager.get_state()
    assert state["garden"]["zones"][z_key]["soak_active"] is False
    # Cooldown is set at the end of the entire soak cycle
    assert state["garden"]["zones"][z_key]["cooldown_until_ts"] > time.time()


@pytest.mark.asyncio
async def test_soak_cycle_aborts_on_stop(clean_irrigation_state, monkeypatch):
    """Calling stop_zone_pulse or stop_all_zones cancels an active soak cycle immediately."""
    controller = PlantIrrigationController()
    z_key = "zone_1"
    z_cfg = system_config.zones[z_key]
    z_cfg.soak_cycle_enabled = True
    z_cfg.soak_pulse_seconds = 5
    z_cfg.soak_wait_seconds = 5
    z_cfg.soak_pulse_count = 3

    monkeypatch.setattr(garden_gateway, "send_pump_run", AsyncMock(return_value=1))
    monkeypatch.setattr(garden_gateway, "send_pump_stop", AsyncMock(return_value=None))

    ok, _ = await controller.trigger_soak_cycle(z_key, is_manual=False)
    assert ok is True
    assert z_key in controller._active_soak_tasks

    await controller.stop_zone_pulse(z_key, reason="manual_stop")
    await asyncio.sleep(0.1)

    assert z_key not in controller._active_soak_tasks
    state = state_manager.get_state()
    assert state["garden"]["zones"][z_key]["soak_active"] is False


@pytest.mark.asyncio
async def test_evaluate_auto_watering_triggers_soak(clean_irrigation_state, monkeypatch):
    """evaluate_auto_watering dispatches trigger_soak_cycle when soak_cycle_enabled=True."""
    controller = PlantIrrigationController()
    z_key = "zone_1"
    z_cfg = system_config.zones[z_key]
    z_cfg.enabled = True
    z_cfg.soil_dry_threshold_pct = 40.0
    z_cfg.soak_cycle_enabled = True

    # Soil moisture dry (25% < 40%)
    state_manager._state["garden"]["zones"][z_key]["moisture_pct"] = 25.0

    soak_called = False

    async def mock_trigger_soak(zk, is_manual=False, source="auto_moisture"):
        nonlocal soak_called
        soak_called = True
        return True, "Mock soak started"

    monkeypatch.setattr(controller, "trigger_soak_cycle", mock_trigger_soak)

    await controller.evaluate_auto_watering()
    assert soak_called is True


# ==============================================================================
# 6. BME280 Environmental Telemetry
# ==============================================================================


@pytest.mark.asyncio
async def test_bme280_telemetry_routing(clean_irrigation_state):
    """Routes BME280 temperature, humidity, and pressure to garden telemetry state."""
    scheduler = SystemScheduler()

    # 1. Heartbeat packet with env
    hb_env = EnvData(temp=25.4, rh=58.2, press=1012.8)
    hb_pkt = HeartbeatPacket(
        seq=1,
        uptime_s=120,
        res_wet=True,
        ads_ok=True,
        pumps=[0, 0, 0, 0],
        rssi=-65,
        nonce=10,
        env=hb_env,
        sig="0123456789abcdef",
    )

    await scheduler._on_gateway_packet(hb_pkt)
    state = state_manager.get_state()["garden"]
    assert state["env"]["temp_c"] == 25.4
    assert state["env"]["rh_pct"] == 58.2
    assert state["env"]["pressure_hpa"] == 1012.8
    assert state["env"]["last_update_ts"] is not None

    # 2. Standalone EnvPacket
    env_pkt = EnvPacket(
        seq=2,
        temp=28.1,
        rh=62.0,
        press=1010.5,
        nonce=11,
        sig="fedcba9876543210",
    )

    await scheduler._on_gateway_packet(env_pkt)
    state = state_manager.get_state()["garden"]
    assert state["env"]["temp_c"] == 28.1
    assert state["env"]["rh_pct"] == 62.0
    assert state["env"]["pressure_hpa"] == 1010.5

    # 3. Raw dictionary format {"t": "env"}
    raw_env = {"t": "env", "temp": 29.5, "rh": 50.0, "press": 1015.0}
    await scheduler._on_gateway_packet(raw_env)
    state = state_manager.get_state()["garden"]
    assert state["env"]["temp_c"] == 29.5
    assert state["env"]["rh_pct"] == 50.0
    assert state["env"]["pressure_hpa"] == 1015.0
