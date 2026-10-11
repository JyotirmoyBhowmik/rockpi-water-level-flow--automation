"""
User Automation Rules & Reusable Templates CRUD Endpoints.
Zero seeded rules on first run. Stores user-defined rules and user-saved templates in SQLite.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, status
from app.engine.rules_evaluator import (
    RuleDefinition,
    UserRuleTemplate,
    rules_evaluator,
)

router = APIRouter(prefix="/api/rules", tags=["rules"])


class SaveTemplateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = Field(default="")


@router.get("", response_model=List[RuleDefinition])
async def list_user_rules() -> List[RuleDefinition]:
    """Returns active user rules. Empty on first start."""
    return rules_evaluator.get_all_rules()


@router.get("/templates", response_model=List[UserRuleTemplate])
async def list_user_templates() -> List[UserRuleTemplate]:
    """Returns user-saved templates from the database. Empty on first start."""
    return await rules_evaluator.get_user_templates()


@router.get("/{rule_id}", response_model=RuleDefinition)
async def get_user_rule(rule_id: str) -> RuleDefinition:
    rule = rules_evaluator.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Rule '{rule_id}' not found.")
    return rule


@router.post("", response_model=RuleDefinition, status_code=status.HTTP_201_CREATED)
async def create_user_rule(rule: RuleDefinition) -> RuleDefinition:
    await rules_evaluator.save_rule(rule)
    return rule


@router.put("/{rule_id}", response_model=RuleDefinition)
async def update_user_rule(rule_id: str, updated_rule: RuleDefinition) -> RuleDefinition:
    existing = rules_evaluator.get_rule(rule_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Rule '{rule_id}' not found.")
    updated_rule.id = rule_id
    await rules_evaluator.save_rule(updated_rule)
    return updated_rule


@router.delete("/{rule_id}")
async def delete_user_rule(rule_id: str) -> Dict[str, Any]:
    success = await rules_evaluator.delete_rule(rule_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Rule '{rule_id}' not found.")
    return {"status": "success", "deleted": rule_id}


@router.post("/{rule_id}/toggle")
async def toggle_user_rule(rule_id: str) -> Dict[str, Any]:
    rule = rules_evaluator.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Rule '{rule_id}' not found.")
    rule.enabled = not rule.enabled
    await rules_evaluator.save_rule(rule)
    return {"status": "success", "id": rule.id, "enabled": rule.enabled}


@router.post("/{rule_id}/save-template", response_model=UserRuleTemplate, status_code=status.HTTP_201_CREATED)
async def save_rule_as_template(rule_id: str, payload: SaveTemplateRequest) -> UserRuleTemplate:
    """Saves a user's custom rule into their database templates library."""
    tpl = await rules_evaluator.save_as_template(
        rule_id=rule_id,
        template_name=payload.name,
        description=payload.description or "",
    )
    if not tpl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Rule '{rule_id}' not found.")
    return tpl


@router.delete("/templates/{template_id}")
async def delete_user_template(template_id: str) -> Dict[str, Any]:
    success = await rules_evaluator.delete_user_template(template_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Template '{template_id}' not found.")
    return {"status": "success", "deleted": template_id}
