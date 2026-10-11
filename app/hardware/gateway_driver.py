"""
Garden Gateway Serial Driver (USB ESP32-C3 Gateway via /dev/garden-gateway).
Implements Protocol v1.1:
  - Canonical JSON Lines with HMAC-SHA256 signatures
  - Sequence tracking and boot announcement handling
  - Command acknowledgment waiting (2s timeout) with 3 retries and exponential backoff
  - HealthMonitor heartbeat integration (Part 2.1)
  - Separate node_last_seen and gateway_last_seen tracking
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Coroutine, Dict, Optional
from app.config import system_config
from app.core.event_bus import bus
from app.core.watchdog import health_monitor
from app.hardware.protocol import (
    AckPacket,
    CommandPacket,
    FlowPacket,
    HeartbeatPacket,
    PacketModel,
    SequenceTracker,
    SoilPacket,
    StopAllPacket,
    StopPacket,
    compute_packet_signature,
    parse_and_validate_packet,
    sign_packet,
)

logger = logging.getLogger("smart_water.gateway")

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    serial = None
    HAS_SERIAL = False


class GatewayCommandFailedException(Exception):
    """Raised when a garden pump command fails after retries."""
    pass


class GardenGatewayDriver:
    """
    Asynchronous serial gateway driver connecting ROCK Pi S to ESP32-C3 Gateway.
    Protocol v1.1 compliant.
    """

    def __init__(self):
        self._serial_port = None
        self._running = False
        self._reader_task: Optional[asyncio.Task] = None
        self._gateway_last_seen: Optional[float] = None
        self._node_last_seen: Optional[float] = None
        self._is_link_online: bool = False
        self._write_lock = asyncio.Lock()
        self._tx_seq = 0
        self._seq_tracker = SequenceTracker()
        self._pending_acks: Dict[str, asyncio.Future] = {}
        self._on_packet_callback: Optional[Callable[[PacketModel], Coroutine[Any, Any, None]]] = None

    @property
    def is_link_online(self) -> bool:
        return self._is_link_online

    @property
    def gateway_last_seen(self) -> Optional[float]:
        return self._gateway_last_seen

    @property
    def node_last_seen(self) -> Optional[float]:
        return self._node_last_seen

    def set_packet_callback(self, cb: Callable[[PacketModel], Coroutine[Any, Any, None]]) -> None:
        self._on_packet_callback = cb

    async def start(self) -> None:
        """Starts the gateway serial receiver loop."""
        if self._running:
            return
        self._running = True
        self._reader_task = asyncio.create_task(self._gateway_loop(), name="garden_gateway_worker")
        logger.info("Garden gateway serial driver loop initiated.")

    async def stop(self) -> None:
        """Stops the gateway worker and closes serial port."""
        self._running = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self._serial_port:
            try:
                self._serial_port.close()
            except Exception:
                pass
            self._serial_port = None
        self._is_link_online = False
        logger.info("Garden gateway serial driver stopped.")

    def _next_tx_seq(self) -> int:
        self._tx_seq += 1
        return self._tx_seq

    async def _write_packet(self, pkt_dict: Dict[str, Any]) -> bool:
        """Signs and writes a packet dict as a single JSON line."""
        key = system_config.garden_gateway.shared_key
        signed_pkt = sign_packet(pkt_dict, key)
        line = json.dumps(signed_pkt, separators=(",", ":")) + "\n"
        encoded = line.encode("utf-8")

        if not self._serial_port:
            logger.warning("Cannot transmit packet: Gateway serial port not connected.")
            return False

        async with self._write_lock:
            def _write():
                try:
                    self._serial_port.write(encoded)
                    self._serial_port.flush()
                    return True
                except Exception as e:
                    logger.error("Failed to write to gateway serial: %s", e)
                    return False

            return await asyncio.to_thread(_write)

    async def send_pump_run(self, zone_idx: int, run_seconds: int) -> str:
        """
        Sends command to run pump for zone (z=1..4).
        Waits up to 2.0s for Ack. Retries up to 3 times with exponential backoff.
        Raises GatewayCommandFailedException if all attempts fail.
        """
        cmd_id = str(uuid.uuid4())
        max_retries = 3
        backoff = 0.25

        for attempt in range(1, max_retries + 1):
            pkt_dict = {
                "v": "1.1",
                "t": "cmd",
                "id": cmd_id,
                "z": zone_idx,
                "run_s": run_seconds,
                "seq": self._next_tx_seq(),
            }

            loop = asyncio.get_running_loop()
            ack_future: asyncio.Future = loop.create_future()
            self._pending_acks[cmd_id] = ack_future

            sent = await self._write_packet(pkt_dict)
            if not sent:
                self._pending_acks.pop(cmd_id, None)
                if attempt == max_retries:
                    raise GatewayCommandFailedException("GATEWAY_COMMAND_FAILED: Serial transmission failed")
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            try:
                ack: AckPacket = await asyncio.wait_for(ack_future, timeout=2.0)
                if ack.status == "ok":
                    logger.info("Pump run command %s acknowledged by node (Zone %d, %ds)", cmd_id, zone_idx, run_seconds)
                    return cmd_id
                else:
                    logger.error("Pump run command %s rejected by node: %s", cmd_id, ack.message)
                    raise GatewayCommandFailedException(f"GATEWAY_COMMAND_FAILED: Node rejected ({ack.status})")
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for Ack on command %s (attempt %d/%d)", cmd_id, attempt, max_retries)
                if attempt == max_retries:
                    raise GatewayCommandFailedException(f"GATEWAY_COMMAND_FAILED: No ACK after {max_retries} retries")
                await asyncio.sleep(backoff)
                backoff *= 2
            finally:
                self._pending_acks.pop(cmd_id, None)

        raise GatewayCommandFailedException("GATEWAY_COMMAND_FAILED: Max retries exceeded")

    async def send_pump_stop(self, zone_idx: int) -> None:
        """Sends command to stop pump for zone (z=1..4)."""
        cmd_id = str(uuid.uuid4())
        pkt_dict = {
            "v": "1.1",
            "t": "stop",
            "id": cmd_id,
            "z": zone_idx,
            "seq": self._next_tx_seq(),
        }
        await self._write_packet(pkt_dict)

    async def send_stop_all(self) -> None:
        """Sends emergency stop all pumps command to garden node."""
        cmd_id = str(uuid.uuid4())
        pkt_dict = {
            "v": "1.1",
            "t": "stop_all",
            "id": cmd_id,
            "seq": self._next_tx_seq(),
        }
        await self._write_packet(pkt_dict)

    async def _gateway_loop(self) -> None:
        """Worker loop maintaining connection and decoding JSON Lines."""
        port_path = system_config.garden_gateway.serial_port
        baud = system_config.garden_gateway.baud_rate
        key = system_config.garden_gateway.shared_key

        while self._running:
            # Report loop liveness to HealthMonitor (Part 2.1)
            health_monitor.beat("gateway_reader")

            if not HAS_SERIAL:
                logger.warning("pyserial is not installed. Gateway serial disabled.")
                await asyncio.sleep(5.0)
                continue

            if not os.path.exists(port_path):
                if self._is_link_online:
                    self._is_link_online = False
                    logger.warning("Garden gateway device %s disconnected.", port_path)
                    await bus.publish("garden:link_lost", {"reason": "device_missing"})
                await asyncio.sleep(2.0)
                continue

            # Open serial port
            try:
                def _open_port():
                    return serial.Serial(
                        port=port_path,
                        baudrate=baud,
                        timeout=1.0,
                    )
                self._serial_port = await asyncio.to_thread(_open_port)
                self._gateway_last_seen = time.time()
                logger.info("Connected to garden gateway serial on %s at %d baud.", port_path, baud)
            except Exception as exc:
                logger.warning("Could not open serial port %s: %s", port_path, exc)
                self._serial_port = None
                await asyncio.sleep(3.0)
                continue

            # Read lines
            while self._running and self._serial_port:
                health_monitor.beat("gateway_reader")
                try:
                    line_bytes = await asyncio.to_thread(self._serial_port.readline)
                    now = time.time()
                    if not line_bytes:
                        # Check node link loss timeout
                        if self._is_link_online and self._node_last_seen:
                            age = now - self._node_last_seen
                            if age > system_config.garden_gateway.link_loss_timeout_s:
                                self._is_link_online = False
                                logger.error("LINK LOST: No telemetry from garden node for %ds.", int(age))
                                await bus.publish("garden:link_lost", {"age_s": age})
                        continue

                    self._gateway_last_seen = now
                    pkt, err = parse_and_validate_packet(line_bytes, key, self._seq_tracker)
                    if err:
                        logger.debug("Packet validation rejected: %s", err)
                        continue

                    if not pkt:
                        continue

                    # Node packet accepted and verified
                    self._node_last_seen = now
                    if not self._is_link_online:
                        self._is_link_online = True
                        logger.info("Garden wireless link established with node (v1.1 verified).")
                        await bus.publish("garden:link_restored", {})

                    # Handle Ack packets
                    if isinstance(pkt, AckPacket):
                        fut = self._pending_acks.get(pkt.id)
                        if fut and not fut.done():
                            fut.set_result(pkt)

                    # Forward to callback
                    if self._on_packet_callback:
                        await self._on_packet_callback(pkt)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("Error reading gateway serial stream: %s", e)
                    break

            if self._serial_port:
                try:
                    self._serial_port.close()
                except Exception:
                    pass
                self._serial_port = None
            self._is_link_online = False
            await asyncio.sleep(1.0)


garden_gateway = GardenGatewayDriver()
