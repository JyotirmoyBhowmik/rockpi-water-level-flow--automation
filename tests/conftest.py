"""
Pytest Fixtures and Test Environment Bootstrap for Smart Water Controller.
Pure safety logic testing with isolated SQLite database and zero hardware dependencies.
"""

from pathlib import Path
import pytest
import pytest_asyncio
from app.core.event_bus import bus
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine


@pytest_asyncio.fixture(autouse=True)
async def setup_test_environment(tmp_path: Path):
    """Initializes clean database and fresh state store for each safety test."""
    test_db = tmp_path / "test_safety.db"
    state_manager.db_path = test_db

    # Initialize empty SQLite tables
    await state_manager.init_db()
    await bus.start()

    # Reset in-memory safety state
    failsafe_engine.wiring_fault_active = False
    failsafe_engine.motor_timeout_tripped = False
    failsafe_engine.motor_cooldown_until = 0.0
    failsafe_engine.manual_motor_lockout = False
    failsafe_engine.leak_alarm_active = False
    failsafe_engine.flow_faults = {f"zone_{i}": False for i in range(1, 5)}

    yield

    await bus.stop()
