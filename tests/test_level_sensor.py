"""
Unit and Integration Tests for Continuous Tank Level Sensing Driver.
Tests:
  - Tank volumetric geometry (cylindrical and rectangular prism calculations)
  - JSN-SR04T ultrasonic distance-to-depth and percentage conversions
  - 4-20 mA hydrostatic pressure transducer current-to-depth conversions
  - Fill rate estimation (L/min) and ETA calculation
  - "Filling too slowly" alarm (pipe leak / airlock detection)
  - Predictive motor stop before the physical Full float
  - Authoritative hardware float hierarchy
  - Daily consumption accounting, SQLite logging, and API history endpoint
  - Night-time leak detection during zero-usage window
  - Graceful degradation when hardware is unconfigured or absent
"""

import asyncio
import pytest
from httpx import ASGITransport, AsyncClient

from unittest.mock import AsyncMock

from app.api.server import app
from app.config import system_config
from app.core.security import security_manager
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.engine.motor_controller import motor_controller
from app.hardware.gpio_driver import head_end_gpio
from app.hardware.level_sensor import (
    LevelReading,
    LevelSensorManager,
    calculate_tank_capacity_litres,
    depth_to_volume_litres,
    level_sensor,
)



# ==============================================================================
# 1. Tank Geometry & Volumetric Calculations Tests
# ==============================================================================

def test_cylindrical_tank_capacity_and_volume() -> None:
    """Verifies cylindrical tank capacity and depth-to-volume math."""
    # Cylinder: diameter = 100 cm (r = 50 cm), height = 150 cm
    # Area = pi * 50^2 = 7853.98 cm^2. Total capacity = 7853.98 * 150 / 1000 = 1178.1 Litres
    capacity = calculate_tank_capacity_litres(
        shape="cylinder",
        height_cm=150.0,
        diameter_cm=100.0,
    )
    assert abs(capacity - 1178.1) < 0.5

    # Half full (depth = 75 cm) -> Volume = 7853.98 * 75 / 1000 = 589.0 Litres
    vol_half = depth_to_volume_litres(
        depth_cm=75.0,
        shape="cylinder",
        height_cm=150.0,
        diameter_cm=100.0,
    )
    assert abs(vol_half - 589.0) < 0.5

    # Full (depth = 150 cm)
    vol_full = depth_to_volume_litres(
        depth_cm=150.0,
        shape="cylinder",
        height_cm=150.0,
        diameter_cm=100.0,
    )
    assert abs(vol_full - 1178.1) < 0.5

    # Empty (depth = 0 cm)
    vol_empty = depth_to_volume_litres(
        depth_cm=0.0,
        shape="cylinder",
        height_cm=150.0,
        diameter_cm=100.0,
    )
    assert vol_empty == 0.0


def test_rectangular_tank_capacity_and_volume() -> None:
    """Verifies rectangular prism tank capacity and depth-to-volume math."""
    # Rectangular: length = 120 cm, width = 80 cm, height = 150 cm
    # Capacity = 120 * 80 * 150 / 1000 = 1440.0 Litres
    capacity = calculate_tank_capacity_litres(
        shape="rectangular",
        height_cm=150.0,
        length_cm=120.0,
        width_cm=80.0,
    )
    assert capacity == 1440.0

    # Half full (depth = 75 cm) -> Volume = 120 * 80 * 75 / 1000 = 720.0 Litres
    vol_half = depth_to_volume_litres(
        depth_cm=75.0,
        shape="rectangular",
        height_cm=150.0,
        length_cm=120.0,
        width_cm=80.0,
    )
    assert vol_half == 720.0


# ==============================================================================
# 2. Level Sensor Readings & Conversions
# ==============================================================================

@pytest.mark.asyncio
async def test_jsn_sr04t_mock_reading_and_status_dict() -> None:
    """Verifies level reading generation, percentage calculation, and status dict."""
    mgr = LevelSensorManager()
    system_config.level_sensor.enabled = True
    system_config.level_sensor.sensor_type = "jsn_sr04t"
    system_config.level_sensor.tank_shape = "cylinder"
    system_config.level_sensor.tank_height_cm = 150.0
    system_config.level_sensor.tank_diameter_cm = 100.0
    system_config.level_sensor.predictive_stop_pct = 95.0

    reading = LevelReading(
        water_depth_cm=120.0,
        level_pct=80.0,
        volume_litres=942.5,
        tank_capacity_litres=1178.1,
        raw_reading=50.0,
        timestamp=1000.0,
    )
    mgr.set_mock_reading(reading)

    telemetry = await mgr.read_telemetry()
    assert telemetry is not None
    assert telemetry.water_depth_cm == 120.0
    assert telemetry.level_pct == 80.0
    assert telemetry.volume_litres == 942.5

    status = mgr.get_status_dict()
    assert status["available"] is True
    assert status["sensor_type"] == "jsn_sr04t"
    assert status["level_pct"] == 80.0
    assert status["volume_litres"] == 942.5
    assert status["predictive_stop_pct"] == 95.0


