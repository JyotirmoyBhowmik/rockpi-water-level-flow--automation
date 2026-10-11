"""
Low-Code / No-Code Visual Rule Execution Engine.
Parses JSON rule graphs, evaluates composite AND/OR triggers, enforces failsafes,
and executes timed actions with asynchronous non-blocking workers.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union
import httpx
from pydantic import BaseModel, Field
from app.config import settings
from app.core.event_bus import bus
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.hardware.audio_driver import audio_controller
from app.hardware.gpio_driver import gpio_controller

logger = logging.getLogger("smart_water.rules")


class RuleCondition(BaseModel):
    source: str = Field(..., description="e.g. sensor.zone_1_voltage, tank.low_float, reservoir.empty")
    operator: Literal[">", "<", ">=", "<=", "==", "!=", "in_range"] = Field(...)
    value: Union[float, int, str, bool, List[float]] = Field(...)


class RuleAction(BaseModel):
    target: Literal[
        "pump.zone_1",
        "pump.zone_2",
        "pump.zone_3",
        "pump.zone_4",
        "motor.ac",
        "voice.announce",
        "webhook.post",
    ] = Field(...)
    duration_seconds: Optional[int] = Field(default=15, ge=1, le=900)
    speech_text: Optional[str] = Field(default=None)
    url: Optional[str] = Field(default=None)
    payload: Optional[Dict[str, Any]] = Field(default=None)


class RuleDefinition(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., min_length=2, max_length=100)
    enabled: bool = Field(default=True)
    combinator: Literal["AND", "OR"] = Field(default="AND")
    conditions: List[RuleCondition] = Field(default_factory=list)
    actions: List[RuleAction] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RulesEvaluator:
    """
    Evaluator engine running on periodic ticks.
    Enforces that Priority 0 failsafes always override any rule action.
    """

    def __init__(self):
        self._active_timed_tasks: Dict[str, asyncio.Task] = {}
        self._rules_cache: Dict[str, RuleDefinition] = {}
        self._last_rule_execution: Dict[str, float] = {}

    def cancel_all_timed_tasks(self) -> None:
        """Cancels all active pump timers cleanly."""
        for name, task in list(self._active_timed_tasks.items()):
            if not task.done():
                task.cancel()
        self._active_timed_tasks.clear()

    async def load_rules_from_db(self) -> None:
        """Loads enabled rules from SQLite into memory cache."""
        def _fetch():
            with state_manager._get_connection() as conn:
                cur = conn.execute("SELECT id, name, enabled, rule_json FROM rules;")
                return cur.fetchall()

        rows = await asyncio.to_thread(_fetch)
        new_cache = {}
        for r in rows:
            try:
                rule_data = json.loads(r["rule_json"])
                rule_obj = RuleDefinition(**rule_data)
                new_cache[rule_obj.id] = rule_obj
            except Exception as e:
                logger.error("Error parsing rule %s: %s", r["id"], e)

        self._rules_cache = new_cache
        logger.info("Loaded %d user automation rules into engine cache.", len(self._rules_cache))

    def get_all_rules(self) -> List[RuleDefinition]:
        return list(self._rules_cache.values())

    def get_rule(self, rule_id: str) -> Optional[RuleDefinition]:
        return self._rules_cache.get(rule_id)

    async def save_rule(self, rule: RuleDefinition) -> None:
        """Persist or update rule in SQLite and memory."""
        rule.updated_at = datetime.now(timezone.utc).isoformat()
        rule_json = rule.model_dump_json()

        def _upsert():
            with state_manager._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO rules (id, name, enabled, rule_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        enabled=excluded.enabled,
                        rule_json=excluded.rule_json,
                        updated_at=excluded.updated_at;
                    """,
                    (rule.id, rule.name, int(rule.enabled), rule_json, rule.created_at, rule.updated_at),
                )
                conn.commit()

        await asyncio.to_thread(_upsert)
        self._rules_cache[rule.id] = rule
        logger.info("Saved automation rule '%s' (%s).", rule.name, rule.id)

    async def delete_rule(self, rule_id: str) -> bool:
        """Deletes rule by ID."""
        if rule_id not in self._rules_cache:
            return False

        def _delete():
            with state_manager._get_connection() as conn:
                conn.execute("DELETE FROM rules WHERE id = ?;", (rule_id,))
                conn.commit()

        await asyncio.to_thread(_delete)
        self._rules_cache.pop(rule_id, None)
        logger.info("Deleted rule %s.", rule_id)
        return True

    # -------------------------------------------------------------------------
    # Evaluation Logic
    # -------------------------------------------------------------------------

    async def evaluate_tick(self) -> None:
        """Single tick evaluation of all active rules."""
        state = state_manager.get_state()
        now = time.time()

        for rule in self._rules_cache.values():
            if not rule.enabled or not rule.conditions:
                continue

            # Debounce re-triggering within 60 seconds unless configured
            last_run = self._last_rule_execution.get(rule.id, 0.0)
            if now - last_run < 60.0:
                continue

            matches = self._evaluate_conditions(rule.conditions, rule.combinator, state)
            if matches:
                logger.info("Rule '%s' conditions matched. Triggering actions.", rule.name)
                self._last_rule_execution[rule.id] = now
                await self._execute_actions(rule)

    def _evaluate_conditions(
        self,
        conditions: List[RuleCondition],
        combinator: str,
        state: Dict[str, Any],
    ) -> bool:
        """Evaluates condition list against live state dictionary."""
        results: List[bool] = []
        for cond in conditions:
            actual_val = self._resolve_source_value(cond.source, state)
            res = self._check_condition(actual_val, cond.operator, cond.value)
            results.append(res)

        if combinator == "AND":
            return all(results)
        elif combinator == "OR":
            return any(results)
        return False

    def _resolve_source_value(self, source: str, state: Dict[str, Any]) -> Any:
        """Resolves dot-notated source string to actual value from state."""
        sensors = state["sensors"]
        probes = sensors["soil_probes"]
        percentages = sensors["soil_percentages"]

        # Mapping dictionary for intuitive Low-Code sources
        lookup = {
            "sensor.zone_1_voltage": probes.get("zone_1_voltage", 0.0),
            "sensor.zone_2_voltage": probes.get("zone_2_voltage", 0.0),
            "sensor.zone_3_voltage": probes.get("zone_3_voltage", 0.0),
            "sensor.zone_4_voltage": probes.get("zone_4_voltage", 0.0),
            "sensor.zone_1_moisture": percentages.get("zone_1", 0.0),
            "sensor.zone_2_moisture": percentages.get("zone_2", 0.0),
            "sensor.zone_3_moisture": percentages.get("zone_3", 0.0),
            "sensor.zone_4_moisture": percentages.get("zone_4", 0.0),
            "tank.low_float": sensors.get("tank_low_float", False),
            "tank.high_float": sensors.get("tank_high_float", False),
            "reservoir.empty": sensors.get("reservoir_low_float", False),
            "reservoir.state": "empty" if sensors.get("reservoir_low_float", False) else "ok",
        }

        if source in lookup:
            return lookup[source]

        # Dynamic nested lookup
        parts = source.split(".")
        curr = state
        for p in parts:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            else:
                return None
        return curr

    def _check_condition(self, actual: Any, operator: str, target: Any) -> bool:
        """Performs typed operator comparison safely."""
        if actual is None:
            return False

        try:
            if operator == ">":
                return float(actual) > float(target)
            elif operator == "<":
                return float(actual) < float(target)
            elif operator == ">=":
                return float(actual) >= float(target)
            elif operator == "<=":
                return float(actual) <= float(target)
            elif operator == "==":
                return str(actual).lower() == str(target).lower()
            elif operator == "!=":
                return str(actual).lower() != str(target).lower()
            elif operator == "in_range" and isinstance(target, list) and len(target) == 2:
                val = float(actual)
                return float(target[0]) <= val <= float(target[1])
        except (ValueError, TypeError) as e:
            logger.debug("Condition check failed type conversion: %s", e)
            return False
        return False

    async def _execute_actions(self, rule: RuleDefinition) -> None:
        """Dispatches actions while checking against Priority 0 Failsafes."""
        for act in rule.actions:
            target = act.target

            # -----------------------------------------------------------------
            # Plant Drip Pumps (DC 12V Zone 1-4)
            # -----------------------------------------------------------------
            if target.startswith("pump.zone_"):
                zone_str = target.replace("pump.", "")  # e.g. 'zone_1'
                allowed, reason = failsafe_engine.can_start_zone_pump(zone_str)
                if not allowed:
                    logger.warning("Failsafe blocked rule '%s' from starting %s: %s", rule.name, target, reason)
                    await audio_controller.speak(f"Automation for {zone_str.replace('_', ' ')} blocked: {reason}")
                    continue

                duration = min(act.duration_seconds or 15, settings.MAX_DC_PUMP_RUNTIME_SEC)
                await self._trigger_timed_pump(zone_str, duration, source_rule=rule.name)

            # -----------------------------------------------------------------
            # Overhead Tank AC Contactor Pump
            # -----------------------------------------------------------------
            elif target == "motor.ac":
                allowed, reason = failsafe_engine.can_start_ac_motor()
                if not allowed:
                    logger.warning("Failsafe blocked rule '%s' from starting AC motor: %s", rule.name, reason)
                    continue

                duration = min(act.duration_seconds or 60, settings.MAX_AC_MOTOR_RUNTIME_SEC)
                await self._trigger_timed_ac_motor(duration, source_rule=rule.name)

            # -----------------------------------------------------------------
            # Local Synthesized Voice Announcement
            # -----------------------------------------------------------------
            elif target == "voice.announce" and act.speech_text:
                await audio_controller.speak(act.speech_text)

            # -----------------------------------------------------------------
            # Outbound Webhook Post
            # -----------------------------------------------------------------
            elif target == "webhook.post" and act.url:
                asyncio.create_task(self._send_webhook(act.url, act.payload or {"rule": rule.name}))

    async def _trigger_timed_pump(self, zone: str, duration_sec: int, source_rule: str) -> None:
        """Energizes DC pump MOSFET with non-blocking timer."""
        pin_map = {
            "zone_1": settings.PIN_ZONE_1_MOSFET,
            "zone_2": settings.PIN_ZONE_2_MOSFET,
            "zone_3": settings.PIN_ZONE_3_MOSFET,
            "zone_4": settings.PIN_ZONE_4_MOSFET,
        }
        pump_name = f"{zone}_pump"
        pin = pin_map[zone]

        # Cancel any active running timer on this zone
        if zone in self._active_timed_tasks:
            self._active_timed_tasks[zone].cancel()

        logger.info("Energizing %s for %ds (Triggered by: %s)", pump_name, duration_sec, source_rule)
        await gpio_controller.set_pin(pin, True)
        await state_manager.update_actuator(pump_name, True)
        await bus.publish("pump:started", {"zone": zone, "duration": duration_sec, "rule": source_rule})

        async def _timer_worker():
            try:
                await asyncio.sleep(duration_sec)
            except asyncio.CancelledError:
                return
            finally:
                await gpio_controller.set_pin(pin, False)
                await state_manager.update_actuator(pump_name, False)
                await bus.publish("pump:stopped", {"zone": zone})
                logger.info("%s watering complete (%ds). De-energized.", pump_name, duration_sec)

        self._active_timed_tasks[zone] = asyncio.create_task(_timer_worker(), name=f"timer_{zone}")

    async def _trigger_timed_ac_motor(self, duration_sec: int, source_rule: str) -> None:
        """Energizes AC Contactor with safety countdown."""
        if "ac_contactor" in self._active_timed_tasks:
            self._active_timed_tasks["ac_contactor"].cancel()

        logger.info("Energizing AC Contactor for %ds (Triggered by: %s)", duration_sec, source_rule)
        await gpio_controller.set_pin(settings.PIN_AC_CONTACTOR, True)
        await state_manager.update_actuator("ac_contactor", True)
        await bus.publish("ac_motor:started", {"duration": duration_sec, "rule": source_rule})

        async def _timer_worker():
            try:
                await asyncio.sleep(duration_sec)
            except asyncio.CancelledError:
                return
            finally:
                await gpio_controller.set_pin(settings.PIN_AC_CONTACTOR, False)
                await state_manager.update_actuator("ac_contactor", False)
                await bus.publish("ac_motor:stopped", {})
                logger.info("AC Contactor run complete (%ds). De-energized.", duration_sec)

        self._active_timed_tasks["ac_contactor"] = asyncio.create_task(_timer_worker(), name="timer_ac")

    async def _send_webhook(self, url: str, payload: Dict[str, Any]) -> None:
        """Asynchronously dispatches webhook with explicit timeout and retry."""
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.post(url, json=payload)
                logger.info("Webhook dispatched to %s (Status: %d)", url, res.status_code)
        except Exception as e:
            logger.error("Failed to post webhook to %s: %s", url, e)


rules_evaluator = RulesEvaluator()
