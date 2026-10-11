"""
ADS1115 16-Bit ADC I2C Driver for Radxa ROCK Pi S (I2C7 /dev/i2c-7).
Reads capacitive soil moisture sensor voltages with automatic fallback to mock.
"""

import asyncio
import logging
import os
import time
from typing import Dict, Optional
from app.config import settings
from app.hardware.base import BaseADCController
from app.hardware.mock import mock_adc

logger = logging.getLogger("smart_water.adc")

try:
    import smbus2
    HAS_SMBUS = True
except ImportError:
    smbus2 = None
    HAS_SMBUS = False


class RealADS1115Driver(BaseADCController):
    """
    Physical ADS1115 I2C driver communicating directly with Linux /dev/i2c-7.
    """

    REG_CONVERSION = 0x00
    REG_CONFIG = 0x01

    # PGA +/- 4.096V -> 1 LSB = 0.125 mV = 0.000125 V
    LSB_VOLTS = 4.096 / 32768.0

    def __init__(self, bus_num: int = settings.I2C_BUS, address: int = settings.ADS1115_I2C_ADDR):
        self.bus_num = bus_num
        self.address = address
        self._bus = None

    async def setup(self) -> None:
        def _open():
            self._bus = smbus2.SMBus(self.bus_num)

        await asyncio.to_thread(_open)
        logger.info("Real ADS1115 connected on I2C bus %d at address 0x%02X", self.bus_num, self.address)

    async def read_channel_voltage(self, channel: int) -> float:
        """
        Trigger single-ended conversion on AIN(channel) vs GND with +/-4.096V range.
        """
        if channel not in (0, 1, 2, 3):
            raise ValueError(f"Invalid ADS1115 channel {channel}. Must be 0..3")

        def _read():
            # MUX configuration: 0x4000 (AIN0), 0x5000 (AIN1), 0x6000 (AIN2), 0x7000 (AIN3)
            mux = (0x4 + channel) << 12
            pga = 0x0200  # +/- 4.096V
            mode = 0x0100  # Single-shot
            dr = 0x0080    # 128 SPS
            comp = 0x0003  # Disable comparator
            config = 0x8000 | mux | pga | mode | dr | comp  # OS bit set to start

            # Write config register (big-endian: high byte, low byte)
            cfg_high = (config >> 8) & 0xFF
            cfg_low = config & 0xFF
            self._bus.write_i2c_block_data(self.address, self.REG_CONFIG, [cfg_high, cfg_low])

            # Wait for conversion complete (~8ms at 128 SPS)
            time.sleep(0.009)

            # Read 2-byte conversion result
            data = self._bus.read_i2c_block_data(self.address, self.REG_CONVERSION, 2)
            raw = (data[0] << 8) | data[1]
            if raw > 32767:
                raw -= 65536
            volts = raw * self.LSB_VOLTS
            return max(0.0, round(volts, 4))

        return await asyncio.to_thread(_read)

    async def read_all_channels(self) -> Dict[str, float]:
        results = {}
        for zone, ch in settings.ADC_PROBE_CHANNELS.items():
            try:
                results[zone] = await self.read_channel_voltage(ch)
            except Exception as e:
                logger.error("Failed to read ADS1115 channel %d (%s): %s", ch, zone, e)
                results[zone] = 0.0
        return results

    async def cleanup(self) -> None:
        def _close():
            if self._bus:
                self._bus.close()

        await asyncio.to_thread(_close)
        logger.info("ADS1115 I2C bus released.")


def get_adc_controller() -> BaseADCController:
    """Factory selecting physical ADS1115 driver or virtual mock."""
    if settings.MOCK_HARDWARE or not HAS_SMBUS or not os.path.exists(settings.I2C_DEV_PATH):
        if not settings.MOCK_HARDWARE:
            logger.warning(
                "Hardware ADC requested, but %s not accessible or smbus2 missing. Falling back to MockADCController.",
                settings.I2C_DEV_PATH,
            )
        return mock_adc
    return RealADS1115Driver(bus_num=settings.I2C_BUS, address=settings.ADS1115_I2C_ADDR)


adc_controller = get_adc_controller()
