"""
High-Fidelity Hardware Simulation Layer with Stochastic Drift & Dynamic Physics.
Virtualizes RK3308 gpiochip1, ADS1115 I2C ADC registers, and audio playback.
"""

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from app.config import settings
from app.hardware.base import BaseADCController, BaseAudioController, BaseGPIOController

logger = logging.getLogger("smart_water.mock")


class MockGPIOController(BaseGPIOController):
    """
    Virtual character device driver mimicking Linux /dev/gpiochip1.
    """

    def __init__(self):
        # Digital pin states: line_offset -> bool
        self._pins: Dict[int, bool] = {
            settings.PIN_TANK_LOW_FLOAT: False,       # Active LOW (False = HIGH = dry float)
            settings.PIN_TANK_HIGH_FLOAT: False,      # Active LOW (False = HIGH = not full)
            settings.PIN_RESERVOIR_LOW_FLOAT: False,  # Active LOW (False = HIGH = plenty of water)
            settings.PIN_AC_CONTACTOR: False,         # Active HIGH (False = OFF)
            settings.PIN_ZONE_1_MOSFET: False,        # Active HIGH
            settings.PIN_ZONE_2_MOSFET: False,
            settings.PIN_ZONE_3_MOSFET: False,
            settings.PIN_ZONE_4_MOSFET: False,
        }
        self._initialized = False

    async def setup(self) -> None:
        self._initialized = True
        logger.info("MockGPIOController initialized with RK3308 pin mappings.")

    async def read_pin(self, line_offset: int) -> bool:
        return self._pins.get(line_offset, False)

    async def set_pin(self, line_offset: int, state: bool) -> None:
        self._pins[line_offset] = state
        logger.debug("Mock GPIO Line %d driven %s", line_offset, "HIGH" if state else "LOW")

    async def cleanup(self) -> None:
        self._initialized = False
        logger.info("MockGPIOController released pins.")

    # Simulation control helpers
    def set_line_direct(self, line_offset: int, state: bool) -> None:
        self._pins[line_offset] = state

    def get_all_pins(self) -> Dict[int, bool]:
        return dict(self._pins)


class MockADCController(BaseADCController):
    """
    Virtual ADS1115 16-Bit I2C ADC with stochastic environmental drift.
    """

    def __init__(self, gpio_ref: Optional[MockGPIOController] = None):
        self.gpio_ref = gpio_ref
        # Voltages: 3.0V = dry soil (0%), 1.0V = wet soil (100%)
        self._channels: Dict[int, float] = {
            0: 2.35,  # Zone 1
            1: 2.45,  # Zone 2
            2: 2.10,  # Zone 3
            3: 2.55,  # Zone 4
        }
        self._initialized = False
        self._last_step = time.time()
        self._manual_overrides: Dict[int, float] = {}

    async def setup(self) -> None:
        self._initialized = True
        logger.info("MockADCController initialized for ADS1115 (0x48 on I2C7).")

    def simulate_physics_step(self) -> None:
        """
        Advance soil moisture physics:
        - Natural evaporation causes voltages to slowly creep up toward dry (3.0V).
        - Energized pumps spray water, dropping probe voltage toward saturated (1.0V).
        - Adds subtle Gaussian noise to mimic real analog reading fluctuations.
        """
        now = time.time()
        dt = max(0.1, min(2.0, now - self._last_step))
        self._last_step = now

        zone_pins = {
            0: settings.PIN_ZONE_1_MOSFET,
            1: settings.PIN_ZONE_2_MOSFET,
            2: settings.PIN_ZONE_3_MOSFET,
            3: settings.PIN_ZONE_4_MOSFET,
        }

        for ch in range(4):
            if ch in self._manual_overrides:
                self._channels[ch] = self._manual_overrides[ch]
                continue

            pin = zone_pins.get(ch)
            pump_is_on = False
            if self.gpio_ref and pin is not None:
                pump_is_on = self.gpio_ref.get_all_pins().get(pin, False)

            current_v = self._channels[ch]

            if pump_is_on:
                # Drip irrigation moistens soil -> voltage drops fast
                current_v -= 0.08 * dt
            else:
                # Evaporation -> voltage drifts upward slowly
                current_v += 0.003 * dt

            # Add subtle stochastic analog jitter (+/- 0.005V)
            current_v += random.uniform(-0.004, 0.004)

            # Clamp within physical probe limits (0.9V to 2.95V)
            self._channels[ch] = max(0.9, min(2.95, round(current_v, 3)))

    async def read_channel_voltage(self, channel: int) -> float:
        self.simulate_physics_step()
        return self._channels.get(channel, 2.5)

    async def read_all_channels(self) -> Dict[str, float]:
        self.simulate_physics_step()
        return {
            "zone_1": self._channels[0],
            "zone_2": self._channels[1],
            "zone_3": self._channels[2],
            "zone_4": self._channels[3],
        }

    async def cleanup(self) -> None:
        self._initialized = False

    def set_channel_override(self, channel: int, voltage: float) -> None:
        """Manual injection for out-of-bounds sensor tests."""
        self._manual_overrides[channel] = voltage
        self._channels[channel] = voltage

    def clear_channel_override(self, channel: int) -> None:
        self._manual_overrides.pop(channel, None)


