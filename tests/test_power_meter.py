"""
Unit and Integration Tests for Motor AC Power & Current Monitoring Driver.
Tests:
  - Modbus RTU CRC16 checksum calculation with test vectors
  - PZEM-004T v3 request building and 25-byte response parsing
  - Voltage window checking (under-voltage, over-voltage, within window)
  - Inrush blanking window behavior
  - Overload / locked-rotor detection and lockout
  - Dry-run / loss-of-prime detection and lockout
  - Baseline current learning mode over 5 healthy runs and SQLite persistence
  - Energy kWh per run calculation and logging in SQLite runs table
  - Graceful degradation when hardware is absent
  - Failsafe safety integration and lockout resets
"""

import asyncio
import pytest
from app.config import system_config
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.hardware.power_meter import (
    PowerMeterManager,
    PowerReading,
    build_pzem_read_request,
    compute_modbus_crc16,
    parse_pzem_response,
    power_meter,
)


def _make_pzem_response_bytes(
    slave_addr: int = 1,
    func_code: int = 4,
    voltage_01v: int = 2300,        # 230.0 V
    current_001a: int = 4500,       # 4.500 A
    power_01w: int = 10350,         # 1035.0 W
    energy_wh: int = 15200,         # 15.200 kWh
    frequency_01hz: int = 500,      # 50.0 Hz
    pf_001: int = 85,               # 0.85
    alarm: int = 0,
    corrupt_crc: bool = False,
) -> bytes:
    """Helper to synthesize valid 25-byte binary PZEM-004T v3 response frames."""
    data = bytearray(23)
    data[0] = slave_addr
    data[1] = func_code
    data[2] = 0x14  # 20 bytes data

    # Voltage (0.1V, unsigned 16-bit)
    data[3] = (voltage_01v >> 8) & 0xFF
    data[4] = voltage_01v & 0xFF

    # Current (0.001A, 32-bit: low 16-bit, high 16-bit)
    curr_low = current_001a & 0xFFFF
    curr_high = (current_001a >> 16) & 0xFFFF
    data[5] = (curr_low >> 8) & 0xFF
    data[6] = curr_low & 0xFF
    data[7] = (curr_high >> 8) & 0xFF
    data[8] = curr_high & 0xFF

    # Power (0.1W, 32-bit: low 16-bit, high 16-bit)
    pwr_low = power_01w & 0xFFFF
    pwr_high = (power_01w >> 16) & 0xFFFF
    data[9] = (pwr_low >> 8) & 0xFF
    data[10] = pwr_low & 0xFF
    data[11] = (pwr_high >> 8) & 0xFF
    data[12] = pwr_high & 0xFF

    # Energy (1Wh, 32-bit: low 16-bit, high 16-bit)
    egy_low = energy_wh & 0xFFFF
    egy_high = (energy_wh >> 16) & 0xFFFF
    data[13] = (egy_low >> 8) & 0xFF
    data[14] = egy_low & 0xFF
    data[15] = (egy_high >> 8) & 0xFF
    data[16] = egy_high & 0xFF

    # Frequency (0.1Hz, unsigned 16-bit)
    data[17] = (frequency_01hz >> 8) & 0xFF
    data[18] = frequency_01hz & 0xFF

    # Power Factor (0.01, unsigned 16-bit)
    data[19] = (pf_001 >> 8) & 0xFF
    data[20] = pf_001 & 0xFF

    # Alarms
    data[21] = (alarm >> 8) & 0xFF
    data[22] = alarm & 0xFF

    crc = compute_modbus_crc16(bytes(data))
    if corrupt_crc:
        crc ^= 0xFFFF

    return bytes(data) + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


# ==============================================================================
# 1. Modbus RTU CRC16 & PZEM-004T Framing Tests
# ==============================================================================

