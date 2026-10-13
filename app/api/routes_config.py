"""
Configuration Management & First-Run Setup Wizard Endpoints.
Supports hardware pin inspection (gpioinfo), input testing, relay dry tests, and wizard completion.
"""

import asyncio
import logging
import shutil
import subprocess
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, status
from app.config import save_config, system_config
from app.hardware.gpio_driver import head_end_gpio

logger = logging.getLogger("smart_water.api.config")
router = APIRouter(prefix="/api", tags=["config"])


class TestRelayRequest(BaseModel):
    motor_disconnected_confirmed: bool = Field(
        ...,
        description="Explicit confirmation that high-voltage motor mains is physically disconnected",
    )


class SetupCompleteRequest(BaseModel):
    confirm_all_passed: bool = Field(..., description="Confirmation that relay and input tests passed")


@router.get("/config")
async def get_system_configuration() -> Dict[str, Any]:
    """Returns system configuration with sensitive tokens redacted."""
    cfg = system_config.model_dump(mode="json")
    if cfg.get("telegram") and cfg["telegram"].get("bot_token"):
        cfg["telegram"]["bot_token"] = "********"
    if cfg.get("garden_gateway") and cfg["garden_gateway"].get("shared_key"):
        cfg["garden_gateway"]["shared_key"] = "********"
    if cfg.get("ntfy") and cfg["ntfy"].get("access_token"):
        cfg["ntfy"]["access_token"] = "********"
    if cfg.get("mqtt") and cfg["mqtt"].get("password"):
        cfg["mqtt"]["password"] = "********"
    return cfg


