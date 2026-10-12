"""
Overhead Tank Motor Controller for 25A AC Contactor on Radxa ROCK Pi S.
Enforces automatic replenishment, hardware interlock checks, and run logging.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Optional, Tuple
from app.alerts.telegram_client import telegram_notifier
from app.config import system_config
from app.core.event_bus import bus
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.hardware.audio_driver import audio_controller
from app.hardware.gpio_driver import head_end_gpio
from app.hardware.rtc import get_monotonic_now

logger = logging.getLogger("smart_water.motor")


class MotorController:
    """
    Controls the 25A AC contactor relay for overhead tank pumping.
    """

    def __init__(self) -> None:
        self._started_at_iso: Optional[str] = None
        self._started_at_mono: Optional[float] = None
        self._lock = asyncio.Lock()

    async def start_motor(self, is_manual: bool = False, confirm: bool = False) -> Tuple[bool, str]:
        """
        Commands the contactor relay ON after rigorous safety verification.
        Requires confirm=True for manual invocations.
        """
        async with self._lock:
            if is_manual and not confirm:
                return False, "Manual start requires explicit 'confirm: true' flag."

            state = state_manager.get_state()
            if state["motor"]["state"] == "RUNNING":
                return True, "Motor is already running."

            # Verify with failsafe engine
            allowed, reason = failsafe_engine.can_start_motor(is_manual=is_manual)
            if not allowed:
                logger.warning("Motor start rejected: %s", reason)
                return False, f"Start rejected: {reason}"

            # Contactor Pre-check: Welded Contact Guard (Part 2.8)
            has_fb_pin = (
                system_config.head_end.contactor_feedback_pin is not None
                or getattr(head_end_gpio, "_feedback_pin", None) is not None
            )
            if has_fb_pin:
                pre_fb = await head_end_gpio.read_contactor_feedback()
                if pre_fb is True:
                    alarm_msg = "WELDED_CONTACT: Contactor auxiliary feedback detected CLOSED while relay is OFF!"
                    await state_manager.set_alarm("welded_contact", alarm_msg)
                    failsafe_engine.manual_motor_lockout = True
                    await state_manager.set_motor_manual_lock(True)
                    await state_manager.set_motor_state("FAULT", reason="welded_contact")
                    logger.critical("SAFETY INTERLOCK: %s", alarm_msg)
                    return False, f"Start rejected: {alarm_msg}"

            # Energize relay
            success = await head_end_gpio.set_contactor_relay(True)
            if not success:
                return False, "Failed to drive contactor relay pin."

            # Contactor Pull-in Check: poll read_contactor_feedback() every 50ms up to pull_in_timeout_ms
            has_fb_pin = (
                system_config.head_end.contactor_feedback_pin is not None
                or getattr(head_end_gpio, "_feedback_pin", None) is not None
            )
            if has_fb_pin:
                pull_in_timeout_s = system_config.motor.pull_in_timeout_ms / 1000.0
                deadline = asyncio.get_running_loop().time() + pull_in_timeout_s
                pulled_in = False
                while True:
                    post_fb = await head_end_gpio.read_contactor_feedback()
                    if post_fb is True:
                        pulled_in = True
                        break
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(0.05, remaining))

                if not pulled_in:
                    await head_end_gpio.set_contactor_relay(False)
                    alarm_msg = f"CONTACTOR_MISMATCH: Contactor failed to close within {system_config.motor.pull_in_timeout_ms}ms of relay energization."
                    await state_manager.set_alarm("contactor_mismatch", alarm_msg)
                    failsafe_engine.manual_motor_lockout = True
                    await state_manager.set_motor_manual_lock(True)
                    await state_manager.set_motor_state("FAULT", reason="contactor_mismatch")
                    logger.critical("SAFETY INTERLOCK: %s", alarm_msg)
                    return False, f"Start failed: {alarm_msg}"

            now_iso = datetime.now(timezone.utc).isoformat()
            self._started_at_iso = now_iso
            self._started_at_mono = get_monotonic_now()
            await state_manager.set_motor_state("RUNNING")

            log_msg = f"Overhead pump started ({'MANUAL' if is_manual else 'AUTO'})."
            logger.info(log_msg)
            await state_manager.log_event("MOTOR_START", "INFO", log_msg, {"manual": is_manual})
            await audio_controller.speak("Main water pump energized.", priority=1)
            await telegram_notifier.send_alert("MOTOR_START", log_msg)
            await bus.publish("motor:started", {"timestamp": now_iso, "manual": is_manual})

            return True, "Motor energized successfully."

    async def stop_motor(self, reason: str = "manual_stop") -> Tuple[bool, str]:
        """De-energizes the contactor relay and logs the completed run."""
        async with self._lock:
            state = state_manager.get_state()
            was_running = (state["motor"]["state"] == "RUNNING")

            success = await head_end_gpio.set_contactor_relay(False)
            if not success:
                await head_end_gpio.force_all_outputs_off()
                alarm_msg = "RELAY_WRITE_FAILED: Failed to de-energize contactor relay output!"
                await state_manager.set_alarm("relay_write_failed", alarm_msg)
                failsafe_engine.manual_motor_lockout = True
                await state_manager.set_motor_manual_lock(True)
                await state_manager.set_motor_state("FAULT", reason="relay_write_failed")
                await audio_controller.speak("Critical alert: Contactor relay write failed. Motor faulted.", priority=0)
                await telegram_notifier.send_alert("RELAY_WRITE_FAILED", alarm_msg)
                logger.critical("SAFETY INTERLOCK: %s", alarm_msg)
                return False, f"Stop failed: {alarm_msg}"

            # If feedback pin configured, poll for drop-out (default 1000 ms); if still closed -> WELDED_CONTACT alarm + lockout
            has_fb_pin = (
                system_config.head_end.contactor_feedback_pin is not None
                or getattr(head_end_gpio, "_feedback_pin", None) is not None
            )
            if has_fb_pin:
                drop_out_timeout_s = getattr(system_config.motor, "drop_out_timeout_ms", 1000) / 1000.0
                deadline = asyncio.get_running_loop().time() + drop_out_timeout_s
                dropped_out = False
                while True:
                    fb = await head_end_gpio.read_contactor_feedback()
                    if fb is False:
                        dropped_out = True
                        break
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(0.05, remaining))

                if not dropped_out:
                    alarm_msg = "WELDED_CONTACT: Contactor auxiliary feedback remained CLOSED after de-energizing relay!"
                    await state_manager.set_alarm("welded_contact", alarm_msg)
                    failsafe_engine.manual_motor_lockout = True
                    await state_manager.set_motor_manual_lock(True)
                    await state_manager.set_motor_state("FAULT", reason="welded_contact")
                    await audio_controller.speak("Critical emergency: Welded contactor contacts detected. Motor locked out.", priority=0)
                    await telegram_notifier.send_alert("WELDED_CONTACT", alarm_msg)
                    logger.critical("SAFETY INTERLOCK: %s", alarm_msg)
                    return False, f"Stop failed: {alarm_msg}"

            await state_manager.set_motor_state("OFF", reason=reason)
            state_manager._state["motor"]["last_stop_monotonic"] = get_monotonic_now()

            duration_s = 0
            if was_running and self._started_at_mono:
                duration_s = int(get_monotonic_now() - self._started_at_mono)
                started_iso = self._started_at_iso or datetime.now(timezone.utc).isoformat()
                await state_manager.log_run("motor.ac", started_iso, duration_s, reason)
                self._started_at_mono = None
                self._started_at_iso = None

            log_msg = f"Overhead pump stopped (Reason: {reason}, Duration: {duration_s}s)."
            logger.info(log_msg)
            await state_manager.log_event("MOTOR_STOP", "INFO", log_msg, {"reason": reason, "duration_s": duration_s})
            await bus.publish("motor:stopped", {"reason": reason, "duration_s": duration_s})

            return True, f"Motor de-energized. Ran for {duration_s}s."

    async def evaluate_auto_replenishment(self) -> None:
        """
        Evaluates auto-replenishment conditions:
        Auto-start when:
          - tank_low_float is True (water level low)
          - interlock_out is HIGH
          - auto_mode is True in settings
          - Not already running
          - No safety lockouts
        """
        state = state_manager.get_state()
        if not state["motor"]["auto_mode"]:
            return

        he = state["head_end"]
        motor = state["motor"]

        # Only evaluate if hardware is configured and not stale
        if not state["hardware_ready"] or he["is_stale"]:
            return

        if motor["state"] == "OFF" and he.get("tank_low_float") is True:
            # Low water condition detected
            allowed, reason = failsafe_engine.can_start_motor(is_manual=False)
            if allowed:
                logger.info("Auto-replenishment triggered: Overhead tank float low.")
                await self.start_motor(is_manual=False)
            else:
                logger.debug("Auto-replenishment suppressed by failsafe: %s", reason)


motor_controller = MotorController()
