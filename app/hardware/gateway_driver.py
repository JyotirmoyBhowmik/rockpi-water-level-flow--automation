"""
Garden Gateway Serial Driver (USB ESP32-C3 Gateway via /dev/garden-gateway).
Speaks JSON Lines protocol (v: 1) at 115200 baud.
Handles async transmission, link-loss timeout detection, and packet validation.
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

logger = logging.getLogger("smart_water.gateway")

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    serial = None
    HAS_SERIAL = False


class GardenGatewayDriver:
    """
    Asynchronous serial gateway driver connecting ROCK Pi S to ESP32-C3 Gateway.
    Serial Protocol: UTF-8 JSON Lines with 'v': 1 version identifier.
    """

    def __init__(self):
        self._serial_port = None
        self._running = False
        self._reader_task: Optional[asyncio.Task] = None
        self._last_packet_time: float = 0.0
        self._is_link_online: bool = False
        self._write_lock = asyncio.Lock()
        self._on_packet_callback: Optional[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]] = None

    @property
    def is_link_online(self) -> bool:
        return self._is_link_online

    @property
    def last_packet_age_s(self) -> float:
        if self._last_packet_time == 0.0:
            return 999999.0
        return time.time() - self._last_packet_time

    def set_packet_callback(self, cb: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
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

    async def send_pump_run(self, zone_idx: int, run_seconds: int) -> str:
        """Sends command to run pump for zone (z=1..4). Returns command UUID."""
        cmd_id = str(uuid.uuid4())
        msg = {
            "v": 1,
            "t": "cmd",
            "id": cmd_id,
            "z": zone_idx,
            "run_s": run_seconds,
        }
        await self._write_json_line(msg)
        return cmd_id

    async def send_pump_stop(self, zone_idx: int) -> None:
        """Sends command to stop pump for zone (z=1..4)."""
        msg = {
            "v": 1,
            "t": "stop",
            "z": zone_idx,
        }
        await self._write_json_line(msg)

    async def send_stop_all(self) -> None:
        """Sends emergency stop all pumps command to garden node."""
        msg = {
            "v": 1,
            "t": "stop_all",
        }
        await self._write_json_line(msg)

    async def _write_json_line(self, data: Dict[str, Any]) -> bool:
        """Encodes and transmits a JSON Line to the gateway serial device."""
        if not self._serial_port:
            logger.warning("Cannot transmit packet: Gateway serial port not connected.")
            return False

        line = json.dumps(data) + "\n"
        encoded = line.encode("utf-8")

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

    async def _gateway_loop(self) -> None:
        """Worker loop maintaining connection and decoding JSON Lines."""
        port_path = system_config.garden_gateway.serial_port
        baud = system_config.garden_gateway.baud_rate

        while self._running:
            if not HAS_SERIAL:
                logger.warning("pyserial is not installed. Gateway serial disabled.")
                await asyncio.sleep(10.0)
                continue

            if not os.path.exists(port_path):
                if self._is_link_online:
                    self._is_link_online = False
                    logger.warning("Garden gateway device %s disconnected.", port_path)
                    await bus.publish("garden:link_lost", {"reason": "device_missing"})
                await asyncio.sleep(3.0)
                continue

            # Open port
            try:
                def _open_port():
                    return serial.Serial(
                        port=port_path,
                        baudrate=baud,
                        timeout=1.0,
                    )
                self._serial_port = await asyncio.to_thread(_open_port)
                logger.info("Connected to garden gateway serial on %s at %d baud.", port_path, baud)
            except Exception as exc:
                logger.warning("Could not open serial port %s: %s", port_path, exc)
                self._serial_port = None
                await asyncio.sleep(5.0)
                continue

            # Read lines
            while self._running and self._serial_port:
                try:
                    line_bytes = await asyncio.to_thread(self._serial_port.readline)
                    if not line_bytes:
                        # Check link loss timeout
                        if self._is_link_online and self.last_packet_age_s > system_config.garden_gateway.link_loss_timeout_s:
                            self._is_link_online = False
                            logger.error(
                                "LINK LOST: No telemetry from garden node for %ds. Locking all zones.",
                                int(self.last_packet_age_s),
                            )
                            await bus.publish("garden:link_lost", {"age_s": self.last_packet_age_s})
                        continue

                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue

                    try:
                        packet = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("Malformed JSON line from gateway: %s", line)
                        continue

                    # Validate version
                    if packet.get("v") != 1:
                        logger.warning("Unsupported packet version from gateway: %s", line)
                        continue

                    self._last_packet_time = time.time()
                    if not self._is_link_online:
                        self._is_link_online = True
                        logger.info("Garden wireless link established with gateway.")
                        await bus.publish("garden:link_restored", {})

                    # Callback dispatch
                    if self._on_packet_callback:
                        await self._on_packet_callback(packet)

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
            await asyncio.sleep(2.0)


garden_gateway = GardenGatewayDriver()