class MockFluidSimulation:
    """
    Simulates water levels in the domestic overhead tank (1000L)
    and plant irrigation reservoir (500L).
    """

    def __init__(self, gpio: MockGPIOController):
        self.gpio = gpio
        self.overhead_tank_liters = 400.0  # Out of 1000L
        self.reservoir_liters = 350.0      # Out of 500L
        self._last_step = time.time()

    def step(self) -> None:
        now = time.time()
        dt = max(0.1, min(2.0, now - self._last_step))
        self._last_step = now

        pins = self.gpio.get_all_pins()
        ac_on = pins.get(settings.PIN_AC_CONTACTOR, False)

        # Overhead Tank: AC pump pumps 2.5 L/sec into tank; domestic consumption draws 0.1 L/sec
        if ac_on:
            self.overhead_tank_liters += 2.5 * dt
        else:
            self.overhead_tank_liters -= 0.08 * dt

        self.overhead_tank_liters = max(0.0, min(1000.0, self.overhead_tank_liters))

        # Float switch trigger levels:
        # Tank Low Float trips when level < 250L
        # Tank High Float trips when level >= 900L
        tank_low_tripped = self.overhead_tank_liters < 250.0
        tank_high_tripped = self.overhead_tank_liters >= 900.0

        # Note: Active LOW inputs: float closed to ground when tripped = True
        self.gpio.set_line_direct(settings.PIN_TANK_LOW_FLOAT, tank_low_tripped)
        self.gpio.set_line_direct(settings.PIN_TANK_HIGH_FLOAT, tank_high_tripped)

        # Reservoir: Zone DC pumps consume 0.15 L/sec each
        active_zone_pumps = sum(
            1 for p in [
                settings.PIN_ZONE_1_MOSFET,
                settings.PIN_ZONE_2_MOSFET,
                settings.PIN_ZONE_3_MOSFET,
                settings.PIN_ZONE_4_MOSFET,
            ] if pins.get(p, False)
        )
        self.reservoir_liters -= 0.15 * active_zone_pumps * dt
        self.reservoir_liters = max(0.0, min(500.0, self.reservoir_liters))

        # Reservoir Low Float trips when reservoir <= 30L (dry run)
        reservoir_empty = self.reservoir_liters <= 30.0
        self.gpio.set_line_direct(settings.PIN_RESERVOIR_LOW_FLOAT, reservoir_empty)

    def refill_reservoir(self, liters: float = 400.0) -> None:
        self.reservoir_liters = min(500.0, self.reservoir_liters + liters)
        self.step()

    def drain_reservoir(self) -> None:
        self.reservoir_liters = 10.0
        self.step()

    def fill_overhead_tank(self) -> None:
        self.overhead_tank_liters = 950.0
        self.step()

    def empty_overhead_tank(self) -> None:
        self.overhead_tank_liters = 150.0
        self.step()


class MockAudioController(BaseAudioController):
    """
    Virtual Voice Synthesizer mimicking ALSA/espeak-ng.
    Captures spoken sentences in an in-memory audit log for API display and test verification.
    """

    def __init__(self, max_logs: int = 50):
        self._speech_logs: List[Dict[str, str]] = []
        self._max_logs = max_logs
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("MockAudioController started.")

    async def speak(self, text: str, priority: int = 1) -> None:
        if not text:
            return
        logger.info("[MOCK VOICE SYNTHESIS] \"%s\"", text)
        self._speech_logs.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "text": text,
            "priority": str(priority),
        })
        if len(self._speech_logs) > self._max_logs:
            self._speech_logs.pop(0)

    async def stop(self) -> None:
        self._running = False
        logger.info("MockAudioController stopped.")

    def get_recent_speech_logs(self, limit: int = 10) -> List[Dict[str, str]]:
        return list(reversed(self._speech_logs[-limit:]))


# Singletons for mock environment
mock_gpio = MockGPIOController()
mock_adc = MockADCController(gpio_ref=mock_gpio)
mock_fluid = MockFluidSimulation(gpio=mock_gpio)
mock_audio = MockAudioController()
