"""
Pure Logic Safety Tests for Priority 0 Non-Bypassable Hardcoded Failsafes.
Covers:
  - Interlock truth table (all four states)
  - Dry sump lockout
  - Full float stop
  - Max continuous runtime guard (15 min) and cooldown lockout
  - Manual lockout reset requirements
  - Stale data actuator lockouts (head-end 15s, garden 90s)
  - Wiring fault (mid=LOW, out=HIGH) immediate lockout and alarm
  - Un-synced clock auto-watering block
  - Serial link loss / garden offline lockout
  - Concurrent plant pump rule (max 2 pumps)
  - Zone cooldown enforcement
  - Probe voltage sanity band filtering
"""

import time
import pytest
from app.config import system_config
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.engine.plant_controller import PlantIrrigationController
from app.hardware.gpio_driver import _decode, SoftwareDebouncer
from app.hardware.rtc import get_monotonic_now


@pytest.fixture(autouse=True)
def reset_failsafe_state():
    state_manager.reset_wiring_fault()
    failsafe_engine.reset_motor_lockout()
    yield
    state_manager.reset_wiring_fault()
    failsafe_engine.reset_motor_lockout()


# -----------------------------------------------------------------------------
# 1. Interlock Truth Table
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_interlock_truth_table():
    """Verifies all four states of the PC817 interlock chain truth table."""
    # State 1: Healthy (out=HIGH, mid=HIGH)
    await state_manager.update_head_end(
        tank_low=False, interlock_out=True, interlock_mid=True, leak_detected=False, hardware_available=True
    )
    st = state_manager.get_state()["head_end"]
    assert st["interlock_cause"] == "healthy"

    # State 2: Tank Full / Cut Wire (out=LOW, mid=LOW)
    await state_manager.update_head_end(
        tank_low=False, interlock_out=False, interlock_mid=False, leak_detected=False, hardware_available=True
    )
    st = state_manager.get_state()["head_end"]
    assert st["interlock_cause"] == "tank_full"

    # State 3: Dry Sump (out=LOW, mid=HIGH)
    await state_manager.update_head_end(
        tank_low=False, interlock_out=False, interlock_mid=True, leak_detected=False, hardware_available=True
    )
    st = state_manager.get_state()["head_end"]
    assert st["interlock_cause"] == "dry_sump"

    # State 4: Impossible Wiring Fault (out=HIGH, mid=LOW)
    await state_manager.update_head_end(
        tank_low=False, interlock_out=True, interlock_mid=False, leak_detected=False, hardware_available=True
    )
    st = state_manager.get_state()["head_end"]
    assert st["interlock_cause"] == "wiring_fault"
    state_manager.reset_wiring_fault()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "active_low,raw_out,raw_mid,expected_out,expected_mid,expected_cause",
    [
        # active_low=True (PC817 default: 0 is active/closed, 1 is open)
        (True, 0, 0, True, True, "healthy"),
        (True, 1, 1, False, False, "tank_full"),
        (True, 1, 0, False, True, "dry_sump"),
        (True, 0, 1, True, False, "wiring_fault"),
        # active_low=False (Active HIGH: 1 is active/closed, 0 is open)
        (False, 1, 1, True, True, "healthy"),
        (False, 0, 0, False, False, "tank_full"),
        (False, 0, 1, False, True, "dry_sump"),
        (False, 1, 0, True, False, "wiring_fault"),
    ],
)
async def test_interlock_truth_table_parametric_polarity(
    active_low, raw_out, raw_mid, expected_out, expected_mid, expected_cause
):
    """
    Parametric test verifying all 4 truth-table rows across both polarity settings.
    Decodes raw GPIO line levels using _decode(raw, active_low) and asserts correct cause.
    """
    state_manager.reset_wiring_fault()
    dec_out = _decode(raw_out, active_low)
    dec_mid = _decode(raw_mid, active_low)

    assert dec_out == expected_out
    assert dec_mid == expected_mid

    await state_manager.update_head_end(
        tank_low=False,
        interlock_out=dec_out,
        interlock_mid=dec_mid,
        leak_detected=False,
        hardware_available=True,
    )
    st = state_manager.get_state()["head_end"]
    assert st["interlock_cause"] == expected_cause
    state_manager.reset_wiring_fault()


