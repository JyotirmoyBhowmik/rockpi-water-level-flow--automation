"""
Head-End libgpiod Linux Character Device Driver for Radxa ROCK Pi S.
Interfaces with 4-channel PC817 optocoupler inputs and 1 AC contactor relay output.
Guarantees outputs default to OFF and safe state SAFE-NO-HARDWARE if hardware is missing.
"""

import asyncio
import logging
import os
from typing import Dict, Optional, Tuple
from app.config import system_config

logger = logging.getLogger("smart_water.gpio")

try:
    import gpiod
    HAS_GPIOD = True
except ImportError:
    gpiod = None
    HAS_GPIOD = False


class HeadEndGPIODriver:
    """
    Physical Linux character device driver for ROCK Pi S Head-End.
    Inputs (PC817 Opto board):
      IN1: tank_low_float (Active-LOW with pull-up)
      IN2: interlock_out (HIGH = hardware chain closed = motor allowed)
      IN3: interlock_mid (Node between Full float and Sump float)
      IN4: leak_sensor (Active-LOW with pull-up)
    Output:
      contactor_relay: 25A AC contactor coil driver (configurable active level)
    """

    def __init__(self):
        self._chip = None
        self._input_lines = None
        self._relay_line = None
        self._hardware_available = False
        self._relay_state = False  # Logical energized state (True = energized)
        self._lock = asyncio.Lock()

    @property
    def is_hardware_available(self) -> bool:
        return self._hardware_available

    async def setup(self) -> bool:
        """
        Initializes claimed GPIO lines.
        Returns True if physical hardware is ready, False otherwise.
        """
        cfg = system_config.head_end
        chip_path = cfg.chip_path

        # If chip path or relay pin is unset, or device file is missing
        if not chip_path or not os.path.exists(chip_path) or not HAS_GPIOD:
            self._hardware_available = False
            logger.warning(
                "Head-end GPIO hardware not accessible (chip_path=%s, gpiod=%s). "
                "System state: SAFE-NO-HARDWARE. Outputs held OFF.",
                chip_path,
                HAS_GPIOD,
            )
            return False

        def _open():
            try:
                self._chip = gpiod.Chip(chip_path)

                # 1. Setup Relay Output (force OFF initially)
                if cfg.contactor_relay_pin is not None:
                    self._relay_line = self._chip.get_line(cfg.contactor_relay_pin)
                    req = gpiod.LineRequest()
                    req.consumer = "smart-water-contactor"
                    req.request_type = gpiod.line_request.DIRECTION_OUTPUT
                    # De-energized electrical value
                    deenergized_val = 0 if cfg.contactor_relay_active_high else 1
                    self._relay_line.request(req, default_val=deenergized_val)
                    self._relay_state = False

                # 2. Setup Inputs
                input_pins = [
                    p for p in [
                        cfg.tank_low_float_pin,
                        cfg.interlock_out_pin,
                        cfg.interlock_mid_pin,
                        cfg.leak_sensor_pin,
                    ] if p is not None
                ]

                if input_pins:
                    self._input_lines = self._chip.get_lines(input_pins)
                    in_req = gpiod.LineRequest()
                    in_req.consumer = "smart-water-opto-inputs"
                    in_req.request_type = gpiod.line_request.DIRECTION_INPUT
                    try:
                        in_req.flags = gpiod.line_request.FLAG_BIAS_PULL_UP
                    except AttributeError:
                        pass
                    self._input_lines.request(in_req)

                self._hardware_available = True
                logger.info("Head-end GPIO initialized successfully on %s.", chip_path)
                return True
            except Exception as exc:
                logger.error("Failed to initialize GPIO chip %s: %s", chip_path, exc)
                self._hardware_available = False
                return False

        return await asyncio.to_thread(_open)

    async def read_head_end_inputs(self) -> Dict[str, Optional[bool]]:
        """
        Reads physical opto-isolated inputs with active level inversion.
        Returns a dict of raw boolean logic states, or None if unconfigured.
        PC817 Opto board:
          - tank_low_float: Float closed connects PC817 cathode to GND -> Opto conducts -> GPIO reads LOW (0)
            Logical tank_low: True if reading is 0 (Active-LOW).
          - interlock_out: Opto reads HIGH (1) when 5V hardware interlock chain is complete.
          - interlock_mid: Opto reads HIGH (1) when Full float is closed.
          - leak_sensor: Sensor tripped grounds line -> Opto conducts -> reads LOW (0).
            Logical leak: True if reading is 0 (Active-LOW).
        """
        cfg = system_config.head_end
        if not self._hardware_available or not self._chip:
            return {
                "tank_low": None,
                "interlock_out": None,
                "interlock_mid": None,
                "leak_detected": None,
            }

        def _read():
            result: Dict[str, Optional[bool]] = {}

            # tank_low_float
            if cfg.tank_low_float_pin is not None:
                try:
                    line = self._chip.get_line(cfg.tank_low_float_pin)
                    raw = line.get_value()
                    result["tank_low"] = (raw == 0)  # Active-LOW
                except Exception:
                    result["tank_low"] = None
            else:
                result["tank_low"] = None

            # interlock_out (HIGH = healthy / closed)
            if cfg.interlock_out_pin is not None:
                try:
                    line = self._chip.get_line(cfg.interlock_out_pin)
                    raw = line.get_value()
                    result["interlock_out"] = (raw == 1)
                except Exception:
                    result["interlock_out"] = None
            else:
                result["interlock_out"] = None

            # interlock_mid (HIGH = closed)
            if cfg.interlock_mid_pin is not None:
                try:
                    line = self._chip.get_line(cfg.interlock_mid_pin)
                    raw = line.get_value()
                    result["interlock_mid"] = (raw == 1)
                except Exception:
                    result["interlock_mid"] = None
            else:
                result["interlock_mid"] = None

            # leak_sensor
            if cfg.leak_sensor_pin is not None:
                try:
                    line = self._chip.get_line(cfg.leak_sensor_pin)
                    raw = line.get_value()
                    result["leak_detected"] = (raw == 0)  # Active-LOW
                except Exception:
                    result["leak_detected"] = None
            else:
                result["leak_detected"] = None

            return result

        return await asyncio.to_thread(_read)

    async def set_contactor_relay(self, energize: bool) -> bool:
        """
        Commands the AC Contactor relay coil.
        energize: True to pull contactor ON, False to release contactor OFF.
        """
        async with self._lock:
            cfg = system_config.head_end
            if not self._hardware_available or not self._relay_line:
                self._relay_state = False
                logger.warning("Cannot energize contactor: Head-end GPIO not configured or unavailable.")
                return False

            electrical_val = 1 if (energize if cfg.contactor_relay_active_high else not energize) else 0

            def _write():
                try:
                    self._relay_line.set_value(electrical_val)
                    self._relay_state = energize
                    logger.info("Contactor relay commanded: %s (electrical=%d)", "ENERGIZED" if energize else "OFF", electrical_val)
                    return True
                except Exception as exc:
                    logger.error("Failed to set contactor relay: %s", exc)
                    self._relay_state = False
                    return False

            return await asyncio.to_thread(_write)

    async def force_all_outputs_off(self) -> None:
        """Emergency cut: forces relay OFF immediately and cleanly."""
        async with self._lock:
            self._relay_state = False
            cfg = system_config.head_end
            if self._relay_line:
                deenergized_val = 0 if cfg.contactor_relay_active_high else 1
                try:
                    await asyncio.to_thread(self._relay_line.set_value, deenergized_val)
                except Exception:
                    pass
            logger.info("Head-end GPIO: All outputs forced OFF.")

    async def cleanup(self) -> None:
        """Releases GPIO lines on shutdown, ensuring outputs stay OFF."""
        await self.force_all_outputs_off()
        def _close():
            if self._input_lines:
                try:
                    self._input_lines.release()
                except Exception:
                    pass
            if self._relay_line:
                try:
                    self._relay_line.release()
                except Exception:
                    pass
            if self._chip:
                try:
                    self._chip.close()
                except Exception:
                    pass
        await asyncio.to_thread(_close)
        self._hardware_available = False
        logger.info("Head-end GPIO driver cleanly shutdown.")

    def get_relay_state(self) -> bool:
        return self._relay_state


head_end_gpio = HeadEndGPIODriver()
