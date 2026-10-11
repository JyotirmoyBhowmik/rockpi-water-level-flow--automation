"""
Plant Irrigation Controller for ESP32 Wireless Garden Node.
Manages 4-zone precision drip pumps, pulse limits, cooldown lockouts, and flow checks.
"""

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, Optional, Tuple
from app.config import system_config
from app.core.event_bus import bus
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.hardware.audio_driver import audio_controller
from app.hardware.gateway_driver import garden_gateway
from app.hardware.rtc import get_monotonic_now

logger = logging.getLogger("smart_water.plants")


class PlantIrrigationController:
    """Coordinates drip irrigation cycles across wireless ESP32 zones."""

    def __init__(self):
        self._active_runs: Dict[str, Dict[str, Any]] = {}
        self._tasks: set = set()
        self._lock = asyncio.Lock()

    async def trigger_zone_pulse(
        self,
        zone_key: str,
        duration_s: Optional[int] = None,
        is_manual: bool = False,
        source: str = "manual",
    ) -> Tuple[bool, str]:
        """
        Commands garden node to run a pump for specified seconds.
        Server-side cap: strictly clamped to max_pulse_seconds (<= 60s).
        """
        async with self._lock:
            # Validate zone exists
            z_cfg = system_config.zones.get(zone_key)
            if not z_cfg:
                return False, f"Unknown zone '{zone_key}'."

            # Authorization check with failsafe engine
            allowed, reason = failsafe_engine.can_water_zone(zone_key, is_manual=is_manual)
            if not allowed:
                logger.warning("Irrigation for %s rejected: %s", zone_key, reason)
                return False, f"Watering blocked: {reason}"

            # Strict duration clamping (Part 2.2: Hard cap 60s per pump run)
            requested_sec = duration_s or z_cfg.pulse_seconds
            clamped_sec = max(1, min(requested_sec, z_cfg.max_pulse_seconds, 60))

            # Limit concurrent running pumps to max 2
            running_count = len([r for r in self._active_runs.values() if r.get("running")])
            if running_count >= 2:
                return False, "Maximum 2 concurrent pumps allowed on battery/solar bus."

            zone_idx = int(zone_key.split("_")[-1])
            cmd_id = await garden_gateway.send_pump_run(zone_idx, clamped_sec)

            now_iso = datetime.now(timezone.utc).isoformat()
            state = state_manager.get_state()
            initial_pulses = state["garden"]["zones"][zone_key].get("flow_pulses", 0)

            failsafe_engine.on_pump_started(zone_key, initial_pulses)
            self._active_runs[zone_key] = {
                "running": True,
                "cmd_id": cmd_id,
                "duration_s": clamped_sec,
                "started_mono": get_monotonic_now(),
                "started_iso": now_iso,
                "source": source,
            }

            log_msg = f"Starting {zone_key} pump for {clamped_sec}s (Source: {source})."
            logger.info(log_msg)
            await state_manager.log_event("ZONE_PUMP_START", "INFO", log_msg, {"zone": zone_key, "duration_s": clamped_sec})
            await audio_controller.speak(f"Watering {zone_key.replace('_', ' ')} for {clamped_sec} seconds.", priority=2)
            await bus.publish("pump:started", {"zone": zone_key, "duration_s": clamped_sec, "source": source})

            # Local fallback timer to record completion if gateway packet is lost
            async def _completion_watcher():
                try:
                    await asyncio.sleep(clamped_sec + 2)
                    await self.on_pump_completed(zone_key, reason="done")
                except asyncio.CancelledError:
                    pass

            t = asyncio.create_task(_completion_watcher())
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

            return True, f"Zone {zone_key} energized for {clamped_sec}s."

    async def stop_zone_pulse(self, zone_key: str, reason: str = "manual_stop") -> Tuple[bool, str]:
        """Manually stops an active pump run."""
        async with self._lock:
            zone_idx = int(zone_key.split("_")[-1])
            await garden_gateway.send_pump_stop(zone_idx)
            await self.on_pump_completed(zone_key, reason=reason)
            return True, f"Zone {zone_key} stop signal dispatched."

    async def stop_all_zones(self, reason: str = "emergency_stop") -> None:
        """Kills all active zone pumps immediately."""
        async with self._lock:
            for t in list(self._tasks):
                t.cancel()
            self._tasks.clear()
            await garden_gateway.send_stop_all()
            for z_key in list(self._active_runs.keys()):
                await self.on_pump_completed(z_key, reason=reason)

    async def on_pump_completed(self, zone_key: str, reason: str = "done") -> None:
        """Called when node signals completion or timer expires."""
        run_info = self._active_runs.pop(zone_key, None)
        now = time.time()
        z_cfg = system_config.zones.get(zone_key)

        duration_s = 0
        if run_info:
            duration_s = int(get_monotonic_now() - run_info["started_mono"])
            started_iso = run_info["started_iso"]
            await state_manager.log_run(f"pump.{zone_key}", started_iso, duration_s, reason)

        # Enforce zone cooldown lockout (default 6h)
        if z_cfg:
            cooldown_s = z_cfg.cooldown_hours * 3600.0
            lockout_until = now + cooldown_s
            await state_manager.set_zone_cooldown(zone_key, lockout_until)

        log_msg = f"{zone_key} watering cycle ended (Duration: {duration_s}s, Reason: {reason})."
        logger.info(log_msg)
        await state_manager.log_event("ZONE_PUMP_STOP", "INFO", log_msg, {"zone": zone_key, "duration_s": duration_s})
        await bus.publish("pump:stopped", {"zone": zone_key, "duration_s": duration_s, "reason": reason})

    async def evaluate_auto_watering(self) -> None:
        """
        Evaluates soil moisture dry thresholds for enabled & calibrated zones.
        """
        state = state_manager.get_state()
        g = state["garden"]

        if not g["gateway_connected"] or not g["node_online"] or g["is_stale"]:
            return

        for z_key, z_cfg in system_config.zones.items():
            if not z_cfg.enabled:
                continue

            z_state = g["zones"].get(z_key, {})
            if not z_state.get("calibrated"):
                continue

            moisture = z_state.get("moisture_pct")
            if moisture is not None and moisture < z_cfg.soil_dry_threshold_pct:
                allowed, _ = failsafe_engine.can_water_zone(z_key, is_manual=False)
                if allowed and not z_state.get("pump_running"):
                    logger.info("Auto-watering triggered for %s (Moisture %.1f%% < %.1f%%)", z_key, moisture, z_cfg.soil_dry_threshold_pct)
                    await self.trigger_zone_pulse(z_key, duration_s=z_cfg.pulse_seconds, is_manual=False, source="auto_moisture")


plant_controller = PlantIrrigationController()
