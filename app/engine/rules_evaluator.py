"""
User Automation Rules Evaluator for Radxa ROCK Pi S.
Sources: tank.low, motor.state, interlock.state, zone_N.moisture, zone_N.volts,
         reservoir.state, leak.state, time.clock, link.state.
Actions: pump.zone_N, motor.start, motor.stop, voice.announce, webhook.post, telegram.send.
NO hardcoded or seeded rules/templates. Includes user template saving and SQLite storage.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Dict, List, Literal, Optional, Union
import uuid
import httpx
from pydantic import BaseModel, Field, model_validator
from app.alerts.telegram_client import telegram_notifier
from app.config import system_config
from app.core.state_manager import state_manager
from app.engine.motor_controller import motor_controller
from app.engine.plant_controller import plant_controller
from app.hardware.audio_driver import audio_controller

logger = logging.getLogger("smart_water.rules")


class RuleCondition(BaseModel):
    source: str = Field(..., description="e.g. tank.low, interlock.state, zone_1.moisture, reservoir.state, time.minutes, time.clock")
    operator: Literal[">", "<", ">=", "<=", "==", "!=", "in_range", "between"] = Field(...)
    value: Union[float, int, str, bool, List[Any]] = Field(...)

    @model_validator(mode="after")
    def validate_time_clock(self):
        if self.source == "time.clock" and self.operator not in ("==", "!="):
            raise ValueError(
                "time.clock only supports == and != operators. Use time.minutes for range or window comparison."
            )
        return self


class RuleAction(BaseModel):
    target: Literal[
        "pump.zone_1",
        "pump.zone_2",
        "pump.zone_3",
        "pump.zone_4",
        "motor.start",
        "motor.stop",
        "voice.announce",
        "webhook.post",
        "telegram.send",
    ] = Field(...)
    duration_seconds: Optional[int] = Field(default=15, ge=1, le=60)
    speech_text: Optional[str] = Field(default=None)
    message: Optional[str] = Field(default=None)
    url: Optional[str] = Field(default=None)
    payload: Optional[Dict[str, Any]] = Field(default=None)


class RuleDefinition(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., min_length=2, max_length=100)
    enabled: bool = Field(default=True)
    combinator: Literal["AND", "OR"] = Field(default="AND")
    cooldown_s: int = Field(default=60, ge=0)
    conditions: List[RuleCondition] = Field(default_factory=list)
    actions: List[RuleAction] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class UserRuleTemplate(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = Field(default="")
    combinator: Literal["AND", "OR"] = Field(default="AND")
    cooldown_s: int = Field(default=60, ge=0)
    conditions: List[RuleCondition] = Field(default_factory=list)
    actions: List[RuleAction] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RulesEvaluator:
    """Evaluates user automations against live system state."""

    def __init__(self):
        self._rules_cache: Dict[str, RuleDefinition] = {}
        self._last_rule_execution: Dict[str, float] = {}

    async def load_rules_from_db(self) -> None:
        """Loads user rules from SQLite."""
        def _fetch():
            with state_manager._get_connection() as conn:
                cur = conn.execute("SELECT id, name, enabled, rule_json FROM user_rules;")
                return cur.fetchall()

        rows = await asyncio.to_thread(_fetch)
        new_cache = {}
        for r in rows:
            try:
                rule_data = json.loads(r["rule_json"])
                rule_obj = RuleDefinition(**rule_data)
                new_cache[rule_obj.id] = rule_obj
            except Exception as e:
                logger.error("Error parsing user rule %s: %s", r["id"], e)

        self._rules_cache = new_cache
        logger.info("Loaded %d user automation rules from database.", len(self._rules_cache))

    def get_all_rules(self) -> List[RuleDefinition]:
        return list(self._rules_cache.values())

    def get_rule(self, rule_id: str) -> Optional[RuleDefinition]:
        return self._rules_cache.get(rule_id)

    async def save_rule(self, rule: RuleDefinition) -> None:
        """Persists or updates user rule in SQLite."""
        rule.updated_at = datetime.now(timezone.utc).isoformat()
        rule_json = rule.model_dump_json()

        def _upsert():
            with state_manager._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO user_rules (id, name, enabled, rule_json, created_at, updated_at)
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
        logger.info("Saved user rule '%s' (%s).", rule.name, rule.id)

    async def delete_rule(self, rule_id: str) -> bool:
        if rule_id not in self._rules_cache:
            return False

        def _delete():
            with state_manager._get_connection() as conn:
                conn.execute("DELETE FROM user_rules WHERE id = ?;", (rule_id,))
                conn.commit()

        await asyncio.to_thread(_delete)
        self._rules_cache.pop(rule_id, None)
        logger.info("Deleted rule %s.", rule_id)
        return True

    # -------------------------------------------------------------------------
    # User Template Management (Part 2.7: Save my rule as a template)
    # -------------------------------------------------------------------------

    async def save_as_template(self, rule_id: str, template_name: str, description: str = "") -> Optional[UserRuleTemplate]:
        """Saves an existing user rule as a reusable template in user_rule_templates."""
        rule = self.get_rule(rule_id)
        if not rule:
            return None

        tpl = UserRuleTemplate(
            name=template_name,
            description=description,
            combinator=rule.combinator,
            cooldown_s=rule.cooldown_s,
            conditions=rule.conditions,
            actions=rule.actions,
        )
        tpl_json = tpl.model_dump_json()

        def _insert():
            with state_manager._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO user_rule_templates (id, name, description, template_json, created_at)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (tpl.id, tpl.name, tpl.description, tpl_json, tpl.created_at),
                )
                conn.commit()

        await asyncio.to_thread(_insert)
        logger.info("User rule '%s' saved as template '%s'.", rule.name, template_name)
        return tpl

    async def get_user_templates(self) -> List[UserRuleTemplate]:
        """Returns all user-saved templates from the database."""
        def _fetch():
            with state_manager._get_connection() as conn:
                cur = conn.execute("SELECT template_json FROM user_rule_templates ORDER BY created_at DESC;")
                return cur.fetchall()

        rows = await asyncio.to_thread(_fetch)
        templates = []
        for r in rows:
            try:
                templates.append(UserRuleTemplate(**json.loads(r["template_json"])))
            except Exception:
                pass
        return templates

    async def delete_user_template(self, template_id: str) -> bool:
        def _delete():
            with state_manager._get_connection() as conn:
                cur = conn.execute("DELETE FROM user_rule_templates WHERE id = ?;", (template_id,))
                conn.commit()
                return cur.rowcount > 0

        return await asyncio.to_thread(_delete)

    # -------------------------------------------------------------------------
    # Rule Evaluation
    # -------------------------------------------------------------------------

    async def evaluate_tick(self) -> None:
        """Evaluates enabled rules on periodic timer."""
        state = state_manager.get_state()
        mono_now = time.monotonic()

        for rule in self._rules_cache.values():
            if not rule.enabled or not rule.conditions:
                continue

            last_run = self._last_rule_execution.get(rule.id, 0.0)
            cooldown = getattr(rule, "cooldown_s", 60)
            if (mono_now - last_run) < cooldown:
                continue

            if self._evaluate_conditions(rule.conditions, rule.combinator, state):
                logger.info("Rule '%s' conditions met. Executing actions.", rule.name)
                self._last_rule_execution[rule.id] = mono_now
                await self._execute_actions(rule)

    def _evaluate_conditions(
        self,
        conditions: List[RuleCondition],
        combinator: str,
        state: Dict[str, Any],
    ) -> bool:
        results = []
        for cond in conditions:
            if cond.source == "time.clock" and cond.operator not in ("==", "!="):
                logger.warning("time.clock only supports == and != operators, ignoring invalid operator '%s'", cond.operator)
                results.append(False)
                continue
            actual = self._resolve_source_value(cond.source, state)
            match = self._check_condition(actual, cond.operator, cond.value)
            results.append(match)

        if combinator == "AND":
            return all(results)
        elif combinator == "OR":
            return any(results)
        return False

    def _resolve_source_value(self, source: str, state: Dict[str, Any]) -> Any:
        """Resolves source string to live state value."""
        he = state.get("head_end") or {}
        motor = state.get("motor") or {}
        g = state.get("garden") or {}
        zones = g.get("zones") or {}

        now_dt = datetime.now()
        current_minutes = now_dt.hour * 60 + now_dt.minute

        mapping = {
            "tank.low": he.get("tank_low_float"),
            "motor.state": motor.get("state"),
            "interlock.state": he.get("interlock_cause"),
            "reservoir.state": "wet" if g.get("reservoir_wet") is True else ("dry" if g.get("reservoir_wet") is False else "unknown"),
            "leak.state": he.get("leak_detected"),
            "link.state": "online" if g.get("node_online") and not g.get("is_stale") else "offline",
            "time.clock": now_dt.strftime("%H:%M"),
            "time.minutes": current_minutes,
        }

        # Check per-zone sources
        for i in range(1, 5):
            z_key = f"zone_{i}"
            z_data = zones.get(z_key, {})
            mapping[f"{z_key}.moisture"] = z_data.get("moisture_pct")
            mapping[f"{z_key}.volts"] = z_data.get("voltage")

        return mapping.get(source)

    @staticmethod
    def _parse_minute(val: Any) -> Optional[int]:
        """Converts an HH:MM string or integer (0..1439) into minutes from midnight."""
        if isinstance(val, (int, float)):
            return int(val) % 1440
        if isinstance(val, str):
            v = val.strip()
            if ":" in v:
                parts = v.split(":")
                if len(parts) == 2:
                    try:
                        h, m = int(parts[0]), int(parts[1])
                        if 0 <= h <= 23 and 0 <= m <= 59:
                            return h * 60 + m
                    except ValueError:
                        return None
            elif v.isdigit():
                return int(v) % 1440
        return None

    @classmethod
    def _check_between(cls, actual: Any, target: Any) -> bool:
        """
        Checks if actual is within target window.
        Supports:
          - HH:MM windows like ["22:00", "04:00"] or "22:00-04:00" (including midnight-crossing).
          - Minute ranges [1320, 240] (including midnight-crossing when start > end).
          - Generic numeric bounds [low, high] where low <= actual <= high.
        """
        if actual is None:
            return False

        # 1. Extract start and end from target
        start_val, end_val = None, None
        if isinstance(target, (list, tuple)) and len(target) == 2:
            start_val, end_val = target[0], target[1]
        elif isinstance(target, str):
            for sep in (" to ", "..", ",", "-"):
                if sep in target:
                    parts = target.split(sep, 1)
                    start_val, end_val = parts[0].strip(), parts[1].strip()
                    break

        if start_val is None or end_val is None:
            return False

        # 2. Check if time-based
        is_time_target = (isinstance(start_val, str) and ":" in start_val) or (isinstance(end_val, str) and ":" in end_val)
        start_m = cls._parse_minute(start_val)
        end_m = cls._parse_minute(end_val)
        cur_m = cls._parse_minute(actual)

        if is_time_target or (cur_m is not None and start_m is not None and end_m is not None and (start_m > end_m or (isinstance(actual, int) and 0 <= actual < 1440))):
            if cur_m is None or start_m is None or end_m is None:
                return False
            if start_m <= end_m:
                return start_m <= cur_m <= end_m
            else:
                # Midnight-crossing window (e.g. 22:00 to 04:00)
                return cur_m >= start_m or cur_m <= end_m

        # 3. Fallback: Generic numeric range comparison
        try:
            num_actual = float(actual)
            num_start = float(start_val)
            num_end = float(end_val)
            if num_start <= num_end:
                return num_start <= num_actual <= num_end
            else:
                return num_actual >= num_start or num_actual <= num_end
        except (ValueError, TypeError):
            return False

    def _check_condition(self, actual: Any, operator: str, target: Any) -> bool:
        if actual is None:
            return False

        try:
            if operator == "between":
                return self._check_between(actual, target)
            elif operator == ">":
                t = self._coerce_target(actual, target)
                return float(actual) > float(t)
            elif operator == "<":
                t = self._coerce_target(actual, target)
                return float(actual) < float(t)
            elif operator == ">=":
                t = self._coerce_target(actual, target)
                return float(actual) >= float(t)
            elif operator == "<=":
                t = self._coerce_target(actual, target)
                return float(actual) <= float(t)
            elif operator == "==":
                return str(actual).lower() == str(target).lower()
            elif operator == "!=":
                return str(actual).lower() != str(target).lower()
            elif operator == "in_range" and isinstance(target, list) and len(target) == 2:
                val = float(actual)
                return float(target[0]) <= val <= float(target[1])
        except (ValueError, TypeError):
            return False
        return False

    @classmethod
    def _coerce_target(cls, actual: Any, target: Any) -> Any:
        if isinstance(actual, int) and 0 <= actual < 1440 and isinstance(target, str) and ":" in target:
            m = cls._parse_minute(target)
            if m is not None:
                return m
        return target

    async def _execute_actions(self, rule: RuleDefinition) -> None:
        """Dispatches rule actions. Priority 0 failsafes always override rules."""
        for act in rule.actions:
            tgt = act.target

            # 1. Plant Pump Action
            if tgt.startswith("pump.zone_"):
                zone_key = tgt.replace("pump.", "")
                duration = act.duration_seconds or 15
                await plant_controller.trigger_zone_pulse(zone_key, duration_s=duration, is_manual=False, source=f"rule:{rule.name}")

            # 2. Motor Actions
            elif tgt == "motor.start":
                await motor_controller.start_motor(is_manual=False)
            elif tgt == "motor.stop":
                await motor_controller.stop_motor(reason=f"rule:{rule.name}")

            # 3. Voice Announcement
            elif tgt == "voice.announce" and act.speech_text:
                await audio_controller.speak(act.speech_text, priority=2)

            # 4. Telegram Notification
            elif tgt == "telegram.send" and act.message:
                await telegram_notifier.send_alert("RULE_TRIGGER", act.message)

            # 5. Webhook Post with SSRF Defense (Part 3.4)
            elif tgt == "webhook.post" and act.url:
                from app.core.security import validate_webhook_url
                is_safe, err_msg = await asyncio.to_thread(
                    validate_webhook_url, act.url, system_config.security.webhook_allowlist
                )
                if not is_safe:
                    logger.error("SSRF PROTECTION: Rule webhook '%s' rejected: %s", act.url, err_msg)
                    continue

                async def _post(target_url: str):
                    try:
                        async with httpx.AsyncClient(timeout=4.0) as client:
                            await client.post(target_url, json=act.payload or {"rule": rule.name})
                    except Exception as e:
                        logger.error("Rule webhook error: %s", e)
                asyncio.create_task(_post(act.url))



rules_evaluator = RulesEvaluator()