def test_modbus_crc16_calculation() -> None:
    """Verifies standard Modbus RTU 16-bit CRC polynomial calculation."""
    # Test vector 1: Standard read input registers command
    cmd = bytes([0x01, 0x04, 0x00, 0x00, 0x00, 0x0A])
    crc = compute_modbus_crc16(cmd)
    # Check low byte in bits 0-7, high byte in bits 8-15
    low_byte = crc & 0xFF
    high_byte = (crc >> 8) & 0xFF
    # Verify CRC appended frame matches PZEM-004T v3 specification (0x70, 0x0D)
    assert (low_byte, high_byte) == (0x70, 0x0D)

    # Test vector 2: Empty bytes
    assert compute_modbus_crc16(b"") == 0xFFFF


def test_build_pzem_read_request() -> None:
    """Verifies PZEM-004T v3 request packet construction."""
    req = build_pzem_read_request(slave_addr=1)
    assert len(req) == 8
    assert req[0] == 0x01
    assert req[1] == 0x04
    assert req[2:6] == bytes([0x00, 0x00, 0x00, 0x0A])
    # Verify CRC bytes
    expected_crc = compute_modbus_crc16(req[:6])
    assert req[6] == (expected_crc & 0xFF)
    assert req[7] == ((expected_crc >> 8) & 0xFF)

    # Different slave address
    req2 = build_pzem_read_request(slave_addr=5)
    assert req2[0] == 0x05
    assert len(req2) == 8


def test_parse_pzem_response_valid() -> None:
    """Verifies correct parsing of valid 25-byte PZEM-004T response."""
    frame = _make_pzem_response_bytes(
        slave_addr=1,
        voltage_01v=2305,     # 230.5 V
        current_001a=5200,    # 5.200 A
        power_01w=11986,      # 1198.6 W
        energy_wh=42150,      # 42.15 kWh
        frequency_01hz=501,   # 50.1 Hz
        pf_001=88,            # 0.88
    )
    reading = parse_pzem_response(frame, expected_addr=1)
    assert reading is not None
    assert reading.voltage_v == 230.5
    assert reading.current_a == 5.2
    assert reading.power_w == 1198.6
    assert reading.energy_kwh == 42.15
    assert reading.frequency_hz == 50.1
    assert reading.power_factor == 0.88

    # Dict serialization
    d = reading.to_dict()
    assert d["voltage_v"] == 230.5
    assert d["current_a"] == 5.2
    assert d["energy_kwh"] == 42.15


def test_parse_pzem_response_corrupt_or_truncated() -> None:
    """Verifies rejection of invalid, corrupted, or truncated responses."""
    # Corrupt CRC
    corrupt_frame = _make_pzem_response_bytes(corrupt_crc=True)
    assert parse_pzem_response(corrupt_frame) is None

    # Truncated frames
    assert parse_pzem_response(b"") is None
    assert parse_pzem_response(corrupt_frame[:20]) is None
    assert parse_pzem_response(corrupt_frame + b"\x00") is None

    # Mismatched slave address
    valid_frame = _make_pzem_response_bytes(slave_addr=2)
    assert parse_pzem_response(valid_frame, expected_addr=1) is None


# ==============================================================================
# 2. Voltage Window Checking Tests
# ==============================================================================

def test_voltage_window_start_authorization() -> None:
    """Verifies mains voltage window check before motor start."""
    mgr = PowerMeterManager()
    system_config.power_meter.enabled = True
    system_config.power_meter.voltage_min_v = 180.0
    system_config.power_meter.voltage_max_v = 260.0

    # 1. No reading yet -> allowed (fail-open for telemetry warmup)
    mgr._last_reading = None
    ok, err = mgr.check_voltage_for_start()
    assert ok is True

    # 2. Healthy 230V mains -> allowed
    mgr._last_reading = PowerReading(230.0, 0.0, 0.0, 0.0, 50.0, 1.0)
    ok, err = mgr.check_voltage_for_start()
    assert ok is True
    assert err is None

    # 3. Under-voltage (175V) -> rejected
    mgr._last_reading = PowerReading(175.0, 0.0, 0.0, 0.0, 50.0, 1.0)
    ok, err = mgr.check_voltage_for_start()
    assert ok is False
    assert "outside safe operating window" in (err or "")

    # 4. Over-voltage (268V) -> rejected
    mgr._last_reading = PowerReading(268.0, 0.0, 0.0, 0.0, 50.0, 1.0)
    ok, err = mgr.check_voltage_for_start()
    assert ok is False
    assert "outside safe operating window" in (err or "")