def test_software_debouncer_glitch_rejection_and_transition():
    """
    Verifies that a 1-sample glitch does NOT change debounced state,
    and only N consecutive identical samples trigger a transition.
    """
    # debounce_ms = 100, poll_interval_ms = 100 -> required_n = max(2, ceil(100/100)) = 2
    debouncer = SoftwareDebouncer(debounce_ms=100, poll_interval_ms=100)
    assert debouncer.get_required_samples(100, 100) == 2

    # 1. First sample initializes the channel immediately
    assert debouncer.update("interlock_out", False) is False
    assert debouncer.get_debounced("interlock_out") is False

    # 2. 1-sample transient glitch (True for 1 sample) MUST NOT change debounced state
    res_glitch = debouncer.update("interlock_out", True)
    assert res_glitch is False
    assert debouncer.get_debounced("interlock_out") is False

    # 3. Input returns to False (glitch ended) -> state remains False
    res_stable = debouncer.update("interlock_out", False)
    assert res_stable is False
    assert debouncer.get_debounced("interlock_out") is False

    # 4. Sustained transition: sample 1 of True (count = 1, required = 2)
    assert debouncer.update("interlock_out", True) is False
    # Sample 2 of True (count = 2 >= required) -> debounced state transitions to True!
    assert debouncer.update("interlock_out", True) is True
    assert debouncer.get_debounced("interlock_out") is True

    # 5. Another 1-sample glitch in the opposite direction (False for 1 sample)
    assert debouncer.update("interlock_out", False) is True
    assert debouncer.get_debounced("interlock_out") is True

    # Glitch recovers to True
    assert debouncer.update("interlock_out", True) is True
    assert debouncer.get_debounced("interlock_out") is True


@pytest.mark.asyncio
async def test_wiring_fault_latches_until_manual_reset():
    """
    Verifies that impossible wiring fault (interlock_out=True, interlock_mid=False)
    latches indefinitely even when physical inputs return to healthy,
    and only clears upon manual operator reset.
    """
    state_manager.reset_wiring_fault()

    # Step 1: Healthy initial state
    await state_manager.update_head_end(
        tank_low=False, interlock_out=True, interlock_mid=True, leak_detected=False, hardware_available=True
    )
    st = state_manager.get_state()["head_end"]
    assert st["interlock_cause"] == "healthy"
    assert st["wiring_fault_latched"] is False

    # Step 2: Trip impossible wiring fault
    await state_manager.update_head_end(
        tank_low=False, interlock_out=True, interlock_mid=False, leak_detected=False, hardware_available=True
    )
    await failsafe_engine.evaluate_head_end_safety()
    st = state_manager.get_state()["head_end"]
    assert st["interlock_cause"] == "wiring_fault"
    assert st["wiring_fault_latched"] is True

    # Step 3: Inputs return to healthy (interlock_out=True, interlock_mid=True)
    # Because wiring_fault is latched, interlock_cause MUST REMAIN "wiring_fault"!
    await state_manager.update_head_end(
        tank_low=False, interlock_out=True, interlock_mid=True, leak_detected=False, hardware_available=True
    )
    st = state_manager.get_state()["head_end"]
    assert st["interlock_cause"] == "wiring_fault"
    assert st["wiring_fault_latched"] is True

    # Step 4: Operator manually resets motor lockout
    ok, err = failsafe_engine.reset_motor_lockout()
    assert ok is True
    assert err is None
    assert state_manager.get_state()["head_end"]["wiring_fault_latched"] is False

    # Step 5: Now next update with healthy inputs transitions back to healthy
    await state_manager.update_head_end(
        tank_low=False, interlock_out=True, interlock_mid=True, leak_detected=False, hardware_available=True
    )
    st = state_manager.get_state()["head_end"]
    assert st["interlock_cause"] == "healthy"
    assert st["wiring_fault_latched"] is False


