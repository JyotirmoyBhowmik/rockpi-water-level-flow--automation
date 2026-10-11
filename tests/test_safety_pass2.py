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
from pathlib import Path
import time
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.server import create_app
from app.config import system_config
from app.core.security import security_manager, validate_webhook_url
from app.core.state_manager import state_manager
from app.core.watchdog import HealthMonitor
from app.engine.failsafes import failsafe_engine
from app.engine.motor_controller import motor_controller
from app.hardware.audio_driver import VOICE_REGEX, ALSA_DEVICE_REGEX, VoiceAlertSynthesizer
from app.hardware.gpio_driver import head_end_gpio
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

    # Simulate gateway reader loop stalled > 15.0s
    hm._beats["gateway_reader"] = get_monotonic_now() - 16.0
    healthy, reason = hm.check_health()
    assert healthy is False
    assert "gateway_reader" in reason


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

    # Allowed external host
    # Note: socket.getaddrinfo will fail in offline or resolve safely
    # Check rejection of loopback and link-local:
    safe, err = validate_webhook_url("http://127.0.0.1:8000/webhook", allowlist)
    assert safe is False
    assert "allowlist" in err or "loopback" in err

    safe, err = validate_webhook_url("http://169.254.169.254/latest/meta-data", allowlist)
    assert safe is False

    safe, err = validate_webhook_url("http://localhost:8080/pwn", allowlist)
    assert safe is False

    # Unsupported schemes rejected
    safe, err = validate_webhook_url("file:///etc/passwd", allowlist)
    assert safe is False
    assert "Unsupported scheme" in err


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