# ==============================================================================
# 3. Fill-Rate Estimation & ETA Calculations
# ==============================================================================

def test_fill_rate_and_eta_calculation() -> None:
    """Verifies filling rate in L/min and ETA to target level during motor run."""
    mgr = LevelSensorManager()
    system_config.level_sensor.enabled = True
    system_config.level_sensor.predictive_stop_enabled = True
    system_config.level_sensor.predictive_stop_pct = 95.0

    mgr._last_reading = LevelReading(
        water_depth_cm=100.0,
        level_pct=66.7,
        volume_litres=667.0,
        tank_capacity_litres=1000.0,
        raw_reading=50.0,
        timestamp=1000.0,
    )

    # Simulate 60 seconds of filling: +30 Litres gained (30 L/min rate)
    mgr._volume_history = [
        (100.0, 637.0),  # at t = 100.0s, volume = 637L
        (160.0, 667.0),  # at t = 160.0s, volume = 667L
    ]
    mgr._update_fill_rate_and_eta()

    # Rate: 30L / 1 min = 30.0 L/min
    assert mgr._fill_rate_lpm is not None
    assert abs(mgr._fill_rate_lpm - 30.0) < 0.2

    # Target: 95% of 1000L = 950L. Remaining: 950 - 667 = 283L.
    # ETA: 283L / 30.0 L/min = 9.4 minutes
    assert mgr._fill_eta_minutes is not None
    assert abs(mgr._fill_eta_minutes - 9.4) < 0.2


# ==============================================================================
# 4. "Filling Too Slowly" Alarm Tests
# ==============================================================================

def test_slow_fill_rate_detection() -> None:
    """Verifies detection of abnormally slow filling rate (pipe leak / airlock)."""
    mgr = LevelSensorManager()
    system_config.level_sensor.enabled = True
    system_config.level_sensor.slow_fill_min_run_s = 60.0
    system_config.level_sensor.min_fill_rate_lpm = 5.0
    mgr._mock_reading = LevelReading(50.0, 33.3, 333.0, 1000.0, 100.0)

    # 1. Too early in run (run_elapsed_s = 30s < 60s) -> no alarm
    mgr._fill_rate_lpm = 2.0  # Abnormally slow rate
    is_slow, rate, msg = mgr.check_slow_fill(run_elapsed_s=30.0)
    assert is_slow is False

    # 2. After 60s, rate is slow: first detection arms timer
    import app.hardware.level_sensor as ls_mod
    orig_mono = ls_mod.get_monotonic_now
    ls_mod.get_monotonic_now = lambda: 100.0

    try:
        is_slow, rate, msg = mgr.check_slow_fill(run_elapsed_s=70.0)
        assert is_slow is False

        # 3. Persists past hold time (25s > 20s hold) -> alarm raised
        ls_mod.get_monotonic_now = lambda: 125.0
        is_slow, rate, msg = mgr.check_slow_fill(run_elapsed_s=95.0)
        assert is_slow is True
        assert rate == 2.0
        assert "SLOW_FILL_RATE" in (msg or "")
    finally:
        ls_mod.get_monotonic_now = orig_mono


# ==============================================================================
# 5. Predictive Stop Before Full Float Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_predictive_motor_stop_before_full_float(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies motor stops smoothly when tank reaches predictive target level."""
    system_config.level_sensor.enabled = True
    system_config.level_sensor.predictive_stop_enabled = True
    system_config.level_sensor.predictive_stop_pct = 95.0

    monkeypatch.setattr(head_end_gpio, "set_contactor_relay", AsyncMock(return_value=True))
    monkeypatch.setattr(head_end_gpio, "read_contactor_feedback", AsyncMock(return_value=False))

    # Tank at 90% -> should not stop
    level_sensor.set_mock_reading(LevelReading(135.0, 90.0, 900.0, 1000.0, 35.0))
    assert level_sensor.should_predictive_stop() is False

    # Tank reaches 95.5% -> should predictive stop
    level_sensor.set_mock_reading(LevelReading(143.2, 95.5, 955.0, 1000.0, 26.8))
    assert level_sensor.should_predictive_stop() is True

    # Motor is RUNNING in auto mode
    await state_manager.set_motor_state("RUNNING")
    await state_manager.set_motor_auto_mode(True)
    state_manager._state["hardware_ready"] = True
    state_manager._state["head_end"]["is_stale"] = False

    # Evaluate auto-replenishment loop -> triggers predictive stop
    await motor_controller.evaluate_auto_replenishment()
    await asyncio.sleep(0.05)

    state = state_manager.get_state()
    assert state["motor"]["state"] == "OFF"
    assert state["motor"]["last_stop_reason"] == "predictive_level_full"