# -----------------------------------------------------------------------------
# 2. Dry Sump Lockout
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dry_sump_trips_motor_lockout():
    """Verifies dry sump immediately cuts off running motor and blocks start."""
    # Set motor initially RUNNING
    await state_manager.set_motor_state("RUNNING", reason="test_start")

    # Trip dry sump: out=LOW, mid=HIGH
    await state_manager.update_head_end(
        tank_low=False, interlock_out=False, interlock_mid=True, leak_detected=False, hardware_available=True
    )
    await failsafe_engine.evaluate_head_end_safety()

    st = state_manager.get_state()
    assert st["motor"]["state"] == "OFF"
    assert st["motor"]["last_stop_reason"] == "dry_sump"

    # Verify authorization rejects new start
    allowed, reason = failsafe_engine.can_start_motor()
    assert allowed is False
    assert "dry_sump" in reason or "Source sump is empty" in reason


# -----------------------------------------------------------------------------
# 3. Full Float Stop
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_full_float_trips_motor_stop():
    """Verifies overhead tank full float cuts motor and rejects start."""
    await state_manager.set_motor_state("RUNNING", reason="test_start")

    # Trip Full float: out=LOW, mid=LOW
    await state_manager.update_head_end(
        tank_low=False, interlock_out=False, interlock_mid=False, leak_detected=False, hardware_available=True
    )
    await failsafe_engine.evaluate_head_end_safety()

    st = state_manager.get_state()
    assert st["motor"]["state"] == "OFF"
    assert st["motor"]["last_stop_reason"] == "tank_full"

    allowed, reason = failsafe_engine.can_start_motor()
    assert allowed is False
    assert "already full" in reason


# -----------------------------------------------------------------------------
# 4. Max Continuous Runtime Guard (15 min) and Cooldown Lockout
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_max_runtime_limit_and_cooldown():
    """Verifies running past 15 min triggers timeout lockout and mandatory cooldown."""
    await state_manager.update_head_end(
        tank_low=False, interlock_out=True, interlock_mid=True, leak_detected=False, hardware_available=True
    )
    await state_manager.set_motor_state("RUNNING", reason="test_start")

    # Simulate start time 901s ago in monotonic clock
    mono_now = get_monotonic_now()
    state_manager._state["motor"]["started_at_monotonic"] = mono_now - 905.0

    res = await failsafe_engine.evaluate_head_end_safety()
    assert res["motor_timeout_tripped"] is True
    assert res["manual_motor_lockout"] is True

    st = state_manager.get_state()
    assert st["motor"]["state"] == "COOLDOWN_LOCKED"
    assert failsafe_engine.motor_timeout_tripped is True

    # Start must be rejected
    allowed, reason = failsafe_engine.can_start_motor()
    assert allowed is False
    assert "locked due to previous max-runtime trip" in reason


# -----------------------------------------------------------------------------
# 5. Lockout Requires Manual Clear After Cooldown
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_lockout_manual_reset_after_cooldown():
    """Verifies reset is rejected during active cooldown and succeeds after cooldown."""
    failsafe_engine.manual_motor_lockout = True
    failsafe_engine.motor_timeout_tripped = True
    # Cooldown active for next 60 seconds
    failsafe_engine.motor_cooldown_until = time.time() + 60.0

    # Reset attempt during cooldown
    ok, err = failsafe_engine.reset_motor_lockout()
    assert ok is False
    assert "Cooldown still active" in err

    # Fast-forward past cooldown
    failsafe_engine.motor_cooldown_until = time.time() - 1.0
    ok, err = failsafe_engine.reset_motor_lockout()
    assert ok is True
    assert failsafe_engine.manual_motor_lockout is False
    assert failsafe_engine.motor_timeout_tripped is False


