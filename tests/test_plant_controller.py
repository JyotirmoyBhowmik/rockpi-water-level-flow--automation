"""
Unit and Integration Tests for PlantIrrigationController.
Covers:
  - Per-zone completion watcher handle tracking and cancellation
  - Idempotent on_pump_completed (second call is a safe no-op)
  - GatewayCommandFailedException handling, failsafe rollback, alarm raising
  - Source-specific zone cooldown lockout (auto_moisture and rule:* only)
  - 60s pulse duration hard cap and 2-pump bus concurrency limit
"""

import time
import pytest

from app.config import system_config
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.engine.plant_controller import PlantIrrigationController
from app.hardware.gateway_driver import GatewayCommandFailedException


@pytest.fixture
def clean_state():
    # Setup valid mock state allowing zone watering
    state = state_manager.get_state()
    state["garden"]["gateway_connected"] = True
    state["garden"]["node_online"] = True
    state["garden"]["is_stale"] = False
    state["garden"]["reservoir_wet"] = True
    now = time.time()
    state["garden"]["last_heartbeat_ts"] = now
    state["garden"]["reservoir_last_update_ts"] = now

    for i in range(1, 5):
        zk = f"zone_{i}"
        state["garden"]["zones"][zk]["calibrated"] = True
        state["garden"]["zones"][zk]["voltage"] = 2.0
        state["garden"]["zones"][zk]["last_update_ts"] = now
        state["garden"]["zones"][zk]["cooldown_until_ts"] = 0.0
        state["garden"]["zones"][zk]["flow_fault"] = False
        state["garden"]["zones"][zk]["pump_running"] = False

    system_config.setup_complete = True
    # Update zone config calibration
    for zk, z_cfg in system_config.zones.items():
        z_cfg.enabled = True
        z_cfg.calibration.dry_v = 3.0
        z_cfg.calibration.wet_v = 1.0
        z_cfg.calibration.margin_v = 0.5
        z_cfg.cooldown_hours = 6.0
        z_cfg.max_pulse_seconds = 60

    state_manager._state = state
    failsafe_engine.flow_faults.clear()
    failsafe_engine._zone_run_start_mono.clear()
    failsafe_engine._zone_pulses_start.clear()
    return state


@pytest.mark.asyncio
async def test_completion_watcher_cancellation_on_stop(clean_state, monkeypatch):
    controller = PlantIrrigationController()

    async def mock_send_pump_run(idx, sec):
        return "cmd-123"

    async def mock_send_pump_stop(idx):
        return "cmd-stop"

    async def mock_send_stop_all():
        return "cmd-stop-all"

    monkeypatch.setattr("app.hardware.gateway_driver.garden_gateway.send_pump_run", mock_send_pump_run)
    monkeypatch.setattr("app.hardware.gateway_driver.garden_gateway.send_pump_stop", mock_send_pump_stop)
    monkeypatch.setattr("app.hardware.gateway_driver.garden_gateway.send_stop_all", mock_send_stop_all)
    monkeypatch.setattr("app.engine.failsafes.is_system_clock_valid", lambda: True)

    # 1. Trigger pulse on zone_1
    ok, msg = await controller.trigger_zone_pulse("zone_1", duration_s=10, is_manual=True)
    assert ok is True
    assert "zone_1" in controller._completion_tasks
    task = controller._completion_tasks["zone_1"]
    assert not task.done()

    # 2. stop_zone_pulse("zone_1") must cancel the watcher task
    ok_stop, _ = await controller.stop_zone_pulse("zone_1")
    assert ok_stop is True
    assert "zone_1" not in controller._completion_tasks
    assert task.cancelled() or task.done()

    # 3. Test stop_all_zones cancels all active watchers
    ok1, _ = await controller.trigger_zone_pulse("zone_1", duration_s=10, is_manual=True)
    ok2, _ = await controller.trigger_zone_pulse("zone_2", duration_s=10, is_manual=True)
    assert ok1 is True and ok2 is True
    task1 = controller._completion_tasks["zone_1"]
    task2 = controller._completion_tasks["zone_2"]

    await controller.stop_all_zones()
    assert len(controller._completion_tasks) == 0
    assert task1.cancelled() or task1.done()
    assert task2.cancelled() or task2.done()


@pytest.mark.asyncio
async def test_on_pump_completed_idempotent(clean_state, monkeypatch):
    controller = PlantIrrigationController()

    async def mock_send_pump_run(idx, sec):
        return "cmd-idemp"

    monkeypatch.setattr("app.hardware.gateway_driver.garden_gateway.send_pump_run", mock_send_pump_run)
    monkeypatch.setattr("app.engine.failsafes.is_system_clock_valid", lambda: True)

    ok, _ = await controller.trigger_zone_pulse("zone_1", duration_s=10, is_manual=True, source="auto_moisture")
    assert ok is True
    assert "zone_1" in controller._active_runs

    log_run_calls = []

    async def mock_log_run(*args, **kwargs):
        log_run_calls.append(args)

    monkeypatch.setattr(state_manager, "log_run", mock_log_run)

    # First completion call
    await controller.on_pump_completed("zone_1", reason="done")
    assert len(log_run_calls) == 1
    assert "zone_1" not in controller._active_runs

    # Second completion call for same zone -> must be a no-op!
    await controller.on_pump_completed("zone_1", reason="done")
    assert len(log_run_calls) == 1  # No duplicate log_run or bus event