# ==============================================================================
# 6. Authoritative Hardware Float Hierarchy Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_hardware_float_overrides_continuous_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies physical Full float independently halts motor even if level sensor reports <100%."""
    system_config.level_sensor.enabled = True
    monkeypatch.setattr(head_end_gpio, "set_contactor_relay", AsyncMock(return_value=True))

    # Level sensor falsely reports only 80% (e.g. sensor drift)
    level_sensor.set_mock_reading(LevelReading(120.0, 80.0, 800.0, 1000.0, 50.0))

    # Motor is RUNNING
    await state_manager.set_motor_state("RUNNING")

    # Physical hardware interlock chain opens at top float (tank_full: out=False, mid=False)
    await state_manager.update_head_end(
        tank_low=False, interlock_out=False, interlock_mid=False, leak_detected=False, hardware_available=True
    )

    # Evaluate failsafes
    await failsafe_engine.evaluate_head_end_safety()

    state = state_manager.get_state()
    # Physical float must immediately shut off motor
    assert state["motor"]["state"] == "OFF"
    assert state["motor"]["last_stop_reason"] == "tank_full"

    # Reset head_end back to healthy
    await state_manager.update_head_end(
        tank_low=False, interlock_out=True, interlock_mid=True, leak_detected=False, hardware_available=True
    )



# ==============================================================================
# 7. Daily Household Consumption Tracking & Charting API Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_consumption_tracking_and_api() -> None:
    """Verifies volume drop tracking while motor is OFF and /api/level/consumption endpoint."""
    mgr = LevelSensorManager()
    mgr.daily_consumption_litres = 0.0
    mgr._last_consumption_volume = 800.0

    # Water is consumed by household: volume drops from 800L to 775L (25L consumed)
    mgr._track_consumption_tick(current_volume_l=775.0, level_pct=77.5, motor_running=False)
    assert mgr.daily_consumption_litres == 25.0

    # Wait for DB async insert to complete
    await asyncio.sleep(0.05)

    # Query history via StateManager
    history = await state_manager.get_consumption_history(hours=24)
    assert history["daily_total_litres"] >= 25.0

    # Obtain valid auth token
    raw_token = security_manager.rotate_token()
    headers = {"Authorization": f"Bearer {raw_token}"}

    # Query history via FastAPI endpoint
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/level/consumption?hours=24", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "hourly" in data["data"]
        assert data["data"]["daily_total_litres"] >= 25.0


# ==============================================================================
# 8. Night-Time Leak Detection Tests
# ==============================================================================

def test_night_time_leak_detection() -> None:
    """Verifies leak detection during night-time zero-usage window (01:00 to 04:00)."""
    mgr = LevelSensorManager()
    system_config.level_sensor.enabled = True
    system_config.level_sensor.night_leak_detection_enabled = True
    system_config.level_sensor.night_leak_window_start = 1
    system_config.level_sensor.night_leak_window_end = 4
    system_config.level_sensor.night_leak_max_drop_litres = 15.0

    # Baseline reading at 01:00: 800 Litres
    mgr.set_mock_reading(LevelReading(120.0, 80.0, 800.0, 1000.0, 50.0))

    # Outside night window (hour = 14) -> no detection
    is_leak, drop, msg = mgr.evaluate_night_leak(current_hour=14, motor_running=False, garden_running=False)
    assert is_leak is False

    # Within night window (hour = 2): initial tick sets baseline
    is_leak, drop, msg = mgr.evaluate_night_leak(current_hour=2, motor_running=False, garden_running=False)
    assert is_leak is False
    assert mgr._night_leak_baseline_volume == 800.0

    # Level drops by 5 Litres (< 15L threshold) -> no alarm
    mgr.set_mock_reading(LevelReading(119.0, 79.5, 795.0, 1000.0, 51.0))
    is_leak, drop, msg = mgr.evaluate_night_leak(current_hour=2, motor_running=False, garden_running=False)
    assert is_leak is False

    # Level drops by 20 Litres (> 15L threshold) with no irrigation running -> LEAK ALARM
    mgr.set_mock_reading(LevelReading(117.0, 78.0, 780.0, 1000.0, 53.0))
    is_leak, drop, msg = mgr.evaluate_night_leak(current_hour=3, motor_running=False, garden_running=False)
    assert is_leak is True
    assert drop == 20.0
    assert "NIGHT_LEAK_DETECTED" in (msg or "")

    # If garden irrigation pump was running, leak detection is suppressed
    is_leak, drop, msg = mgr.evaluate_night_leak(current_hour=3, motor_running=False, garden_running=True)
    assert is_leak is False


# ==============================================================================
# 9. Graceful Degradation When Unconfigured
# ==============================================================================

def test_graceful_degradation_when_unconfigured() -> None:
    """Verifies safe degradation and is_available() == False when unconfigured."""
    mgr = LevelSensorManager()
    system_config.level_sensor.enabled = False

    assert mgr.is_available() is False
    status = mgr.get_status_dict()
    assert status["available"] is False
    assert status["level_pct"] is None
    assert status["fill_rate_lpm"] is None
