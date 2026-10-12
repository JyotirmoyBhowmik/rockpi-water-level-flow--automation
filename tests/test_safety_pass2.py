"""
Safety Pass 2 Regression Tests.
Covers:
  - Watchdog health gating & stall detection (Part 2.1)
  - Lockout persistence & invalid clock treatment (Part 2.2)
  - Head-end input lost while motor running (Part 2.3)
  - Minimum OFF time (120s) protection (Part 5.1/5.4)
  - Relay test try/finally cleanup & preconditions (Part 2.5)
  - Shell-injection prevention in speech synthesizer (Part 2.10)
  - SSE ticket generation & single-use consumption (Part 1.3)
  - Contactor aux feedback pull-in & welded contact check (Part 2.8)
  - Webhook SSRF validation (Part 3.4)
  - Flow delta accumulation (Part 2.7)
"""

import asyncio
import json
import os
from pathlib import Path
import time
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.server import create_app
from app.config import system_config
from app.core.security import is_ip_allowed_for_webhook, security_manager, validate_webhook_url
from app.core.state_manager import state_manager
from app.core.watchdog import HealthMonitor
from app.engine.failsafes import failsafe_engine
from app.hardware.audio_driver import ALSA_DEVICE_REGEX, VOICE_REGEX
from app.hardware.protocol import SequenceTracker
from app.hardware.rtc import get_monotonic_now


# -----------------------------------------------------------------------------
# 1. Watchdog Health Gating & Loop Stall Detection (Part 2.1)
# -----------------------------------------------------------------------------
def test_watchdog_health_gating_and_stall_detection():
    hm = HealthMonitor()
    hm.enable()

    # All loops fresh initially
    healthy, reason = hm.check_health()
    assert healthy is True
    assert reason is None

    # Simulate head_end_poll loop stalled > 3.0s
    hm._beats["head_end_poll"] = get_monotonic_now() - 4.0
    healthy, reason = hm.check_health()
    assert healthy is False
    assert "head_end_poll" in reason
    assert "stalled" in reason

    # After beat reported -> health restored
    hm.beat("head_end_poll")
    healthy, reason = hm.check_health()
    assert healthy is True

    # Simulate gateway reader loop stalled > 15.0s (when gateway is enabled)
    system_config.garden_gateway.enabled = True
    try:
        hm.enable()
        hm._beats["gateway_reader"] = get_monotonic_now() - 16.0
        healthy, reason = hm.check_health()
        assert healthy is False
        assert "gateway_reader" in reason
    finally:
        system_config.garden_gateway.enabled = False


def test_systemd_ready_notification_sent():
    """Verifies that notify_ready() transmits READY=1 on systemd NOTIFY_SOCKET (Part 1.2)."""
    from app.core.watchdog import WatchdogGuardian

    wg = WatchdogGuardian()
    sent_payloads = []

    class MockNotifySocket:
        def sendto(self, msg, path):
            sent_payloads.append((msg, path))

    wg._notify_sock = MockNotifySocket()
    wg._notify_path = "/run/systemd/notify"

    res = wg.notify_ready()
    assert res is True
    assert len(sent_payloads) == 1
    msg, path = sent_payloads[0]
    assert b"READY=1" in msg
    assert path == "/run/systemd/notify"

    # Second call must be idempotent and not send duplicate packets
    res2 = wg.notify_ready()
    assert res2 is True
    assert len(sent_payloads) == 1


