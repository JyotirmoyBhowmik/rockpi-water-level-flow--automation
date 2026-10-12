"""
Head-End GPIO Driver for Radxa ROCK Pi S using GPIOBackend abstraction.
Interfaces with 4-channel PC817 optocoupler inputs, contactor relay output,
and optional contactor auxiliary contact feedback.
Wraps all I/O in asyncio.wait_for(1.0s).
Enforces relay polarity safety rules at startup.
"""

import asyncio
import logging
import os
from typing import Dict, Optional
from app.config import system_config
from app.hardware.gpio_backend import GPIOBackend, create_gpio_backend

logger = logging.getLogger("smart_water.gpio")


class HeadEndGPIODriver:
    """
    Physical Linux character device driver for ROCK Pi S Head-End.
    Uses GPIOBackend abstraction (libgpiod v1 or v2).
    Inputs (PC817 Opto board):
      IN1: tank_low_float (Active-LOW with pull-up)
      IN2: interlock_out (HIGH = hardware chain closed = motor allowed)
      IN3: interlock_mid (Node between Full float and Sump float)
      IN4: leak_sensor (Active-LOW with pull-up)
      Feedback: contactor_feedback (Optional auxiliary contact)
    Output:
      contactor_relay: 25A AC contactor coil driver (active-high guaranteed unless bias confirmed)
    """

    def __init__(self):
        self._backend: Optional[GPIOBackend] = None
        self._hardware_available = False
        self._relay_state = False  # Logical energized state (True = energized)
        self._relay_pin: Optional[int] = None
        self._feedback_pin: Optional[int] = None
        # Immutable startup polarity settings
        self._active_high: bool = True
        self._lock = asyncio.Lock()

    @property
    def is_hardware_available(self) -> bool:
        return self._hardware_available

    @property
    def relay_state(self) -> bool:
        return self._relay_state

    async def setup(self) -> bool:
        """
        Initializes claimed GPIO lines using runtime backend.
        Returns True if physical hardware is ready, False otherwise.
        """
        cfg = system_config.head_end
        chip_path = cfg.chip_path

        # 1. Snapshot and enforce polarity safety at boot (Part 2.6)
        self._active_high = bool(cfg.contactor_relay_active_high)
        if not self._active_high:
            if cfg.relay_idle_bias_confirmed != "pull_up":
                logger.critical(
                    "RELAY POLARITY REJECTED: Active-low relay requested without confirmed "
                    "hardware pull-up (relay_idle_bias_confirmed != 'pull_up'). "
                    "Output is LOCKED OFF for safety."
                )
                self._hardware_available = False
                return False

        # 2. Check if hardware device file exists
        if not chip_path or not os.path.exists(chip_path):
            self._hardware_available = False
            logger.info(
                "Head-end GPIO chip %s not accessible. State: SAFE-NO-HARDWARE. Outputs held OFF.",
                chip_path,
            )
            return False

        # 3. Create backend
        self._backend = create_gpio_backend()
        if not self._backend:
            self._hardware_available = False
            logger.info("libgpiod not available. State: SAFE-NO-HARDWARE.")
            return False

        def _sync_setup() -> bool:
            if not self._backend.open(chip_path):
                return False

            # Setup Output Relay (Force OFF initially)
            if cfg.contactor_relay_pin is not None:
                self._relay_pin = cfg.contactor_relay_pin
                deenergized_val = 0 if self._active_high else 1
                if not self._backend.request_output(
                    self._relay_pin, consumer="smart-water-contactor", initial_value=deenergized_val
                ):
                    return False
                self._relay_state = False

            # Setup Optocoupled Inputs
            for pin_name, pin_offset in [
                ("tank_low_float", cfg.tank_low_float_pin),
                ("interlock_out", cfg.interlock_out_pin),
                ("interlock_mid", cfg.interlock_mid_pin),
                ("leak_sensor", cfg.leak_sensor_pin),
            ]:
                if pin_offset is not None:
                    if not self._backend.request_input(pin_offset, consumer=f"smart-water-{pin_name}", bias="pull_up"):
                        logger.error("Failed to claim %s on pin %d", pin_name, pin_offset)

            # Setup optional contactor feedback pin (Part 5.1)
            if cfg.contactor_feedback_pin is not None:
                self._feedback_pin = cfg.contactor_feedback_pin
                self._backend.request_input(self._feedback_pin, consumer="smart-water-contactor-fb", bias="pull_up")

            return True

        try:
            success = await asyncio.wait_for(asyncio.to_thread(_sync_setup), timeout=1.0)
            self._hardware_available = success
            if success:
                logger.info("Head-end GPIO initialized successfully on %s (active_high=%s)", chip_path, self._active_high)
            else:
                logger.warning("Failed to configure lines on %s", chip_path)
            return success
        except asyncio.TimeoutError:
            logger.error("Timeout (1s) opening GPIO chip %s", chip_path)
            self._hardware_available = False
            return False
        except Exception as e:
            logger.error("Exception during GPIO setup: %s", e)
            self._hardware_available = False
            return False

    async def read_head_end_inputs(self) -> Dict[str, Optional[bool]]:
        """
        Reads opto inputs with a 1.0s timeout:
          IN1 (tank_low_float): Active LOW -> True when float closed (water low)
          IN2 (interlock_out): Active HIGH -> True when chain is closed
          IN3 (interlock_mid): Active HIGH -> True when mid-tap is energized
          IN4 (leak_sensor): Active LOW -> True when water bridges contacts
        Returns dict of pin states (or None for unconfigured / unreadable).
        """
        if not self._hardware_available or not self._backend:
            return {
                "tank_low_float": None,
                "interlock_out": None,
                "interlock_mid": None,
                "leak_sensor": None,
            }

        cfg = system_config.head_end

        def _sync_read():
            results = {}
            # IN1: Tank Low (PC817 inverts active-low, closed=LOW)
            if cfg.tank_low_float_pin is not None:
                raw = self._backend.get_value(cfg.tank_low_float_pin)
                results["tank_low_float"] = (raw == 0)
            else:
                results["tank_low_float"] = None

            # IN2: Interlock Out (Active HIGH = Chain closed)
            if cfg.interlock_out_pin is not None:
                raw = self._backend.get_value(cfg.interlock_out_pin)
                results["interlock_out"] = (raw == 1)
            else:
                results["interlock_out"] = None

            # IN3: Interlock Mid (Active HIGH)
            if cfg.interlock_mid_pin is not None:
                raw = self._backend.get_value(cfg.interlock_mid_pin)
                results["interlock_mid"] = (raw == 1)
            else:
                results["interlock_mid"] = None

            # IN4: Leak Sensor (Active LOW = Moisture detected)
            if cfg.leak_sensor_pin is not None:
                raw = self._backend.get_value(cfg.leak_sensor_pin)
                results["leak_sensor"] = (raw == 0)
            else:
                results["leak_sensor"] = None

            return results

        try:
            return await asyncio.wait_for(asyncio.to_thread(_sync_read), timeout=1.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.error("Error reading head-end GPIO inputs: %s", e)
            return {
                "tank_low_float": None,
                "interlock_out": None,
                "interlock_mid": None,
                "leak_sensor": None,
            }

    async def read_contactor_feedback(self) -> Optional[bool]:
        """Reads contactor aux contact feedback with 1.0s timeout."""
        if not self._hardware_available or not self._backend or self._feedback_pin is None:
            return None

        def _sync_read_fb():
            raw = self._backend.get_value(self._feedback_pin)
            # Active LOW when auxiliary contact closed (closed = energized)
            return raw == 0

        try:
            return await asyncio.wait_for(asyncio.to_thread(_sync_read_fb), timeout=1.0)
        except Exception as e:
            logger.error("Error reading contactor feedback: %s", e)
            return None

    async def set_contactor_relay(self, energized: bool) -> bool:
        """
        Drives the 25A contactor relay driver.
        Energized=True closes the contactor.
        Enforces polarity and wraps in 1.0s timeout.
        """
        async with self._lock:
            if not self._hardware_available or not self._backend or self._relay_pin is None:
                logger.warning("Cannot set contactor: GPIO hardware not available.")
                self._relay_state = False
                return False

            # Polarity calculation: active_high True -> energized=1, deenergized=0
            electrical_val = 1 if energized else 0
            if not self._active_high:
                electrical_val = 0 if energized else 1

            def _sync_set():
                self._backend.set_value(self._relay_pin, electrical_val)
                self._relay_state = energized

            try:
                await asyncio.wait_for(asyncio.to_thread(_sync_set), timeout=1.0)
                logger.info("Contactor relay set to %s (electrical=%d)", "ENERGIZED" if energized else "OFF", electrical_val)
                return True
            except asyncio.TimeoutError:
                logger.critical("Timeout (1s) driving contactor relay pin!")
                self._relay_state = False
                return False
            except Exception as e:
                logger.critical("Exception driving contactor relay: %s", e)
                self._relay_state = False
                return False

    async def force_all_outputs_off(self) -> None:
        """Failsafe shutdown: unconditionally de-energizes contactor relay."""
        self._relay_state = False
        if self._hardware_available and self._backend and self._relay_pin is not None:
            deenergized_val = 0 if self._active_high else 1
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._backend.set_value, self._relay_pin, deenergized_val),
                    timeout=1.0,
                )
            except Exception as e:
                logger.error("Error during force_all_outputs_off: %s", e)

    async def cleanup(self) -> None:
        """Releases GPIO handles and shuts off outputs."""
        await self.force_all_outputs_off()
        if self._backend:
            try:
                self._backend.close()
            except Exception:
                pass
            self._backend = None
        self._hardware_available = False


head_end_gpio = HeadEndGPIODriver()
