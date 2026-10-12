"""
Central operational state manager & SQLite persistence coordinator.
Implements:
  - lockouts table (motor cooldown, manual lock, zone cooldowns, flow faults, alarms)
  - PRAGMA auto_vacuum = INCREMENTAL (Part 5.3)
  - Per-field timestamps (soil 60s, reservoir 20s, heartbeat 30s) (Part 2.9)
  - Zero seeded rows on first start
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional
from app.config import DATA_DIR, system_config
from app.hardware.rtc import get_monotonic_now, is_system_clock_valid

logger = logging.getLogger("smart_water.state_manager")


class StalenessResult(dict):
    """Result object supporting both dict key access and (he_stale, gd_stale) tuple unpacking."""
    def __init__(self, head_end_stale: bool, node_stale: bool, reservoir_stale: bool, zones_stale: Dict[str, bool]):
        super().__init__({
            "head_end_stale": head_end_stale,
            "node_stale": node_stale,
            "reservoir_stale": reservoir_stale,
            "zones_stale": zones_stale,
        })
        self.head_end_stale = head_end_stale
        self.node_stale = node_stale
        self.reservoir_stale = reservoir_stale
        self.zones_stale = zones_stale

    def __iter__(self):
        return iter((self.head_end_stale, self.node_stale))


class StateManager:
    """
    Central operational memory and SQLite log coordinator.
    Production state only: unread inputs remain None,
    unconfigured hardware reports SAFE-NO-HARDWARE.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path or (DATA_DIR / "system.db"))
        self._lock = asyncio.Lock()
        self._start_monotonic = get_monotonic_now()
        self._wiring_fault_latched: bool = False

        # In-Memory Live State (Zero seeded numbers)
        self._state: Dict[str, Any] = {
            "system_status": "SAFE-NO-HARDWARE",
            "hardware_ready": False,
            "uptime_seconds": 0,
            "system_clock_valid": True,
            "head_end": {
                "configured": False,
                "last_update_ts": None,
                "is_stale": True,
                "tank_low_float": None,       # None = No reading yet (debounced logical)
                "interlock_out": None,
                "interlock_mid": None,
                "leak_detected": None,
                "raw": {
                    "tank_low_float": None,
                    "interlock_out": None,
                    "interlock_mid": None,
                    "leak_sensor": None,
                    "raw_levels": {},
                },
                "debounced": {
                    "tank_low_float": None,
                    "interlock_out": None,
                    "interlock_mid": None,
                    "leak_sensor": None,
                },
                "interlock_cause": "unconfigured",
                "contactor_relay": False,
                "contactor_feedback": None,
                "healthy_debounce_count": 0,  # Consecutive healthy cycles
                "wiring_fault_latched": False,
            },
            "motor": {
                "state": "OFF",              # OFF | RUNNING | COOLDOWN_LOCKED | MANUAL_LOCKED
                "started_at_monotonic": None,
                "started_at_iso": None,
                "last_stop_monotonic": None,
                "current_run_seconds": 0,
                "cooldown_until_ts": 0.0,
                "manual_locked": False,
                "last_stop_reason": None,
                "auto_mode": False,
            },
            "garden": {
                "gateway_connected": False,
                "gateway_last_seen": None,
                "node_online": False,
                "node_last_seen": None,
                "last_packet_ts": None,
                "is_stale": True,
                "rssi": None,
                "uptime_s": None,
                "boot_count": 0,
                "ads_ok": None,
                "reservoir_wet": None,       # None = No reading yet
                "reservoir_last_update_ts": None,
                "zones": {
                    f"zone_{i}": {
                        "voltage": None,
                        "moisture_pct": None,
                        "calibrated": False,
                        "pump_running": False,
                        "flow_delta": 0,
                        "flow_fault": False,
                        "cooldown_until_ts": 0.0,
                        "last_update_ts": None,
                    }
                    for i in range(1, 5)
                },
            },
            "alarms": {},
        }

    # -------------------------------------------------------------------------
    # State Accessors
    # -------------------------------------------------------------------------

    def get_state(self) -> Dict[str, Any]:
        """Returns deep snapshot of operational state."""
        state = json.loads(json.dumps(self._state))
        state["uptime_seconds"] = int(get_monotonic_now() - self._start_monotonic)
        state["system_clock_valid"] = is_system_clock_valid()

        if state["motor"]["state"] == "RUNNING" and self._state["motor"]["started_at_monotonic"]:
            run_sec = int(get_monotonic_now() - self._state["motor"]["started_at_monotonic"])
            state["motor"]["current_run_seconds"] = run_sec

        return state

    # -------------------------------------------------------------------------
    # Head-End State Updates & Cause Logic
    # -------------------------------------------------------------------------

    def reset_wiring_fault(self) -> None:
        """Clears latched wiring fault state."""
        self._wiring_fault_latched = False
        if "head_end" in self._state:
            self._state["head_end"]["wiring_fault_latched"] = False
        self._state["alarms"].pop("wiring_fault", None)

    async def update_head_end(
        self,
        tank_low: Optional[bool],
        interlock_out: Optional[bool],
        interlock_mid: Optional[bool],
        leak_detected: Optional[bool],
        hardware_available: bool,
        contactor_feedback: Optional[bool] = None,
        raw: Optional[Dict[str, Any]] = None,
        debounced: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Updates hardwired head-end inputs and computes hardware interlock cause logic:
          - interlock_out HIGH & interlock_mid HIGH = healthy
          - interlock_out LOW & interlock_mid LOW   = Full float tripped (tank full / wire cut)
          - interlock_out LOW & interlock_mid HIGH  = Sump float open (dry sump)
          - interlock_out HIGH & interlock_mid LOW  = impossible -> WIRING FAULT (latches until manual reset)
        """
        async with self._lock:
            now = time.time()
            he = self._state["head_end"]
            he["configured"] = hardware_available
            self._state["hardware_ready"] = hardware_available
            he["contactor_feedback"] = contactor_feedback

            # If debounced dict passed, use debounced logical values
            if debounced:
                tank_low = debounced.get("tank_low_float", debounced.get("tank_low", tank_low))
                interlock_out = debounced.get("interlock_out", interlock_out)
                interlock_mid = debounced.get("interlock_mid", interlock_mid)
                leak_detected = debounced.get("leak_sensor", debounced.get("leak_detected", leak_detected))
            else:
                debounced = {
                    "tank_low_float": tank_low,
                    "interlock_out": interlock_out,
                    "interlock_mid": interlock_mid,
                    "leak_sensor": leak_detected,
                }

            if raw is None:
                raw = {
                    "tank_low_float": tank_low,
                    "interlock_out": interlock_out,
                    "interlock_mid": interlock_mid,
                    "leak_sensor": leak_detected,
                    "raw_levels": {},
                }

            he["raw"] = raw
            he["debounced"] = debounced

            if not hardware_available or interlock_out is None or interlock_mid is None:
                he["tank_low_float"] = None
                he["interlock_out"] = None
                he["interlock_mid"] = None
                he["leak_detected"] = None
                he["interlock_cause"] = "unconfigured"
                he["is_stale"] = True
                he["healthy_debounce_count"] = 0
                self._state["system_status"] = "SAFE-NO-HARDWARE"
                return

            he["last_update_ts"] = now
            he["is_stale"] = False
            he["tank_low_float"] = tank_low
            he["interlock_out"] = interlock_out
            he["interlock_mid"] = interlock_mid
            he["leak_detected"] = leak_detected

            # Latch wiring fault if impossible state occurs
            if interlock_out is True and interlock_mid is False:
                self._wiring_fault_latched = True

            he["wiring_fault_latched"] = self._wiring_fault_latched

            # Cause logic evaluation (derived from debounced values)
            if self._wiring_fault_latched:
                he["interlock_cause"] = "wiring_fault"
                he["healthy_debounce_count"] = 0
            elif interlock_out is True and interlock_mid is True:
                he["interlock_cause"] = "healthy"
                he["healthy_debounce_count"] += 1
            elif interlock_out is False and interlock_mid is False:
                he["interlock_cause"] = "tank_full"
                he["healthy_debounce_count"] = 0
            elif interlock_out is False and interlock_mid is True:
                he["interlock_cause"] = "dry_sump"
                he["healthy_debounce_count"] = 0
            else:
                he["interlock_cause"] = "unconfigured"
                he["healthy_debounce_count"] = 0

    async def check_staleness(self) -> StalenessResult:
        """
        Evaluates staleness across all inputs:
          - head_end: max 15s
          - garden packet: max 90s
          - node heartbeat: max 30s
        """
        async with self._lock:
            now = time.time()
            th = system_config.stale_thresholds

            # 1. Head-end check
            he = self._state["head_end"]
            if he["last_update_ts"] is None or (now - he["last_update_ts"]) > th.head_end_s:
                he["is_stale"] = True
                he["healthy_debounce_count"] = 0
                if he["interlock_cause"] == "healthy":
                    he["interlock_cause"] = "stale"
            else:
                he["is_stale"] = False

            # 2. Garden Node check (tracked by node_last_seen and last_packet_ts)
            g = self._state["garden"]
            node_seen = g.get("node_last_seen")
            pkt_seen = g.get("last_packet_ts")
            garden_ts = pkt_seen if pkt_seen is not None else node_seen

            if garden_ts is None or (now - garden_ts) > th.garden_s:
                g["node_online"] = False
                g["is_stale"] = True
            elif node_seen is not None and (now - node_seen) > th.heartbeat_s:
                g["node_online"] = False
                g["is_stale"] = True
            else:
                g["node_online"] = True
                g["is_stale"] = False

            # 3. Reservoir staleness (max 20s)
            res_ts = g["reservoir_last_update_ts"]
            res_stale = (res_ts is None) or ((now - res_ts) > th.reservoir_s)

            # 4. Per-zone probe staleness (max 60s)
            zone_stale_map = {}
            for z_key, z_data in g["zones"].items():
                z_ts = z_data["last_update_ts"]
                is_probe_stale = (z_ts is None) or ((now - z_ts) > th.soil_s)
                zone_stale_map[z_key] = is_probe_stale

            return StalenessResult(
                head_end_stale=he["is_stale"],
                node_stale=g["is_stale"],
                reservoir_stale=res_stale,
                zones_stale=zone_stale_map,
            )

    # -------------------------------------------------------------------------
    # Garden Telemetry Updates
    # -------------------------------------------------------------------------

    async def update_garden_heartbeat(
        self,
        seq: int,
        uptime_s: int,
        res_wet: bool,
        ads_ok: bool,
        pumps: List[int],
        rssi: int,
        boot: int = 0,
        nonce: Optional[int] = None,
    ) -> None:
        async with self._lock:
            now = time.time()
            effective_nonce = nonce if nonce is not None else boot
            g = self._state["garden"]
            g["gateway_connected"] = True
            g["gateway_last_seen"] = now
            g["node_online"] = True
            g["node_last_seen"] = now
            g["last_packet_ts"] = now
            g["last_heartbeat_ts"] = now
            g["is_stale"] = False
            g["state"] = "ONLINE"
            g["uptime_s"] = uptime_s
            g["boot_count"] = effective_nonce
            g["nonce"] = effective_nonce
            g["ads_ok"] = ads_ok
            g["rssi"] = rssi
            g["reservoir_wet"] = res_wet
            g["reservoir_last_update_ts"] = now

            for idx, p_state in enumerate(pumps):
                z_key = f"zone_{idx + 1}"
                if z_key in g["zones"]:
                    g["zones"][z_key]["pump_running"] = bool(p_state)

    async def update_garden_soil(self, zone_idx: int, volts: float, n_samples: int = 8) -> None:
        async with self._lock:
            now = time.time()
            g = self._state["garden"]
            g["node_online"] = True
            g["node_last_seen"] = now
            g["last_packet_ts"] = now
            z_key = f"zone_{zone_idx}"
            if z_key in g["zones"]:
                z = g["zones"][z_key]
                z["voltage"] = volts
                z["last_update_ts"] = now
                z_cfg = system_config.zones.get(z_key)
                if z_cfg and z_cfg.calibration.dry_v is not None and z_cfg.calibration.wet_v is not None:
                    dry_v = z_cfg.calibration.dry_v
                    wet_v = z_cfg.calibration.wet_v
                    if abs(dry_v - wet_v) > 0.05:
                        pct = ((dry_v - volts) / (dry_v - wet_v)) * 100.0
                        z["moisture_pct"] = max(0.0, min(100.0, round(pct, 1)))
                        z["calibrated"] = True
                else:
                    z["moisture_pct"] = None
                    z["calibrated"] = False

    async def update_garden_flow_delta(self, zone_idx: int, delta_pulses: int, period_ms: int = 1000) -> None:
        async with self._lock:
            now = time.time()
            g = self._state["garden"]
            g["node_online"] = True
            g["node_last_seen"] = now
            g["last_packet_ts"] = now
            z_key = f"zone_{zone_idx}"
            if z_key in g["zones"]:
                z = g["zones"][z_key]
                z["flow_delta"] = delta_pulses
                z["flow_period_ms"] = period_ms
                z["flow_pulses"] = z.get("flow_pulses", 0) + delta_pulses
                z["last_update_ts"] = now

    async def update_garden_pump(self, zone_idx: int, is_on: bool, reason: str = "unknown") -> None:
        async with self._lock:
            now = time.time()
            g = self._state["garden"]
            g["node_online"] = True
            g["node_last_seen"] = now
            g["last_packet_ts"] = now
            z_key = f"zone_{zone_idx}"
            if z_key in g["zones"]:
                z = g["zones"][z_key]
                z["pump_running"] = bool(is_on)
                z["last_update_ts"] = now

    async def set_garden_degraded(self, reason: str = "Stale heartbeat") -> None:
        async with self._lock:
            g = self._state["garden"]
            g["is_stale"] = True
            g["state"] = "DEGRADED"
            self._state["system_status"] = "DEGRADED"

    async def update_garden_link_status(self, gateway_connected: bool, node_online: bool) -> None:
        async with self._lock:
            g = self._state["garden"]
            g["gateway_connected"] = gateway_connected
            g["node_online"] = node_online
            if not node_online:
                g["is_stale"] = True
                g["state"] = "DEGRADED"
                self._state["system_status"] = "DEGRADED"

    # -------------------------------------------------------------------------
    # Motor & Actuator State
    # -------------------------------------------------------------------------

    async def set_motor_state(self, new_state: str, reason: Optional[str] = None) -> None:
        async with self._lock:
            m = self._state["motor"]
            old_state = m["state"]
            m["state"] = new_state
            mono_now = get_monotonic_now()

            if new_state == "RUNNING" and old_state != "RUNNING":
                m["started_at_monotonic"] = mono_now
                m["started_at_iso"] = datetime.now(timezone.utc).isoformat()
                m["last_stop_reason"] = None
            elif new_state != "RUNNING" and old_state == "RUNNING":
                m["last_stop_monotonic"] = mono_now
                m["last_stop_reason"] = reason or "unknown"
                started_iso = m.get("started_at_iso") or datetime.now(timezone.utc).isoformat()
                duration_s = int(mono_now - (m["started_at_monotonic"] or mono_now))
                m["started_at_monotonic"] = None
                # Asynchronously log run in runs table
                asyncio.create_task(self.log_run("motor", started_iso, duration_s, reason or "stopped"))
            elif new_state == "OFF":
                m["last_stop_reason"] = reason

    async def set_motor_cooldown(self, until_epoch: float) -> None:
        async with self._lock:
            self._state["motor"]["cooldown_until_ts"] = until_epoch
            await self.save_lockout("motor_cooldown", {"cooldown_until": until_epoch})

    async def set_motor_manual_lock(self, locked: bool) -> None:
        async with self._lock:
            self._state["motor"]["manual_locked"] = locked
            await self.save_lockout("motor_manual_lock", {"manual_locked": locked})

    async def set_motor_auto_mode(self, enabled: bool) -> None:
        async with self._lock:
            self._state["motor"]["auto_mode"] = enabled
            system_config.motor.auto_mode = enabled

    async def set_zone_cooldown(self, zone_key: str, until_epoch: float) -> None:
        async with self._lock:
            if zone_key in self._state["garden"]["zones"]:
                self._state["garden"]["zones"][zone_key]["cooldown_until_ts"] = until_epoch
            # Persist zone cooldown
            await self.save_lockout(f"cooldown_{zone_key}", {"cooldown_until": until_epoch})

    async def set_zone_flow_fault(self, zone_key: str, faulted: bool) -> None:
        async with self._lock:
            if zone_key in self._state["garden"]["zones"]:
                self._state["garden"]["zones"][zone_key]["flow_fault"] = faulted
            await self.save_lockout(f"flow_fault_{zone_key}", {"faulted": faulted})

    async def set_alarm(self, alarm_key: str, message: str) -> None:
        async with self._lock:
            self._state["alarms"][alarm_key] = {
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._state["system_status"] = "ALARM"
            await self.save_lockout(f"alarm_{alarm_key}", {"message": message, "active": True})

    async def clear_alarm(self, alarm_key: str) -> None:
        async with self._lock:
            self._state["alarms"].pop(alarm_key, None)
            await self.save_lockout(f"alarm_{alarm_key}", {"active": False})
            if not self._state["alarms"]:
                self._state["system_status"] = "OK" if self._state["hardware_ready"] else "SAFE-NO-HARDWARE"

    # -------------------------------------------------------------------------
    # SQLite Database Persistence (Empty On First Start, PRAGMA cache ~2MB)
    # -------------------------------------------------------------------------

    async def init_db(self) -> None:
        """Initializes SQLite tables and loads persisted lockouts."""
        await asyncio.to_thread(self._init_db_sync)
        await self.load_persisted_lockouts()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute(f"PRAGMA cache_size = {system_config.database.cache_size_kb};")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        return conn

    def _init_db_sync(self) -> None:
        # Check if database file exists before connect to set auto_vacuum if new
        is_new = not self.db_path.exists()
        with self._get_connection() as conn:
            if is_new:
                # PRAGMA auto_vacuum must be set before tables are created
                conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")

            # 1. Schema version
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                """
            )
            cur = conn.execute("SELECT COUNT(*) FROM schema_version;")
            if cur.fetchone()[0] == 0:
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (2, ?);",
                    (datetime.now(timezone.utc).isoformat(),),
                )

            # 2. Lockouts & Latches Table (Part 2.2)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lockouts (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

            # 3. Events Table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT
                );
                """
            )

            # 4. Downsampled Readings Table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    tank_low INTEGER,
                    interlock_cause TEXT,
                    motor_running INTEGER,
                    res_wet INTEGER,
                    z1_volts REAL,
                    z2_volts REAL,
                    z3_volts REAL,
                    z4_volts REAL,
                    z1_flow INTEGER
                );
                """
            )

            # 5. Actuator Runs Table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    duration_s INTEGER NOT NULL,
                    end_reason TEXT NOT NULL
                );
                """
            )

            # 6. User Automation Rules Table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_rules (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    rule_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

            # 7. User Saved Rule Templates Table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_rule_templates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    template_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_target ON runs(target);")
            conn.commit()
            logger.info("Database initialized with empty tables at %s", self.db_path)

    async def save_lockout(self, key: str, value: Any) -> None:
        """Persists a lockout flag or cooldown to SQLite."""
        val_json = json.dumps(value)
        now = time.time()

        def _sync_save():
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO lockouts (key, value_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at;
                    """,
                    (key, val_json, now),
                )
                conn.commit()

        try:
            await asyncio.to_thread(_sync_save)
        except Exception as e:
            logger.error("Failed to persist lockout %s: %s", key, e)

    async def load_persisted_lockouts(self) -> int:
        """
        Loads lockouts and cooldowns at startup.
        If wall-clock is invalid at boot, treats every cooldown as active! (Part 2.2)
        """
        def _sync_load() -> List[Dict[str, Any]]:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT key, value_json, updated_at FROM lockouts;")
                return [dict(row) for row in cur.fetchall()]

        try:
            rows = await asyncio.to_thread(_sync_load)
        except Exception as e:
            logger.error("Error reading lockouts table: %s", e)
            return 0

        clock_valid = is_system_clock_valid()
        now = time.time()

        async with self._lock:
            for row in rows:
                k = row["key"]
                try:
                    val = json.loads(row["value_json"])
                except Exception:
                    continue

                if k == "motor_manual_lock":
                    self._state["motor"]["manual_locked"] = bool(val.get("manual_locked", False))
                elif k == "motor_cooldown":
                    cd = val.get("cooldown_until", 0.0)
                    if not clock_valid:
                        # Clock invalid -> treat cooldown as perpetually active until clock fixed
                        self._state["motor"]["cooldown_until_ts"] = float("inf")
                    else:
                        self._state["motor"]["cooldown_until_ts"] = cd if cd > now else 0.0
                elif k.startswith("cooldown_zone_"):
                    z_key = k.replace("cooldown_", "")
                    if z_key in self._state["garden"]["zones"]:
                        cd = val.get("cooldown_until", 0.0)
                        if not clock_valid:
                            self._state["garden"]["zones"][z_key]["cooldown_until_ts"] = float("inf")
                        else:
                            self._state["garden"]["zones"][z_key]["cooldown_until_ts"] = cd if cd > now else 0.0
                elif k.startswith("flow_fault_zone_"):
                    z_key = k.replace("flow_fault_", "")
                    if z_key in self._state["garden"]["zones"]:
                        self._state["garden"]["zones"][z_key]["flow_fault"] = bool(val.get("faulted", False))
                elif k.startswith("alarm_"):
                    alarm_name = k.replace("alarm_", "")
                    if val.get("active"):
                        self._state["alarms"][alarm_name] = {
                            "message": val.get("message", "Persisted alarm"),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        self._state["system_status"] = "ALARM"

        logger.info("Loaded %d persisted lockouts (clock_valid=%s)", len(rows), clock_valid)
        return len(rows)

    async def log_event(self, event_type: str, severity: str, message: str, details: Optional[Dict] = None) -> None:
        """Records an event in the events table."""
        ts = datetime.now(timezone.utc).isoformat()
        details_json = json.dumps(details or {})

        def _insert():
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO events (timestamp, event_type, severity, message, details_json)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (ts, event_type, severity, message, details_json),
                )
                conn.commit()

        await asyncio.to_thread(_insert)

    async def log_run(self, target: str, started_at_iso: str, duration_s: int, end_reason: str) -> None:
        """Records completed actuator run in runs table."""
        ended_at_iso = datetime.now(timezone.utc).isoformat()

        def _insert():
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO runs (target, started_at, ended_at, duration_s, end_reason)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (target, started_at_iso, ended_at_iso, duration_s, end_reason),
                )
                conn.commit()

        await asyncio.to_thread(_insert)

    async def purge_by_retention(self) -> int:
        """Purges events and readings older than configured retention_days, then runs incremental vacuum."""
        days = system_config.database.retention_days

        def _purge() -> int:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "DELETE FROM events WHERE timestamp < datetime('now', ?);",
                    (f"-{days} days",),
                )
                deleted_events = cur.rowcount
                cur2 = conn.execute(
                    "DELETE FROM readings WHERE timestamp < datetime('now', ?);",
                    (f"-{days} days",),
                )
                deleted_readings = cur2.rowcount
                conn.execute("PRAGMA incremental_vacuum;")
                conn.commit()
                return deleted_events + deleted_readings

        return await asyncio.to_thread(_purge)


state_manager = StateManager()
