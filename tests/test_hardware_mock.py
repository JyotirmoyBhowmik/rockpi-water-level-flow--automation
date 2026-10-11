"""
Unit Tests for Hardware Mock Layer.
Verifies virtualized GPIO lines, ADS1115 I2C ADC registers, and audio synthesis.
"""

import asyncio
import pytest
from app.config import settings
from app.hardware.mock import MockADCController, MockAudioController, MockFluidSimulation, MockGPIOController


@pytest.mark.asyncio
async def test_mock_gpio_controller_operations():
    gpio = MockGPIOController()
    await gpio.setup()

    # Verify initial lines
    assert await gpio.read_pin(settings.PIN_AC_CONTACTOR) is False
    assert await gpio.read_pin(settings.PIN_ZONE_1_MOSFET) is False

    # Drive AC Contactor line HIGH
    await gpio.set_pin(settings.PIN_AC_CONTACTOR, True)
    assert await gpio.read_pin(settings.PIN_AC_CONTACTOR) is True

    # Drive Zone 1 MOSFET HIGH
    await gpio.set_pin(settings.PIN_ZONE_1_MOSFET, True)
    assert await gpio.read_pin(settings.PIN_ZONE_1_MOSFET) is True

    # Drive LOW
    await gpio.set_pin(settings.PIN_AC_CONTACTOR, False)
    assert await gpio.read_pin(settings.PIN_AC_CONTACTOR) is False

    await gpio.cleanup()


@pytest.mark.asyncio
async def test_mock_adc_controller_readings():
    gpio = MockGPIOController()
    await gpio.setup()
    adc = MockADCController(gpio_ref=gpio)
    await adc.setup()

    # Read default channels
    channels = await adc.read_all_channels()
    assert "zone_1" in channels
    assert "zone_2" in channels
    assert "zone_3" in channels
    assert "zone_4" in channels

    for zone, v in channels.items():
        assert 0.5 <= v <= 3.0

    # Inject manual channel override
    adc.set_channel_override(0, 0.35)
    v0 = await adc.read_channel_voltage(0)
    assert v0 == 0.35

    adc.clear_channel_override(0)
    await adc.cleanup()


@pytest.mark.asyncio
async def test_mock_fluid_simulation_dynamics():
    gpio = MockGPIOController()
    await gpio.setup()
    fluid = MockFluidSimulation(gpio=gpio)

    # 1. Test Overhead Tank filling when AC Contactor is ON
    fluid.overhead_tank_liters = 200.0
    fluid.step()
    # Tank low float should trip when level < 250L
    assert await gpio.read_pin(settings.PIN_TANK_LOW_FLOAT) is True

    # Turn AC Contactor ON
    await gpio.set_pin(settings.PIN_AC_CONTACTOR, True)
    # Simulate time passing
    fluid.step()
    fluid.overhead_tank_liters = 950.0
    fluid.step()
    # Tank high float should trip when level >= 900L
    assert await gpio.read_pin(settings.PIN_TANK_HIGH_FLOAT) is True
    # Low float should not be tripped
    assert await gpio.read_pin(settings.PIN_TANK_LOW_FLOAT) is False

    # 2. Test Ground Reservoir depletion
    fluid.reservoir_liters = 20.0
    fluid.step()
    # Reservoir empty float should trip when <= 30L
    assert await gpio.read_pin(settings.PIN_RESERVOIR_LOW_FLOAT) is True

    # Refill reservoir
    fluid.refill_reservoir(400.0)
    assert await gpio.read_pin(settings.PIN_RESERVOIR_LOW_FLOAT) is False


@pytest.mark.asyncio
async def test_mock_audio_controller():
    audio = MockAudioController()
    await audio.start()

    await audio.speak("Test announcement alpha")
    await audio.speak("Test announcement beta")

    # Give queue worker a brief moment to process
    await asyncio.sleep(0.15)

    logs = audio.get_recent_speech_logs(limit=5)
    assert len(logs) >= 2
    assert any("Test announcement alpha" in item["text"] for item in logs)
    assert any("Test announcement beta" in item["text"] for item in logs)

    await audio.stop()
