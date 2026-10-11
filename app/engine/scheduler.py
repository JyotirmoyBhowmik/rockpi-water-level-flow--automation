"""
Periodic Task Scheduler & Hardware Loop Coordinator for Rock Pi S.
Schedules failsafe checks, ADC/GPIO sampling, rule evaluations, and telemetry streaming.
"""

import asyncio
import logging
from typing import Optional
from app.config import settings
from app.core.event_bus import bus
from app.core.state_manager import state_manager
from app.core.watchdog import watchdog_manager
from app.engine.failsafes import failsafe_engine
from app.engine.rules_evaluator import rules_evaluator
from app.hardware.adc_driver import adc_controller
from app.hardware.gpio_driver import gpio_controller
from app.hardware.mock import mock_fluid

logger = logging.getLogger("smart_water.scheduler")


class SystemScheduler:
    """
    Main orchestration loop coordinating real-time hardware polling,
    safety interlock verification, and automation rules execution.
    """

    def __init__(self):
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._rules_task: Optional[asyncio.Task] = None
        self._maintenance_task: Optional[asyncio.Task] = None
        self._db_log_counter = 0

    async def start(self) -> None:
        """Starts all background loops."""
        if self._running:
            return
        self._running = True

        self._poll_task = asyncio.create_task(self._hardware_poll_loop(), name="hardware_poll_loop")
        self._rules_task = asyncio.create_task(self._rules_loop(), name="rules_eval_loop")
        self._maintenance_task = asyncio.create_task(self._maintenance_loop(), name="db_maintenance_loop")
        logger.info("SystemScheduler started background task loops.")

    async def stop(self) -> None:
        """Gracefully halts all periodic loops."""
        self._running = False
        for t in [self._poll_task, self._rules_task, self._maintenance_task]:
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        logger.info("SystemScheduler stopped.")

    async def _hardware_poll_loop(self) -> None:
        """
        High-frequency loop (1Hz): Reads physical sensors, updates state,
        and enforces Priority 0 safety interlocks.
        """
        while self._running:
            try:
                # 1. Advance simulation step if in mock mode
                if settings.MOCK_HARDWARE:
                    mock_fluid.step()

                # 2. Read GPIO Float Inputs
                tank_low = await gpio_controller.read_pin(settings.PIN_TANK_LOW_FLOAT)
                tank_high = await gpio_controller.read_pin(settings.PIN_TANK_HIGH_FLOAT)
                res_empty = await gpio_controller.read_pin(settings.PIN_RESERVOIR_LOW_FLOAT)

                # 3. Read ADS1115 Soil Probe Analog Voltages
                probes = await adc_controller.read_all_channels()

                # 4. Update in-memory state
                await state_manager.update_sensors(
                    tank_low=tank_low,
                    tank_high=tank_high,
                    reservoir_empty=res_empty,
                    probes=probes,
                )

                # 5. Non-bypassable Priority 0 Failsafes
                await failsafe_engine.evaluate_safety_interlocks(
                    tank_low=tank_low,
                    tank_high=tank_high,
                    reservoir_empty=res_empty,
                    soil_voltages=probes,
                )

                # 6. Publish telemetry to bus for SSE live streams
                current_state = state_manager.get_state()
                await bus.publish("telemetry:update", current_state)

                # 7. Periodic telemetry snapshot to SQLite (every 30s)
                self._db_log_counter += 1
                if self._db_log_counter >= 30:
                    self._db_log_counter = 0
                    await state_manager.log_telemetry_snapshot()

                await asyncio.sleep(settings.TELEMETRY_SAMPLE_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in hardware poll loop: %s", e, exc_info=True)
                await asyncio.sleep(1.0)

    async def _rules_loop(self) -> None:
        """
        Medium-frequency loop (every 5s): Evaluates Low-Code user automation rules.
        """
        while self._running:
            try:
                await rules_evaluator.evaluate_tick()
                await asyncio.sleep(settings.RULES_EVALUATION_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in rules evaluation loop: %s", e)
                await asyncio.sleep(5.0)

    async def _maintenance_loop(self) -> None:
        """
        Low-frequency maintenance loop (hourly): Prunes old database records
        to protect SD card wear and keep SQLite compact.
        """
        while self._running:
            try:
                await asyncio.sleep(3600)
                await state_manager.prune_old_telemetry()
                logger.info("Executed SQLite maintenance: purged old telemetry records.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in maintenance loop: %s", e)


scheduler = SystemScheduler()
