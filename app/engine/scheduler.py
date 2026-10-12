"""
Periodic Loop Scheduler & Hardware Event Coordinator for Radxa ROCK Pi S.
Schedules 1Hz head-end safety polling, 5Hz rule evaluations, and gateway packet routing.
"""

from datetime import datetime, timezone
import asyncio
import logging
from typing import Any, Dict, Optional
from app.core.event_bus import bus
from app.core.state_manager import state_manager
from app.core.watchdog import health_monitor
from app.engine.failsafes import failsafe_engine
from app.engine.motor_controller import motor_controller
from app.engine.plant_controller import plant_controller
from app.engine.rules_evaluator import rules_evaluator
from app.hardware.gateway_driver import garden_gateway
from app.hardware.gpio_driver import head_end_gpio
from app.hardware.protocol import (
    AckPacket,
    FlowPacket,
    HeartbeatPacket,
    PumpEventPacket,
    SoilPacket,
)

logger = logging.getLogger("smart_water.scheduler")


class SystemScheduler:
    """Coordinates background loops and telemetry events."""

    def __init__(self) -> None:
        self._running = False
        self._poll_task: Optional[asyncio.Task[None]] = None
        self._rules_task: Optional[asyncio.Task[None]] = None
        self._purge_task: Optional[asyncio.Task[None]] = None
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

    async def _handle_heartbeat(
        self,
        seq: int,
        uptime_s: int,
        res_wet: bool,
        ads_ok: bool,
        pumps: list[int],
        rssi: int,
        nonce: int,
    ) -> None:
        await state_manager.update_garden_heartbeat(
            seq=seq,
            uptime_s=uptime_s,
            res_wet=res_wet,
            ads_ok=ads_ok,
            pumps=pumps,
            rssi=rssi,
            nonce=nonce,
            boot=nonce,
        )

    async def _handle_soil(self, zone_idx: int, volts: float) -> None:
        await state_manager.update_garden_soil(zone_idx, volts)

    async def _handle_flow(self, zone_idx: int, delta_pulses: int, period_ms: int) -> None:
        await state_manager.update_garden_flow_delta(zone_idx, delta_pulses, period_ms=period_ms)
        z_key = f"zone_{zone_idx}"
        state = state_manager.get_state()
        current_total = state["garden"]["zones"][z_key].get("flow_pulses", 0)
        await failsafe_engine.check_flow_meter_health(z_key, current_total)

    async def _handle_pump(self, zone_idx: int, is_on: bool, reason: str) -> None:
        await state_manager.update_garden_pump(zone_idx, is_on, reason)
        if not is_on:
            await plant_controller.on_pump_completed(f"zone_{zone_idx}", reason=reason)

    async def _handle_ack(self, ack: Any) -> None:
        garden_gateway.resolve_ack(ack)

    async def _on_gateway_packet(self, packet: Any) -> None:
        """Processes incoming Protocol v1.1 packets from garden gateway."""
        if isinstance(packet, HeartbeatPacket):
            await self._handle_heartbeat(
                seq=packet.seq,
                uptime_s=packet.uptime_s,
                res_wet=packet.res_wet,
                ads_ok=packet.ads_ok,
                pumps=packet.pumps,
                rssi=packet.rssi,
                nonce=packet.nonce,
            )
        elif isinstance(packet, SoilPacket):
            await self._handle_soil(zone_idx=packet.z, volts=packet.volts)
        elif isinstance(packet, FlowPacket):
            await self._handle_flow(
                zone_idx=packet.z,
                delta_pulses=packet.delta_pulses,
                period_ms=packet.period_ms,
            )
        elif isinstance(packet, PumpEventPacket):
            await self._handle_pump(
                zone_idx=packet.z,
                is_on=packet.on,
                reason=packet.reason,
            )
        elif isinstance(packet, AckPacket):
            await self._handle_ack(packet)
        elif isinstance(packet, dict):
            # Dict compatibility for raw or legacy handlers routed through identical functions
            ptype = packet.get("t")
            if ptype == "hb":
                await self._handle_heartbeat(
                    seq=packet.get("seq", 0),
                    uptime_s=packet.get("uptime_s", 0),
                    res_wet=packet.get("res_wet", False),
                    ads_ok=packet.get("ads_ok", True),
                    pumps=packet.get("pumps", [0, 0, 0, 0]),
                    rssi=packet.get("rssi", 0),
                    nonce=packet.get("nonce", packet.get("boot", 0)),
                )
            elif ptype == "soil":
                await self._handle_soil(
                    zone_idx=packet.get("z", 1),
                    volts=packet.get("volts", 0.0),
                )
            elif ptype == "flow":
                delta = packet.get("delta_pulses", packet.get("pulses_delta", packet.get("pulses", 0)))
                period = packet.get("period_ms", 1000)
                await self._handle_flow(
                    zone_idx=packet.get("z", 1),
                    delta_pulses=delta,
                    period_ms=period,
                )
            elif ptype == "pump":
                await self._handle_pump(
                    zone_idx=packet.get("z", 1),
                    is_on=packet.get("on", False),
                    reason=packet.get("reason", "unknown"),
                )
            elif ptype == "ack":
                await self._handle_ack(packet)

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
                    raw=inputs.get("raw"),
                    debounced=inputs.get("debounced"),
                    contactor_feedback=inputs.get("contactor_feedback"),
                )

                # 2b. Read power meter telemetry if enabled
                from app.hardware.power_meter import power_meter
                power_reading = await power_meter.read_telemetry()
                if power_reading is not None:
                    await state_manager.update_power_meter(power_reading.to_dict())

                # 2c. Read continuous level sensor telemetry if enabled
                from app.hardware.level_sensor import level_sensor
                level_reading = await level_sensor.read_telemetry()
                if level_reading is not None:
                    await state_manager.update_level_sensor(level_reading.to_dict())

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

        def _insert() -> None:
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