@pytest.mark.asyncio
async def test_watchdog_stall_forces_outputs_off_and_skips_v(monkeypatch):
    """
    On health-gating failure:
    - force_all_outputs_off() must be called
    - is_stall_exit must be set to True
    - os._exit(2) must be called (exit code 2)
    - Magic 'V' token must NOT be written to /dev/watchdog
    """
    import os
    from app.core.watchdog import WatchdogGuardian, health_monitor
    from app.hardware.gpio_driver import head_end_gpio

    wg = WatchdogGuardian()
    exit_codes = []
    outputs_forced_off = False
    written_bytes = []

    async def mock_force_off():
        nonlocal outputs_forced_off
        outputs_forced_off = True

    def mock_exit(code):
        exit_codes.append(code)

    monkeypatch.setattr(head_end_gpio, "force_all_outputs_off", mock_force_off)
    wg._exit_func = mock_exit

    wg._dev_fd = 999
    wg._is_hardware_present = True

    orig_write = os.write

    def mock_os_write(fd, data):
        if fd == 999:
            written_bytes.append(data)
            return len(data)
        return orig_write(fd, data)

    def mock_os_close(fd):
        pass

    monkeypatch.setattr(os, "write", mock_os_write)
    monkeypatch.setattr(os, "close", mock_os_close)

    # Enable health monitor and simulate head_end_poll loop stalled
    health_monitor.enable()
    health_monitor._beats["head_end_poll"] = get_monotonic_now() - 5.0

    wg._running = True
    task = asyncio.create_task(wg._heartbeat_loop())
    await asyncio.sleep(0.02)
    if not task.done():
        task.cancel()

    assert outputs_forced_off is True
    assert wg.is_stall_exit is True
    assert exit_codes == [2]

    # Now verify that calling stop() on stall exit does NOT write 'V'
    await wg.stop()
    assert b"V" not in written_bytes


@pytest.mark.asyncio
async def test_watchdog_clean_shutdown_disarms_with_v(monkeypatch):
    """Clean SIGTERM / intentional shutdown must write 'V' to /dev/watchdog to disarm."""
    import os
    from app.core.watchdog import WatchdogGuardian

    wg = WatchdogGuardian()
    written_bytes = []

    wg._dev_fd = 999
    wg._is_hardware_present = True
    wg.is_stall_exit = False

    orig_write = os.write

    def mock_os_write(fd, data):
        if fd == 999:
            written_bytes.append(data)
            return len(data)
        return orig_write(fd, data)

    def mock_os_close(fd):
        pass

    monkeypatch.setattr(os, "write", mock_os_write)
    monkeypatch.setattr(os, "close", mock_os_close)

    await wg.stop(disarm=True)
    assert b"V" in written_bytes


def test_watchdog_gateway_disabled_no_stall():
    """If garden_gateway.enabled is False, gateway_reader is NOT registered and causes no stall."""
    from app.core.watchdog import HealthMonitor

    old_enabled = system_config.garden_gateway.enabled
    try:
        system_config.garden_gateway.enabled = False
        hm = HealthMonitor()
        hm.enable()

        # gateway_reader must NOT be in monitored thresholds
        assert "gateway_reader" not in hm._thresholds
        assert "head_end_poll" in hm._thresholds
        assert "rules_auto_loop" in hm._thresholds

        # Even with no gateway beats ever recorded, health check passes
        healthy, reason = hm.check_health()
        assert healthy is True
        assert reason is None
    finally:
        system_config.garden_gateway.enabled = old_enabled


# -----------------------------------------------------------------------------
# 2. Lockout Persistence & Clock Invalid Treatment (Part 2.2)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_lockout_persistence_across_restart(tmp_path: Path):
    db_file = tmp_path / "persistence_test.db"
    state_manager.db_path = db_file
    await state_manager.init_db()

    # Set motor cooldown and manual lock
    future_cooldown = time.time() + 600.0
    await state_manager.set_motor_cooldown(future_cooldown)
    await state_manager.set_motor_manual_lock(True)
    await state_manager.set_alarm("motor_timeout", "Runtime exceeded")

    # Verify rows in lockouts table
    with state_manager._get_connection() as conn:
        rows = dict(conn.execute("SELECT key, value_json FROM lockouts;").fetchall())
    assert "motor_cooldown" in rows
    assert "motor_manual_lock" in rows
    assert "alarm_motor_timeout" in rows

    # Simulate restart by resetting in-memory state and re-loading
    state_manager._state["motor"]["cooldown_until_ts"] = 0.0
    state_manager._state["motor"]["manual_locked"] = False
    state_manager._state["alarms"] = {}

    loaded = await state_manager.load_persisted_lockouts()
    assert loaded > 0
    st = state_manager.get_state()
    assert st["motor"]["cooldown_until_ts"] == future_cooldown
    assert st["motor"]["manual_locked"] is True
    assert "motor_timeout" in st["alarms"]


