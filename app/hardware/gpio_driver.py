"""
Linux Character Device GPIO Driver for Radxa ROCK Pi S (/dev/gpiochip1).
Uses libgpiod v1/v2 bindings with automatic fallback to mock simulation.
"""

import asyncio
import logging
import os
from typing import Dict, Optional
from app.config import settings
from app.hardware.base import BaseGPIOController
from app.hardware.mock import mock_gpio

logger = logging.getLogger("smart_water.gpio")

try:
    import gpiod
    HAS_GPIOD = True
except ImportError:
    gpiod = None
    HAS_GPIOD = False


class RealGPIODriver(BaseGPIOController):
    """
    Physical libgpiod Linux character device driver for ROCK Pi S.
    """

    def __init__(self, chip_path: str = settings.GPIO_CHIP_PATH):
        self.chip_path = chip_path
        self._chip = None
        self._input_lines = None
        self._output_lines = None
        self._cached_outputs: Dict[int, bool] = {}

    async def setup(self) -> None:
        """Claims GPIO lines via libgpiod."""
        def _open():
            self._chip = gpiod.Chip(self.chip_path)
            # Setup input lines (Float switches: active low)
            in_pins = [
                settings.PIN_TANK_LOW_FLOAT,
                settings.PIN_TANK_HIGH_FLOAT,
                settings.PIN_RESERVOIR_LOW_FLOAT,
            ]
            self._input_lines = self._chip.get_lines(in_pins)
            req = gpiod.LineRequest()
            req.consumer = "smart-water-in"
            req.request_type = gpiod.line_request.DIRECTION_INPUT
            # Configure pull-up if supported by kernel driver
            try:
                req.flags = gpiod.line_request.FLAG_BIAS_PULL_UP
            except AttributeError:
                pass
            self._input_lines.request(req)

            # Setup output lines (AC Contactor, Zone 1-4 MOSFETs)
            out_pins = [
                settings.PIN_AC_CONTACTOR,
                settings.PIN_ZONE_1_MOSFET,
                settings.PIN_ZONE_2_MOSFET,
                settings.PIN_ZONE_3_MOSFET,
                settings.PIN_ZONE_4_MOSFET,
            ]
            self._output_lines = self._chip.get_lines(out_pins)
            out_req = gpiod.LineRequest()
            out_req.consumer = "smart-water-out"
            out_req.request_type = gpiod.line_request.DIRECTION_OUTPUT
            self._output_lines.request(out_req, default_values=[0] * len(out_pins))

            for p in out_pins:
                self._cached_outputs[p] = False

        await asyncio.to_thread(_open)
        logger.info("Physical libgpiod claimed lines on %s successfully.", self.chip_path)

    async def read_pin(self, line_offset: int) -> bool:
        """
        Reads line state.
        For active LOW floats, a closed switch connects to GND (reads 0).
        Returns True if switch is tripped (Active state).
        """
        def _read():
            line = self._chip.get_line(line_offset)
            raw = line.get_value()
            # Active LOW logic: 0 = tripped (True), 1 = idle (False)
            return raw == 0

        return await asyncio.to_thread(_read)

    async def set_pin(self, line_offset: int, state: bool) -> None:
        """Drives output pin HIGH (1) or LOW (0)."""
        def _write():
            line = self._chip.get_line(line_offset)
            line.set_value(1 if state else 0)
            self._cached_outputs[line_offset] = state

        await asyncio.to_thread(_write)

    async def cleanup(self) -> None:
        def _close():
            if self._input_lines:
                self._input_lines.release()
            if self._output_lines:
                self._output_lines.release()
            if self._chip:
                self._chip.close()

        await asyncio.to_thread(_close)
        logger.info("Physical libgpiod lines released.")


def get_gpio_controller() -> BaseGPIOController:
    """Factory selecting physical libgpiod driver or virtual mock."""
    if settings.MOCK_HARDWARE or not HAS_GPIOD or not os.path.exists(settings.GPIO_CHIP_PATH):
        if not settings.MOCK_HARDWARE:
            logger.warning(
                "Hardware GPIO requested, but %s not accessible or gpiod missing. Falling back to MockGPIOController.",
                settings.GPIO_CHIP_PATH,
            )
        return mock_gpio
    return RealGPIODriver(chip_path=settings.GPIO_CHIP_PATH)


gpio_controller = get_gpio_controller()
