"""
Virtual ESP32 Wireless Node Simulator
Runs inside the Debian container and communicates via virtual serial PTY /tmp/node-gateway.
Emits Protocol v1.1 HMAC-SHA256 signed packets:
- Gateway init and heartbeats
- ESP-NOW Node heartbeats with reservoir_wet=True
- Soil moisture readings (Zone 1)
- BME280 microclimate telemetry (env)
- Responds to pump commands with signed Ack and accumulates flow pulses.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, "/opt/smart-water")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.hardware.protocol import (
    format_ack_unsigned,
    format_flow_unsigned,
    format_hb_unsigned,
    format_pump_unsigned,
    format_soil_unsigned,
    sign_packet_string,
    verify_and_strip_sig,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [VirtualESP32] %(message)s",
)
logger = logging.getLogger("VirtualESP32")

PORT_PATH = "/tmp/node-gateway"
SHARED_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


class VirtualESP32Node:
    def __init__(self, port_path: str, shared_key: str):
        self.port_path = port_path
        self.shared_key = shared_key
        self.node_nonce = int(time.time()) & 0xFFFFFFFF
        self.seq = 100
        self.active_zone_pump: Optional[int] = None
        self.pump_stop_time: float = 0.0
        self.pulse_accumulator: int = 0
        self.start_time = time.time()

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def send_raw_signed(self, fd: int, unsigned_canonical_str: str) -> None:
        try:
            line = sign_packet_string(unsigned_canonical_str, self.shared_key)
            os.write(fd, line.encode("utf-8"))
        except Exception as e:
            logger.warning("Error writing packet: %s", e)

    def send_gateway_raw(self, fd: int, line_dict: dict) -> None:
        try:
            line = json.dumps(line_dict, separators=(",", ":")) + "\n"
            os.write(fd, line.encode("utf-8"))
        except Exception as e:
            logger.warning("Error writing gateway line: %s", e)

    def run(self):
        logger.info("Starting Virtual ESP32 Node on serial port %s...", self.port_path)

        while not os.path.exists(self.port_path):
            logger.info("Waiting for virtual PTY link at %s...", self.port_path)
            time.sleep(1.0)

        fd = os.open(self.port_path, os.O_RDWR | os.O_NOCTTY)
        logger.info("Connected to serial descriptor %d", fd)

        # 1. Send Gateway Init Packet
        self.send_gateway_raw(fd, {
            "v": "1.1",
            "t": "gw_init",
            "uptime_s": 0,
            "channel": 1,
            "mac": "24:6F:28:AA:BB:CC",
        })

        buffer = ""
        last_gw_hb = 0.0
        last_node_hb = 0.0
        last_soil = 0.0
        last_flow = 0.0

        os.set_blocking(fd, False)

        while True:
            now = time.time()

            # Read incoming commands from Host
            try:
                data = os.read(fd, 1024)
                if data:
                    buffer += data.decode("utf-8", errors="ignore")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if line:
                            self.handle_host_command(fd, line)
            except BlockingIOError:
                pass
            except Exception as e:
                logger.warning("Serial read error: %s", e)

            # Check if active zone watering is running
            if self.active_zone_pump is not None:
                if now >= self.pump_stop_time:
                    # Pump finished
                    logger.info("Zone %d pulse duration complete.", self.active_zone_pump)
                    p_unsigned = format_pump_unsigned(
                        nonce=self.node_nonce,
                        seq=self.next_seq(),
                        z=self.active_zone_pump,
                        on=False,
                        reason="complete",
                        run_s=0,
                    )
                    self.send_raw_signed(fd, p_unsigned)
                    self.active_zone_pump = None
                elif now - last_flow >= 1.0:
                    # Emit flow packet every 1s
                    last_flow = now
                    delta = 12
                    self.pulse_accumulator += delta
                    f_unsigned = format_flow_unsigned(
                        nonce=self.node_nonce,
                        seq=self.next_seq(),
                        z=self.active_zone_pump,
                        delta_pulses=delta,
                        period_ms=1000,
                    )
                    self.send_raw_signed(fd, f_unsigned)

            # Send Gateway Heartbeat every 5s
            if now - last_gw_hb >= 5.0:
                last_gw_hb = now
                self.send_gateway_raw(fd, {"v": "1.1", "t": "gw_hb", "uptime_s": int(now - self.start_time)})

            # Send Node Heartbeat (res_wet=True, ads_ok=True, env) every 3s
            if now - last_node_hb >= 3.0:
                last_node_hb = now
                pumps = [1 if self.active_zone_pump == i else 0 for i in (1, 2, 3, 4)]
                hb_unsigned = format_hb_unsigned(
                    nonce=self.node_nonce,
                    seq=self.next_seq(),
                    uptime_s=int(now - self.start_time),
                    res_wet=True,
                    ads_ok=True,
                    pumps=pumps,
                    reset_reason=1,
                    rssi=-58,
                    env={"temp": 28.5, "rh": 62.0, "press": 1012.4},
                )
                self.send_raw_signed(fd, hb_unsigned)

            # Send Soil Moisture Telemetry every 5s
            if now - last_soil >= 5.0:
                last_soil = now
                soil_unsigned = format_soil_unsigned(
                    nonce=self.node_nonce,
                    seq=self.next_seq(),
                    z=1,
                    volts=1.85,
                )
                self.send_raw_signed(fd, soil_unsigned)

            time.sleep(0.1)

    def handle_host_command(self, fd: int, line: str) -> None:
        logger.info("Received line from Host: %s", line)
        is_valid, unsigned_str, actual_sig, err = verify_and_strip_sig(line, self.shared_key)
        if not is_valid or not unsigned_str:
            logger.warning("Host packet signature invalid: %s", err)
            return

        try:
            cmd = json.loads(unsigned_str)
        except Exception:
            return

        cmd_t = cmd.get("t")
        cmd_id = cmd.get("id")

        if cmd_t == "cmd":
            zone = int(cmd.get("z", 1))
            run_s = int(cmd.get("run_s", 10))
            logger.info("Command accepted: Water Zone %d for %ds (ID=%s)", zone, run_s, cmd_id)

            # Send signed AckPacket
            ack_unsigned = format_ack_unsigned(
                nonce=self.node_nonce,
                seq=self.next_seq(),
                id_str=cmd_id or "",
                status="ok",
            )
            self.send_raw_signed(fd, ack_unsigned)

            self.active_zone_pump = zone
            self.pump_stop_time = time.time() + run_s
            self.pulse_accumulator = 0

            # Emit pump START event
            pump_unsigned = format_pump_unsigned(
                nonce=self.node_nonce,
                seq=self.next_seq(),
                z=zone,
                on=True,
                reason="manual",
                run_s=run_s,
            )
            self.send_raw_signed(fd, pump_unsigned)

        elif cmd_t in ("stop", "stop_all"):
            logger.info("Stop command received (ID=%s)", cmd_id)
            if self.active_zone_pump is not None:
                p_unsigned = format_pump_unsigned(
                    nonce=self.node_nonce,
                    seq=self.next_seq(),
                    z=self.active_zone_pump,
                    on=False,
                    reason="manual_stop",
                    run_s=0,
                )
                self.send_raw_signed(fd, p_unsigned)
            self.active_zone_pump = None
            if cmd_id:
                ack_unsigned = format_ack_unsigned(
                    nonce=self.node_nonce,
                    seq=self.next_seq(),
                    id_str=cmd_id,
                    status="ok",
                )
                self.send_raw_signed(fd, ack_unsigned)


if __name__ == "__main__":
    node = VirtualESP32Node(PORT_PATH, SHARED_KEY)
    node.run()
