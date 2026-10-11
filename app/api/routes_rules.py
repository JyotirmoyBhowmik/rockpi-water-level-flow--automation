"""
Low-Code / No-Code Rule CRUD Endpoints and Preconfigured Automation Templates.
"""

import logging
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, status
from app.engine.rules_evaluator import RuleDefinition, rules_evaluator

logger = logging.getLogger("smart_water.api.rules")
router = APIRouter(prefix="/api/rules", tags=["rules"])


PRECONFIGURED_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "tpl-morning-drip",
        "name": "Morning Precision Drip",
        "description": "Waters Zone 1 & 2 for 20 seconds if soil moisture is below 35% and reservoir has water.",
        "combinator": "AND",
        "conditions": [
            {"source": "sensor.zone_1_moisture", "operator": "<", "value": 35.0},
            {"source": "reservoir.state", "operator": "==", "value": "ok"},
        ],
        "actions": [
            {"target": "pump.zone_1", "duration_seconds": 20},
            {"target": "voice.announce", "speech_text": "Executing morning precision drip for Zone 1."},
        ],
    },
    {
        "id": "tpl-hot-weather",
        "name": "Hot Weather Boost",
        "description": "Triggers 30s watering cycle on Zone 3 & 4 if voltage indicates high dryness (>2.6V).",
        "combinator": "AND",
        "conditions": [
            {"source": "sensor.zone_3_voltage", "operator": ">", "value": 2.6},
            {"source": "reservoir.state", "operator": "==", "value": "ok"},
        ],
        "actions": [
            {"target": "pump.zone_3", "duration_seconds": 30},
            {"target": "voice.announce", "speech_text": "Dry conditions detected. Watering Zone 3."},
        ],
    },
    {
        "id": "tpl-tank-topup",
        "name": "Tank Auto-Top-Up",
        "description": "Automatically energizes AC Contactor pump for 60s when Tank Low Float switch trips.",
        "combinator": "AND",
        "conditions": [
            {"source": "tank.low_float", "operator": "==", "value": True},
        ],
        "actions": [
            {"target": "motor.ac", "duration_seconds": 60},
            {"target": "voice.announce", "speech_text": "Overhead tank level low. Energizing main pump."},
        ],
    },
    {
        "id": "tpl-freeze-guard",
        "name": "Moisture Bounds Guard",
        "description": "Alerts when soil probe reaches extreme saturation (<1.1V).",
        "combinator": "AND",
        "conditions": [
            {"source": "sensor.zone_1_voltage", "operator": "<", "value": 1.1},
        ],
        "actions": [
            {"target": "voice.announce", "speech_text": "Soil is completely saturated. Check for standing water."},
        ],
    },
]


@router.get("", response_model=List[RuleDefinition])
async def list_rules() -> List[RuleDefinition]:
    """Returns all configured automation rules."""
    return rules_evaluator.get_all_rules()


@router.get("/templates")
async def list_templates() -> List[Dict[str, Any]]:
    """Returns factory pre-configured low-code templates."""
    return PRECONFIGURED_TEMPLATES


@router.get("/{rule_id}", response_model=RuleDefinition)
async def get_rule(rule_id: str) -> RuleDefinition:
    """Fetch rule by ID."""
    rule = rules_evaluator.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Rule '{rule_id}' not found.")
    return rule


@router.post("", response_model=RuleDefinition, status_code=status.HTTP_201_CREATED)
async def create_rule(rule: RuleDefinition) -> RuleDefinition:
    """Creates a new rule definition."""
    await rules_evaluator.save_rule(rule)
    return rule


@router.put("/{rule_id}", response_model=RuleDefinition)
async def update_rule(rule_id: str, updated_rule: RuleDefinition) -> RuleDefinition:
    """Updates an existing rule."""
    existing = rules_evaluator.get_rule(rule_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Rule '{rule_id}' not found.")
    updated_rule.id = rule_id
    await rules_evaluator.save_rule(updated_rule)
    return updated_rule


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str) -> Dict[str, Any]:
    """Deletes a rule by ID."""
    success = await rules_evaluator.delete_rule(rule_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Rule '{rule_id}' not found.")
    return {"status": "success", "deleted": rule_id}


@router.post("/{rule_id}/toggle")
async def toggle_rule(rule_id: str) -> Dict[str, Any]:
    """Toggles active/disabled status of a rule."""
    rule = rules_evaluator.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Rule '{rule_id}' not found.")
    rule.enabled = not rule.enabled
    await rules_evaluator.save_rule(rule)
    return {"status": "success", "id": rule.id, "enabled": rule.enabled}
