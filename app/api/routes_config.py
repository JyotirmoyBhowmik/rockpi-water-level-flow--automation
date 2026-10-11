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
    return cfg


@router.post("/config")
async def update_system_configuration(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Updates system configuration in memory and saves to YAML."""
    try:
        # Preserve telegram token if masked
        if payload.get("telegram") and payload["telegram"].get("bot_token") == "********":
            payload["telegram"]["bot_token"] = system_config.telegram.bot_token

        updated = system_config.model_validate(payload)
        for field in updated.model_fields_set:
            setattr(system_config, field, getattr(updated, field))
        save_config(system_config)
        logger.info("System configuration updated and written to disk.")
        return {"status": "success", "message": "Configuration saved."}
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


@router.post("/setup/test-input")
async def test_head_end_input() -> Dict[str, Any]:
    """
    Step 3 of Setup Wizard:
    Reads physical PC817 opto inputs directly so user can physically toggle float switches
    and watch the states change in real time.
    """
    inputs = await head_end_gpio.read_head_end_inputs()
    return {
        "status": "success",
        "inputs": inputs,
        "hardware_ready": head_end_gpio.is_hardware_available,
    }


@router.post("/setup/test-relay")
async def test_relay_cycle(payload: TestRelayRequest) -> Dict[str, Any]:
    """
    Step 4 of Setup Wizard:
    Briefly clicks relay on for 1.5 seconds to test coil actuation.
    REQUIRES confirmation that motor mains is physically disconnected.
    """
    if not payload.motor_disconnected_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Relay test blocked: You MUST confirm that the high-voltage motor is physically disconnected.",
        )

    if not head_end_gpio.is_hardware_available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Head-end GPIO hardware not available or relay pin not configured.",
        )

    # Click relay on then off
    await head_end_gpio.set_contactor_relay(True)
    await asyncio.sleep(1.5)
    await head_end_gpio.set_contactor_relay(False)

    return {"status": "success", "message": "Relay test cycle completed (energized for 1.5s then turned OFF)."}


@router.post("/setup/complete")
async def complete_setup(payload: SetupCompleteRequest) -> Dict[str, Any]:
    """
    Step 8 of Setup Wizard:
    Marks setup_complete as True once tests have passed.
    """
    if not payload.confirm_all_passed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Setup cannot be finalized until all verification tests pass.",
        )

    system_config.setup_complete = True
    save_config(system_config)
    logger.info("First-run setup wizard completed successfully.")
    return {"status": "success", "setup_complete": True}
