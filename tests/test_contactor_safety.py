"""
Contactor Safety and Reliability Regression Tests.
Covers:
  - Slow contactor pull-in (< pull_in_timeout_ms) succeeds without alarm
  - Missing pull-in trips CONTACTOR_MISMATCH alarm, shuts off relay, transitions to FAULT
  - Contactor relay write failure on stop transitions to FAULT, raises RELAY_WRITE_FAILED alarm
  - Welded contactor contact on stop trips WELDED_CONTACT alarm, locks out motor, sets FAULT
  - Periodic (1 Hz) commanded vs feedback mismatch (> 2s) trips safety shutdown + FAULT
  - Relay state unknown tracking (_relay_state_known), never claiming OFF when unknown
"""

import time
from typing import Callable, Dict, List, Optional, Tuple
import pytest

from app.config import system_config
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.engine.motor_controller import motor_controller
from app.hardware.gpio_backend import GPIOBackend
from app.hardware.gpio_driver import head_end_gpio
from app.hardware.rtc import get_monotonic_now


class FakeContactorGPIOBackend(GPIOBackend):
    def __init__(self):
        self.pins: Dict[int, int] = {}
        self.set_value_calls: List[Tuple[int, int]] = []
        self.fail_set_value: bool = False
        self.closed: bool = False
        self.delayed_pin_transitions: Dict[int, Tuple[float, int]] = {}
        self.on_set_value_hook: Optional[Callable[[int, int], None]] = None

    def open(self, chip_path: str) -> bool:
        return True

    def close(self) -> None:
        self.closed = True

    def request_input(self, line_offset: int, consumer: str = "test", bias: str = "pull_up") -> bool:
        if line_offset not in self.pins:
            self.pins[line_offset] = 1  # default pull-up (HIGH)
        return True

    def request_output(self, line_offset: int, consumer: str = "test", initial_value: int = 0) -> bool:
        self.pins[line_offset] = initial_value
        return True

    def set_delayed_transition(self, pin: int, delay_s: float, target_val: int) -> None:
        self.delayed_pin_transitions[pin] = (time.monotonic() + delay_s, target_val)

    def get_value(self, line_offset: int) -> int:
        if line_offset in self.delayed_pin_transitions:
            target_time, target_val = self.delayed_pin_transitions[line_offset]
            if time.monotonic() >= target_time:
                self.pins[line_offset] = target_val
                del self.delayed_pin_transitions[line_offset]
        return self.pins.get(line_offset, 0)

    def set_value(self, line_offset: int, value: int) -> None:
        if self.fail_set_value:
            raise OSError("I/O error communicating with GPIO controller")
        self.pins[line_offset] = value
        self.set_value_calls.append((line_offset, value))
        if self.on_set_value_hook:
            self.on_set_value_hook(line_offset, value)


@pytest.fixture
def setup_gpio_environment():
    """Sets up a driver with FakeContactorGPIOBackend and safe pin mappings."""
    backend = FakeContactorGPIOBackend()
    driver = head_end_gpio

    old_backend = driver._backend
    old_hw = driver._hardware_available
    old_relay_pin = driver._relay_pin
    old_feedback_pin = driver._feedback_pin
    old_active_high = driver._active_high
    old_pull_in_timeout = system_config.motor.pull_in_timeout_ms
    old_drop_out_timeout = system_config.motor.drop_out_timeout_ms
    old_fb_pin_cfg = system_config.head_end.contactor_feedback_pin

    RELAY_PIN = 10
    FB_PIN = 11

    driver._backend = backend
    driver._hardware_available = True
    driver._relay_pin = RELAY_PIN
    driver._feedback_pin = FB_PIN
    driver._active_high = True
    driver._relay_state = False
    driver._relay_state_known = True
    driver._last_commanded_relay_state = False

    system_config.head_end.contactor_feedback_pin = FB_PIN
    system_config.head_end.feedback_active_low = True
    # Initial electrical levels: relay off (0), feedback aux contact open (1 -> decoded False)
    backend.pins[RELAY_PIN] = 0
    backend.pins[FB_PIN] = 1

    # Reset any lingering alarms or lockouts
    state_manager._state["alarms"].clear()
    state_manager._state["motor"]["state"] = "OFF"
    state_manager._state["motor"]["manual_locked"] = False
    failsafe_engine.reset_motor_lockout()

    yield backend, driver, RELAY_PIN, FB_PIN

    driver._backend = old_backend
    driver._hardware_available = old_hw
    driver._relay_pin = old_relay_pin
    driver._feedback_pin = old_feedback_pin
    driver._active_high = old_active_high
    system_config.motor.pull_in_timeout_ms = old_pull_in_timeout
    system_config.motor.drop_out_timeout_ms = old_drop_out_timeout
    system_config.head_end.contactor_feedback_pin = old_fb_pin_cfg
    state_manager._state["alarms"].clear()
    failsafe_engine.reset_motor_lockout()


