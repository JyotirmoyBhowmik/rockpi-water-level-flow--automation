"""
Actuator Control, Emergency Overrides, Voice Tests, and Hardware Mock Controls.
"""

import asyncio
import logging
from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, status
from app.config import settings
from app.core.event_bus import bus
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.hardware.audio_driver import audio_controller
from app.hardware.gpio_driver import gpio_controller
from app.hardware.mock import mock_adc, mock_fluid, mock_gpio

logger = logging.getLogger("smart_water.api.control")
router = APIRouter(prefix="/api/control", tags=["control"])


class PumpControlRequest(BaseModel):
    target: Literal["pump.zone_1", "pump.zone_2", "pump.zone_3", "pump.zone_4", "motor.ac"] = Field(...)
    state: bool = Field(..., description="True to turn ON, False to turn OFF")
    duration_seconds: Optional[int] = Field(default=15, ge=1, le=900, description="Auto-off timer")
    ignore_cooldown: bool = Field(default=False, description="Manual operator override of 6-hour zone cooldown")


class VoiceTestRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=200)
    priority: int = Field(default=1, ge=1, le=5)


class MockTankRequest(BaseModel):
    level_liters: float = Field(..., ge=0.0, le=1000.0)


class MockReservoirRequest(BaseModel):
    level_liters: float = Field(..., ge=0.0, le=500.0)


class MockProbeRequest(BaseModel):
    channel: int = Field(..., ge=0, le=3)
    voltage: float = Field(..., ge=0.0, le=5.0)


@router.post("/pump")
async def control_pump(payload: PumpControlRequest) -> Dict[str, Any]:
    """
    Manual override switch for DC pumps and AC Contactor.
    Enforces Priority 0 safety interlocks before granting permission.
    """
    target = payload.target
    state = payload.state

    # 1. AC Contactor Control
    if target == "motor.ac":
        if state:
            allowed, reason = failsafe_engine.can_start_ac_motor()
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Safety Lockout: {reason}",
                )

            await gpio_controller.set_pin(settings.PIN_AC_CONTACTOR, True)
            await state_manager.update_actuator("ac_contactor", True)
            await audio_controller.speak("Main pump manually energized.")
            await bus.publish("manual:ac_motor_on", {"duration": payload.duration_seconds})

            # Auto-off task if duration specified
            if payload.duration_seconds:
                async def _auto_off():
                    await asyncio.sleep(payload.duration_seconds)
                    await gpio_controller.set_pin(settings.PIN_AC_CONTACTOR, False)
                    await state_manager.update_actuator("ac_contactor", False)
                    await audio_controller.speak("Main pump manual timer completed.")
                asyncio.create_task(_auto_off())

        else:
            await gpio_controller.set_pin(settings.PIN_AC_CONTACTOR, False)
            await state_manager.update_actuator("ac_contactor", False)
            await audio_controller.speak("Main pump manually stopped.")
            await bus.publish("manual:ac_motor_off", {})

        return {"status": "success", "target": target, "state": state}

    # 2. Plant DC Pumps
    zone = target.replace("pump.", "")  # 'zone_1'
    pin_map = {
        "zone_1": settings.PIN_ZONE_1_MOSFET,
        "zone_2": settings.PIN_ZONE_2_MOSFET,
        "zone_3": settings.PIN_ZONE_3_MOSFET,
        "zone_4": settings.PIN_ZONE_4_MOSFET,
    }
    pin = pin_map[zone]
    pump_name = f"{zone}_pump"

    if state:
        allowed, reason = failsafe_engine.can_start_zone_pump(zone, ignore_cooldown=payload.ignore_cooldown)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Safety Lockout: {reason}",
            )

        await gpio_controller.set_pin(pin, True)
        await state_manager.update_actuator(pump_name, True)
        await audio_controller.speak(f"Starting {zone.replace('_', ' ')} irrigation pump.")
        await bus.publish("manual:zone_pump_on", {"zone": zone, "duration": payload.duration_seconds})

        if payload.duration_seconds:
            async def _zone_auto_off():
                await asyncio.sleep(payload.duration_seconds)
                await gpio_controller.set_pin(pin, False)
                await state_manager.update_actuator(pump_name, False)
                await audio_controller.speak(f"{zone.replace('_', ' ')} watering cycle finished.")
            asyncio.create_task(_zone_auto_off())
    else:
        await gpio_controller.set_pin(pin, False)
        await state_manager.update_actuator(pump_name, False)
        await bus.publish("manual:zone_pump_off", {"zone": zone})

    return {"status": "success", "target": target, "state": state}


