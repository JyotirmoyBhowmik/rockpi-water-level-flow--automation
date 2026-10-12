"""
Hardware Piezo Buzzer Controller for Radxa ROCK Pi S.
Provides acoustic alarm alerting on GPIO when voice audio synthesis is unavailable,
disabled, or suppressed by quiet hours.

Features:
  - Uses GPIOBackend abstraction (libgpiod v1 or v2) with safe dummy/simulation fallback.
  - Distinctive acoustic alert patterns for critical safety alarms:
      * Emergency Stop / Wiring Fault / Welded Contactor: 5 rapid pulses
      * Locked-Rotor Overload / Voltage Fault: 4 pulses
      * Dry Run / Dry Sump / Loss of Prime: 3 pulses
      * Water Leak: 2 long pulses
      * Generic Alarms: 3 pulses
  - Asynchronous non-blocking pulse trains with clean task cancellation.
  - Automatic fallback trigger when voice audio is unavailable.
"""

import asyncio
import logging
from typing import Optional
from app.config import system_config
from app.hardware.gpio_backend import GPIOBackend, create_gpio_backend

logger = logging.getLogger("smart_water.buzzer")


class BuzzerController:
    """
    GPIO Piezo Buzzer driver providing audible alarm notifications.
    Fails gracefully if physical GPIO or libgpiod is absent.
    """

    def __init__(self, backend: Optional[GPIOBackend] = None) -> None:
        self._backend: Optional[GPIOBackend] = backend
        self._hardware_available: bool = False
        self._pin: Optional[int] = None
        self._active_high: bool = True
        self._beep_task: Optional[asyncio.Task[None]] = None
        self._is_beeping: bool = False
        self._owns_backend: bool = backend is None
        self._warned: bool = False

    @property
    def is_available(self) -> bool:
        """True if buzzer hardware GPIO line has been successfully acquired."""
        return self._hardware_available

    @property
    def is_beeping(self) -> bool:
        """True if a beeping pulse train is actively sounding."""
        return self._is_beeping

    async def setup(self) -> bool:
        """
        Initializes buzzer GPIO output based on system_config.buzzer.
        Returns True if hardware was acquired, False if disabled or unavailable.
        """
        cfg = system_config.buzzer
        if not cfg.enabled:
            logger.info("Hardware piezo buzzer is disabled in configuration.")
            self._hardware_available = False
            return False

        if cfg.gpio_pin is None:
            logger.warning("Buzzer is enabled but gpio_pin is null; operating in simulated mode.")
            self._hardware_available = False
            return False

        self._pin = cfg.gpio_pin
        self._active_high = cfg.active_high

        if self._backend is None:
            self._backend = create_gpio_backend()

        if self._backend is None:
            if not self._warned:
                logger.info("libgpiod not available; buzzer operating in simulated mode.")
                self._warned = True
            self._hardware_available = False
            return False

        chip_path = system_config.head_end.chip_path or "/dev/gpiochip1"
        try:
            if not self._backend.open(chip_path):
                logger.warning("Failed to open GPIO chip %s for buzzer.", chip_path)
                self._hardware_available = False
                return False

            # Idle value: inactive (0 if active-high, 1 if active-low)
            idle_val = 0 if self._active_high else 1
            if not self._backend.request_output(self._pin, consumer="smart-water-buzzer", initial_value=idle_val):
                logger.error("Failed to request buzzer GPIO pin %d as output.", self._pin)
                self._hardware_available = False
                return False

            self._hardware_available = True
            logger.info("Buzzer initialized on pin %d (active_high=%s, chip=%s).", self._pin, self._active_high, chip_path)
            return True
        except Exception as exc:
            logger.warning("Error initializing buzzer hardware: %s", exc)
            self._hardware_available = False
            return False

    def _drive_buzzer(self, sound: bool) -> None:
        """Sets physical GPIO level according to polarity."""
        if not self._hardware_available or self._backend is None or self._pin is None:
            return
        val = 1 if (sound if self._active_high else not sound) else 0
        try:
            self._backend.set_value(self._pin, val)
        except Exception as exc:
            logger.error("Failed to set buzzer pin %d: %s", self._pin, exc)

    def silence(self) -> None:
        """Immediately silences buzzer and cancels any ongoing pulse task."""
        if self._beep_task and not self._beep_task.done():
            self._beep_task.cancel()
        self._drive_buzzer(False)
        self._is_beeping = False

    async def beep(self, duration_s: float = 0.2, count: int = 1, interval_s: float = 0.1) -> None:
        """
        Asynchronously plays count beeps of duration_s separated by interval_s.
        Cancels any prior ongoing pulse sequence.
        """
        self.silence()
        self._beep_task = asyncio.create_task(
            self._run_beep_pattern(duration_s=duration_s, count=count, interval_s=interval_s),
            name="buzzer_beep_task",
        )

    async def _run_beep_pattern(self, duration_s: float, count: int, interval_s: float) -> None:
        """Internal worker executing pulse trains."""
        self._is_beeping = True
        try:
            for i in range(count):
                self._drive_buzzer(True)
                await asyncio.sleep(duration_s)
                self._drive_buzzer(False)
                if i < count - 1:
                    await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            self._drive_buzzer(False)
            raise
        finally:
            self._drive_buzzer(False)
            self._is_beeping = False

    async def alarm_beep(self, alarm_key: str = "emergency", message: str = "") -> None:
        """
        Plays distinctive acoustic alarm patterns based on alarm type:
          - E-Stop / Wiring Fault / Welded: 5 fast bursts (80ms on, 80ms off)
          - Overload / Locked-Rotor / Voltage: 4 bursts (150ms on, 100ms off)
          - Dry Run / Dry Sump: 3 bursts (200ms on, 150ms off)
          - Water Leak: 2 long bursts (400ms on, 200ms off)
          - Default / General: 3 bursts (200ms on, 150ms off)
        """
        key_lower = alarm_key.lower()
        if any(w in key_lower for w in ("emergency", "estop", "wiring", "welded", "interlock")):
            duration_s = 0.08
            count = 5
            interval_s = 0.08
            pattern = "rapid_critical_5x"
        elif any(w in key_lower for w in ("overload", "locked_rotor", "voltage")):
            duration_s = 0.15
            count = 4
            interval_s = 0.10
            pattern = "electrical_fault_4x"
        elif any(w in key_lower for w in ("dry_run", "dry_sump", "loss_of_prime", "unprime")):
            duration_s = 0.20
            count = 3
            interval_s = 0.15
            pattern = "dry_fault_3x"
        elif any(w in key_lower for w in ("leak", "leakage")):
            duration_s = 0.40
            count = 2
            interval_s = 0.20
            pattern = "leak_warning_2x"
        else:
            duration_s = 0.20
            count = 3
            interval_s = 0.15
            pattern = "standard_alarm_3x"

        logger.warning(
            "BUZZER ALARM SOUNDED: key='%s', message='%s', pattern='%s' (count=%d, duration=%.2fs)",
            alarm_key,
            message,
            pattern,
            count,
            duration_s,
        )
        await self.beep(duration_s=duration_s, count=count, interval_s=interval_s)

    async def trigger_alarm_if_audio_unavailable(
        self, alarm_key: str, message: str = "", priority: int = 0
    ) -> bool:
        """
        Checks whether voice audio is unavailable or suppressed, and if so, sounds the buzzer.
        Returns True if buzzer was triggered, False otherwise.
        """
        cfg = system_config.buzzer
        if not cfg.enabled or not cfg.beep_on_alarm:
            return False

        from app.hardware.audio_driver import audio_controller

        # Audio is considered unavailable if:
        # 1. Voice audio is disabled in config
        # 2. Audio hardware (espeak-ng/aplay) is absent from SBC
        # 3. Alert is non-emergency (priority > 0) and currently in quiet hours
        audio_unavailable = (
            not system_config.audio.enabled
            or not audio_controller._has_audio_hardware
            or (priority > 0 and audio_controller._is_in_quiet_hours())
        )

        if audio_unavailable:
            logger.info("Voice audio unavailable/suppressed; falling back to buzzer for alarm '%s'", alarm_key)
            await self.alarm_beep(alarm_key=alarm_key, message=message)
            return True

        return False

    async def stop(self) -> None:
        """Silences buzzer and releases hardware handles."""
        self.silence()
        if self._beep_task and not self._beep_task.done():
            self._beep_task.cancel()
            try:
                await self._beep_task
            except asyncio.CancelledError:
                pass
        if self._hardware_available and self._backend and self._owns_backend:
            try:
                self._backend.close()
            except Exception:
                pass
        self._hardware_available = False
        logger.info("BuzzerController stopped.")


buzzer_controller = BuzzerController()