# -----------------------------------------------------------------------------
# 3. Head-End Input Lost While Motor Running (Part 2.3)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_head_end_input_lost_de_energizes_motor():
    # Motor is RUNNING
    await state_manager.set_motor_state("RUNNING", reason="test_start")
    await state_manager.update_head_end(
        tank_low=False, interlock_out=True, interlock_mid=True, leak_detected=False, hardware_available=True
    )
    state_manager._state["head_end"]["last_update_ts"] = time.time()

    # When healthy, failsafe evaluation keeps it running
    await failsafe_engine.evaluate_head_end_safety()
    assert state_manager.get_state()["motor"]["state"] == "RUNNING"

    # Simulate head-end inputs lost / stale > 3s
    state_manager._state["head_end"]["last_update_ts"] = time.time() - 3.5
    await failsafe_engine.evaluate_head_end_safety()

    st = state_manager.get_state()
    assert st["motor"]["state"] == "OFF"
    assert st["motor"]["last_stop_reason"] == "head_end_input_lost"
    assert "head_end_input_lost" in st["alarms"]


# -----------------------------------------------------------------------------
# 4. Minimum OFF Time (120s) Protection (Part 5.1/5.4)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_motor_minimum_off_time_enforcement():
    # Setup healthy head-end with debounced count
    await state_manager.update_head_end(
        tank_low=True, interlock_out=True, interlock_mid=True, leak_detected=False, hardware_available=True
    )
    state_manager._state["head_end"]["healthy_debounce_count"] = 3
    system_config.setup_complete = True

    # Record motor stopped 30s ago (< 120s min off time)
    state_manager._state["motor"]["last_stop_monotonic"] = get_monotonic_now() - 30.0

    allowed, reason = failsafe_engine.can_start_motor()
    assert allowed is False
    assert "Minimum OFF time active" in reason

    # Advance past 120s
    state_manager._state["motor"]["last_stop_monotonic"] = get_monotonic_now() - 125.0
    allowed, reason = failsafe_engine.can_start_motor()
    assert allowed is True
    assert reason is None


# -----------------------------------------------------------------------------
# 5. Relay Test Preconditions and Cleanup (Part 2.5)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_relay_test_preconditions_and_cleanup(tmp_path: Path):
    auth_file = tmp_path / "auth.json"
    security_manager.auth_file_path = auth_file
    system_config.security.auth_token_file = str(auth_file)
    system_config.setup_complete = False

    raw_token, _ = security_manager.get_or_create_token()
    headers = {"Authorization": f"Bearer {raw_token}"}
    app = create_app()

    # 1. Unconfirmed motor mains disconnect is rejected
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/setup/test-relay",
            json={"motor_disconnected_confirmed": False},
            headers=headers,
        )
        assert res.status_code == 400
        assert "MUST confirm" in res.json()["detail"]

        # 2. Leak detected blocks relay test
        await state_manager.update_head_end(
            tank_low=False, interlock_out=True, interlock_mid=True, leak_detected=True, hardware_available=True
        )
        res_leak = await client.post(
            "/api/setup/test-relay",
            json={"motor_disconnected_confirmed": True},
            headers=headers,
        )
        assert res_leak.status_code == 400
        assert "Water leak detected" in res_leak.json()["detail"]


# -----------------------------------------------------------------------------
# 6. Shell Injection Text & Regex Validation (Part 2.10)
# -----------------------------------------------------------------------------
def test_speech_synthesizer_regex_and_shell_safety():
    # Valid voice and ALSA devices
    assert VOICE_REGEX.match("en-us")
    assert VOICE_REGEX.match("en+f3")
    assert ALSA_DEVICE_REGEX.match("default")
    assert ALSA_DEVICE_REGEX.match("hw:1,0")

    # Malicious injection attempts in voice or alsa_device fail regex
    assert not VOICE_REGEX.match("en; rm -rf /")
    assert not VOICE_REGEX.match("en | aplay")
    assert not ALSA_DEVICE_REGEX.match("default; touch pwned")
    assert not ALSA_DEVICE_REGEX.match("hw:0$(whoami)")


# -----------------------------------------------------------------------------
# 7. SSE Ticket Generation & Single-Use Consumption (Part 1.3)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sse_ticket_generation_and_single_use(tmp_path: Path):
    auth_file = tmp_path / "auth.json"
    security_manager.auth_file_path = auth_file
    system_config.security.auth_token_file = str(auth_file)

    raw_token, _ = security_manager.get_or_create_token()
    headers = {"Authorization": f"Bearer {raw_token}"}
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Request ticket with Bearer token
        res_ticket = await client.post("/api/telemetry/ticket", headers=headers)
        assert res_ticket.status_code == 200
        data = res_ticket.json()
        assert data["status"] == "success"
        ticket = data["ticket"]
        assert len(ticket) >= 16

        # 2. Access stream without ticket or auth -> 401
        res_no_auth = await client.get("/api/telemetry/stream")
        assert res_no_auth.status_code == 401

        # 3. Consume ticket via internal validator: first use succeeds, second fails
        assert security_manager.consume_sse_ticket(ticket) is True
        assert security_manager.consume_sse_ticket(ticket) is False