def test_voltage_running_window_hold_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies running motor trips on sustained out-of-voltage window after hold time."""
    mgr = PowerMeterManager()
    system_config.power_meter.enabled = True
    system_config.power_meter.voltage_min_v = 180.0
    system_config.power_meter.voltage_max_v = 260.0
    system_config.power_meter.voltage_fault_hold_s = 3.0

    fake_time = 1000.0
    monkeypatch.setattr("app.hardware.power_meter.get_monotonic_now", lambda: fake_time)

    # Initial under-voltage reading (170V)
    under_reading = PowerReading(170.0, 5.0, 850.0, 1.0, 50.0, 1.0)

    # First detection (hold timer armed)
    ok, fault, msg = mgr.evaluate_power_safety(under_reading, run_elapsed_s=10.0)
    assert ok is True

    # Advance time by 2.0s (< 3.0s hold time) -> still ok
    fake_time = 1002.0
    ok, fault, msg = mgr.evaluate_power_safety(under_reading, run_elapsed_s=12.0)
    assert ok is True

    # Advance time past hold time (3.5s total) -> trips
    fake_time = 1003.5
    ok, fault, msg = mgr.evaluate_power_safety(under_reading, run_elapsed_s=13.5)
    assert ok is False
    assert fault == "voltage_out_of_bounds"
    assert "VOLTAGE_OUT_OF_BOUNDS" in (msg or "")


# ==============================================================================
# 3. Inrush Blanking Window & Current Safety Evaluations
# ==============================================================================

def test_inrush_blanking_window_ignores_spikes() -> None:
    """Verifies startup inrush window ignores starting current spikes."""
    mgr = PowerMeterManager()
    system_config.power_meter.enabled = True
    system_config.power_meter.baseline_current_a = 5.0
    system_config.power_meter.inrush_blanking_s = 3.0
    mgr.baseline_current_a = 5.0

    # Locked-rotor current spike (25A) during inrush (run_elapsed_s = 1.0s < 3.0s)
    spike_reading = PowerReading(230.0, 25.0, 5000.0, 1.0, 50.0, 1.0)
    ok, fault, msg = mgr.evaluate_power_safety(spike_reading, run_elapsed_s=1.0)
    assert ok is True
    assert fault is None

    # Zero current (loss of prime) during pull-in
    zero_reading = PowerReading(230.0, 0.5, 50.0, 1.0, 50.0, 1.0)
    ok, fault, msg = mgr.evaluate_power_safety(zero_reading, run_elapsed_s=2.0)
    assert ok is True
    assert fault is None


def test_overload_locked_rotor_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies overload detection trips motor when current > 135% for > 3.0s."""
    mgr = PowerMeterManager()
    system_config.power_meter.enabled = True
    system_config.power_meter.baseline_current_a = 5.0
    system_config.power_meter.overload_current_pct = 135.0  # Threshold = 6.75 A
    system_config.power_meter.overload_timeout_s = 3.0
    mgr.baseline_current_a = 5.0

    fake_time = 2000.0
    monkeypatch.setattr("app.hardware.power_meter.get_monotonic_now", lambda: fake_time)

    # 8.0 A current (> 6.75 A threshold)
    overload_reading = PowerReading(230.0, 8.0, 1840.0, 1.0, 50.0, 0.9)

    # Step 1: Arm timer at t=2000.0
    ok, fault, msg = mgr.evaluate_power_safety(overload_reading, run_elapsed_s=5.0)
    assert ok is True

    # Step 2: 2.0s elapsed (< 3.0s timeout)
    fake_time = 2002.0
    ok, fault, msg = mgr.evaluate_power_safety(overload_reading, run_elapsed_s=7.0)
    assert ok is True

    # Step 3: 3.5s elapsed -> trip
    fake_time = 2003.5
    ok, fault, msg = mgr.evaluate_power_safety(overload_reading, run_elapsed_s=8.5)
    assert ok is False
    assert fault == "motor_overload_current"
    assert "MOTOR_OVERLOAD_CURRENT" in (msg or "")


