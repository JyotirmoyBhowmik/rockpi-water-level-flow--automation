"""
Thread-Safe System State Coordinator & SQLite WAL Persistence Store.
Enforces zero-seeded data policy, stale data detection, and strict SQLite memory limits.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple
from app.config import system_config
from app.hardware.rtc import get_monotonic_now, is_system_clock_valid

logger = logging.getLogger("smart_water.state_manager")


class StateManager:
    """
    Central operational memory and SQLite log coordinator.
    Strictly zero fake or mock data: unread inputs remain None,
    unconfigured hardware reports SAFE-NO-HARDWARE.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path or system_config.database.path)
        self._lock = asyncio.Lock()
        self._start_monotonic = get_monotonic_now()

        # In-Memory Live State (Zero seeded fake numbers)
        self._state: Dict[str, Any] = {
            "system_status": "SAFE-NO-HARDWARE",
            "hardware_ready": False,
            "uptime_seconds": 0,
            "system_clock_valid": True,
            "head_end": {
                "configured": False,
                "last_update_ts": None,
                "is_stale": True,
                "tank_low_float": None,       # None = No reading yet / not configured
                "interlock_out": None,
                "interlock_mid": None,
                "leak_detected": None,
                "interlock_cause": "unconfigured",  # unconfigured | healthy | tank_full | dry_sump | wiring_fault | stale
                "contactor_relay": False,
            },
            "motor": {
                "state": "OFF",              # OFF | RUNNING | COOLDOWN_LOCKED | MANUAL_LOCKED
                "started_at_monotonic": None,
                "current_run_seconds": 0,
                "cooldown_until_ts": 0.0,
                "manual_locked": False,
                "last_stop_reason": None,
                "auto_mode": system_config.motor.auto_mode,
            },
            "garden": {
                "gateway_connected": False,
                "node_online": False,
                "last_packet_ts": None,
                "is_stale": True,
                "rssi": None,
                "uptime_s": None,
                "reservoir_wet": None,       # None = No reading yet
                "zones": {
                    f"zone_{i}": {
                        "voltage": None,
                        "moisture_pct": None,
                        "calibrated": False,
                        "pump_running": False,
                        "flow_pulses": 0,
                        "flow_fault": False,
                        "cooldown_until_ts": 0.0,
                        "last_update_ts": None,
                    }
                    for i in range(1, 5)
                },
            },
            "alarms": {},                    # key -> message
        }

    def get_state(self) -> Dict[str, Any]:
        """Returns snapshot of current live system state."""
        state = json.loads(json.dumps(self._state))
        state["uptime_seconds"] = int(get_monotonic_now() - self._start_monotonic)
        state["system_clock_valid"] = is_system_clock_valid()

        # Live run timer calculation
        if state["motor"]["state"] == "RUNNING" and self._state["motor"]["started_at_monotonic"]:
            run_sec = int(get_monotonic_now() - self._state["motor"]["started_at_monotonic"])
            state["motor"]["current_run_seconds"] = run_sec

        return state

    # -------------------------------------------------------------------------
    # Head-End State Updates & Cause Logic
    # -------------------------------------------------------------------------

    async def update_head_end(
        self,
        tank_low: Optional[bool],
        interlock_out: Optional[bool],
        interlock_mid: Optional[bool],
        leak_detected: Optional[bool],
        hardware_available: bool,
    ) -> None:
        """
        Updates hardwired head-end inputs and computes hardware interlock cause logic:
          - interlock_out HIGH & interlock_mid HIGH = healthy, motor allowed
          - interlock_out LOW & interlock_mid LOW   = Full float tripped (tank full or wire cut)
          - interlock_out LOW & interlock_mid HIGH  = Sump float open (dry sump)
          - interlock_out HIGH & interlock_mid LOW  = impossible -> WIRING FAULT
        """
        async with self._lock:
            now = time.time()
            he = self._state["head_end"]
            he["configured"] = hardware_available
            self._state["hardware_ready"] = hardware_available

            if not hardware_available or interlock_out is None or interlock_mid is None:
                he["tank_low_float"] = None
                he["interlock_out"] = None
                he["interlock_mid"] = None
                he["leak_detected"] = None
                he["interlock_cause"] = "unconfigured"
                he["is_stale"] = True
                self._state["system_status"] = "SAFE-NO-HARDWARE"
                return

            he["last_update_ts"] = now
            he["is_stale"] = False
            he["tank_low_float"] = tank_low
            he["interlock_out"] = interlock_out
            he["interlock_mid"] = interlock_mid
            he["leak_detected"] = leak_detected

            # Cause logic evaluation
            if interlock_out is True and interlock_mid is True:
                he["interlock_cause"] = "healthy"
            elif interlock_out is False and interlock_mid is False:
                he["interlock_cause"] = "tank_full"
            elif interlock_out is False and interlock_mid is True:
                he["interlock_cause"] = "dry_sump"
            elif interlock_out is True and interlock_mid is False:
                he["interlock_cause"] = "wiring_fault"
            else:
                he["interlock_cause"] = "unconfigured"

    async def check_staleness(self) -> Tuple[bool, bool]:
        """
        Evaluates data freshness against configured limits:
          Head-end: max 15s (default)
          Garden: max 90s (default)
        Returns (head_end_stale, garden_stale).
        """
        async with self._lock:
            now = time.time()
            th = system_config.stale_thresholds

            # 1. Head-end check
            he = self._state["head_end"]
            if he["last_update_ts"] is None or (now - he["last_update_ts"]) > th.head_end_s:
                he["is_stale"] = True
                if he["interlock_cause"] == "healthy":
                    he["interlock_cause"] = "stale"
            else:
                he["is_stale"] = False

            # 2. Garden check
            g = self._state["garden"]
            if g["last_packet_ts"] is None or (now - g["last_packet_ts"]) > th.garden_s:
                g["is_stale"] = True
                g["node_online"] = False
            else:
                g["is_stale"] = False

            return he["is_stale"], g["is_stale"]

    # -------------------------------------------------------------------------
    # Motor & Contactor State Updates
    # -------------------------------------------------------------------------

    async def set_motor_state(self, state_str: str, reason: Optional[str] = None) -> None:
        """Sets motor state (OFF, RUNNING, COOLDOWN_LOCKED, MANUAL_LOCKED)."""
        async with self._lock:
            prev = self._state["motor"]["state"]
            self._state["motor"]["state"] = state_str
            self._state["head_end"]["contactor_relay"] = (state_str == "RUNNING")

            if state_str == "RUNNING" and prev != "RUNNING":
                self._state["motor"]["started_at_monotonic"] = get_monotonic_now()
                self._state["motor"]["current_run_seconds"] = 0
            elif state_str != "RUNNING":
                self._state["motor"]["started_at_monotonic"] = None
                self._state["motor"]["current_run_seconds"] = 0
                if reason:
                    self._state["motor"]["last_stop_reason"] = reason

    async def set_motor_cooldown(self, cooldown_until: float) -> None:
        async with self._lock:
            self._state["motor"]["cooldown_until_ts"] = cooldown_until

    async def set_motor_manual_lock(self, locked: bool) -> None:
        async with self._lock:
            self._state["motor"]["manual_locked"] = locked

    async def set_motor_auto_mode(self, enabled: bool) -> None:
        async with self._lock:
            self._state["motor"]["auto_mode"] = enabled
            system_config.motor.auto_mode = enabled

    # -------------------------------------------------------------------------
    # Wireless Garden Telemetry Updates
    # -------------------------------------------------------------------------

    async def update_garden_heartbeat(self, rssi: int, uptime_s: int) -> None:
        async with self._lock:
            g = self._state["garden"]
            g["gateway_connected"] = True
            g["node_online"] = True
            g["last_packet_ts"] = time.time()
            g["is_stale"] = False
            g["rssi"] = rssi
            g["uptime_s"] = uptime_s

    async def update_garden_soil(self, zone_idx: int, voltage: float) -> None:
        async with self._lock:
            z_key = f"zone_{zone_idx}"
            if z_key not in self._state["garden"]["zones"]:
                return
            z = self._state["garden"]["zones"][z_key]
            z["voltage"] = round(voltage, 3)
            z["last_update_ts"] = time.time()
            self._state["garden"]["last_packet_ts"] = time.time()
            self._state["garden"]["node_online"] = True
            self._state["garden"]["is_stale"] = False

            # Calculate moisture percentage if calibration exists
            z_cfg = system_config.zones.get(z_key)
            if z_cfg and z_cfg.calibration.dry_v is not None and z_cfg.calibration.wet_v is not None:
                dry = z_cfg.calibration.dry_v
                wet = z_cfg.calibration.wet_v
                span = dry - wet
                if abs(span) > 0.05:
                    pct = max(0.0, min(100.0, (dry - voltage) / span * 100.0))
                    z["moisture_pct"] = round(pct, 1)
                    z["calibrated"] = True
                else:
                    z["moisture_pct"] = None
                    z["calibrated"] = False
            else:
                z["moisture_pct"] = None
                z["calibrated"] = False

    async def update_garden_reservoir(self, wet: bool) -> None:
        async with self._lock:
            self._state["garden"]["reservoir_wet"] = wet
            self._state["garden"]["last_packet_ts"] = time.time()
            self._state["garden"]["node_online"] = True
            self._state["garden"]["is_stale"] = False

    async def update_garden_flow(self, zone_idx: int, pulses: int) -> None:
        async with self._lock:
            z_key = f"zone_{zone_idx}"
            if z_key in self._state["garden"]["zones"]:
                self._state["garden"]["zones"][z_key]["flow_pulses"] += pulses
                self._state["garden"]["last_packet_ts"] = time.time()

    async def update_garden_pump(self, zone_idx: int, is_on: bool, reason: str) -> None:
        async with self._lock:
            z_key = f"zone_{zone_idx}"
            if z_key in self._state["garden"]["zones"]:
                z = self._state["garden"]["zones"][z_key]
                z["pump_running"] = is_on
                self._state["garden"]["last_packet_ts"] = time.time()

    async def set_zone_cooldown(self, zone_key: str, cooldown_until: float) -> None:
        async with self._lock:
            if zone_key in self._state["garden"]["zones"]:
                self._state["garden"]["zones"][zone_key]["cooldown_until_ts"] = cooldown_until

    async def set_zone_flow_fault(self, zone_key: str, fault: bool) -> None:
        async with self._lock:
            if zone_key in self._state["garden"]["zones"]:
                self._state["garden"]["zones"][zone_key]["flow_fault"] = fault

    # -------------------------------------------------------------------------
    # Alarms Management
    # -------------------------------------------------------------------------

    async def set_alarm(self, key: str, message: str) -> None:
        async with self._lock:
            self._state["alarms"][key] = message
            self._state["system_status"] = "ALARM"

    async def clear_alarm(self, key: str) -> None:
        async with self._lock:
            self._state["alarms"].pop(key, None)
            if not self._state["alarms"]:
                self._state["system_status"] = "OK" if self._state["hardware_ready"] else "SAFE-NO-HARDWARE"

    # -------------------------------------------------------------------------
    # SQLite Database Persistence (Empty On First Start, PRAGMA cache ~2MB)
    # -------------------------------------------------------------------------

    async def init_db(self) -> None:
        """Initializes SQLite tables without any seeded sample or demo rows."""
        await asyncio.to_thread(self._init_db_sync)

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
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                """
            )
            # Record schema version if table is new
            cur = conn.execute("SELECT COUNT(*) FROM schema_version;")
            if cur.fetchone()[0] == 0:
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (1, ?);",
                    (datetime.now(timezone.utc).isoformat(),),
                )

            # Events table (failsafe trips, manual overrides, wiring faults)
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

            # Downsampled readings table
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

            # Motor & pump run history table
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

            # User automation rules table (Empty on first run - NO seeded rules)
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

            # User saved rule templates (Empty on first run - NO pre-seeded templates)
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

    async def purge_old_data(self) -> None:
        """Purges records older than configured retention days."""
        days = system_config.database.retention_days

        def _purge():
            with self._get_connection() as conn:
                conn.execute(
                    f"DELETE FROM readings WHERE timestamp < datetime('now', '-{days} days');"
                )
                conn.execute(
                    f"DELETE FROM events WHERE timestamp < datetime('now', '-{days} days');"
                )
                conn.execute(
                    f"DELETE FROM runs WHERE started_at < datetime('now', '-{days} days');"
                )
                conn.commit()
                conn.execute("PRAGMA incremental_vacuum;")

        await asyncio.to_thread(_purge)


state_manager = StateManager()