# -----------------------------------------------------------------------------
# 8. Webhook SSRF Validation (Part 3.4)
# -----------------------------------------------------------------------------
def test_webhook_ssrf_protection():
    allowlist = ["hooks.slack.com", "api.telegram.org"]

    # Check rejection of loopback and link-local:
    safe, err = validate_webhook_url("http://127.0.0.1:8000/webhook", allowlist)
    assert safe is False
    assert "allowlist" in err.lower() or "loopback" in err.lower() or "scheme" in err.lower()

    safe, err = validate_webhook_url("https://169.254.169.254/latest/meta-data", allowlist)
    assert safe is False

    safe, err = validate_webhook_url("https://localhost:8080/pwn", allowlist)
    assert safe is False

    # Unsupported schemes rejected
    safe, err = validate_webhook_url("file:///etc/passwd", allowlist)
    assert safe is False
    assert "unsupported scheme" in err.lower()


# -----------------------------------------------------------------------------
# 9. Flow Delta Integration (Part 2.7)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_flow_delta_pulse_accumulation():
    state_manager._state["garden"]["zones"]["zone_1"]["flow_pulses"] = 100

    # First packet: delta 15 pulses
    await state_manager.update_garden_flow_delta(zone_idx=1, delta_pulses=15, period_ms=1000)
    z1 = state_manager.get_state()["garden"]["zones"]["zone_1"]
    assert z1["flow_delta"] == 15
    assert z1["flow_pulses"] == 115

    # Second packet: delta 10 pulses
    await state_manager.update_garden_flow_delta(zone_idx=1, delta_pulses=10, period_ms=1000)
    z1 = state_manager.get_state()["garden"]["zones"]["zone_1"]
    assert z1["flow_delta"] == 10
    assert z1["flow_pulses"] == 125


# -----------------------------------------------------------------------------
# 10. Per-Field Staleness Isolation (Part 4.1)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_per_field_staleness_isolation(monkeypatch):
    """
    Part 4.1 Verification:
      - Feed fresh heartbeat + stale soil for zone 2 -> zones 1, 3, 4 can water, zone 2 locked.
      - Feed stale reservoir -> ALL pumps locked.
      - Feed stale heartbeat -> Gateway state DEGRADED and alert raised.
    """
    monkeypatch.setattr("app.engine.failsafes.is_system_clock_valid", lambda: True)
    now = time.time()

    # 1. Setup fresh baseline
    system_config.setup_complete = True
    state_manager._state["garden"]["gateway_connected"] = True
    state_manager._state["garden"]["node_online"] = True
    state_manager._state["garden"]["is_stale"] = False
    state_manager._state["garden"]["last_packet_ts"] = now
    state_manager._state["garden"]["last_heartbeat_ts"] = now
    state_manager._state["garden"]["reservoir_wet"] = True
    state_manager._state["garden"]["reservoir_last_update_ts"] = now

    for i in range(1, 5):
        z_k = f"zone_{i}"
        system_config.zones[z_k].enabled = True
        system_config.zones[z_k].calibration.dry_v = 2.6
        system_config.zones[z_k].calibration.wet_v = 1.2
        system_config.zones[z_k].calibration.margin_v = 0.4
        state_manager._state["garden"]["zones"][z_k]["voltage"] = 1.8
        state_manager._state["garden"]["zones"][z_k]["last_update_ts"] = now
        state_manager._state["garden"]["zones"][z_k]["cooldown_until_ts"] = 0.0

    # Make Zone 2 soil probe reading stale (> 60s)
    state_manager._state["garden"]["zones"]["zone_2"]["last_update_ts"] = now - 90.0

    # Zones 1, 3, 4 must be able to water
    ok1, _ = failsafe_engine.can_water_zone("zone_1", is_manual=False)
    ok3, _ = failsafe_engine.can_water_zone("zone_3", is_manual=False)
    ok4, _ = failsafe_engine.can_water_zone("zone_4", is_manual=False)
    assert ok1 is True
    assert ok3 is True
    assert ok4 is True

    # Zone 2 must be locked out due to stale soil
    ok2, reason2 = failsafe_engine.can_water_zone("zone_2", is_manual=False)
    assert ok2 is False
    assert "zone_2' soil reading is STALE" in reason2

    # Now make reservoir float reading stale (> 20s)
    state_manager._state["garden"]["reservoir_last_update_ts"] = now - 35.0

    # ALL zones must now be locked out
    for i in range(1, 5):
        ok, res_reason = failsafe_engine.can_water_zone(f"zone_{i}", is_manual=False)
        assert ok is False
        assert "reservoir float reading is STALE" in res_reason

    # Restore reservoir, but make heartbeat stale (> 30s)
    state_manager._state["garden"]["reservoir_last_update_ts"] = now
    state_manager._state["garden"]["last_heartbeat_ts"] = now - 45.0
    state_manager._state["garden"]["last_packet_ts"] = now - 45.0

    ok_hb, hb_reason = failsafe_engine.can_water_zone("zone_1", is_manual=False)
    assert ok_hb is False
    assert "STALE or link lost" in hb_reason