# -----------------------------------------------------------------------------
# 6. Stale Data Lockouts
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stale_data_locks_out_actuators():
    """Verifies stale head-end data (>15s) and stale garden data (>90s) block actuators."""
    # Setup healthy head-end then make it stale
    await state_manager.update_head_end(
        tank_low=False, interlock_out=True, interlock_mid=True, leak_detected=False, hardware_available=True
    )
    # Force timestamp to 20s in the past (> 15s threshold)
    state_manager._state["head_end"]["last_update_ts"] = time.time() - 20.0

    he_stale, gd_stale = await state_manager.check_staleness()
    assert he_stale is True

    allowed, reason = failsafe_engine.can_start_motor()
    assert allowed is False
    assert "STALE" in reason

    # Now verify garden staleness
    state_manager._state["garden"]["gateway_connected"] = True
    state_manager._state["garden"]["node_online"] = True
    state_manager._state["garden"]["reservoir_wet"] = True
    state_manager._state["garden"]["last_packet_ts"] = time.time() - 100.0  # > 90s threshold

    he_stale, gd_stale = await state_manager.check_staleness()
    assert gd_stale is True

    allowed, reason = failsafe_engine.can_water_zone("zone_1", is_manual=True)
    assert allowed is False
    assert "STALE" in reason


# -----------------------------------------------------------------------------
# 7. Wiring Fault Lockout and Alarm
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wiring_fault_immediate_lockout():
    """Verifies impossible state (mid=LOW, out=HIGH) immediately locks motor and alarms."""
    await state_manager.set_motor_state("RUNNING", reason="test_start")

    # Trip wiring fault
    await state_manager.update_head_end(
        tank_low=False, interlock_out=True, interlock_mid=False, leak_detected=False, hardware_available=True
    )
    await failsafe_engine.evaluate_head_end_safety()

    st = state_manager.get_state()
    assert st["motor"]["state"] == "OFF"
    assert "wiring_fault" in st["alarms"]

    allowed, reason = failsafe_engine.can_start_motor()
    assert allowed is False
    assert "Wiring fault" in reason


# -----------------------------------------------------------------------------
# 8. Clock Not Valid Blocks Auto-Watering
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_clock_invalid_blocks_auto_watering(monkeypatch):
    """Verifies that unsynced system clock blocks automatic watering cycles."""
    # Setup healthy garden node
    state_manager._state["garden"]["gateway_connected"] = True
    state_manager._state["garden"]["node_online"] = True
    state_manager._state["garden"]["is_stale"] = False
    state_manager._state["garden"]["reservoir_wet"] = True

    # Configure zone 1 calibration
    system_config.zones["zone_1"].calibration.dry_v = 2.6
    system_config.zones["zone_1"].calibration.wet_v = 1.2
    system_config.zones["zone_1"].calibration.margin_v = 0.4
    state_manager._state["garden"]["zones"]["zone_1"]["voltage"] = 1.8

    # Stub clock validity check to False
    monkeypatch.setattr("app.engine.failsafes.is_system_clock_valid", lambda: False)

    # Automatic watering must be rejected
    auto_ok, auto_reason = failsafe_engine.can_water_zone("zone_1", is_manual=False)
    assert auto_ok is False
    assert "System clock is invalid" in auto_reason

    # Manual pulse should still be permitted if requested by operator
    manual_ok, _ = failsafe_engine.can_water_zone("zone_1", is_manual=True)
    assert manual_ok is True


# -----------------------------------------------------------------------------
# 9. Serial Link Loss Locks Plant Pumps
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_serial_link_loss_locks_plant_pumps():
    """Verifies link loss to garden gateway or node immediately locks irrigation."""
    state_manager._state["garden"]["gateway_connected"] = False
    state_manager._state["garden"]["node_online"] = False

    ok, reason = failsafe_engine.can_water_zone("zone_1", is_manual=True)
    assert ok is False
    assert "STALE or link lost" in reason


