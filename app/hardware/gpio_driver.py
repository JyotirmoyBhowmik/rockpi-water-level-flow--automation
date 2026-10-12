"""
Head-End GPIO Driver for Radxa ROCK Pi S using GPIOBackend abstraction.
Interfaces with 4-channel PC817 optocoupler inputs, contactor relay output,
and optional contactor auxiliary contact feedback.
Wraps all I/O in asyncio.wait_for(1.0s).
Enforces relay polarity safety rules at startup.
"""

import asyncio
import logging
import math
import os
from typing import Any, Dict, Optional
from app.config import system_config
from app.hardware.gpio_backend import GPIOBackend, create_gpio_backend

logger = logging.getLogger("smart_water.gpio")


def _decode(raw: Optional[int], active_low: bool) -> Optional[bool]:
    """
    Decodes physical GPIO line value (0 or 1) based on active_low polarity.
    active_low=True (PC817 optocoupler): 0 (LOW) -> True (active/closed/energized), 1 (HIGH) -> False
    active_low=False (active-high): 1 (HIGH) -> True (active/closed/energized), 0 (LOW) -> False
    Returns None if raw is None.
    """
    if raw is None:
        return None
    return bool((raw == 0) if active_low else (raw == 1))


class SoftwareDebouncer:
    """
    Poll-interval-aware software debouncer for discrete binary inputs.
    A state change is only accepted after N consecutive identical samples.
    N is calculated from debounce_ms and poll_interval_ms:
      N = max(2, math.ceil(debounce_ms / poll_interval_ms))
    Ensures that a 1-sample transient glitch never changes the debounced state.
    """

    def __init__(self, debounce_ms: int = 100, poll_interval_ms: int = 1000):
        self.debounce_ms = debounce_ms
        self.poll_interval_ms = poll_interval_ms
        self._channels: Dict[str, Dict[str, Any]] = {}

    def get_required_samples(
        self,
        debounce_ms: Optional[int] = None,
        poll_interval_ms: Optional[int] = None,
    ) -> int:
        d_ms = debounce_ms if debounce_ms is not None else self.debounce_ms
        p_ms = poll_interval_ms if poll_interval_ms is not None else self.poll_interval_ms
        p_ms = max(1, p_ms)
        return max(2, math.ceil(d_ms / p_ms))

    def update(
        self,
        channel: str,
        value: Optional[bool],
        debounce_ms: Optional[int] = None,
        poll_interval_ms: Optional[int] = None,
    ) -> Optional[bool]:
        """
        Updates the channel with a new raw sample and returns the debounced state.
        If value is None (unconfigured / disconnected), clears debounce state for that channel and returns None.
        On first non-None sample for a channel, initializes debounced state to that value.
        Subsequent changes require N consecutive identical samples to take effect.
        """
        if value is None:
            self._channels.pop(channel, None)
            return None

        required_n = self.get_required_samples(debounce_ms, poll_interval_ms)

        ch = self._channels.get(channel)
        if ch is None or ch.get("debounced") is None:
            # First sample: initialize immediately
            self._channels[channel] = {
                "debounced": value,
                "candidate": value,
                "count": 1,
            }
            return value

        current_debounced = ch["debounced"]
        candidate = ch["candidate"]
        count = ch["count"]

        if value == current_debounced:
            # Value matches current debounced state: reset candidate tracking
            ch["candidate"] = value
            ch["count"] = 1
            return current_debounced

        # Value differs from current debounced state
        if value == candidate:
            count += 1
            ch["count"] = count
            if count >= required_n:
                # Reached N consecutive samples: transition to new value!
                ch["debounced"] = value
                ch["count"] = 1
                return value
            else:
                return current_debounced
        else:
            # Candidate changed before reaching N: reset counter for new candidate
            ch["candidate"] = value
            ch["count"] = 1
            return current_debounced

    def get_debounced(self, channel: str) -> Optional[bool]:
        ch = self._channels.get(channel)
        return ch["debounced"] if ch else None

    def reset(self, channel: Optional[str] = None) -> None:
        if channel:
            self._channels.pop(channel, None)
        else:
            self._channels.clear()


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
        self._relay_state: Optional[bool] = False  # Logical energized state (True = energized)
        self._relay_state_known: bool = False
        self._last_commanded_relay_state: Optional[bool] = None
        self._relay_pin: Optional[int] = None
        self._feedback_pin: Optional[int] = None
        # Immutable startup polarity settings
        self._active_high: bool = True
        self._debouncer = SoftwareDebouncer()
        self._lock = asyncio.Lock()

    @property
    def is_hardware_available(self) -> bool:
        return self._hardware_available

    @property
    def relay_state(self) -> Optional[bool]:
        """Returns True if energized, False if de-energized, or None if state is unknown."""
        return self._relay_state if self._relay_state_known else None

    @property
    def relay_state_known(self) -> bool:
        return self._relay_state_known

    @property
    def last_commanded_relay_state(self) -> Optional[bool]:
        return self._last_commanded_relay_state

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
                    self._relay_state_known = False
                    return False
                self._relay_state = False
                self._relay_state_known = True
                self._last_commanded_relay_state = False

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
                        return False

            # Setup optional contactor feedback pin (Part 5.1)
            if cfg.contactor_feedback_pin is not None:
                self._feedback_pin = cfg.contactor_feedback_pin
                if not self._backend.request_input(self._feedback_pin, consumer="smart-water-contactor-fb", bias="pull_up"):
                    logger.error("Failed to claim contactor feedback on pin %d", self._feedback_pin)
                    return False

            return True

        try:
            success = await asyncio.wait_for(asyncio.to_thread(_sync_setup), timeout=1.0)
            self._hardware_available = success
            if success:
                logger.info("Head-end GPIO initialized successfully on %s (active_high=%s)", chip_path, self._active_high)
            else:
                logger.warning("Failed to configure lines on %s. Outputs locked OFF.", chip_path)
                if self._backend:
                    self._backend.close()
            return success
        except asyncio.TimeoutError:
            logger.error("Timeout (1s) opening GPIO chip %s", chip_path)
            self._hardware_available = False
            return False
        except Exception as e:
            logger.error("Exception during GPIO setup: %s", e)
            self._hardware_available = False
            return False

    async def read_head_end_inputs(self) -> Dict[str, Any]:
        """
        Reads opto inputs with a 1.0s timeout:
          IN1 (tank_low_float): decoded via cfg.tank_low_active_low
          IN2 (interlock_out): decoded via cfg.interlock_out_active_low
          IN3 (interlock_mid): decoded via cfg.interlock_mid_active_low
          IN4 (leak_sensor): decoded via cfg.leak_active_low
        Applies software debounce using cfg.debounce_ms.
        Returns dict with debounced states, raw logical states, and physical raw levels.
        """
        if not self._hardware_available or not self._backend:
            return {
                "tank_low_float": None,
                "interlock_out": None,
                "interlock_mid": None,
                "leak_sensor": None,
                "tank_low": None,
                "leak_detected": None,
                "raw": {
                    "tank_low_float": None,
                    "interlock_out": None,
                    "interlock_mid": None,
                    "leak_sensor": None,
                    "raw_levels": {},
                },
                "debounced": {
                    "tank_low_float": None,
                    "interlock_out": None,
                    "interlock_mid": None,
                    "leak_sensor": None,
                },
            }

        cfg = system_config.head_end

        def _sync_read():
            raw_levels: Dict[str, Optional[int]] = {}
            raw_logical: Dict[str, Optional[bool]] = {}

            # Read raw pin levels and decode via per-input active_low configuration
            for pin_name, pin_offset, active_low in [
                ("tank_low_float", cfg.tank_low_float_pin, cfg.tank_low_active_low),
                ("interlock_out", cfg.interlock_out_pin, cfg.interlock_out_active_low),
                ("interlock_mid", cfg.interlock_mid_pin, cfg.interlock_mid_active_low),
                ("leak_sensor", cfg.leak_sensor_pin, cfg.leak_active_low),
            ]:
                if pin_offset is not None:
                    raw_val = self._backend.get_value(pin_offset)
                    raw_levels[pin_name] = raw_val
                    raw_logical[pin_name] = _decode(raw_val, active_low)
                else:
                    raw_levels[pin_name] = None
                    raw_logical[pin_name] = None

            feedback_val = None
            if self._feedback_pin is not None:
                raw_fb = self._backend.get_value(self._feedback_pin)
                feedback_val = _decode(raw_fb, cfg.feedback_active_low)

            return raw_levels, raw_logical, feedback_val

        try:
            raw_levels, raw_logical, feedback_val = await asyncio.wait_for(asyncio.to_thread(_sync_read), timeout=1.0)

            # Apply software debouncing
            debounced: Dict[str, Optional[bool]] = {}
            for name, val in raw_logical.items():
                debounced[name] = self._debouncer.update(
                    channel=name,
                    value=val,
                    debounce_ms=cfg.debounce_ms,
                    poll_interval_ms=1000,
                )

            return {
                "tank_low_float": debounced["tank_low_float"],
                "interlock_out": debounced["interlock_out"],
                "interlock_mid": debounced["interlock_mid"],
                "leak_sensor": debounced["leak_sensor"],
                "tank_low": debounced["tank_low_float"],
                "leak_detected": debounced["leak_sensor"],
                "contactor_feedback": feedback_val,
                "raw": {
                    **raw_logical,
                    "contactor_feedback": feedback_val,
                    "raw_levels": raw_levels,
                },
                "debounced": debounced,
            }
        except (asyncio.TimeoutError, Exception) as e:
            logger.error("Error reading head-end GPIO inputs: %s", e)
            return {
                "tank_low_float": None,
                "interlock_out": None,
                "interlock_mid": None,
                "leak_sensor": None,
                "tank_low": None,
                "leak_detected": None,
                "contactor_feedback": None,
                "raw": {
                    "tank_low_float": None,
                    "interlock_out": None,
                    "interlock_mid": None,
                    "leak_sensor": None,
                    "contactor_feedback": None,
                    "raw_levels": {},
                },
                "debounced": {
                    "tank_low_float": None,
                    "interlock_out": None,
                    "interlock_mid": None,
                    "leak_sensor": None,
                },
            }

    async def read_contactor_feedback(self) -> Optional[bool]:
        """Reads contactor aux contact feedback with 1.0s timeout."""
        if not self._hardware_available or not self._backend or self._feedback_pin is None:
            return None

        cfg = system_config.head_end

        def _sync_read_fb():
            raw = self._backend.get_value(self._feedback_pin)
            return _decode(raw, cfg.feedback_active_low)

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
        Tracks _relay_state_known and _last_commanded_relay_state.
        On timeout/exception sets _relay_state_known=False and never claims OFF when unknown.
        """
        async with self._lock:
            self._last_commanded_relay_state = energized
            if not self._hardware_available or not self._backend or self._relay_pin is None:
                logger.warning("Cannot set contactor: GPIO hardware not available.")
                self._relay_state_known = False
                return False

            # Polarity calculation: active_high True -> energized=1, deenergized=0
            electrical_val = 1 if energized else 0
            if not self._active_high:
                electrical_val = 0 if energized else 1

            def _sync_set():
                self._backend.set_value(self._relay_pin, electrical_val)
                self._relay_state = energized
                self._relay_state_known = True

            try:
                await asyncio.wait_for(asyncio.to_thread(_sync_set), timeout=1.0)
                logger.info("Contactor relay set to %s (electrical=%d)", "ENERGIZED" if energized else "OFF", electrical_val)
                return True
            except asyncio.TimeoutError:
                logger.critical("Timeout (1s) driving contactor relay pin!")
                self._relay_state_known = False
                return False
            except Exception as e:
                logger.critical("Exception driving contactor relay: %s", e)
                self._relay_state_known = False
                return False

    async def force_all_outputs_off(self) -> None:
        """Failsafe shutdown: unconditionally de-energizes contactor relay."""
        self._last_commanded_relay_state = False
        if self._hardware_available and self._backend and self._relay_pin is not None:
            deenergized_val = 0 if self._active_high else 1
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._backend.set_value, self._relay_pin, deenergized_val),
                    timeout=1.0,
                )
                self._relay_state = False
                self._relay_state_known = True
            except Exception as e:
                logger.error("Error during force_all_outputs_off: %s", e)
                self._relay_state_known = False
        else:
            self._relay_state_known = False

    async def cleanup(self) -> None:
        """Releases GPIO handles and shuts off outputs."""
        await self.force_all_outputs_off()
        self._debouncer.reset()
        if self._backend:
            try:
                self._backend.close()
            except Exception:
                pass
            self._backend = None
        self._hardware_available = False
        self._relay_state_known = False


head_end_gpio = HeadEndGPIODriver()