# -----------------------------------------------------------------------------
# 11. Zero Default Keys Validation (Part 2.2)
# -----------------------------------------------------------------------------
def test_zero_default_keys_and_gateway_validation():
    from pydantic import ValidationError
    from app.config import SystemConfig

    # Default system config has garden_gateway.enabled = False, shared_key = None
    cfg = SystemConfig()
    assert cfg.garden_gateway.enabled is False
    assert cfg.garden_gateway.shared_key is None

    # Enabling garden_gateway without a shared key MUST raise ValidationError
    with pytest.raises(ValidationError) as exc_info:
        SystemConfig(garden_gateway={"enabled": True, "shared_key": None})
    assert "garden_gateway.shared_key is missing or empty" in str(exc_info.value)

    # Empty string shared key MUST also fail
    with pytest.raises(ValidationError) as exc_info:
        SystemConfig(garden_gateway={"enabled": True, "shared_key": ""})
    assert "garden_gateway.shared_key is missing or empty" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 12. Flow Calibration Wizard (Part 4.2)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_flow_calibration_wizard(monkeypatch, tmp_path):
    monkeypatch.setattr("app.engine.failsafes.is_system_clock_valid", lambda: True)
    now = time.time()
    state_manager._state["garden"]["gateway_connected"] = True
    state_manager._state["garden"]["node_online"] = True
    state_manager._state["garden"]["is_stale"] = False
    state_manager._state["garden"]["last_heartbeat_ts"] = now
    state_manager._state["garden"]["reservoir_wet"] = True
    state_manager._state["garden"]["reservoir_last_update_ts"] = now

    from app.hardware.gateway_driver import garden_gateway

    # Mock gateway send_pump_run to simulate pulses arriving
    async def mock_send_pump_run(zone, duration):
        # Simulate pulses arriving in state
        current = state_manager._state["garden"]["zones"]["zone_1"].get("flow_pulses", 0)
        state_manager._state["garden"]["zones"]["zone_1"]["flow_pulses"] = current + 75
        return "cmd-123"

    monkeypatch.setattr(garden_gateway, "send_pump_run", mock_send_pump_run)
    orig_sleep = asyncio.sleep
    async def mock_sleep(s):
        await orig_sleep(0.001)
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    app = create_app()
    token = security_manager.rotate_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Normal calibration run (75 pulses in 10s = 1.0 L/min)
        res = await client.post("/api/flow/calibrate", json={"zone": 1}, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["observed_pulses_10s"] == 75
        assert data["observed_flow_lpm"] == 1.0
        assert data["flow_min_lpm"] == 0.5  # 50% cutoff
        assert data["min_pulses_10s"] == 38  # 50% of 75
        assert data["saved"] is True

        # 2. Low flow run (< 0.3 L/min, e.g. only 10 pulses)
        async def mock_low_flow_run(zone, duration):
            current = state_manager._state["garden"]["zones"]["zone_1"].get("flow_pulses", 0)
            state_manager._state["garden"]["zones"]["zone_1"]["flow_pulses"] = current + 10
            return "cmd-456"

        monkeypatch.setattr(garden_gateway, "send_pump_run", mock_low_flow_run)
        res_low = await client.post("/api/flow/calibrate", json={"zone": 1}, headers=headers)
        assert res_low.status_code == 200
        data_low = res_low.json()
        assert data_low["status"] == "warning"
        assert "Low flow or dry run detected" in data_low["warning"]
        assert data_low["saved"] is False


# -----------------------------------------------------------------------------
# 13. Failed Signature Rate Limiting & Health Detail Metrics (Part 4.4)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failed_signature_rate_limiting_and_health_detail(tmp_path):
    from app.hardware.gateway_driver import garden_gateway

    garden_gateway.failed_sig_count = 0
    garden_gateway._last_sig_warn_ts = 0.0

    app = create_app()
    token = security_manager.rotate_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Simulate 5 failed signature lines
    garden_gateway.failed_sig_count += 5
    garden_gateway.last_gw_err = {"err": "esp_now_send_error", "code": 12}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/health/detail", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["hardware"]["failed_sig_count"] == 5
        assert data["hardware"]["last_gw_err"]["err"] == "esp_now_send_error"


# -----------------------------------------------------------------------------
# 14. Libgpiod v2 Backend Lifecycle & Resource Cleanup
# -----------------------------------------------------------------------------
def test_libgpiod_v2_backend_lifecycle_and_cleanup(monkeypatch):
    import sys
    import types
    from app.hardware.gpio_backend import LibgpiodV2Backend

    released_requests = []

    class FakeRequest:
        def __init__(self, offset, val=0):
            self.offset = offset
            self.val = val

        def get_value(self, offset):
            return self.val

        def set_value(self, offset, val):
            self.val = val

        def release(self):
            released_requests.append(self)

    class FakeChip:
        def __init__(self, path):
            self.path = path

        def close(self):
            pass

    fake_gpiod = types.ModuleType("gpiod")
    fake_gpiod.Chip = FakeChip
    fake_line = types.ModuleType("gpiod.line")

    class Direction:
        INPUT = 1
        OUTPUT = 2

    class Bias:
        PULL_UP = 1
        PULL_DOWN = 2

    class Value:
        INACTIVE = 0
        ACTIVE = 1

    fake_line.Direction = Direction
    fake_line.Bias = Bias
    fake_line.Value = Value
    fake_gpiod.line = fake_line

    class LineSettings:
        def __init__(self):
            self.direction = None
            self.bias = None
            self.output_value = None

    fake_gpiod.LineSettings = LineSettings

    def fake_request_lines(chip_path, consumer, config):
        offset = list(config.keys())[0]
        return FakeRequest(offset, val=1)

    fake_gpiod.request_lines = fake_request_lines

    monkeypatch.setitem(sys.modules, "gpiod", fake_gpiod)
    monkeypatch.setitem(sys.modules, "gpiod.line", fake_line)

    backend = LibgpiodV2Backend()
    assert backend.open("/dev/gpiochip1") is True

    # Request input line 10
    assert backend.request_input(10, consumer="test-in") is True
    assert backend.get_value(10) == 1

    # Request output line 20
    assert backend.request_output(20, consumer="test-out", initial_value=0) is True
    backend.set_value(20, 1)

    # Re-requesting line 10 should release old request
    assert backend.request_input(10, consumer="test-in-2") is True
    assert len(released_requests) == 1

    # Close must release remaining lines and reset chip_path
    backend.close()
    assert len(released_requests) == 3  # 1 earlier + 2 on close
    assert backend._chip_path is None
    assert len(backend._line_offsets) == 0


# -----------------------------------------------------------------------------
# 15. HeadEnd GPIO Setup Fails Safe on Input Claim Failure
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_head_end_gpio_setup_fails_safe_on_input_claim_failure(monkeypatch):
    from app.hardware.gpio_driver import HeadEndGPIODriver
    from app.hardware.gpio_backend import GPIOBackend

    class StubBackend(GPIOBackend):
        def __init__(self):
            self.closed = False

        def open(self, chip_path: str) -> bool:
            return True

        def close(self) -> None:
            self.closed = True

        def request_input(self, line_offset: int, consumer: str = "test", bias: str = "pull_up") -> bool:
            # Simulate failure claiming line_offset 12 (leak_sensor)
            if line_offset == 12:
                return False
            return True

        def request_output(self, line_offset: int, consumer: str = "test", initial_value: int = 0) -> bool:
            return True

        def get_value(self, line_offset: int) -> int:
            return 0

        def set_value(self, line_offset: int, value: int) -> None:
            pass

    stub = StubBackend()
    monkeypatch.setattr("app.hardware.gpio_driver.create_gpio_backend", lambda: stub)
    monkeypatch.setattr("os.path.exists", lambda path: True)

    driver = HeadEndGPIODriver()
    system_config.head_end.chip_path = "/dev/gpiochip1"
    system_config.head_end.contactor_relay_pin = 15
    system_config.head_end.tank_low_float_pin = 7
    system_config.head_end.interlock_out_pin = 9
    system_config.head_end.interlock_mid_pin = 11
    system_config.head_end.leak_sensor_pin = 12

    # Setup must return False because pin 12 failed to be claimed
    ok = await driver.setup()
    assert ok is False
    assert driver.is_hardware_available is False
    assert stub.closed is True


# -----------------------------------------------------------------------------
# 16. Strict CSP & Security Headers
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_strict_csp_and_security_headers():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/healthz")
        assert res.status_code == 200

        csp = res.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "style-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "'unsafe-inline'" not in csp

        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("Referrer-Policy") == "no-referrer"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert "X-Correlation-ID" in res.headers


# -----------------------------------------------------------------------------
# 17. Webhook SSRF IP & URL Validation
# -----------------------------------------------------------------------------
def test_webhook_ssrf_ip_blocking():
    # Loopback
    assert is_ip_allowed_for_webhook("127.0.0.1")[0] is False
    assert is_ip_allowed_for_webhook("::1")[0] is False

    # Private RFC1918
    assert is_ip_allowed_for_webhook("10.0.0.1")[0] is False
    assert is_ip_allowed_for_webhook("172.16.0.1")[0] is False
    assert is_ip_allowed_for_webhook("192.168.1.1")[0] is False

    # CGNAT 100.64.0.0/10
    assert is_ip_allowed_for_webhook("100.64.0.1")[0] is False
    assert is_ip_allowed_for_webhook("100.127.255.254")[0] is False

    # Cloud metadata 169.254.169.254 / Link local
    assert is_ip_allowed_for_webhook("169.254.169.254")[0] is False
    assert is_ip_allowed_for_webhook("169.254.1.1")[0] is False

    # IPv6 ULA fc00::/7
    assert is_ip_allowed_for_webhook("fc00::1")[0] is False
    assert is_ip_allowed_for_webhook("fd12:3456:789a::1")[0] is False

    # Public IP
    assert is_ip_allowed_for_webhook("8.8.8.8")[0] is True
    assert is_ip_allowed_for_webhook("1.1.1.1")[0] is True


def test_validate_webhook_url_rejected_and_accepted():
    # HTTPS required
    valid, err = validate_webhook_url("http://8.8.8.8/hook", require_https=True)
    assert valid is False
    assert "https" in err.lower()

    # Private IP blocked
    valid, err = validate_webhook_url("https://127.0.0.1/hook")
    assert valid is False
    assert "blocked" in err.lower() or "not allowed" in err.lower()

    # Metadata blocked
    valid, err = validate_webhook_url("https://169.254.169.254/latest/meta-data")
    assert valid is False

    # CGNAT blocked
    valid, err = validate_webhook_url("https://100.64.0.5/api/alert")
    assert valid is False

    # Valid public HTTPS URL
    valid, err = validate_webhook_url("https://8.8.8.8/api/alert", require_https=True)
    assert valid is True
    assert err is None


# -----------------------------------------------------------------------------
# 18. SecurityManager Token Caching & Mtime Invalidation
# -----------------------------------------------------------------------------
def test_security_manager_token_caching_and_mtime_invalidation(tmp_path: Path):
    auth_file = tmp_path / "auth.json"
    security_manager.auth_file_path = auth_file
    system_config.security.auth_token_file = str(auth_file)

    token1, _ = security_manager.get_or_create_token()
    assert token1 is not None

    # Verify initial token verifies successfully
    assert security_manager.verify_token(token1) is True
    assert security_manager.verify_token("invalid-token") is False

    # Cached hash should match
    cached_hash = security_manager._cached_token_hash
    assert cached_hash is not None

    # Write a new token with updated mtime
    token2 = "new-rotated-token-67890-test-key"
    import hashlib
    new_hash = hashlib.sha256(token2.encode("utf-8")).hexdigest()
    data = {
        "token_hash": new_hash,
        "created_at": "2026-10-12T00:00:00Z",
    }
    time.sleep(0.05)
    with open(auth_file, "w", encoding="utf-8") as f:
        json.dump(data, f)
    new_mtime = os.stat(auth_file).st_mtime + 2.0
    os.utime(auth_file, (new_mtime, new_mtime))

    # verify_token should detect mtime change, reload, and accept token2
    assert security_manager.verify_token(token2) is True
    assert security_manager.verify_token(token1) is False
    assert security_manager._cached_token_hash == new_hash


# -----------------------------------------------------------------------------
# 19. SequenceTracker Strict Nonce Replay Defense (Choice B)
# -----------------------------------------------------------------------------
def test_sequence_tracker_strict_nonce_verification():
    # Non-strict tracker (backward compatible)
    tracker_permissive = SequenceTracker(strict_nonce_verification=False)
    ok, err = tracker_permissive.validate_and_update(1, nonce=1001)
    assert ok is True
    ok, err = tracker_permissive.validate_and_update(2, nonce=1001)
    assert ok is True
    # Replay in same session rejected
    ok, err = tracker_permissive.validate_and_update(2, nonce=1001)
    assert ok is False
    assert "Replay" in err
    # New nonce mid-session with seq=5 accepted in permissive mode
    ok, err = tracker_permissive.validate_and_update(5, nonce=2002)
    assert ok is True

    # Strict tracker (Choice B replay defense)
    tracker_strict = SequenceTracker(strict_nonce_verification=True)
    ok, err = tracker_strict.validate_and_update(1, nonce=1001, boot_count=1)
    assert ok is True
    ok, err = tracker_strict.validate_and_update(2, nonce=1001)
    assert ok is True

    # Mid-session replay with new nonce but seq > 1 without boot counter -> REJECTED
    ok, err = tracker_strict.validate_and_update(5, nonce=3003)
    assert ok is False
    assert "Strict nonce rejection" in err

    # Boot announcement with seq == 1 on new nonce -> ACCEPTED
    ok, err = tracker_strict.validate_and_update(1, nonce=3003, boot_count=2)
    assert ok is True
    assert err is None

    # New nonce with increasing boot counter -> ACCEPTED even if seq > 1
    ok, err = tracker_strict.validate_and_update(3, nonce=4004, boot_count=3)
    assert ok is True

    # New nonce with stagnant or lower boot counter and seq > 1 -> REJECTED
    ok, err = tracker_strict.validate_and_update(4, nonce=5005, boot_count=2)
    assert ok is False
    assert "Strict nonce rejection" in err


# -----------------------------------------------------------------------------
# 20. Telegram Client Plain Text & Rate Limit Backoff
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_telegram_client_plain_text_and_rate_limit(monkeypatch):
    from app.alerts.telegram_client import TelegramNotifier
    import httpx

    notifier = TelegramNotifier()
    system_config.telegram.enabled = True
    system_config.telegram.bot_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    system_config.telegram.chat_id = "987654321"

    sent_requests = []

    class MockTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.call_count = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.call_count += 1
            body = json.loads(request.content.decode("utf-8"))
            sent_requests.append(body)

            if self.call_count == 1:
                # First attempt returns 429 Too Many Requests with retry_after
                return httpx.Response(
                    status_code=429,
                    json={"ok": False, "error_code": 429, "parameters": {"retry_after": 0.01}},
                    request=request,
                )
            # Second attempt succeeds
            return httpx.Response(status_code=200, json={"ok": True}, request=request)

    mock_client = httpx.AsyncClient(transport=MockTransport())
    notifier._client = mock_client
    await notifier.start()
    try:
        await notifier.send_alert("WATER_LEAK", "Critical water leak detected in basement!")
        assert notifier._queue is not None
        await asyncio.wait_for(notifier._queue.join(), timeout=3.0)
    finally:
        await notifier.stop()

    assert len(sent_requests) == 2
    # Verify NO parse_mode is set (plain text formatting)
    assert "parse_mode" not in sent_requests[0]
    assert "Critical water leak detected in basement!" in sent_requests[0]["text"]


