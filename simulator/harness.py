"""
Simulator Hardware & Physics Harness.
Periodically updates simulated tank depth/volume and motor current/voltage
to provide realistic live telemetry on the dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.state_manager import state_manager
from app.hardware.level_sensor import LevelReading, level_sensor
from app.hardware.power_meter import PowerReading, power_meter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [SimHarness] %(message)s")
logger = logging.getLogger("simulator.harness")


class SimulationPhysicsHarness:
    def __init__(self):
        self.tank_height_cm = 200.0
        self.water_depth_cm = 120.0  # Starts at 60% full
        self.tank_capacity_l = 3534.0
        self.last_update = time.time()
        self.total_energy_kwh = 14.25

    async def run(self) -> None:
        logger.info("Starting Simulation Physics Harness...")
        while True:
            await asyncio.sleep(1.0)
            now = time.time()
            dt = now - self.last_update
            self.last_update = now

            state = state_manager.get_state()
            motor_state = state.get("motor", {}).get("state", "IDLE")
            is_motor_running = (motor_state == "RUNNING")

            # 1. Update Tank Level Physics
            if is_motor_running:
                # Pump adds water at ~20 L/min -> Height increase ~ 0.018 cm/s
                self.water_depth_cm = min(self.tank_height_cm, self.water_depth_cm + (0.018 * dt))
            else:
                # Baseline small leakage / consumption: 0.001 cm/s
                self.water_depth_cm = max(10.0, self.water_depth_cm - (0.001 * dt))

            pct = round((self.water_depth_cm / self.tank_height_cm) * 100.0, 1)
            vol_l = round(self.tank_capacity_l * (pct / 100.0), 1)

            level_reading = LevelReading(
                water_depth_cm=round(self.water_depth_cm, 1),
                level_pct=pct,
                volume_litres=vol_l,
                tank_capacity_litres=self.tank_capacity_l,
                raw_reading=round(self.tank_height_cm - self.water_depth_cm, 1),
                timestamp=now,
            )
            level_sensor.set_mock_reading(level_reading)

            # 2. Update Motor Power Physics
            if is_motor_running:
                import random
                current = round(4.25 + random.uniform(-0.08, 0.08), 2)
                voltage = round(231.0 + random.uniform(-1.0, 1.0), 1)
                power_w = round(voltage * current * 0.88, 1)
                self.total_energy_kwh += (power_w * (dt / 3600.0)) / 1000.0
            else:
                current = 0.0
                voltage = 230.5
                power_w = 0.0

            p_reading = PowerReading(
                voltage_v=voltage,
                current_a=current,
                power_w=power_w,
                energy_kwh=round(self.total_energy_kwh, 3),
                frequency_hz=50.0,
                power_factor=0.88 if is_motor_running else 0.0,
                timestamp=now,
            )
            power_meter.set_mock_reading(p_reading)


async def main():
    harness = SimulationPhysicsHarness()
    await harness.run()


if __name__ == "__main__":
    asyncio.run(main())
