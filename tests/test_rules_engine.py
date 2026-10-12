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


@pytest.mark.asyncio
async def test_time_minutes_and_between_operator_including_midnight():
    evaluator = RulesEvaluator()

    # 1. Source resolution: time.minutes returns int in 0..1439
    resolved = evaluator._resolve_source_value("time.minutes", {})
    assert isinstance(resolved, int)
    assert 0 <= resolved <= 1439

    # 2. Daytime window ["08:00", "17:00"] (minutes 480 to 1020)
    assert evaluator._check_condition(600, "between", ["08:00", "17:00"]) is True
    assert evaluator._check_condition(480, "between", ["08:00", "17:00"]) is True
    assert evaluator._check_condition(1020, "between", ["08:00", "17:00"]) is True
    assert evaluator._check_condition(479, "between", ["08:00", "17:00"]) is False
    assert evaluator._check_condition(1021, "between", ["08:00", "17:00"]) is False
    assert evaluator._check_condition(1200, "between", ["08:00", "17:00"]) is False

    # 3. String format: "08:00-17:00" and "08:00 to 17:00"
    assert evaluator._check_condition(600, "between", "08:00-17:00") is True
    assert evaluator._check_condition(300, "between", "08:00 to 17:00") is False

    # 4. Midnight-crossing window ["22:00", "04:00"] (minutes 1320 to 240)
    # Late night (before midnight): 22:00 (1320), 23:00 (1380), 23:59 (1439)
    assert evaluator._check_condition(1320, "between", ["22:00", "04:00"]) is True
    assert evaluator._check_condition(1380, "between", ["22:00", "04:00"]) is True
    assert evaluator._check_condition(1439, "between", ["22:00", "04:00"]) is True
    # Early morning (after midnight): 00:00 (0), 02:00 (120), 04:00 (240)
    assert evaluator._check_condition(0, "between", ["22:00", "04:00"]) is True
    assert evaluator._check_condition(120, "between", ["22:00", "04:00"]) is True
    assert evaluator._check_condition(240, "between", ["22:00", "04:00"]) is True
    # Outside window: 21:59 (1319), 04:01 (241), 12:00 (720)
    assert evaluator._check_condition(1319, "between", ["22:00", "04:00"]) is False
    assert evaluator._check_condition(241, "between", ["22:00", "04:00"]) is False
    assert evaluator._check_condition(720, "between", ["22:00", "04:00"]) is False

    # 5. String format for midnight-crossing: "22:00-04:00"
    assert evaluator._check_condition(1350, "between", "22:00-04:00") is True
    assert evaluator._check_condition(180, "between", "22:00-04:00") is True
    assert evaluator._check_condition(500, "between", "22:00-04:00") is False

    # 6. Integer minute list for midnight-crossing: [1320, 240]
    assert evaluator._check_condition(1350, "between", [1320, 240]) is True
    assert evaluator._check_condition(100, "between", [1320, 240]) is True
    assert evaluator._check_condition(500, "between", [1320, 240]) is False

    # 7. Generic numeric bounds with "between": [20.0, 30.0]
    assert evaluator._check_condition(25.0, "between", [20.0, 30.0]) is True
    assert evaluator._check_condition(35.0, "between", [20.0, 30.0]) is False


@pytest.mark.asyncio
async def test_time_clock_operator_restrictions():
    evaluator = RulesEvaluator()

    # == and != are allowed
    c1 = RuleCondition(source="time.clock", operator="==", value="14:30")
    assert c1.operator == "=="
    c2 = RuleCondition(source="time.clock", operator="!=", value="14:30")
    assert c2.operator == "!="

    # Operators other than == and != are rejected by schema validator
    with pytest.raises(ValueError, match="time.clock only supports == and !="):
        RuleCondition(source="time.clock", operator="between", value=["12:00", "14:00"])

    with pytest.raises(ValueError, match="time.clock only supports == and !="):
        RuleCondition(source="time.clock", operator="<", value="14:00")

    with pytest.raises(ValueError, match="time.clock only supports == and !="):
        RuleCondition(source="time.clock", operator=">", value="14:00")

    # If an invalid condition is somehow passed into _evaluate_conditions directly, it returns False
    cond_mock = type("MockCond", (), {"source": "time.clock", "operator": "<", "value": "14:00"})()
    assert evaluator._evaluate_conditions([cond_mock], "AND", {}) is False


@pytest.mark.asyncio
async def test_rule_monotonic_cooldown(monkeypatch):
    import time
    evaluator = RulesEvaluator()

    executed = []

    async def fake_execute(rule):
        executed.append(rule.id)

    monkeypatch.setattr(evaluator, "_execute_actions", fake_execute)

    rule = RuleDefinition(
        name="Cooldown Test Rule",
        cooldown_s=30,
        conditions=[RuleCondition(source="reservoir.state", operator="==", value="wet")],
        actions=[RuleAction(target="voice.announce", speech_text="Water available")],
    )
    evaluator._rules_cache[rule.id] = rule

    mock_state = {"garden": {"reservoir_wet": True}}
    monkeypatch.setattr("app.core.state_manager.state_manager.get_state", lambda: mock_state)

    fake_mono = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: fake_mono)

    # 1. First tick -> fires
    await evaluator.evaluate_tick()
    assert len(executed) == 1
    assert evaluator._last_rule_execution[rule.id] == 1000.0

    # 2. Second tick at mono + 10s -> skipped (10s < 30s cooldown)
    fake_mono = 1010.0
    await evaluator.evaluate_tick()
    assert len(executed) == 1

    # 3. Third tick at mono + 29s -> still skipped
    fake_mono = 1029.0
    await evaluator.evaluate_tick()
    assert len(executed) == 1

    # 4. Fourth tick at mono + 31s -> fires again
    fake_mono = 1031.0
    await evaluator.evaluate_tick()
    assert len(executed) == 2
    assert evaluator._last_rule_execution[rule.id] == 1031.0


@pytest.mark.asyncio
async def test_webhook_ssrf_async_thread_offload(monkeypatch):
    import asyncio
    evaluator = RulesEvaluator()

    rule = RuleDefinition(
        name="Webhook Test Rule",
        conditions=[],
        actions=[RuleAction(target="webhook.post", url="https://example.com/webhook", payload={"status": "ok"})],
    )

    thread_calls = []

    async def mock_to_thread(func, *args, **kwargs):
        thread_calls.append((func.__name__, args))
        return (False, "SSRF blocked for testing")

    monkeypatch.setattr(asyncio, "to_thread", mock_to_thread)

    await evaluator._execute_actions(rule)

    assert len(thread_calls) == 1
    func_name, args = thread_calls[0]
    assert func_name == "validate_webhook_url"
    assert args[0] == "https://example.com/webhook"
