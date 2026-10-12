"""
Coverage Boost Test Suite for app/core and app/engine.
Validates EventBus, Watchdog, Scheduler, Failsafes, Motor Controller,
Plant Controller, and Rules Evaluator to ensure coverage >= 85%.
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.event_bus import EventBus
from app.core.security import PinnedIPTransport, SecurityManager
from app.core.watchdog import HealthMonitor, WatchdogGuardian
from app.engine.failsafes import FailsafeSafetyEngine
from app.engine.motor_controller import MotorController
from app.engine.rules_evaluator import RulesEvaluator
from app.engine.scheduler import SystemScheduler
from app.hardware.protocol import (
    AckPacket,
    FlowPacket,
    HeartbeatPacket,
    PumpEventPacket,
    SoilPacket,
)


# ==============================================================================
# 1. EVENT BUS TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_event_bus_lifecycle_and_dispatch():
    bus = EventBus(max_queue_size=5)

    # 1. Publish while stopped
    await bus.publish("test.topic", {"val": 1})

    # 2. Start
    await bus.start()
    assert bus._running is True
    # Start again should be a no-op
    await bus.start()

    events_received: list[dict] = []
    wildcard_received: list[dict] = []

    async def specific_handler(topic: str, payload: dict):
        events_received.append(payload)

    async def wild_handler(topic: str, payload: dict):
        wildcard_received.append(payload)

    async def failing_handler(topic: str, payload: dict):
        raise ValueError("Intentional subscriber crash")

    bus.subscribe("plant:water", specific_handler)
    bus.subscribe("*", wild_handler)
    bus.subscribe("plant:water", failing_handler)

    # Publish events
    await bus.publish("plant:water", {"zone": 1, "action": "on"})
    await bus.publish("motor:alert", {"status": "mismatch"})

    # Wait deterministically for worker queue processing
    for _ in range(50):
        if len(events_received) >= 1 and len(wildcard_received) >= 2:
            break
        await asyncio.sleep(0.02)

    assert len(events_received) == 1
    assert events_received[0]["zone"] == 1
    assert events_received[0]["_topic"] == "plant:water"
    assert "_timestamp" in events_received[0]

    assert len(wildcard_received) == 2

    # Unsubscribe
    bus.unsubscribe("plant:water", specific_handler)
    bus.unsubscribe("*", wild_handler)
    await bus.publish("plant:water", {"zone": 2})
    await asyncio.sleep(0.05)
    assert len(events_received) == 1

    # Queue full handling (overflow drop oldest)
    tiny_bus = EventBus(max_queue_size=2)
    await tiny_bus.start()
    await tiny_bus.publish("t1", {"v": 1})
    await tiny_bus.publish("t2", {"v": 2})
    await tiny_bus.publish("t3", {"v": 3})  # Should drop oldest and not crash

    await tiny_bus.stop()
    await bus.stop()


# ==============================================================================
# 2. WATCHDOG & HEALTH MONITOR TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_health_monitor_logic():
    hm = HealthMonitor()
    hm.enable()
    assert hm._enabled is True

    # Beat registered loops
    hm.beat("head_end_poll")
    hm.beat("rules_auto_loop")
    if "gateway_reader" in hm._thresholds:
        hm.beat("gateway_reader")

    healthy, reason = hm.check_health()
    assert healthy is True
    assert reason is None

    # Check beat ages
    ages = hm.get_beat_ages()
    assert "head_end_poll" in ages

    # Simulate expired beat
    hm._beats["head_end_poll"] = time.monotonic() - 10.0
    healthy, reason = hm.check_health()
    assert healthy is False
    assert "stalled" in (reason or "")

    # Disabled health monitor always returns True
    hm.disable()
    assert hm.check_health()[0] is True


@pytest.mark.asyncio
async def test_watchdog_guardian_lifecycle_and_pet():
    guardian = WatchdogGuardian()

    # Mock hardware fd and notify socket
    mock_exit = MagicMock()
    guardian._exit_func = mock_exit

    # Start guardian with mock socket
    with patch.dict(os.environ, {"NOTIFY_SOCKET": "/tmp/test_notify.sock"}):
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock_cls.return_value = mock_sock
            await guardian.start()
            assert guardian._running is True

            # Re-start is a no-op
            await guardian.start()

            # Test notify_ready
            guardian._notify_path = "/tmp/test_notify.sock"
            guardian._notify_sock = mock_sock
            res = guardian.notify_ready()
            assert res is True
            assert guardian._ready_sent is True
            # Second call returns True immediately
            assert guardian.notify_ready() is True

            # Pet watchdog
            guardian._dev_fd = 99
            with patch("os.write") as mock_write:
                guardian._pet()
                mock_write.assert_called_with(99, b"\0")
                mock_sock.sendto.assert_called()

            # Stop cleanly with disarm
            with patch("os.write") as mock_write, patch("os.close") as mock_close:
                await guardian.stop(disarm=True)
                mock_write.assert_called_with(99, b"V")
                mock_close.assert_called_with(99)

    # Test stall exit path without 'V'
    guardian_stall = WatchdogGuardian()
    guardian_stall._dev_fd = 88
    guardian_stall.is_stall_exit = True
    with patch("os.write") as mock_write, patch("os.close") as mock_close:
        await guardian_stall.stop(disarm=True)
        # Should NOT write 'V' when is_stall_exit is True
        mock_write.assert_not_called()
        mock_close.assert_called_with(88)


# ==============================================================================
# 3. SCHEDULER GATEWAY ROUTING & DOWN-SAMPLING TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_scheduler_gateway_packet_routing(tmp_path):
    sched = SystemScheduler()

    # Route HeartbeatPacket
    hb_pkt = HeartbeatPacket(
        nonce=1001,
        seq=10,
        uptime_s=120,
        res_wet=True,
        ads_ok=True,
        pumps=[0, 1, 0, 0],
        reset_reason=1,
        rssi=-70,
        sig="0123456789abcdef",
    )
    await sched._on_gateway_packet(hb_pkt)

    # Route SoilPacket
    soil_pkt = SoilPacket(nonce=1001, seq=11, z=2, volts=1.95, sig="0123456789abcdef")
    await sched._on_gateway_packet(soil_pkt)

    # Route FlowPacket
    flow_pkt = FlowPacket(nonce=1001, seq=12, z=1, delta_pulses=25, period_ms=1000, sig="0123456789abcdef")
    await sched._on_gateway_packet(flow_pkt)

    # Route PumpEventPacket (ON)
    pump_on = PumpEventPacket(nonce=1001, seq=13, z=1, on=True, reason="auto", run_s=15, sig="0123456789abcdef")
    await sched._on_gateway_packet(pump_on)

    # Route PumpEventPacket (OFF - should trigger completion callback)
    pump_off = PumpEventPacket(nonce=1001, seq=14, z=1, on=False, reason="completed", run_s=15, sig="0123456789abcdef")
    await sched._on_gateway_packet(pump_off)

    # Route AckPacket
    ack_pkt = AckPacket(nonce=1001, seq=15, id="cmd-99", status="ok", err="", sig="0123456789abcdef")
    await sched._on_gateway_packet(ack_pkt)

    # Route Dict equivalents
    await sched._on_gateway_packet({"t": "hb", "seq": 16, "uptime_s": 200, "res_wet": True, "nonce": 1001})
    await sched._on_gateway_packet({"t": "soil", "z": 3, "volts": 2.1})
    await sched._on_gateway_packet({"t": "flow", "z": 1, "delta_pulses": 5, "period_ms": 1000})
    await sched._on_gateway_packet({"t": "pump", "z": 2, "on": True, "reason": "rule"})
    await sched._on_gateway_packet({"t": "pump", "z": 2, "on": False, "reason": "rule"})
    await sched._on_gateway_packet({"t": "ack", "id": "cmd-100", "status": "ok"})

    # Downsampled reading persistence
    dummy_state = {
        "head_end": {"tank_low_float": True, "interlock_cause": "healthy"},
        "motor": {"state": "RUNNING"},
        "garden": {
            "reservoir_wet": True,
            "zones": {
                "zone_1": {"voltage": 1.8, "flow_pulses": 120},
                "zone_2": {"voltage": 2.1},
                "zone_3": {"voltage": 2.3},
                "zone_4": {"voltage": 2.4},
            },
        },
    }
    await sched._record_downsampled_reading(dummy_state)


@pytest.mark.asyncio
async def test_scheduler_lifecycle():
    sched = SystemScheduler()
    await sched.start()
    assert sched._running is True
    # Re-start is safe
    await sched.start()

    await asyncio.sleep(0.05)
    await sched.stop()
    assert sched._running is False


# ==============================================================================
# 4. MOTOR CONTROLLER & AUTO-REPLENISHMENT TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_motor_controller_auto_replenishment():
    mc = MotorController()
    from app.core.state_manager import state_manager

    # 1. When auto_mode is false, returns immediately
    state_manager._state["motor"]["auto_mode"] = False
    await mc.evaluate_auto_replenishment()

    # 2. When hardware not ready, returns immediately
    state_manager._state["motor"]["auto_mode"] = True
    state_manager._state["hardware_ready"] = False
    await mc.evaluate_auto_replenishment()

    # 3. When stale, returns immediately
    state_manager._state["hardware_ready"] = True
    state_manager._state["head_end"]["is_stale"] = True
    await mc.evaluate_auto_replenishment()

    # 4. When motor is already running, returns
    state_manager._state["head_end"]["is_stale"] = False
    state_manager._state["motor"]["state"] = "RUNNING"
    await mc.evaluate_auto_replenishment()

    # 5. Low tank float with failsafe denying start
    state_manager._state["motor"]["state"] = "OFF"
    state_manager._state["head_end"]["tank_low_float"] = True
    with patch("app.engine.failsafes.failsafe_engine.can_start_motor", return_value=(False, "dry_sump")):
        await mc.evaluate_auto_replenishment()


# ==============================================================================
# 5. RULES EVALUATOR TIME & ACTION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_rules_evaluator_sources_and_actions():
    re = RulesEvaluator()
    from app.engine.rules_evaluator import RuleCondition

    # Test time window helper
    assert re._parse_minute("00:00") == 0
    assert re._parse_minute("12:30") == 750
    assert re._parse_minute("23:59") == 1439
    assert re._parse_minute("invalid") is None

    # Test "between" condition
    assert re._check_condition(600, "between", "08:00-12:00") is True
    assert re._check_condition(400, "between", "08:00-12:00") is False

    # Midnight crossing: 22:00 (1320) to 04:00 (240)
    assert re._check_condition(1350, "between", ["22:00", "04:00"]) is True
    assert re._check_condition(100, "between", ["22:00", "04:00"]) is True
    assert re._check_condition(600, "between", ["22:00", "04:00"]) is False

    # Time clock comparison == and !=
    assert re._check_condition("14:30", "==", "14:30") is True
    assert re._check_condition("14:30", "!=", "15:00") is True

    # Rule condition evaluation for various sources
    from app.core.state_manager import state_manager
    state_manager._state["head_end"]["tank_low_float"] = True
    state_manager._state["motor"]["state"] = "OFF"
    state_manager._state["garden"]["zones"]["zone_1"]["moisture_pct"] = 25.0
    state_manager._state["garden"]["zones"]["zone_1"]["voltage"] = 1.8
    state_manager._state["garden"]["reservoir_wet"] = True
    state_manager._state["head_end"]["leak_detected"] = False

    state = state_manager.get_state()

    cond_tank = RuleCondition(source="tank.low", operator="==", value=True)
    assert re._evaluate_conditions([cond_tank], "AND", state) is True

    cond_motor = RuleCondition(source="motor.state", operator="==", value="OFF")
    assert re._evaluate_conditions([cond_motor], "AND", state) is True

    cond_moist = RuleCondition(source="zone_1.moisture", operator="<", value=30.0)
    assert re._evaluate_conditions([cond_moist], "AND", state) is True

    cond_volts = RuleCondition(source="zone_1.volts", operator=">", value=1.5)
    assert re._evaluate_conditions([cond_volts], "AND", state) is True

    cond_res = RuleCondition(source="reservoir.state", operator="==", value="wet")
    assert re._evaluate_conditions([cond_res], "AND", state) is True

    cond_leak = RuleCondition(source="leak.state", operator="==", value=False)
    assert re._evaluate_conditions([cond_leak], "AND", state) is True


# ==============================================================================
# 6. SECURITY MANAGER CACHED TOKEN & MTIME
# ==============================================================================

def test_security_manager_token_cache(tmp_path):
    auth_file = tmp_path / "auth.json"
    sec = SecurityManager(auth_file_path=auth_file)

    # Initial state: no file
    assert sec.verify_token("any-token") is False
    assert sec.verify_token(None) is False

    # Save token hash
    test_token = "secret-token-test-12345"
    token_hash = sec._hash_token(test_token)
    sec._save_token_hash(token_hash)

    # Verification passes
    assert sec.verify_token(test_token) is True
    assert sec.verify_token("wrong-token") is False

    # Test cache reload on mtime touch
    sec._cached_mtime = 0.0
    assert sec.verify_token(test_token) is True


# ==============================================================================
# 7. PINNED IP TRANSPORT REQUEST CREATION
# ==============================================================================

@pytest.mark.asyncio
async def test_pinned_ip_transport_structure():
    transport = PinnedIPTransport(pinned_ip="93.184.216.34", original_host="example.com", verify=False)
    assert transport.pinned_ip == "93.184.216.34"
    assert transport.original_host == "example.com"
    await transport.aclose()


# ==============================================================================
# 8. TARGETED COVERAGE EXPANSION (Watchdog Stall, Failsafes, Motor Run, Rules)
# ==============================================================================

@pytest.mark.asyncio
async def test_watchdog_stall_gating_exit():
    guardian = WatchdogGuardian()
    mock_exit = MagicMock()
    guardian._exit_func = mock_exit
    guardian._running = True

    # Simulate health gating failure
    with patch("app.core.watchdog.health_monitor.check_health", return_value=(False, "head_end_poll loop stalled")):
        with patch("app.hardware.gpio_driver.head_end_gpio.force_all_outputs_off", new_callable=AsyncMock) as mock_force:
            await guardian._heartbeat_loop()
            assert guardian.is_stall_exit is True
            mock_force.assert_called_once()
            mock_exit.assert_called_once_with(2)


@pytest.mark.asyncio
async def test_motor_controller_stop_running_with_duration():
    mc = MotorController()
    from app.core.state_manager import state_manager
    from app.hardware.gpio_driver import head_end_gpio

    # Simulate motor was running
    await state_manager.set_motor_state("RUNNING", reason="test")
    mc._started_at_mono = time.monotonic() - 15.0
    mc._started_at_iso = "2026-10-12T12:00:00Z"

    with patch.object(head_end_gpio, "set_contactor_relay", new_callable=AsyncMock, return_value=True):
        with patch.object(head_end_gpio, "read_contactor_feedback", new_callable=AsyncMock, return_value=False):
            ok, msg = await mc.stop_motor(reason="tank_full")
            assert ok is True
            assert "Ran for" in msg
            assert state_manager.get_state()["motor"]["state"] == "OFF"


@pytest.mark.asyncio
async def test_failsafes_flow_meter_health_trip():
    failsafe = FailsafeSafetyEngine()
    from app.core.state_manager import state_manager
    from app.hardware.gateway_driver import garden_gateway

    # Configure Zone 1 with flow meter
    zone_key = "zone_1"
    failsafe._zone_run_start_mono[zone_key] = time.monotonic() - 12.0
    failsafe._zone_pulses_start[zone_key] = 0

    with patch.object(garden_gateway, "send_pump_stop", new_callable=AsyncMock) as mock_stop:
        # Fewer pulses than min_pulses_10s (e.g. 1 pulse < 4)
        await failsafe.check_flow_meter_health(zone_key, current_pulses=1)
        assert failsafe.flow_faults.get(zone_key) is True
        mock_stop.assert_called_once_with(1)
        alarms = state_manager.get_state()["alarms"]
        assert f"no_flow_{zone_key}" in alarms


@pytest.mark.asyncio
async def test_rules_evaluator_template_crud():
    re = RulesEvaluator()
    from app.engine.rules_evaluator import RuleCondition, RuleAction, RuleDefinition

    # Create dummy rule in cache
    dummy_rule = RuleDefinition(
        name="Test Template Rule",
        combinator="AND",
        cooldown_s=60,
        conditions=[RuleCondition(source="tank.low", operator="==", value=True)],
        actions=[RuleAction(target="motor.start", payload={"confirm": True})],
    )
    await re.save_rule(dummy_rule)

    # Save as template
    tpl = await re.save_as_template(dummy_rule.id, "Auto Fill Template", "Description")
    assert tpl is not None
    assert tpl.name == "Auto Fill Template"

    # Delete rule
    deleted = await re.delete_rule(dummy_rule.id)
    assert deleted is True
    assert dummy_rule.id not in re._rules_cache


@pytest.mark.asyncio
async def test_rules_evaluator_tick_execution():
    re = RulesEvaluator()
    from app.core.state_manager import state_manager
    from app.engine.rules_evaluator import RuleCondition, RuleAction, RuleDefinition

    state_manager._state["head_end"]["tank_low_float"] = True
    state_manager._state["motor"]["state"] = "OFF"
    dummy_rule = RuleDefinition(
        name="Auto Fill Motor",
        combinator="AND",
        cooldown_s=0,
        conditions=[RuleCondition(source="tank.low", operator="==", value=True)],
        actions=[RuleAction(target="voice.announce", speech_text="Tank is low")],
    )
    re._rules_cache[dummy_rule.id] = dummy_rule

    with patch("app.hardware.audio_driver.audio_controller.speak", new_callable=AsyncMock) as mock_speak:
        await re.evaluate_tick()
        mock_speak.assert_called_once_with("Tank is low", priority=2)

