"""
Periodic Loop Scheduler & Hardware Event Coordinator for Radxa ROCK Pi S.
Schedules 1Hz head-end safety polling, 5Hz rule evaluations, and gateway packet routing.
"""

import asyncio
import logging
from typing import Any, Dict, Optional
from app.config import system_config
from app.core.event_bus import bus
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.engine.motor_controller import motor_controller
from app.engine.plant_controller import plant_controller
from app.engine.rules_evaluator import rules_evaluator
from app.hardware.gateway_driver import garden_gateway
from app.hardware.gpio_driver import head_end_gpio

logger = logging.getLogger("smart_water.scheduler")


from app.core.watchdog import health_monitor
from app.hardware.protocol import FlowPacket, HeartbeatPacket, SoilPacket


class SystemScheduler:
    """Coordinates background loops and telemetry events."""

    def __init__(self):
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._rules_task: Optional[asyncio.Task] = None
        self._purge_task: Optional[asyncio.Task] = None
        self._db_downsample_counter = 0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        # Hook gateway packet handler
        garden_gateway.set_packet_callback(self._on_gateway_packet)

        self._poll_task = asyncio.create_task(self._head_end_poll_loop(), name="head_end_poll_loop")
        self._rules_task = asyncio.create_task(self._rules_and_auto_loop(), name="rules_auto_loop")
        self._purge_task = asyncio.create_task(self._data_purge_loop(), name="data_purge_loop")
        logger.info("SystemScheduler loops started.")

    async def stop(self) -> None:
        self._running = False
        for t in [self._poll_task, self._rules_task, self._purge_task]:
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        logger.info("SystemScheduler stopped.")

    async def _on_gateway_packet(self, packet: Any) -> None:
        """Processes incoming Protocol v1.1 packets from garden gateway."""
        if isinstance(packet, HeartbeatPacket):
            await state_manager.update_garden_heartbeat(
                seq=packet.seq,
                uptime_s=packet.uptime_s,
                res_wet=packet.res_wet,
                ads_ok=packet.ads_ok,
                pumps=packet.pumps,
                rssi=packet.rssi,
                boot=packet.boot,
            )
        elif isinstance(packet, SoilPacket):
            await state_manager.update_garden_soil(packet.z, packet.volts)
        elif isinstance(packet, FlowPacket):
            await state_manager.update_garden_flow_delta(packet.z, packet.pulses_delta, period_ms=1000)
            z_key = f"zone_{packet.z}"
            state = state_manager.get_state()
            current_total = state["garden"]["zones"][z_key].get("flow_pulses", 0)
            await failsafe_engine.check_flow_meter_health(z_key, current_total)
        elif isinstance(packet, dict):
            # Dict compatibility for raw or legacy handlers
            ptype = packet.get("t")
            if ptype == "hb":
                await state_manager.update_garden_heartbeat(
                    seq=packet.get("seq", 0),
                    uptime_s=packet.get("uptime_s", 0),
                    res_wet=packet.get("res_wet", False),
                    ads_ok=packet.get("ads_ok", True),
                    pumps=packet.get("pumps", [0, 0, 0, 0]),
                    rssi=packet.get("rssi", 0),
                    boot=packet.get("boot", 0),
                )
            elif ptype == "soil":
                await state_manager.update_garden_soil(packet.get("z", 1), packet.get("volts", 0.0))
            elif ptype == "flow":
                delta = packet.get("pulses_delta", packet.get("pulses", 0))
                zone_idx = packet.get("z", 1)
                await state_manager.update_garden_flow_delta(zone_idx, delta, period_ms=1000)
                z_key = f"zone_{zone_idx}"
                state = state_manager.get_state()
                current_total = state["garden"]["zones"][z_key].get("flow_pulses", 0)
                await failsafe_engine.check_flow_meter_health(z_key, current_total)
            elif ptype == "pump":
                zone_idx = packet.get("z", 1)
                is_on = packet.get("on", False)
                reason = packet.get("reason", "unknown")
                await state_manager.update_garden_pump(zone_idx, is_on, reason)
                if not is_on:
                    await plant_controller.on_pump_completed(f"zone_{zone_idx}", reason=reason)

    async def _head_end_poll_loop(self) -> None:
        """1Hz Loop: Polls PC817 inputs, evaluates interlocks, checks staleness."""
        while self._running:
            # Heartbeat to health monitor (Part 2.1)
            health_monitor.beat("head_end_poll")
            try:
                # 1. Read hardware inputs
                inputs = await head_end_gpio.read_head_end_inputs()

                # 2. Update state manager
                await state_manager.update_head_end(
                    tank_low=inputs["tank_low_float"],
                    interlock_out=inputs["interlock_out"],
                    interlock_mid=inputs["interlock_mid"],
                    leak_detected=inputs["leak_sensor"],
                    hardware_available=head_end_gpio.is_hardware_available,
                )

                # 3. Check staleness
                await state_manager.check_staleness()

                # 4. Evaluate failsafes
                await failsafe_engine.evaluate_head_end_safety()

                # 5. Evaluate motor auto-replenishment
                await motor_controller.evaluate_auto_replenishment()

                # 6. Publish telemetry to event bus for SSE
                state = state_manager.get_state()
                await bus.publish("telemetry:tick", state)

                # 7. Downsampled database record every 60s
                self._db_downsample_counter += 1
                if self._db_downsample_counter >= 60:
                    self._db_downsample_counter = 0
                    await self._record_downsampled_reading(state)

                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in head-end polling loop: %s", exc)
                await asyncio.sleep(1.0)

    async def _rules_and_auto_loop(self) -> None:
        """5s Loop: Evaluates auto-watering for calibrated zones and user rules."""
        while self._running:
            # Heartbeat to health monitor (Part 2.1)
            health_monitor.beat("rules_auto_loop")
            try:
                await plant_controller.evaluate_auto_watering()
                await rules_evaluator.evaluate_tick()
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in rules and auto-watering loop: %s", exc)
                await asyncio.sleep(5.0)

    async def _data_purge_loop(self) -> None:
        """Daily loop: Purges database records older than configured retention."""
        while self._running:
            try:
                await asyncio.sleep(86400)
                await state_manager.purge_old_data()
                logger.info("Database historical data retention purge executed.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in data purge loop: %s", e)

    async def _record_downsampled_reading(self, state: Dict[str, Any]) -> None:
        """Logs a single downsampled telemetry row to the readings table."""
        he = state["head_end"]
        motor = state["motor"]
        g = state["garden"]
        z = g["zones"]

        def _insert():
            with state_manager._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO readings (
                        timestamp, tank_low, interlock_cause, motor_running, res_wet,
                        z1_volts, z2_volts, z3_volts, z4_volts, z1_flow
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        datetime.now(timezone.utc).isoformat(),
                        1 if he.get("tank_low_float") is True else (0 if he.get("tank_low_float") is False else None),
                        he.get("interlock_cause"),
                        1 if motor["state"] == "RUNNING" else 0,
                        1 if g.get("reservoir_wet") is True else (0 if g.get("reservoir_wet") is False else None),
                        z.get("zone_1", {}).get("voltage"),
                        z.get("zone_2", {}).get("voltage"),
                        z.get("zone_3", {}).get("voltage"),
                        z.get("zone_4", {}).get("voltage"),
                        z.get("zone_1", {}).get("flow_pulses", 0),
                    ),
                )
                conn.commit()

        await asyncio.to_thread(_insert)


scheduler = SystemScheduler()
