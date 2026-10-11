"""
Unit Tests for Automation Rules Evaluator Engine.
Tests pure logic: condition evaluation, schema validation, comparison operators, and SQLite rule CRUD.
"""

import pytest
from app.engine.rules_evaluator import (
    RuleAction,
    RuleCondition,
    RuleDefinition,
    RulesEvaluator,
)


@pytest.mark.asyncio
async def test_rule_schema_and_condition_evaluation():
    evaluator = RulesEvaluator()

    # Create test rule: IF zone_1 moisture < 30% AND reservoir wet THEN water 10s
    rule = RuleDefinition(
        name="Test Drip Rule",
        combinator="AND",
        conditions=[
            RuleCondition(source="zone_1.moisture", operator="<", value=30.0),
            RuleCondition(source="reservoir.state", operator="==", value="wet"),
        ],
        actions=[
            RuleAction(target="pump.zone_1", duration_seconds=10),
            RuleAction(target="voice.announce", speech_text="Test watering zone 1"),
        ],
    )

    # State where conditions match
    match_state = {
        "garden": {
            "reservoir_wet": True,
            "zones": {
                "zone_1": {"moisture_pct": 25.0, "voltage": 2.2},
            },
        }
    }
    assert evaluator._evaluate_conditions(rule.conditions, rule.combinator, match_state) is True

    # State where moisture is 45% (> 30%) -> should not match
    no_match_state = {
        "garden": {
            "reservoir_wet": True,
            "zones": {
                "zone_1": {"moisture_pct": 45.0, "voltage": 1.9},
            },
        }
    }
    assert evaluator._evaluate_conditions(rule.conditions, rule.combinator, no_match_state) is False


@pytest.mark.asyncio
async def test_rule_operators():
    evaluator = RulesEvaluator()

    # Greater than
    assert evaluator._check_condition(2.8, ">", 2.5) is True
    assert evaluator._check_condition(2.2, ">", 2.5) is False

    # Less than
    assert evaluator._check_condition(1.5, "<", 2.0) is True

    # Equals
    assert evaluator._check_condition("HEALTHY", "==", "HEALTHY") is True
    assert evaluator._check_condition(True, "==", True) is True

    # in_range
    assert evaluator._check_condition(2.2, "in_range", [1.0, 3.0]) is True
    assert evaluator._check_condition(3.5, "in_range", [1.0, 3.0]) is False


@pytest.mark.asyncio
async def test_rule_persistence_and_crud():
    evaluator = RulesEvaluator()

    new_rule = RuleDefinition(
        name="Persistent Test Rule",
        combinator="OR",
        conditions=[
            RuleCondition(source="zone_4.volts", operator=">", value=2.7),
        ],
        actions=[
            RuleAction(target="pump.zone_4", duration_seconds=15),
        ],
    )

    # Save to SQLite
    await evaluator.save_rule(new_rule)
    assert evaluator.get_rule(new_rule.id) is not None

    # Reload from DB into a fresh evaluator instance
    fresh_evaluator = RulesEvaluator()
    await fresh_evaluator.load_rules_from_db()
    loaded = fresh_evaluator.get_rule(new_rule.id)
    assert loaded is not None
    assert loaded.name == "Persistent Test Rule"
    assert loaded.combinator == "OR"
    assert len(loaded.conditions) == 1

    # Delete rule
    deleted = await fresh_evaluator.delete_rule(new_rule.id)
    assert deleted is True
    assert fresh_evaluator.get_rule(new_rule.id) is None
