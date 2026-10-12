"""
Non-Bypassable Hardcoded Failsafes and Interlock Logic for Radxa ROCK Pi S Controller.
Enforces hardware interlock chain cause logic, motor protection, and plant irrigation guards.
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple
from app.alerts.telegram_client import telegram_notifier
from app.config import system_config
from app.core.state_manager import state_manager
from app.hardware.audio_driver import audio_controller
from app.hardware.gateway_driver import garden_gateway
from app.hardware.gpio_driver import head_end_gpio
from app.hardware.rtc import get_monotonic_now, is_system_clock_valid

logger = logging.getLogger("smart_water.failsafes")


class FailsafeSafetyEngine:
    """
    Comprehensive industrial safety guardian.
    Evaluates physical interlock chain, motor limits, link loss, and sensor sanity bands.
    """

    def __init__(self):
        self.wiring_fault_active = False
        self.motor_timeout_tripped = False
        self.motor_cooldown_until: float = 0.0
        self.manual_motor_lockout = False
        self.leak_alarm_active = False
        self._feedback_mismatch_start_mono: Optional[float] = None
        self.flow_faults: Dict[str, bool] = {f"zone_{i}": False for i in range(1, 5)}
        self._zone_run_start_mono: Dict[str, Optional[float]] = {f"zone_{i}": None for i in range(1, 5)}
        self._zone_pulses_start: Dict[str, int] = {f"zone_{i}": 0 for i in range(1, 5)}

    # -------------------------------------------------------------------------
    # 1. Head-End Interlock Chain & Motor Cut-off Logic
    # -------------------------------------------------------------------------

    async def evaluate_head_end_safety(self) -> Dict[str, Any]:
        """
        Evaluates head-end inputs from PC817 opto board:
          - Leak detection
          - Hardware interlock chain cause logic
          - Max continuous motor runtime guard
        """
        state = state_manager.get_state()
        he = state["head_end"]
        motor = state["motor"]
        now = time.time()
        mono_now = get_monotonic_now()

        # 1. Leak Detection Guard
        if he.get("leak_detected") is True:
            if not self.leak_alarm_active:
                self.leak_alarm_active = True
                await state_manager.set_alarm("leak", "LEAK DETECTED: Water present around manifold or floor.")
                await head_end_gpio.set_contactor_relay(False)
                await state_manager.set_motor_state("OFF", reason="leak_detected")
                await garden_gateway.send_stop_all()
                await audio_controller.speak("Emergency: Water leak detected. All pumps stopped.", priority=0)
                await telegram_notifier.send_alert("LEAK_DETECTED", "Water leak detected on head-end sensor. All outputs killed.")
                logger.critical("SAFETY INTERLOCK: Leak sensor tripped! All pumps killed.")
        else:
            if self.leak_alarm_active:
                self.leak_alarm_active = False
                await state_manager.clear_alarm("leak")
                logger.info("Leak sensor cleared.")

        # 2. Hardware Interlock Chain Cause Logic
        cause = he.get("interlock_cause")
        if cause == "wiring_fault":
            self.wiring_fault_active = True
            await state_manager.set_alarm("wiring_fault", "CRITICAL WIRING FAULT: interlock_out HIGH while interlock_mid LOW.")
            if motor["state"] == "RUNNING":
                await head_end_gpio.set_contactor_relay(False)
                await state_manager.set_motor_state("OFF", reason="wiring_fault")
                await audio_controller.speak("Critical wiring fault detected in interlock chain. Motor locked.", priority=0)
                await telegram_notifier.send_alert("WIRING_FAULT", "Impossible interlock state (out=HIGH, mid=LOW). Motor locked.")
        elif cause in ("tank_full", "dry_sump"):
            if motor["state"] == "RUNNING":
                await head_end_gpio.set_contactor_relay(False)
                stop_reason = "tank_full" if cause == "tank_full" else "dry_sump"
                await state_manager.set_motor_state("OFF", reason=stop_reason)
                msg = "Tank full: Motor shut off by hardware float." if cause == "tank_full" else "Dry sump: Motor shut off."
                await audio_controller.speak(msg, priority=1)
                await telegram_notifier.send_alert("MOTOR_STOP", msg)
                logger.info("Motor shut off by hardware interlock (%s)", cause)

        # 3. Head-End Input Lost Guard (Part 2.3: motor running & inputs lost/stale > 3s)
        if motor["state"] == "RUNNING":
            last_ts = he.get("last_update_ts")
            is_unconfigured = not he.get("configured", False)
            is_lost = (last_ts is None) or ((now - last_ts) > 3.0) or is_unconfigured
            if is_lost:
                await head_end_gpio.set_contactor_relay(False)
                await state_manager.set_motor_state("OFF", reason="head_end_input_lost")
                alarm_msg = "HEAD_END_INPUT_LOST: Head-end inputs stale, unreadable, or lost while motor running (>3s)."
                await state_manager.set_alarm("head_end_input_lost", alarm_msg)
                await audio_controller.speak("Emergency: Head-end sensor signal lost while pump running. Motor shut off.", priority=0)
                await telegram_notifier.send_alert("HEAD_END_INPUT_LOST", alarm_msg)
                logger.critical("SAFETY INTERLOCK: %s", alarm_msg)

        # 4. Max Continuous Motor Runtime Guard (Monotonic Timer)
        if motor["state"] == "RUNNING":
            started_mono = state_manager._state["motor"]["started_at_monotonic"]
            if started_mono:
                runtime_s = mono_now - started_mono
                if runtime_s >= system_config.motor.max_runtime_s:
                    self.motor_timeout_tripped = True
                    self.manual_motor_lockout = True
                    self.motor_cooldown_until = now + system_config.motor.cooldown_s
                    await head_end_gpio.set_contactor_relay(False)
                    await state_manager.set_motor_state("COOLDOWN_LOCKED", reason="max_runtime_trip")
                    await state_manager.set_motor_cooldown(self.motor_cooldown_until)
                    await state_manager.set_motor_manual_lock(True)

                    alarm_msg = (
                        f"Motor exceeded max runtime ({int(runtime_s)}s). "
                        f"Cooldown active for {int(system_config.motor.cooldown_s / 60)}m. Manual reset required."
                    )
                    await state_manager.set_alarm("motor_timeout", alarm_msg)
                    await audio_controller.speak("Emergency: Main pump continuous runtime exceeded. Lockout active.", priority=0)
                    await telegram_notifier.send_alert("MOTOR_MAX_RUNTIME", alarm_msg)
                    logger.error("SAFETY INTERLOCK: %s", alarm_msg)

        # 5. Periodic Contactor Commanded vs Feedback Consistency Check (> 2s mismatch -> stop + alarm)
        fb = he.get("contactor_feedback")
        commanded = head_end_gpio.last_commanded_relay_state
        has_fb_pin = (
            system_config.head_end.contactor_feedback_pin is not None
            or getattr(head_end_gpio, "_feedback_pin", None) is not None
        )
        if (
            head_end_gpio.is_hardware_available
            and has_fb_pin
            and fb is not None
            and commanded is not None
        ):
            if commanded != fb:
                if self._feedback_mismatch_start_mono is None:
                    self._feedback_mismatch_start_mono = mono_now
                elif (mono_now - self._feedback_mismatch_start_mono) > 2.0:
                    if fb is True and not commanded:
                        alarm_key = "welded_contact"
                        alarm_msg = "CONTACTOR_MISMATCH: Contactor feedback closed while commanded OFF (>2s). Welded contact or manual override detected."
                    else:
                        alarm_key = "contactor_mismatch"
                        alarm_msg = "CONTACTOR_MISMATCH: Contactor feedback open while commanded ON (>2s). Drop-out or coil trip detected."

                    await head_end_gpio.force_all_outputs_off()
                    await state_manager.set_alarm(alarm_key, alarm_msg)
                    self.manual_motor_lockout = True
                    await state_manager.set_motor_manual_lock(True)
                    await state_manager.set_motor_state("FAULT", reason=alarm_key)
                    await audio_controller.speak("Emergency: Contactor state mismatch detected. Motor locked out.", priority=0)
                    await telegram_notifier.send_alert("CONTACTOR_MISMATCH", alarm_msg)
                    logger.critical("SAFETY INTERLOCK: %s", alarm_msg)
            else:
                self._feedback_mismatch_start_mono = None
        else:
            self._feedback_mismatch_start_mono = None

        return {
            "wiring_fault": self.wiring_fault_active,
            "leak_active": self.leak_alarm_active,
            "motor_timeout_tripped": self.motor_timeout_tripped,
            "motor_cooldown_until": self.motor_cooldown_until,
            "manual_motor_lockout": self.manual_motor_lockout,
            "feedback_mismatch": self._feedback_mismatch_start_mono is not None,
        }

    # -------------------------------------------------------------------------
    # 2. Motor Start Authorization Check
    # -------------------------------------------------------------------------

    def can_start_motor(self, is_manual: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Determines whether the AC Contactor motor may be started.
        Enforces:
          - Setup complete (Part 3.3)
          - Head-end hardware available
          - Data not stale (< 15s)
          - No leak detected
          - No wiring fault
          - Interlock chain healthy (interlock_out == True and interlock_mid == True)
          - Debounced healthy count >= 3 (Part 2.11)
          - Minimum 120s OFF time between stop and next start (Part 5.1/5.4)
          - Not locked by max-runtime trip or in cooldown
          - Tank not already full
        """
        state = state_manager.get_state()
        he = state["head_end"]
        motor = state["motor"]
        now = time.time()
        mono_now = get_monotonic_now()

        # Setup Wizard Enforcement (Part 3.3)
        if not system_config.setup_complete:
            return False, "System setup wizard is incomplete. Motor operation blocked."

        if not state["hardware_ready"] or not he["configured"]:
            return False, "Head-end hardware is unconfigured or not connected (SAFE-NO-HARDWARE)."

        if he["is_stale"]:
            return False, "Head-end input telemetry is STALE (>15s). Motor start locked for safety."

        if self.leak_alarm_active or he.get("leak_detected") is True:
            return False, "Leak sensor is tripped. Motor locked."

        if self.wiring_fault_active or he.get("interlock_cause") == "wiring_fault":
            return False, "Wiring fault detected in hardware interlock chain. Motor locked."

        cause = he.get("interlock_cause")
        if cause == "tank_full":
            return False, "Overhead tank is already full (Full float open)."
        if cause == "dry_sump":
            return False, "Source sump is empty (Sump float open)."
        if cause != "healthy":
            return False, f"Hardware interlock chain is not complete (Cause: {cause})."

        # N-of-M Debounce check for start condition (Part 2.11)
        if he.get("healthy_debounce_count", 0) < 3:
            return False, f"Hardware interlock chain not yet stabilized (healthy count {he.get('healthy_debounce_count', 0)}/3)."

        if motor.get("state") == "FAULT":
            return False, "Motor is in FAULT state. Manual reset required via UI/API."

        if self.manual_motor_lockout:
            return False, "Motor is locked due to previous max-runtime trip. Manual reset required via UI/API."

        if self.motor_cooldown_until > now:
            remaining_m = int((self.motor_cooldown_until - now) / 60)
            return False, f"Motor is cooling down ({remaining_m}m remaining). Start locked."

        # Minimum OFF time enforcement (Part 5.1, Part 5.4)
        last_stop_mono = motor.get("last_stop_monotonic")
        min_off_s = system_config.motor.min_off_time_s
        if last_stop_mono is not None:
            off_elapsed = mono_now - last_stop_mono
            if off_elapsed < min_off_s:
                remaining_s = int(min_off_s - off_elapsed)
                return False, f"Minimum OFF time active ({remaining_s}s remaining). Contactor protected."

        return True, None

    def reset_motor_lockout(self) -> Tuple[bool, Optional[str]]:
        """
        Resets motor lockout:
        - Clears manual_motor_lockout
        - Clears motor_cooldown_until
        - Clears motor_timeout_tripped and wiring_fault_active
        - State returns to OFF (Part 2.4)
        - Clears associated alarms
        """
        now = time.time()
        if self.motor_cooldown_until > now:
            rem = int((self.motor_cooldown_until - now) / 60)
            return False, f"Cooldown still active ({rem} minutes remaining)."

        self.motor_timeout_tripped = False
        self.manual_motor_lockout = False
        self.motor_cooldown_until = 0.0
        self.wiring_fault_active = False
        self._feedback_mismatch_start_mono = None

        state = state_manager._state["motor"]
        state["state"] = "OFF"
        state["manual_locked"] = False
        state["cooldown_until_ts"] = 0.0

        # Clear in-memory alarms and reset latched wiring fault
        state_manager.reset_wiring_fault()
        for k in ["motor_timeout", "contactor_mismatch", "welded_contact", "relay_write_failed", "head_end_input_lost"]:
            state_manager._state["alarms"].pop(k, None)

        logger.info("Motor safety lockouts and cooldown cleared by operator. State reset to OFF.")
        return True, None

    # -------------------------------------------------------------------------
    # 3. Garden Plant Irrigation Safety Checks
    # -------------------------------------------------------------------------

    def can_water_zone(self, zone_key: str, is_manual: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Determines whether a plant drip pump may be energized:
          - Gateway must be online and telemetry not stale (< 90s)
          - Reservoir must be wet (reservoir_wet == True)
          - Probe must be calibrated (dry_v and wet_v set)
          - Probe voltage must be within sanity band [wet_v - margin, dry_v + margin]
          - Zone not in 6h cooldown (unless manual pulse)
          - System clock must be valid (if invalid, blocks auto-watering)
          - Zone not flagged with flow fault
        """
        state = state_manager.get_state()
        g = state["garden"]
        now = time.time()

        # 1. Gateway & Heartbeat Staleness Check (Part 4.1)
        max_hb_age = getattr(system_config.stale_thresholds, "max_heartbeat_age_s", system_config.stale_thresholds.heartbeat_s)
        last_hb = g.get("last_heartbeat_ts") or g.get("last_packet_ts") or g.get("gateway_last_seen") or g.get("node_last_seen")
        is_hb_stale = g.get("is_stale", False) or (last_hb is not None and (now - last_hb) > max_hb_age)
        if not g.get("gateway_connected") or not g.get("node_online") or is_hb_stale:
            hb_age = int(now - last_hb) if last_hb else 999999
            return False, f"Garden node telemetry is STALE or link lost (heartbeat {hb_age}s > {max_hb_age}s limit). Plant watering locked."

        # 2. Reservoir Float Staleness & Dry Check (Part 4.1: stale reservoir locks ALL pumps)
        max_res_age = getattr(system_config.stale_thresholds, "max_reservoir_age_s", system_config.stale_thresholds.reservoir_s)
        res_ts = g.get("reservoir_last_update_ts")
        if res_ts is not None and (now - res_ts) > max_res_age:
            res_age = int(now - res_ts)
            return False, f"Irrigation reservoir float reading is STALE ({res_age}s > {max_res_age}s limit). ALL pumps locked."

        if g.get("reservoir_wet") is not True:
            return False, "Irrigation reservoir is dry. DC pumps locked."

        # Setup Wizard Enforcement (Part 3.3)
        if not is_manual and not system_config.setup_complete:
            return False, "System setup wizard is incomplete. Automatic watering blocked."

        # Clock Validity Check (Part 2.3)
        if not is_system_clock_valid() and not is_manual:
            return False, "System clock is invalid (un-synced). Automatic watering blocked."

        # Zone Configuration & Calibration Check (Part 2.2)
        z_cfg = system_config.zones.get(zone_key)
        if not z_cfg:
            return False, f"Zone '{zone_key}' is not defined in configuration."

        if not z_cfg.enabled and not is_manual:
            return False, f"Zone '{zone_key}' is disabled."

        cal = z_cfg.calibration
        if cal.dry_v is None or cal.wet_v is None:
            return False, f"Zone '{zone_key}' probe is uncalibrated. Auto-watering disallowed."

        # Probe Sanity Band & Staleness Check (Part 4.1: stale soil locks ONLY that zone's auto-watering)
        z_state = g["zones"].get(zone_key, {})
        max_soil_age = getattr(system_config.stale_thresholds, "max_soil_age_s", system_config.stale_thresholds.soil_s)
        z_last_ts = z_state.get("last_update_ts")
        if not is_manual:
            if z_last_ts is None or (now - z_last_ts) > max_soil_age:
                soil_age = int(now - z_last_ts) if z_last_ts else 999999
                return False, f"Zone '{zone_key}' soil reading is STALE ({soil_age}s > {max_soil_age}s limit). Auto-watering locked."

        volts = z_state.get("voltage")
        if volts is not None:
            min_valid = min(cal.wet_v, cal.dry_v) - cal.margin_v
            max_valid = max(cal.wet_v, cal.dry_v) + cal.margin_v
            if volts < min_valid or volts > max_valid:
                return False, f"Probe voltage ({volts:.2f}V) outside sanity band [{min_valid:.2f}V - {max_valid:.2f}V]."

        # Flow Meter Fault Check
        if self.flow_faults.get(zone_key, False):
            return False, f"Zone '{zone_key}' has active NO FLOW fault. Inspection required."

        # Cooldown Check (unless manual)
        if not is_manual:
            cooldown_until = z_state.get("cooldown_until_ts", 0.0)
            if cooldown_until > now:
                rem_h = round((cooldown_until - now) / 3600.0, 1)
                return False, f"Zone in cooldown protection ({rem_h}h remaining) to prevent root rot."

        return True, None

    def on_pump_started(self, zone_key: str, initial_pulses: int = 0) -> None:
        """Records start of pump run for flow verification."""
        self._zone_run_start_mono[zone_key] = get_monotonic_now()
        self._zone_pulses_start[zone_key] = initial_pulses

    def rollback_pump_started(self, zone_key: str) -> None:
        """Rolls back start tracking if pump command failed or ended."""
        self._zone_run_start_mono.pop(zone_key, None)
        self._zone_pulses_start.pop(zone_key, None)

    async def check_flow_meter_health(self, zone_key: str, current_pulses: int) -> None:
        """
        Flow check (Part 2.2):
        If zone has flow meter and pump has run 10s with fewer pulses than min_pulses_10s,
        raise NO FLOW, lock zone, and alert.
        """
        z_cfg = system_config.zones.get(zone_key)
        if not z_cfg or not z_cfg.has_flow_meter:
            return

        start_mono = self._zone_run_start_mono.get(zone_key)
        if start_mono is None:
            return

        run_time = get_monotonic_now() - start_mono
        if run_time >= 10.0:
            accumulated = current_pulses - self._zone_pulses_start.get(zone_key, 0)
            if accumulated < z_cfg.min_pulses_10s:
                self.flow_faults[zone_key] = True
                await state_manager.set_zone_flow_fault(zone_key, True)
                # Cut pump immediately
                zone_idx = int(zone_key.split("_")[-1])
                await garden_gateway.send_pump_stop(zone_idx)
                msg = f"NO FLOW DETECTED on {zone_key} after 10s ({accumulated} pulses < {z_cfg.min_pulses_10s}). Pump locked."
                await state_manager.set_alarm(f"no_flow_{zone_key}", msg)
                await audio_controller.speak(f"Alert: No water flow detected in {zone_key.replace('_', ' ')}. Pump locked.", priority=0)
                await telegram_notifier.send_alert("NO_FLOW", msg)
                logger.error("SAFETY INTERLOCK: %s", msg)


failsafe_engine = FailsafeSafetyEngine()