def test_dry_run_loss_of_prime_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies dry-run detection trips motor when current < 65% for > 5.0s."""
    mgr = PowerMeterManager()
    system_config.power_meter.enabled = True
    system_config.power_meter.baseline_current_a = 5.0
    system_config.power_meter.dry_run_current_pct = 65.0   # Threshold = 3.25 A
    system_config.power_meter.dry_run_timeout_s = 5.0
    mgr.baseline_current_a = 5.0

    fake_time = 3000.0
    monkeypatch.setattr("app.hardware.power_meter.get_monotonic_now", lambda: fake_time)

    # 2.0 A current (< 3.25 A threshold, dry impeller spinning freely in air)
    dry_reading = PowerReading(230.0, 2.0, 460.0, 1.0, 50.0, 0.7)

    # Step 1: Arm timer at t=3000.0
    ok, fault, msg = mgr.evaluate_power_safety(dry_reading, run_elapsed_s=5.0)
    assert ok is True

    # Step 2: 4.0s elapsed (< 5.0s timeout)
    fake_time = 3004.0
    ok, fault, msg = mgr.evaluate_power_safety(dry_reading, run_elapsed_s=9.0)
    assert ok is True

    # Step 3: 5.5s elapsed -> trip
    fake_time = 3005.5
    ok, fault, msg = mgr.evaluate_power_safety(dry_reading, run_elapsed_s=10.5)
    assert ok is False
    assert fault == "dry_run_current"
    assert "DRY_RUN_CURRENT" in (msg or "")


# ==============================================================================
# 4. Automated Baseline Learning Mode Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_baseline_learning_mode_five_healthy_runs() -> None:
    """Verifies automated baseline learning records 5 healthy runs and establishes baseline."""
    mgr = PowerMeterManager()
    system_config.power_meter.enabled = True
    system_config.power_meter.learning_runs_required = 5
    system_config.power_meter.baseline_current_a = None

    # Initially in learning mode
    mgr.learning_mode = True
    mgr.baseline_current_a = None
    mgr.learning_runs_completed = 0
    mgr.learning_samples = []

    # Short run (< 30s) -> ignored for learning
    mgr.on_motor_started()
    mgr._run_current_samples = [5.1, 5.0, 5.2, 5.0, 5.1]
    mgr.on_motor_stopped(duration_s=20, reason="tank_full")
    assert mgr.learning_runs_completed == 0
    assert mgr.learning_mode is True

    # Fault stop (not healthy) -> ignored for learning
    mgr.on_motor_started()
    mgr._run_current_samples = [5.1, 5.0, 5.2, 5.0, 5.1]
    mgr.on_motor_stopped(duration_s=45, reason="fault_dry_run")
    assert mgr.learning_runs_completed == 0

    # Simulate 5 healthy completed runs
    current_values = [4.8, 5.0, 5.1, 4.9, 5.2]
    for i, current in enumerate(current_values):
        mgr.on_motor_started()
        mgr._run_current_samples = [current] * 10
        mgr.on_motor_stopped(duration_s=40, reason="tank_full")
        assert mgr.learning_runs_completed == (i + 1)

    # After 5 healthy runs, baseline established
    assert mgr.learning_mode is False
    assert mgr.baseline_current_a is not None
    # Average of [4.8, 5.0, 5.1, 4.9, 5.2] = 5.0
    assert abs(mgr.baseline_current_a - 5.0) < 0.05
    await mgr.wait_pending_saves()


# ==============================================================================
# 5. Energy Accounting & Logging in SQLite runs Table
# ==============================================================================

@pytest.mark.asyncio
async def test_energy_kwh_logged_in_runs_table() -> None:
    """Verifies motor run energy consumption in kWh is recorded to SQLite runs table."""
    mgr = PowerMeterManager()
    mgr._last_reading = PowerReading(230.0, 5.0, 1150.0, 10.0, 50.0, 0.9)
    mgr.on_motor_started()

    # Advance energy by 0.25 kWh
    mgr._last_reading = PowerReading(230.0, 5.0, 1150.0, 10.25, 50.0, 0.9)
    energy_kwh = mgr.on_motor_stopped(duration_s=60, reason="manual_stop")
    assert energy_kwh == 0.25
    assert mgr.last_run_energy_kwh == 0.25

    # Log to SQLite
    await state_manager.log_run("motor.ac", "2026-10-13T00:00:00Z", 60, "manual_stop", energy_kwh=energy_kwh)

    # Verify SQLite entry
    def _fetch_run() -> dict:
        with state_manager._get_connection() as conn:
            row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1;").fetchone()
            return dict(row) if row else {}

    row = await asyncio.to_thread(_fetch_run)
    assert row["target"] == "motor.ac"
    assert row["duration_s"] == 60
    assert row["end_reason"] == "manual_stop"
    assert abs(row["energy_kwh"] - 0.25) < 0.001


# ==============================================================================
# 6. Failsafe Integration & Motor Lockout Reset Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_failsafe_power_meter_trip_and_reset() -> None:
    """Verifies failsafe_engine trips motor into FAULT state on dry-run and operator can reset."""
    system_config.power_meter.enabled = True
    power_meter.baseline_current_a = 5.0
    state_manager._state["hardware_ready"] = True
    state_manager._state["head_end"]["configured"] = True

    # Put motor in RUNNING state
    await state_manager.set_motor_state("RUNNING")
    assert state_manager.get_state()["motor"]["state"] == "RUNNING"

    # Evaluate dry run trip via failsafe_engine
    trip_reading = PowerReading(230.0, 1.5, 345.0, 1.0, 50.0, 0.8)
    # Fast forward dry run hold timer
    power_meter._dry_run_start_mono = 100.0
    import app.hardware.power_meter as pm_mod
    orig_mono = pm_mod.get_monotonic_now
    pm_mod.get_monotonic_now = lambda: 110.0  # 10s elapsed > 5s timeout

    try:
        is_safe, err = await failsafe_engine.evaluate_motor_power_safety(trip_reading, run_elapsed_s=15.0)
        assert is_safe is False
        assert failsafe_engine.manual_motor_lockout is True

        state = state_manager.get_state()
        assert state["motor"]["state"] == "FAULT"
        assert state["motor"]["manual_locked"] is True
        assert "dry_run_current" in state["alarms"]

        # Motor start should now be blocked
        ok, reason = failsafe_engine.can_start_motor()
        assert ok is False
        assert "FAULT" in (reason or "") or "locked" in (reason or "")

        # Reset lockout
        reset_ok, reset_err = failsafe_engine.reset_motor_lockout()
        assert reset_ok is True

        state_after = state_manager.get_state()
        assert state_after["motor"]["state"] == "OFF"
        assert state_after["motor"]["manual_locked"] is False
        assert "dry_run_current" not in state_after["alarms"]
    finally:
        pm_mod.get_monotonic_now = orig_mono


def test_graceful_degradation_when_hardware_absent() -> None:
    """Verifies power meter degrades safely and returns is_available() == False when absent."""
    mgr = PowerMeterManager()
    system_config.power_meter.enabled = True
    system_config.power_meter.sensor_type = "pzem004t"
    system_config.power_meter.serial_port = "/dev/nonexistent_port"

    mgr.pzem_driver = None
    assert mgr.is_available() is False

    status = mgr.get_status_dict()
    assert status["available"] is False
    assert status["voltage_v"] is None
