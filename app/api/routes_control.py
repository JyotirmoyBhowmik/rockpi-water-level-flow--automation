"""
Actuator Control Endpoints: Motor, Plant Drip Zones, Probe Calibration, Emergency Stop.
All actions strictly validated against hardware interlocks and safety policies.
"""

import asyncio
import logging
from typing import Any, Dict, Literal
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


class FlowCalibrateRequest(BaseModel):
    zone: int = Field(default=1, ge=1, le=4, description="Zone number to calibrate flow for (default Zone 1)")


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


@router.post("/flow/calibrate")
async def calibrate_zone_flow(payload: FlowCalibrateRequest) -> Dict[str, Any]:
    """
    Flow calibration wizard (Part 4.2):
    1. Runs requested zone pump for 10s.
    2. Measures flow meter pulse delta over that period.
    3. Computes observed flow (L/min) and pulses/L.
    4. Minimum flow cutoff: sets flow_min_lpm to 50% of observed flow.
       If observed flow < 0.3 L/min, raises warning and does NOT save calibration.
    """
    zone_idx = payload.zone
    z_key = f"zone_{zone_idx}"
    can_water, err_msg = failsafe_engine.can_water_zone(z_key, is_manual=True)
    if not can_water:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot run flow calibration for {z_key}: {err_msg}",
        )

    # Initial pulses
    state = state_manager.get_state()
    initial_pulses = state["garden"]["zones"].get(z_key, {}).get("flow_pulses", 0)

    # Start pump for 10s via gateway
    try:
        from app.hardware.gateway_driver import garden_gateway
        await garden_gateway.send_pump_run(zone_idx, 10)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start pump for flow calibration: {exc}",
        )

    # Wait for 10s pump run plus telemetry interval
    await asyncio.sleep(10.5)

    # Measure final pulses
    state_after = state_manager.get_state()
    final_pulses = state_after["garden"]["zones"].get(z_key, {}).get("flow_pulses", 0)
    observed_pulses = max(0, final_pulses - initial_pulses)

    # Compute flow (YF-S201 nominal 7.5 Hz per L/min -> 75 pulses in 10s per L/min)
    observed_flow_lpm = observed_pulses / 75.0
    pulses_per_l = 450.0

    if observed_flow_lpm < 0.3:
        warning_msg = "Low flow or dry run detected during calibration"
        logger.warning(
            "Zone %d flow calibration failed sanity check: %.2f L/min (< 0.3 L/min). Pulses: %d",
            zone_idx,
            observed_flow_lpm,
            observed_pulses,
        )
        return {
            "status": "warning",
            "zone": zone_idx,
            "observed_pulses_10s": observed_pulses,
            "observed_flow_lpm": round(observed_flow_lpm, 3),
            "pulses_per_liter": pulses_per_l,
            "warning": warning_msg,
            "saved": False,
        }

    # Minimum cutoff: 50% of observed flow
    flow_min_lpm = round(0.5 * observed_flow_lpm, 3)
    min_pulses_10s = max(1, int(round(0.5 * observed_pulses)))

    # Persist in configuration
    z_cfg = system_config.zones.get(z_key)
    if z_cfg:
        z_cfg.has_flow_meter = True
        z_cfg.min_pulses_10s = min_pulses_10s
        save_config(system_config)

    log_msg = (
        f"Zone {zone_idx} flow calibrated: {observed_flow_lpm:.2f} L/min "
        f"({observed_pulses} pulses in 10s). Cutoff set to {flow_min_lpm:.2f} L/min ({min_pulses_10s} pulses/10s)."
    )
    logger.info(log_msg)
    await state_manager.log_event("FLOW_CALIBRATED", "INFO", log_msg)

    return {
        "status": "success",
        "zone": zone_idx,
        "observed_pulses_10s": observed_pulses,
        "observed_flow_lpm": round(observed_flow_lpm, 3),
        "flow_min_lpm": flow_min_lpm,
        "min_pulses_10s": min_pulses_10s,
        "pulses_per_liter": pulses_per_l,
        "saved": True,
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
