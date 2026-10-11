"""
Unit Tests for Low-Code / No-Code Rule Evaluator Engine.
Verifies condition matching, schema parsing, failsafe enforcement, and CRUD operations.
"""

import asyncio
import pytest
from app.config import settings
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.engine.rules_evaluator import (
    RuleAction,
    RuleCondition,
    RuleDefinition,
    RulesEvaluator,
)
from app.hardware.gpio_driver import gpio_controller


@pytest.mark.asyncio
async def test_rule_schema_and_condition_evaluation():
    evaluator = RulesEvaluator()

    # Create test rule: IF Zone 1 soil moisture < 30% AND Reservoir is not empty THEN Water 10s
    rule = RuleDefinition(
        name="Test Drip Rule",
        combinator="AND",
        conditions=[
            RuleCondition(source="sensor.zone_1_moisture", operator="<", value=30.0),
            RuleCondition(source="reservoir.empty", operator="==", value=False),
        ],
        actions=[
            RuleAction(target="pump.zone_1", duration_seconds=10),
            RuleAction(target="voice.announce", speech_text="Test watering zone 1"),
        ],
    )

    # State where condition matches
    mock_state_match = {
        "sensors": {
            "reservoir_low_float": False,
            "soil_percentages": {"zone_1": 25.0},
            "soil_probes": {"zone_1_voltage": 2.6},
        }
    }
    assert evaluator._evaluate_conditions(rule.conditions, rule.combinator, mock_state_match) is True

    # State where soil is moist (45%) -> should not match
    mock_state_no_match = {
        "sensors": {
            "reservoir_low_float": False,
            "soil_percentages": {"zone_1": 45.0},
            "soil_probes": {"zone_1_voltage": 2.1},
        }
    }
    assert evaluator._evaluate_conditions(rule.conditions, rule.combinator, mock_state_no_match) is False


@pytest.mark.asyncio
async def test_rule_operators():
    evaluator = RulesEvaluator()

    # Greater than
    assert evaluator._check_condition(2.8, ">", 2.5) is True
    assert evaluator._check_condition(2.2, ">", 2.5) is False

    # Less than
    assert evaluator._check_condition(1.5, "<", 2.0) is True

    # Equals
    assert evaluator._check_condition("ok", "==", "ok") is True
    assert evaluator._check_condition(True, "==", True) is True

    # in_range
    assert evaluator._check_condition(2.2, "in_range", [1.0, 3.0]) is True
    assert evaluator._check_condition(3.5, "in_range", [1.0, 3.0]) is False


@pytest.mark.asyncio
async def test_rule_execution_and_failsafe_prevention():
    evaluator = RulesEvaluator()

    rule = RuleDefinition(
        name="Emergency Test Rule",
        combinator="AND",
        conditions=[
            RuleCondition(source="reservoir.empty", operator="==", value=True),
        ],
        actions=[
            RuleAction(target="pump.zone_1", duration_seconds=5),
        ],
    )

    # 1. Normal execution when failsafe permits
    # Clear any previous lockouts
    failsafe_engine.dry_run_lockout_active = False
    state_manager._state["runtime_tracking"]["zone_cooldown_until"]["zone_1"] = 0.0

    await evaluator._execute_actions(rule)
    # Zone 1 should be energized
    assert await gpio_controller.read_pin(settings.PIN_ZONE_1_MOSFET) is True
    assert state_manager.get_state()["actuators"]["zone_1_pump"] is True

    # Stop pump
    await gpio_controller.set_pin(settings.PIN_ZONE_1_MOSFET, False)
    await state_manager.update_actuator("zone_1_pump", False)

    # 2. Blocked execution when Dry-Run Failsafe is active
    failsafe_engine.dry_run_lockout_active = True
    await evaluator._execute_actions(rule)

    # Must remain OFF due to Priority 0 Failsafe!
    assert await gpio_controller.read_pin(settings.PIN_ZONE_1_MOSFET) is False
    assert state_manager.get_state()["actuators"]["zone_1_pump"] is False

    # Cleanup
    failsafe_engine.dry_run_lockout_active = False


@pytest.mark.asyncio
async def test_rule_persistence_and_crud():
    evaluator = RulesEvaluator()

    new_rule = RuleDefinition(
        name="Persistent Test Rule",
        combinator="OR",
        conditions=[
            RuleCondition(source="sensor.zone_4_voltage", operator=">", value=2.7),
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
