"""
Pytest Fixtures and Test Environment Bootstrap for Smart Water Controller.
Pure safety logic testing with isolated SQLite database and zero hardware dependencies.
"""

from pathlib import Path
import pytest_asyncio
from app.core.event_bus import bus
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine


import asyncio
from app.config import system_config
from app.engine.motor_controller import motor_controller


@pytest_asyncio.fixture(autouse=True)
async def setup_test_environment(tmp_path: Path):
    """Initializes clean database and fresh state store for each safety test."""
    test_db = tmp_path / "test_safety.db"
    state_manager.db_path = test_db
    state_manager._lock = asyncio.Lock()
    motor_controller._lock = asyncio.Lock()

    # Default to setup_complete=True for logic tests unless specifically testing wizard
    system_config.setup_complete = True

    # Initialize empty SQLite tables
    await state_manager.init_db()
    await bus.start()

    # Reset in-memory safety state
    state_manager._state["motor"]["state"] = "OFF"
    state_manager._state["motor"]["last_stop_monotonic"] = None
    state_manager._state["motor"]["cooldown_until_ts"] = 0.0
    state_manager._state["motor"]["manual_locked"] = False
    state_manager._state["head_end"]["healthy_debounce_count"] = 3
    state_manager._state["head_end"]["interlock_cause"] = "healthy"
    state_manager._state["alarms"] = {}

    failsafe_engine.wiring_fault_active = False
    failsafe_engine.motor_timeout_tripped = False
    failsafe_engine.motor_cooldown_until = 0.0
    failsafe_engine.manual_motor_lockout = False
    failsafe_engine.leak_alarm_active = False
    import time
    now = time.time()
    state_manager._state["garden"]["gateway_connected"] = True
    state_manager._state["garden"]["node_online"] = True
    state_manager._state["garden"]["is_stale"] = False
    state_manager._state["garden"]["last_packet_ts"] = now
    state_manager._state["garden"]["last_heartbeat_ts"] = now
    state_manager._state["garden"]["node_last_seen"] = now
    state_manager._state["garden"]["gateway_last_seen"] = now
    state_manager._state["garden"]["reservoir_last_update_ts"] = now
    for z in state_manager._state["garden"]["zones"].values():
        z["last_update_ts"] = now

    yield

    await bus.stop()