@router.post("/config")
async def update_system_configuration(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Updates system configuration in memory and saves to YAML settings."""
    try:
        # Preserve sensitive tokens if masked with asterisks
        if payload.get("telegram") and str(payload["telegram"].get("bot_token", "")).startswith("***"):
            payload["telegram"]["bot_token"] = system_config.telegram.bot_token

        if payload.get("garden_gateway") and str(payload["garden_gateway"].get("shared_key", "")).startswith("***"):
            payload["garden_gateway"]["shared_key"] = (
                system_config.garden_gateway.shared_key.get_secret_value()
                if hasattr(system_config.garden_gateway.shared_key, "get_secret_value")
                else system_config.garden_gateway.shared_key
            )

        if payload.get("ntfy") and str(payload["ntfy"].get("access_token", "")).startswith("***"):
            payload["ntfy"]["access_token"] = (
                system_config.ntfy.access_token.get_secret_value()
                if hasattr(system_config.ntfy.access_token, "get_secret_value")
                else system_config.ntfy.access_token
            )

        if payload.get("mqtt") and str(payload["mqtt"].get("password", "")).startswith("***"):
            payload["mqtt"]["password"] = (
                system_config.mqtt.password.get_secret_value()
                if hasattr(system_config.mqtt.password, "get_secret_value")
                else system_config.mqtt.password
            )

        updated = system_config.model_validate(payload)

        # Check if hardware pin or polarity changes require system restart (Part 1.2)
        from app.config import check_if_restart_required
        restart_req = check_if_restart_required(updated.head_end)
        if restart_req:
            updated.restart_required = True

        for field in updated.model_fields_set:
            setattr(system_config, field, getattr(updated, field))

        save_config(system_config)
        logger.info("System configuration updated and written to disk. Restart required: %s", restart_req)
        return {
            "status": "success",
            "restart_required": system_config.restart_required,
            "message": (
                "Configuration saved successfully."
                + (" Restart required (sudo systemctl restart smart-water) to re-bind GPIO pins." if system_config.restart_required else "")
            ),
        }
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))



@router.get("/gpioinfo")
async def get_gpioinfo_output() -> Dict[str, Any]:
    """
    Executes 'gpioinfo' command to display available character device lines.
    Provides read-only board information for the setup wizard pin mapping.
    """
    if not shutil.which("gpioinfo"):
        return {
            "status": "unavailable",
            "output": "gpioinfo utility is not installed in system PATH (apt install gpiod).",
        }

    def _run():
        try:
            res = subprocess.run(["gpioinfo"], capture_output=True, text=True, timeout=3.0)
            return res.stdout
        except Exception as e:
            return f"Error executing gpioinfo: {e}"

    out = await asyncio.to_thread(_run)
    return {"status": "success", "output": out}


class TestInputPolarityPayload(BaseModel):
    tank_low_active_low: Optional[bool] = None
    interlock_out_active_low: Optional[bool] = None
    interlock_mid_active_low: Optional[bool] = None
    leak_active_low: Optional[bool] = None
    feedback_active_low: Optional[bool] = None


@router.post("/setup/test-input")
async def test_head_end_input(payload: Optional[TestInputPolarityPayload] = None) -> Dict[str, Any]:
    """
    Step 3 of Setup Wizard:
    Reads physical PC817 opto inputs directly and optionally updates polarities on-the-fly
    so installer can flip polarity while physically moving each float.
    """
    if payload:
        changed = False
        if payload.tank_low_active_low is not None:
            system_config.head_end.tank_low_active_low = payload.tank_low_active_low
            changed = True
        if payload.interlock_out_active_low is not None:
            system_config.head_end.interlock_out_active_low = payload.interlock_out_active_low
            changed = True
        if payload.interlock_mid_active_low is not None:
            system_config.head_end.interlock_mid_active_low = payload.interlock_mid_active_low
            changed = True
        if payload.leak_active_low is not None:
            system_config.head_end.leak_active_low = payload.leak_active_low
            changed = True
        if payload.feedback_active_low is not None:
            system_config.head_end.feedback_active_low = payload.feedback_active_low
            changed = True
        if changed:
            save_config(system_config)

    inputs = await head_end_gpio.read_head_end_inputs()
    if head_end_gpio.is_hardware_available:
        system_config.setup_checks.input_test = True
        save_config(system_config)

    he_cfg = system_config.head_end
    return {
        "status": "success",
        "inputs": inputs,
        "hardware_ready": head_end_gpio.is_hardware_available,
        "polarities": {
            "tank_low_active_low": he_cfg.tank_low_active_low,
            "interlock_out_active_low": he_cfg.interlock_out_active_low,
            "interlock_mid_active_low": he_cfg.interlock_mid_active_low,
            "leak_active_low": he_cfg.leak_active_low,
            "feedback_active_low": he_cfg.feedback_active_low,
        },
    }


@router.post("/setup/test-relay")
async def test_relay_cycle(payload: TestRelayRequest) -> Dict[str, Any]:
    """
    Step 4 of Setup Wizard:
    Briefly clicks relay on for 1.5 seconds to test coil actuation.
    REQUIRES:
      - Explicit confirmation that high-voltage motor mains is physically disconnected
      - Interlock chain healthy (Part 2.5)
      - No leak detected (Part 2.5)
      - Guaranteed de-energization via try/finally (Part 2.5)
    """
    if not payload.motor_disconnected_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Relay test blocked: You MUST confirm that the high-voltage motor is physically disconnected.",
        )

    # Precondition Checks (Part 2.5)
    from app.core.state_manager import state_manager
    state = state_manager.get_state()
    he = state.get("head_end", {})
    if he.get("leak_detected") is True:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Relay test blocked: Water leak detected on head-end sensor.",
        )
    if he.get("interlock_cause") != "healthy":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Relay test blocked: Hardware interlock chain is not complete (Cause: {he.get('interlock_cause')}).",
        )

    if not head_end_gpio.is_hardware_available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Head-end GPIO hardware not available or relay pin not configured.",
        )

    # Click relay on then off inside try/finally (Part 2.5)
    try:
        await head_end_gpio.set_contactor_relay(True)
        await asyncio.sleep(1.5)
    finally:
        await head_end_gpio.set_contactor_relay(False)

    system_config.setup_checks.relay_test = True
    save_config(system_config)

    return {"status": "success", "message": "Relay test cycle completed (energized for 1.5s then turned OFF)."}


@router.post("/setup/complete")
async def complete_setup(payload: SetupCompleteRequest) -> Dict[str, Any]:
    """
    Step 8 of Setup Wizard:
    Marks setup_complete as True once all 4 verification tests pass (Part 3.3):
      - input_test
      - relay_test
      - gateway_pairing
      - calibration
    """
    if not payload.confirm_all_passed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Setup cannot be finalized until all verification tests pass.",
        )

    # Evaluate checks
    checks = system_config.setup_checks
    # Also check if gateway is connected to satisfy pairing
    from app.hardware.gateway_driver import garden_gateway
    if garden_gateway.is_link_online:
        checks.gateway_pairing = True

    # Check if at least one zone is calibrated
    for z in system_config.zones.values():
        if z.calibration.dry_v is not None and z.calibration.wet_v is not None:
            checks.calibration = True
            break

    missing = []
    if not checks.input_test:
        missing.append("input_test (PC817 input verification)")
    if not checks.relay_test:
        missing.append("relay_test (contactor relay dry click test)")
    if not checks.gateway_pairing:
        missing.append("gateway_pairing (ESP32 garden node wireless link)")
    if not checks.calibration:
        missing.append("calibration (at least one soil probe calibrated)")

    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Setup completion blocked. Incomplete prerequisites: {', '.join(missing)}.",
        )

    system_config.setup_complete = True
    save_config(system_config)
    logger.info("First-run setup wizard completed successfully.")
    return {"status": "success", "setup_complete": True}
