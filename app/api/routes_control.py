"""
Actuator Control Endpoints: Motor, Plant Drip Zones, Probe Calibration, Emergency Stop.
All actions strictly validated against hardware interlocks and safety policies.
"""

import logging
from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, status
from app.config import save_config, system_config
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.engine.motor_controller import motor_controller
from app.engine.plant_controller import plant_controller
from app.hardware.audio_driver import audio_controller

logger = logging.getLogger("smart_water.api.control")
router = APIRouter(prefix="/api", tags=["control"])


class MotorStartRequest(BaseModel):
    confirm: bool = Field(..., description="Explicit confirmation required to energize 230V contactor")


class MotorAutoModeRequest(BaseModel):
    enabled: bool = Field(..., description="Enable or disable automated overhead tank replenishment")


class ZoneWaterRequest(BaseModel):
    duration_seconds: int = Field(default=15, ge=1, le=60, description="Pulse watering duration (1-60s cap)")


class ZoneCalibrateRequest(BaseModel):
    step: Literal["dry", "wet"] = Field(..., description="Calibration step: 'dry' in air, 'wet' in water")


class VoiceTestRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=200)


# -----------------------------------------------------------------------------
# Motor Control Endpoints (Part 2.1)
# -----------------------------------------------------------------------------

@router.post("/motor/start")
async def start_motor_manually(payload: MotorStartRequest) -> Dict[str, Any]:
    """Manually energizes 25A AC contactor after verifying all interlocks."""
    success, message = await motor_controller.start_motor(is_manual=True, confirm=payload.confirm)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return {"status": "success", "message": message}


@router.post("/motor/stop")
async def stop_motor_manually() -> Dict[str, Any]:
    """Stops the AC contactor immediately. Manual stop is always permitted."""
    success, message = await motor_controller.stop_motor(reason="manual_stop")
    return {"status": "success", "message": message}


@router.post("/motor/reset")
async def reset_motor_lockout() -> Dict[str, Any]:
    """Clears motor lockout following cooldown expiry."""
    success, message = failsafe_engine.reset_motor_lockout()
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    await state_manager.clear_alarm("motor_timeout")
    await audio_controller.speak("Main motor safety lockout reset by operator.", priority=1)
    return {"status": "success", "message": "Motor lockout cleared."}


@router.post("/motor/auto-mode")
async def set_motor_auto_mode(payload: MotorAutoModeRequest) -> Dict[str, Any]:
    """Toggles automatic replenishment of overhead water tank."""
    await state_manager.set_motor_auto_mode(payload.enabled)
    save_config(system_config)
    return {"status": "success", "auto_mode": payload.enabled}


# -----------------------------------------------------------------------------
# Plant Irrigation Zones Endpoints (Part 2.2)
# -----------------------------------------------------------------------------

@router.post("/zones/{zone_id}/water")
async def water_zone(zone_id: str, payload: ZoneWaterRequest) -> Dict[str, Any]:
    """Triggers precision pulse watering on wireless garden zone (1..4)."""
    success, message = await plant_controller.trigger_zone_pulse(
        zone_key=zone_id,
        duration_s=payload.duration_seconds,
        is_manual=True,
        source="manual_override",
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return {"status": "success", "message": message}


@router.post("/zones/{zone_id}/stop")
async def stop_zone(zone_id: str) -> Dict[str, Any]:
    """Stops active pulse on specified zone."""
    success, message = await plant_controller.stop_zone_pulse(zone_key=zone_id, reason="manual_stop")
    return {"status": "success", "message": message}


@router.post("/zones/stop-all")
async def stop_all_zones() -> Dict[str, Any]:
    """Dispatches emergency stop to all plant drip zones."""
    await plant_controller.stop_all_zones(reason="manual_stop_all")
    return {"status": "success", "message": "All plant pumps commanded OFF."}


@router.post("/zones/{zone_id}/calibrate")
async def calibrate_zone_probe(zone_id: str, payload: ZoneCalibrateRequest) -> Dict[str, Any]:
    """
    Calibrates capacitive soil probe (Part 2.2):
    Captures live voltage as dry_v (in air) or wet_v (submerged in water).
    Saves calibration to /etc/smart-water/config.yaml.
    """
    state = state_manager.get_state()
    z_data = state["garden"]["zones"].get(zone_id)
    if not z_data or z_data.get("voltage") is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot calibrate {zone_id}: No live voltage telemetry received from garden node yet.",
        )

    current_v = z_data["voltage"]
    z_cfg = system_config.zones.get(zone_id)
    if not z_cfg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Zone {zone_id} not configured.")

    if payload.step == "dry":
        z_cfg.calibration.dry_v = current_v
    elif payload.step == "wet":
        z_cfg.calibration.wet_v = current_v

    save_config(system_config)
    log_msg = f"Zone {zone_id} calibrated {payload.step.upper()} voltage captured at {current_v:.3f}V."
    logger.info(log_msg)
    await state_manager.log_event("CALIBRATION_UPDATE", "INFO", log_msg)

    return {
        "status": "success",
        "zone_id": zone_id,
        "step": payload.step,
        "captured_volts": current_v,
        "dry_v": z_cfg.calibration.dry_v,
        "wet_v": z_cfg.calibration.wet_v,
    }


# -----------------------------------------------------------------------------
# Panic Emergency Stop & Voice Alerts
# -----------------------------------------------------------------------------

@router.post("/control/emergency-stop")
async def emergency_stop_all() -> Dict[str, Any]:
    """Panic Button: Cuts contactor relay and stops all plant pumps immediately."""
    await motor_controller.stop_motor(reason="emergency_stop")
    await plant_controller.stop_all_zones(reason="emergency_stop")
    await state_manager.log_event("EMERGENCY_STOP", "CRITICAL", "Operator activated Emergency Stop.")
    await audio_controller.speak("Emergency stop activated. All outputs killed.", priority=0)
    return {"status": "success", "message": "Emergency stop executed. All outputs de-energized."}


@router.post("/control/speak")
async def test_voice_alert(payload: VoiceTestRequest) -> Dict[str, Any]:
    """Queues voice announcement for testing ALSA audio interface."""
    await audio_controller.speak(payload.text, priority=1)
    return {"status": "success", "message": f"Queued announcement: '{payload.text}'"}