@router.post("/emergency-stop")
async def emergency_stop_all() -> Dict[str, Any]:
    """
    Panic button: Immediately cuts all physical outputs LOW.
    """
    pins = [
        settings.PIN_AC_CONTACTOR,
        settings.PIN_ZONE_1_MOSFET,
        settings.PIN_ZONE_2_MOSFET,
        settings.PIN_ZONE_3_MOSFET,
        settings.PIN_ZONE_4_MOSFET,
    ]
    for p in pins:
        await gpio_controller.set_pin(p, False)

    for act in ["ac_contactor", "zone_1_pump", "zone_2_pump", "zone_3_pump", "zone_4_pump"]:
        await state_manager.update_actuator(act, False)

    await state_manager.log_event("EMERGENCY_STOP", "CRITICAL", "Operator triggered Emergency Stop All.")
    await audio_controller.speak("Emergency stop activated. All pumps terminated.")
    await bus.publish("emergency:stop", {})

    return {"status": "success", "message": "All actuators disabled."}


@router.post("/reset-lockout")
async def reset_safety_lockouts() -> Dict[str, Any]:
    """Manually resets motor timeout lockout and clears active alarms."""
    failsafe_engine.reset_motor_lockout()
    await state_manager.clear_alarm("motor_timeout")
    await audio_controller.speak("System lockouts reset by operator.")
    return {"status": "success", "message": "Lockouts reset."}


@router.post("/speak")
async def test_voice_speech(payload: VoiceTestRequest) -> Dict[str, Any]:
    """Enqueues test phrase on USB ALSA speaker."""
    await audio_controller.speak(payload.text, priority=payload.priority)
    await state_manager.set_last_speech(payload.text)
    return {"status": "success", "enqueued": payload.text}


# -----------------------------------------------------------------------------
# Virtual Hardware Simulation Injection Endpoints
# -----------------------------------------------------------------------------

@router.post("/mock/set-tank")
async def set_mock_overhead_tank(payload: MockTankRequest) -> Dict[str, Any]:
    mock_fluid.overhead_tank_liters = payload.level_liters
    mock_fluid.step()
    return {"status": "success", "overhead_tank_liters": mock_fluid.overhead_tank_liters}


@router.post("/mock/set-reservoir")
async def set_mock_reservoir(payload: MockReservoirRequest) -> Dict[str, Any]:
    mock_fluid.reservoir_liters = payload.level_liters
    mock_fluid.step()
    return {"status": "success", "reservoir_liters": mock_fluid.reservoir_liters}


@router.post("/mock/set-probe")
async def set_mock_probe_voltage(payload: MockProbeRequest) -> Dict[str, Any]:
    mock_adc.set_channel_override(payload.channel, payload.voltage)
    return {"status": "success", "channel": payload.channel, "voltage": payload.voltage}


@router.post("/mock/reset-physics")
async def reset_mock_physics() -> Dict[str, Any]:
    mock_fluid.overhead_tank_liters = 500.0
    mock_fluid.reservoir_liters = 400.0
    for ch in range(4):
        mock_adc.clear_channel_override(ch)
    mock_fluid.step()
    return {"status": "success", "message": "Physics reset to baseline values."}