# -----------------------------------------------------------------------------
# 1. Slow Pull-In Succeeds (< pull_in_timeout_ms)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slow_pull_in_succeeds(setup_gpio_environment, monkeypatch):
    """Aux feedback goes True after 80 ms (<= 500 ms); motor starts successfully in RUNNING state."""
    backend, driver, relay_pin, fb_pin = setup_gpio_environment
    system_config.motor.pull_in_timeout_ms = 500

    monkeypatch.setattr(failsafe_engine, "can_start_motor", lambda is_manual: (True, None))

    def on_relay_energized(pin, val):
        if pin == relay_pin and val == 1:
            backend.set_delayed_transition(fb_pin, delay_s=0.08, target_val=0)

    backend.on_set_value_hook = on_relay_energized

    ok, msg = await motor_controller.start_motor(is_manual=True, confirm=True)
    assert ok is True
    assert "Motor energized successfully" in msg
    assert state_manager.get_state()["motor"]["state"] == "RUNNING"
    assert "contactor_mismatch" not in state_manager.get_state()["alarms"]
    assert driver.relay_state is True
    assert driver.relay_state_known is True


# -----------------------------------------------------------------------------
# 2. No Pull-In Triggers Contactor Mismatch Alarm and Transitions to FAULT
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_pull_in_triggers_mismatch_alarm(setup_gpio_environment, monkeypatch):
    """Aux feedback stays open; start_motor() fails after timeout, trips alarm, sets FAULT, de-energizes relay."""
    backend, driver, relay_pin, fb_pin = setup_gpio_environment
    system_config.motor.pull_in_timeout_ms = 100  # Fast timeout for test

    monkeypatch.setattr(failsafe_engine, "can_start_motor", lambda is_manual: (True, None))

    # Aux feedback remains 1 (open -> decoded False)
    backend.pins[fb_pin] = 1

    ok, msg = await motor_controller.start_motor(is_manual=True, confirm=True)
    assert ok is False
    assert "CONTACTOR_MISMATCH" in msg

    st = state_manager.get_state()
    assert st["motor"]["state"] == "FAULT"
    assert st["motor"]["manual_locked"] is True
    assert failsafe_engine.manual_motor_lockout is True
    assert "contactor_mismatch" in st["alarms"]
    assert backend.pins[relay_pin] == 0


# -----------------------------------------------------------------------------
# 3. Relay Write Failure on Stop Sets FAULT and Alarm
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_relay_write_failure_on_stop_sets_fault(setup_gpio_environment):
    """Bus failure driving relay low sets relay_state_known=False, FAULT state, and RELAY_WRITE_FAILED alarm."""
    backend, driver, relay_pin, fb_pin = setup_gpio_environment

    # Simulate motor currently running
    await state_manager.set_motor_state("RUNNING")
    driver._relay_state = True
    driver._relay_state_known = True
    driver._last_commanded_relay_state = True
    backend.pins[relay_pin] = 1
    backend.pins[fb_pin] = 0

    # Inject write failure
    backend.fail_set_value = True

    ok, msg = await motor_controller.stop_motor(reason="test_failure")
    assert ok is False
    assert "RELAY_WRITE_FAILED" in msg

    st = state_manager.get_state()
    assert st["motor"]["state"] == "FAULT"
    assert st["motor"]["manual_locked"] is True
    assert failsafe_engine.manual_motor_lockout is True
    assert "relay_write_failed" in st["alarms"]

    # Relay state must NOT be reported as False when unknown!
    assert driver.relay_state_known is False
    assert driver.relay_state is None
    assert driver.last_commanded_relay_state is False