# -----------------------------------------------------------------------------
# 10. Concurrent Plant Pump Rule (Max 2 Pumps)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_plant_pumps_limit(monkeypatch):
    """Verifies maximum of 2 concurrent plant pumps are permitted on DC bus."""
    controller = PlantIrrigationController()

    # Inline stub: stub gateway dispatch
    async def stub_send_pump_run(zone: int, duration_s: int):
        return f"cmd-{zone}"
    monkeypatch.setattr("app.hardware.gateway_driver.garden_gateway.send_pump_run", stub_send_pump_run)
    monkeypatch.setattr(failsafe_engine, "can_water_zone", lambda z, is_manual=False: (True, None))

    # Start zone 1
    ok1, _ = await controller.trigger_zone_pulse("zone_1", duration_s=10, is_manual=True)
    assert ok1 is True

    # Start zone 2
    ok2, _ = await controller.trigger_zone_pulse("zone_2", duration_s=10, is_manual=True)
    assert ok2 is True

    # Attempt to start zone 3 (must be rejected due to 2-pump concurrency limit)
    ok3, reason3 = await controller.trigger_zone_pulse("zone_3", duration_s=10, is_manual=True)
    assert ok3 is False
    assert "Maximum 2 concurrent pumps" in reason3

    # Clean up background timers
    await controller.stop_all_zones()


# -----------------------------------------------------------------------------
# 11. Zone Cooldown Enforced
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_zone_cooldown_enforcement(monkeypatch):
    """Verifies zone in 6h cooldown rejects repeat automatic irrigation."""
    state_manager._state["garden"]["gateway_connected"] = True
    state_manager._state["garden"]["node_online"] = True
    state_manager._state["garden"]["is_stale"] = False
    state_manager._state["garden"]["reservoir_wet"] = True
    state_manager._state["garden"]["zones"]["zone_1"]["voltage"] = 1.8
    monkeypatch.setattr("app.engine.failsafes.is_system_clock_valid", lambda: True)

    system_config.zones["zone_1"].enabled = True
    system_config.zones["zone_1"].calibration.dry_v = 2.6
    system_config.zones["zone_1"].calibration.wet_v = 1.2
    system_config.zones["zone_1"].calibration.margin_v = 0.4

    # Set cooldown in future
    state_manager._state["garden"]["zones"]["zone_1"]["cooldown_until_ts"] = time.time() + 3600.0

    ok, reason = failsafe_engine.can_water_zone("zone_1", is_manual=False)
    assert ok is False
    assert "cooldown protection" in reason

    # Reset cooldown to prevent polluting subsequent tests
    state_manager._state["garden"]["zones"]["zone_1"]["cooldown_until_ts"] = 0.0


# -----------------------------------------------------------------------------
# 12. Probe Sanity Band Filtering
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_probe_sanity_band_filtering(monkeypatch):
    """Verifies voltages outside [wet_v - margin, dry_v + margin] are rejected."""
    state_manager._state["garden"]["gateway_connected"] = True
    state_manager._state["garden"]["node_online"] = True
    state_manager._state["garden"]["is_stale"] = False
    state_manager._state["garden"]["reservoir_wet"] = True
    monkeypatch.setattr("app.engine.failsafes.is_system_clock_valid", lambda: True)

    system_config.zones["zone_1"].calibration.dry_v = 2.6
    system_config.zones["zone_1"].calibration.wet_v = 1.2
    system_config.zones["zone_1"].calibration.margin_v = 0.4
    # Valid band: [0.8V .. 3.0V]

    # Test under-voltage (e.g. 0.5V - short circuit)
    state_manager._state["garden"]["zones"]["zone_1"]["voltage"] = 0.5
    ok_low, reason_low = failsafe_engine.can_water_zone("zone_1", is_manual=True)
    assert ok_low is False
    assert "outside sanity band" in reason_low

    # Test over-voltage (e.g. 3.2V - open probe / 3.3V rail short)
    state_manager._state["garden"]["zones"]["zone_1"]["voltage"] = 3.2
    ok_high, reason_high = failsafe_engine.can_water_zone("zone_1", is_manual=True)
    assert ok_high is False
    assert "outside sanity band" in reason_high

    # Test normal in-band voltage (e.g. 1.8V)
    state_manager._state["garden"]["zones"]["zone_1"]["voltage"] = 1.8
    ok_normal, _ = failsafe_engine.can_water_zone("zone_1", is_manual=True)
    assert ok_normal is True