@pytest.mark.asyncio
async def test_gateway_command_failed_rollback_and_alarm(clean_state, monkeypatch):
    controller = PlantIrrigationController()

    async def mock_failing_send_pump_run(idx, sec):
        raise GatewayCommandFailedException("Serial write timeout: ACK not received")

    monkeypatch.setattr("app.hardware.gateway_driver.garden_gateway.send_pump_run", mock_failing_send_pump_run)
    monkeypatch.setattr("app.engine.failsafes.is_system_clock_valid", lambda: True)

    # Initial state verification
    assert "zone_1" not in failsafe_engine._zone_run_start_mono

    # Trigger pulse - gateway command fails
    ok, err_msg = await controller.trigger_zone_pulse("zone_1", duration_s=15, is_manual=True)

    # Assertions
    assert ok is False
    assert "Gateway command failed" in err_msg or "Serial write timeout" in err_msg
    # Failsafe start tracking rolled back
    assert "zone_1" not in failsafe_engine._zone_run_start_mono
    assert "zone_1" not in failsafe_engine._zone_pulses_start
    # Active run was not created
    assert "zone_1" not in controller._active_runs
    # Alarm was raised
    assert "pump_fail_zone_1" in state_manager._state["alarms"]


@pytest.mark.asyncio
async def test_selective_zone_cooldown_by_source(clean_state, monkeypatch):
    controller = PlantIrrigationController()

    async def mock_send_pump_run(idx, sec):
        return f"cmd-{idx}"

    monkeypatch.setattr("app.hardware.gateway_driver.garden_gateway.send_pump_run", mock_send_pump_run)
    monkeypatch.setattr("app.engine.failsafes.is_system_clock_valid", lambda: True)

    cooldowns_set = {}

    async def mock_set_zone_cooldown(zk, lockout_until):
        cooldowns_set[zk] = lockout_until

    monkeypatch.setattr(state_manager, "set_zone_cooldown", mock_set_zone_cooldown)

    # 1. Manual run -> MUST NOT set cooldown
    await controller.trigger_zone_pulse("zone_1", duration_s=5, is_manual=True, source="manual")
    await controller.on_pump_completed("zone_1", reason="done")
    assert "zone_1" not in cooldowns_set

    # 2. Calibration run -> MUST NOT set cooldown
    await controller.trigger_zone_pulse("zone_2", duration_s=5, is_manual=True, source="calibration")
    await controller.on_pump_completed("zone_2", reason="done")
    assert "zone_2" not in cooldowns_set

    # 3. Emergency stop run -> MUST NOT set cooldown
    await controller.trigger_zone_pulse("zone_3", duration_s=5, is_manual=True, source="emergency_stop")
    await controller.on_pump_completed("zone_3", reason="emergency_stop")
    assert "zone_3" not in cooldowns_set

    # 4. auto_moisture run -> MUST set 6h cooldown
    await controller.trigger_zone_pulse("zone_1", duration_s=5, is_manual=False, source="auto_moisture")
    await controller.on_pump_completed("zone_1", reason="done")
    assert "zone_1" in cooldowns_set
    assert cooldowns_set["zone_1"] > time.time() + 20000

    # 5. rule:* run -> MUST set 6h cooldown
    await controller.trigger_zone_pulse("zone_2", duration_s=5, is_manual=False, source="rule:morning_watering")
    await controller.on_pump_completed("zone_2", reason="done")
    assert "zone_2" in cooldowns_set
    assert cooldowns_set["zone_2"] > time.time() + 20000

    # 6. rule:* stopped by emergency_stop -> MUST NOT set cooldown
    cooldowns_set.clear()
    await controller.trigger_zone_pulse("zone_4", duration_s=5, is_manual=False, source="rule:daily_pulse")
    await controller.on_pump_completed("zone_4", reason="emergency_stop")
    assert "zone_4" not in cooldowns_set


@pytest.mark.asyncio
async def test_duration_cap_and_concurrency_limit(clean_state, monkeypatch):
    controller = PlantIrrigationController()

    dispatched_sec = []

    async def mock_send_pump_run(idx, sec):
        dispatched_sec.append(sec)
        return f"cmd-{idx}"

    monkeypatch.setattr("app.hardware.gateway_driver.garden_gateway.send_pump_run", mock_send_pump_run)
    monkeypatch.setattr("app.engine.failsafes.is_system_clock_valid", lambda: True)

    # 1. 120s requested -> clamped strictly to 60s
    ok, _ = await controller.trigger_zone_pulse("zone_1", duration_s=120, is_manual=True)
    assert ok is True
    assert dispatched_sec[0] == 60

    # 2. Start zone 2 -> second pump is allowed
    ok2, _ = await controller.trigger_zone_pulse("zone_2", duration_s=30, is_manual=True)
    assert ok2 is True

    # 3. Start zone 3 -> rejected because 2 pumps already running
    ok3, reason = await controller.trigger_zone_pulse("zone_3", duration_s=10, is_manual=True)
    assert ok3 is False
    assert "Maximum 2 concurrent pumps" in reason