# -----------------------------------------------------------------------------
# 4. Welded Contact on Stop Triggers Lockout and FAULT
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_welded_contact_on_stop_triggers_lockout(setup_gpio_environment):
    """Relay write succeeds but aux contact stays closed; drop-out times out, raises WELDED_CONTACT alarm, sets FAULT."""
    backend, driver, relay_pin, fb_pin = setup_gpio_environment
    system_config.motor.drop_out_timeout_ms = 100  # Fast timeout for test

    await state_manager.set_motor_state("RUNNING")
    driver._relay_state = True
    driver._relay_state_known = True
    driver._last_commanded_relay_state = True
    backend.pins[relay_pin] = 1

    # Welded aux contact: stays 0 (closed -> decoded True)
    backend.pins[fb_pin] = 0

    ok, msg = await motor_controller.stop_motor(reason="welded_test")
    assert ok is False
    assert "WELDED_CONTACT" in msg

    st = state_manager.get_state()
    assert st["motor"]["state"] == "FAULT"
    assert st["motor"]["manual_locked"] is True
    assert failsafe_engine.manual_motor_lockout is True
    assert "welded_contact" in st["alarms"]
    # Relay was commanded low
    assert backend.pins[relay_pin] == 0


# -----------------------------------------------------------------------------
# 5. Periodic Commanded vs Feedback Mismatch Trips Failsafe (> 2s)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_periodic_commanded_vs_feedback_mismatch_failsafe(setup_gpio_environment, monkeypatch):
    """Periodic consistency check: mismatch for > 2s forces outputs off, sets FAULT, and raises alarm."""
    backend, driver, relay_pin, fb_pin = setup_gpio_environment

    # Commanded OFF, but feedback indicates contactor closed
    driver._last_commanded_relay_state = False
    driver._relay_state = False
    driver._relay_state_known = True

    await state_manager.update_head_end(
        tank_low=False,
        interlock_out=True,
        interlock_mid=True,
        leak_detected=False,
        hardware_available=True,
        contactor_feedback=True,  # Mismatch: commanded False vs fb True
    )

    # Initial tick (0s): records mismatch start, but does not trip immediately
    now_mono = get_monotonic_now()
    monkeypatch.setattr("app.engine.failsafes.get_monotonic_now", lambda: now_mono)
    res1 = await failsafe_engine.evaluate_head_end_safety()
    assert res1["feedback_mismatch"] is True
    assert "welded_contact" not in state_manager.get_state()["alarms"]

    # Tick after 1.0s: still within grace window
    monkeypatch.setattr("app.engine.failsafes.get_monotonic_now", lambda: now_mono + 1.0)
    res2 = await failsafe_engine.evaluate_head_end_safety()
    assert res2["feedback_mismatch"] is True
    assert "welded_contact" not in state_manager.get_state()["alarms"]

    # Tick after 2.5s: exceeds 2.0s limit -> TRIPS safety cutoff
    monkeypatch.setattr("app.engine.failsafes.get_monotonic_now", lambda: now_mono + 2.5)
    res3 = await failsafe_engine.evaluate_head_end_safety()
    assert res3["feedback_mismatch"] is True

    st = state_manager.get_state()
    assert st["motor"]["state"] == "FAULT"
    assert st["motor"]["manual_locked"] is True
    assert failsafe_engine.manual_motor_lockout is True
    assert "welded_contact" in st["alarms"]


# -----------------------------------------------------------------------------
# 6. Relay State Unknown Handling and Telemetry Exposing None
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_relay_state_unknown_handling_and_telemetry(setup_gpio_environment):
    """Verifies that unknown relay state correctly exposes None in telemetry and driver."""
    backend, driver, relay_pin, fb_pin = setup_gpio_environment

    driver._relay_state_known = False
    driver._last_commanded_relay_state = True

    assert driver.relay_state is None
    assert driver.relay_state_known is False
    assert driver.last_commanded_relay_state is True

    # State manager synchronizes driver relay state
    await state_manager.update_head_end(
        tank_low=False,
        interlock_out=True,
        interlock_mid=True,
        leak_detected=False,
        hardware_available=True,
    )

    st = state_manager.get_state()["head_end"]
    assert st["contactor_relay"] is None
    assert st["contactor_relay_known"] is False
    assert st["last_commanded_relay_state"] is True
