"""
Pytest Fixtures and Test Environment Bootstrap.
"""

import asyncio
import os
import tempfile
from pathlib import Path
import pytest
import pytest_asyncio

# Force mock mode for test execution
os.environ["MOCK_HARDWARE"] = "true"

from app.config import settings
from app.core.event_bus import bus
from app.core.state_manager import state_manager
from app.engine.rules_evaluator import rules_evaluator
from app.hardware.mock import mock_adc, mock_audio, mock_fluid, mock_gpio


@pytest_asyncio.fixture(autouse=True)
async def setup_test_environment(tmp_path: Path):
    """Initializes clean database and resets mock hardware before each test."""
    test_db = tmp_path / "test_system.db"
    state_manager.db_path = test_db

    # Reset mock hardware states
    await mock_gpio.setup()
    await mock_adc.setup()
    await mock_audio.start()
    mock_fluid.overhead_tank_liters = 500.0
    mock_fluid.reservoir_liters = 400.0
    mock_fluid.step()

    # Clear ADC overrides
    for ch in range(4):
        mock_adc.clear_channel_override(ch)

    await state_manager.init_db()
    await bus.start()

    yield

    rules_evaluator.cancel_all_timed_tasks()
    await bus.stop()
    await mock_audio.stop()
