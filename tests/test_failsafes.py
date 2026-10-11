"""
Critical Safety Tests for Priority 0 Hardcoded Failsafes.
Verifies dry-run lockout, overhead tank cutoff, 15m runtime guard, sensor bounds, and 6h cooldown.
"""

import time
import pytest
from app.config import settings
from app.core.state_manager import state_manager
from app.engine.failsafes import FailsafeEngine
from app.hardware.gpio_driver import gpio_controller


@pytest.mark.asyncio
async def test_failsafe_dry_run_lockout():
    failsafe = FailsafeEngine()

    # Pre-condition: Start Zone 1 pump
    await gpio_controller.set_pin(settings.PIN_ZONE_1_MOSFET, True)
    await state_manager.update_actuator("zone_1_pump", True)
    state = state_manager.get_state()
    assert state["actuators"]["zone_1_pump"] is True

    # Trigger Dry-Run condition (reservoir empty = True)
    status = await failsafe.evaluate_safety_interlocks(
        tank_low=False,
        tank_high=False,
        reservoir_empty=True,
        soil_voltages={"zone_1": 2.2, "zone_2": 2.2, "zone_3": 2.2, "zone_4": 2.2},
    )

    assert status["dry_run_locked"] is True
    # Verify Zone 1 pump was immediately forced OFF
    assert await gpio_controller.read_pin(settings.PIN_ZONE_1_MOSFET) is False
    updated_state = state_manager.get_state()
    assert updated_state["actuators"]["zone_1_pump"] is False

    # Verify authorization check denies new starts
    can_start, reason = failsafe.can_start_zone_pump("zone_1")
    assert can_start is False
    assert "Dry-Run" in reason or "empty" in reason


@pytest.mark.asyncio
async def test_failsafe_overhead_tank_high_cutoff():
    failsafe = FailsafeEngine()

    # Pre-condition: Start AC Contactor
    await gpio_controller.set_pin(settings.PIN_AC_CONTACTOR, True)
    await state_manager.update_actuator("ac_contactor", True)
    assert await gpio_controller.read_pin(settings.PIN_AC_CONTACTOR) is True

    # Trigger Tank High Float (tank_high = True)
    status = await failsafe.evaluate_safety_interlocks(
        tank_low=False,
        tank_high=True,
        reservoir_empty=False,
        soil_voltages={"zone_1": 2.2, "zone_2": 2.2, "zone_3": 2.2, "zone_4": 2.2},
    )

    assert status["tank_high_locked"] is True
    # Verify AC Contactor was cut immediately
    assert await gpio_controller.read_pin(settings.PIN_AC_CONTACTOR) is False
    updated_state = state_manager.get_state()
    assert updated_state["actuators"]["ac_contactor"] is False

    # Verify authorization check rejects AC motor start
    can_start, reason = failsafe.can_start_ac_motor()
    assert can_start is False
    assert "full" in reason.lower()


@pytest.mark.asyncio
async def test_failsafe_max_motor_runtime_guard():
    failsafe = FailsafeEngine()

    # Start AC Contactor
    await gpio_controller.set_pin(settings.PIN_AC_CONTACTOR, True)
    await state_manager.update_actuator("ac_contactor", True)

    # Manually simulate continuous runtime exceeding 15 minutes (901 seconds ago)
    past_timestamp = time.time() - 950.0
    state_manager._state["runtime_tracking"]["ac_contactor_started_at"] = past_timestamp

    # Evaluate safety interlocks
    status = await failsafe.evaluate_safety_interlocks(
        tank_low=False,
        tank_high=False,
        reservoir_empty=False,
        soil_voltages={"zone_1": 2.2, "zone_2": 2.2, "zone_3": 2.2, "zone_4": 2.2},
    )

    assert status["motor_timeout_locked"] is True
    assert await gpio_controller.read_pin(settings.PIN_AC_CONTACTOR) is False

    can_start, reason = failsafe.can_start_ac_motor()
    assert can_start is False
    assert "runtime guard" in reason.lower() or "reset" in reason.lower()

    # Manual reset test
    failsafe.reset_motor_lockout()
    assert failsafe.motor_lockout_active is False
    can_start_again, _ = failsafe.can_start_ac_motor()
    assert can_start_again is True


@pytest.mark.asyncio
async def test_failsafe_out_of_bounds_sensor_guard():
    failsafe = FailsafeEngine()

    # Start Zone 2 pump
    await gpio_controller.set_pin(settings.PIN_ZONE_2_MOSFET, True)
    await state_manager.update_actuator("zone_2_pump", True)

    # Inject out-of-bounds voltage for Zone 2 (0.3V < 0.5V minimum)
    bad_voltages = {"zone_1": 2.2, "zone_2": 0.3, "zone_3": 2.2, "zone_4": 2.2}
    status = await failsafe.evaluate_safety_interlocks(
        tank_low=False,
        tank_high=False,
        reservoir_empty=False,
        soil_voltages=bad_voltages,
    )

    assert "zone_2" in status["faulty_probes"]
    # Zone 2 pump must be cut immediately
    assert await gpio_controller.read_pin(settings.PIN_ZONE_2_MOSFET) is False
    assert state_manager.get_state()["actuators"]["zone_2_pump"] is False

    # Authorization must reject watering
    can_start, reason = failsafe.can_start_zone_pump("zone_2")
    assert can_start is False
    assert "out-of-bounds" in reason.lower()


@pytest.mark.asyncio
async def test_failsafe_zone_cooldown_lockout():
    failsafe = FailsafeEngine()

    # Simulate Zone 3 recently completed a watering cycle
    now = time.time()
    await state_manager.update_actuator("zone_3_pump", True)
    await state_manager.update_actuator("zone_3_pump", False)

    # Cooldown should now be active for 6 hours
    can_start, reason = failsafe.can_start_zone_pump("zone_3")
    assert can_start is False
    assert "cooldown protection" in reason.lower()

    # Verify manual override flag works when explicitly authorized
    can_start_override, _ = failsafe.can_start_zone_pump("zone_3", ignore_cooldown=True)
    assert can_start_override is True
