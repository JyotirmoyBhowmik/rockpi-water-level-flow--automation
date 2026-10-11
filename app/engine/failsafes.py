"""
Non-Bypassable Hardcoded Safety Interlocks (Priority 0).
Hardened for industrial reliability and equipment protection on Rock Pi S.
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Set, Tuple
from app.config import settings
from app.core.event_bus import bus
from app.core.state_manager import state_manager
from app.hardware.audio_driver import audio_controller
from app.hardware.gpio_driver import gpio_controller

logger = logging.getLogger("smart_water.failsafes")


class FailsafeEngine:
    """
    Priority 0 Safety Guardian.
    Guarantees physical safety constraints are continuously enforced
    regardless of user rules, manual overrides, or external API commands.
    """

    def __init__(self):
        self.motor_lockout_active = False
        self.dry_run_lockout_active = False
        self.tank_high_lockout_active = False
        self.faulty_probes: Set[str] = set()

    async def evaluate_safety_interlocks(
        self,
        tank_low: bool,
        tank_high: bool,
        reservoir_empty: bool,
        soil_voltages: Dict[str, float],
    ) -> Dict[str, bool]:
        """
        Executes all Priority 0 checks against current physical inputs.
        Directly commands GPIO lines LOW if an unsafe condition is detected.
        Returns a dictionary of active lockout flags.
        """
        now = time.time()
        current_state = state_manager.get_state()
        actuators = current_state["actuators"]
        tracking = current_state["runtime_tracking"]

        # =====================================================================
        # 1. DRY-RUN LOCKOUT (Reservoir Empty)
        # =====================================================================
        if reservoir_empty:
            self.dry_run_lockout_active = True
            await state_manager.set_alarm("dry_run", "Irrigation reservoir empty. DC pumps locked.")

            # Force all 4 DC pumps OFF immediately
            zone_pins = [
                ("zone_1_pump", settings.PIN_ZONE_1_MOSFET),
                ("zone_2_pump", settings.PIN_ZONE_2_MOSFET),
                ("zone_3_pump", settings.PIN_ZONE_3_MOSFET),
                ("zone_4_pump", settings.PIN_ZONE_4_MOSFET),
            ]
            for pump_name, pin in zone_pins:
                if actuators.get(pump_name, False):
                    await gpio_controller.set_pin(pin, False)
                    await state_manager.update_actuator(pump_name, False)
                    logger.warning("Failsafe P0: Cut %s due to dry-run condition.", pump_name)

            await audio_controller.speak("Warning: Irrigation reservoir is empty. Plant pumps locked.")
            await bus.publish("failsafe:dry_run", {"active": True})
        else:
            if self.dry_run_lockout_active:
                self.dry_run_lockout_active = False
                await state_manager.clear_alarm("dry_run")
                logger.info("Failsafe: Reservoir refilled. Dry-run lockout cleared.")

        # =====================================================================
        # 2. OVERHEAD TANK HIGH CUTOFF (Tank Full)
        # =====================================================================
        if tank_high:
            self.tank_high_lockout_active = True
            await state_manager.set_alarm("tank_full", "Overhead tank high-level reached.")

            if actuators.get("ac_contactor", False):
                await gpio_controller.set_pin(settings.PIN_AC_CONTACTOR, False)
                await state_manager.update_actuator("ac_contactor", False)
                logger.warning("Failsafe P0: Overhead tank full. Cut AC contactor.")
                await audio_controller.speak("Overhead tank full. Main pump shut off.")
                await bus.publish("failsafe:tank_high", {"active": True})
        else:
            if self.tank_high_lockout_active:
                self.tank_high_lockout_active = False
                await state_manager.clear_alarm("tank_full")

        # =====================================================================
        # 3. MAX MOTOR RUNTIME GUARD (15 min continuous limit)
        # =====================================================================
        ac_started = tracking.get("ac_contactor_started_at")
        if actuators.get("ac_contactor", False) and ac_started is not None:
            runtime = now - ac_started
            if runtime > settings.MAX_AC_MOTOR_RUNTIME_SEC:
                self.motor_lockout_active = True
                await gpio_controller.set_pin(settings.PIN_AC_CONTACTOR, False)
                await state_manager.update_actuator("ac_contactor", False)
                await state_manager.set_alarm(
                    "motor_timeout",
                    f"AC Motor ran {int(runtime)}s (max {settings.MAX_AC_MOTOR_RUNTIME_SEC}s). Lockout active.",
                )
                logger.error("Failsafe P0: Max motor runtime exceeded (%ds). Lockout engaged.", int(runtime))
                await audio_controller.speak("Emergency: Main pump continuous runtime exceeded fifteen minutes.")
                await bus.publish("failsafe:motor_timeout", {"runtime_seconds": runtime})

        # =====================================================================
        # 4. OUT-OF-BOUNDS SENSOR GUARD (<0.5V or >3.0V)
        # =====================================================================
        active_faulty_probes = set()
        for zone, voltage in soil_voltages.items():
            if voltage < settings.SENSOR_VOLTAGE_MIN_V or voltage > settings.SENSOR_VOLTAGE_MAX_V:
                active_faulty_probes.add(zone)
                alarm_key = f"probe_fault_{zone}"
                await state_manager.set_alarm(
                    alarm_key,
                    f"Probe {zone} reading out-of-bounds: {voltage:.2f}V (Safe: {settings.SENSOR_VOLTAGE_MIN_V}-{settings.SENSOR_VOLTAGE_MAX_V}V)",
                )
                # If zone pump is running, cut it
                pump_name = f"{zone}_pump"
                pin_map = {
                    "zone_1_pump": settings.PIN_ZONE_1_MOSFET,
                    "zone_2_pump": settings.PIN_ZONE_2_MOSFET,
                    "zone_3_pump": settings.PIN_ZONE_3_MOSFET,
                    "zone_4_pump": settings.PIN_ZONE_4_MOSFET,
                }
                if actuators.get(pump_name, False):
                    pin = pin_map[pump_name]
                    await gpio_controller.set_pin(pin, False)
                    await state_manager.update_actuator(pump_name, False)
                    logger.warning("Failsafe P0: Cut %s due to sensor fault (%0.2fV).", pump_name, voltage)

                if zone not in self.faulty_probes:
                    await audio_controller.speak(f"Alert: Soil moisture probe in {zone.replace('_', ' ')} out of bounds.")
            else:
                alarm_key = f"probe_fault_{zone}"
                await state_manager.clear_alarm(alarm_key)

        self.faulty_probes = active_faulty_probes

        # Update aggregated status in StateManager
        await state_manager.update_failsafe_status(
            dry_run=self.dry_run_lockout_active,
            tank_high=self.tank_high_lockout_active,
            motor_timeout=self.motor_lockout_active,
            faulty_probes=list(self.faulty_probes),
        )

        return {
            "dry_run_locked": self.dry_run_lockout_active,
            "tank_high_locked": self.tank_high_lockout_active,
            "motor_timeout_locked": self.motor_lockout_active,
            "faulty_probes": list(self.faulty_probes),
        }

    # =========================================================================
    # Authorization checks for incoming actuation commands
    # =========================================================================

    def can_start_ac_motor(self) -> Tuple[bool, Optional[str]]:
        """Verify whether AC Contactor can safely be energized."""
        if self.motor_lockout_active:
            return False, "AC Motor locked out due to continuous runtime guard. Manual reset required."
        if self.tank_high_lockout_active:
            return False, "Cannot start AC Motor: Overhead tank is already full."
        return True, None

    def can_start_zone_pump(self, zone: str, ignore_cooldown: bool = False) -> Tuple[bool, Optional[str]]:
        """Verify whether a plant drip pump can safely be energized."""
        if self.dry_run_lockout_active:
            return False, "Cannot start irrigation: Ground reservoir is empty (Dry-Run Lockout)."
        if zone in self.faulty_probes:
            return False, f"Cannot start irrigation for {zone}: Capacitive probe reading is out-of-bounds."

        # 5. Zone Cooldown Check (minimum 6 hours to prevent root rot)
        if not ignore_cooldown:
            state = state_manager.get_state()
            cooldown_until = state["runtime_tracking"]["zone_cooldown_until"].get(zone, 0.0)
            now = time.time()
            if cooldown_until > now:
                remaining_min = int((cooldown_until - now) / 60)
                return (
                    False,
                    f"Zone {zone} in cooldown protection ({remaining_min}m remaining). Prevents root rot.",
                )

        return True, None

    def reset_motor_lockout(self) -> None:
        """Manual operator reset for motor timeout lockout."""
        self.motor_lockout_active = False
        logger.info("Operator manually reset AC Motor lockout.")


failsafe_engine = FailsafeEngine()
