"""
System State Store & High-Performance SQLite WAL Persistence.
Optimized strictly for low memory (<2MB PRAGMA cache) and flash SD wear reduction on RK3308.
"""

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.config import settings

logger = logging.getLogger("smart_water.state_manager")


class StateManager:
    """
    Unified System State Coordinator.
    Provides sub-millisecond atomic memory access for real-time control loops
    paired with asynchronous non-blocking SQLite WAL logging.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.db_path
        self._lock = asyncio.Lock()

        # In-Memory Real-Time State Representation
        self._state: Dict[str, Any] = {
            "sensors": {
                "tank_low_float": False,      # True = switch tripped (water level >= low float)
                "tank_high_float": False,     # True = switch tripped (tank completely full)
                "reservoir_low_float": False, # True = switch tripped (reservoir empty / dry run)
                "soil_probes": {
                    "zone_1_voltage": 2.2,    # Volts (capacitive soil sensor, 0.5V - 3.0V)
                    "zone_2_voltage": 2.3,
                    "zone_3_voltage": 2.1,
                    "zone_4_voltage": 2.4,
                },
                "soil_percentages": {
                    "zone_1": 45.0,           # Moisture %
                    "zone_2": 40.0,
                    "zone_3": 50.0,
                    "zone_4": 35.0,
                },
            },
            "actuators": {
                "ac_contactor": False,        # 25A Overhead AC Pump
                "zone_1_pump": False,         # 12V DC Pump 1
                "zone_2_pump": False,         # 12V DC Pump 2
                "zone_3_pump": False,         # 12V DC Pump 3
                "zone_4_pump": False,         # 12V DC Pump 4
            },
            "runtime_tracking": {
                "ac_contactor_started_at": None,       # Epoch timestamp when energized
                "zone_pumps_started_at": {
                    "zone_1": None,
                    "zone_2": None,
                    "zone_3": None,
                    "zone_4": None,
                },
                "zone_last_watered_at": {
                    "zone_1": 0.0,
                    "zone_2": 0.0,
                    "zone_3": 0.0,
                    "zone_4": 0.0,
                },
                "zone_cooldown_until": {
                    "zone_1": 0.0,
                    "zone_2": 0.0,
                    "zone_3": 0.0,
                    "zone_4": 0.0,
                },
            },
            "alarms": {},                     # Key-value active alarms
            "failsafe_status": {
                "dry_run_locked": False,
                "tank_high_locked": False,
                "motor_timeout_locked": False,
                "probe_faults": [],
            },
            "system": {
                "mode": "MOCK" if settings.MOCK_HARDWARE else "HARDWARE",
                "uptime_seconds": 0,
                "last_speech": None,
                "watchdog_ok": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

        self._start_time = time.time()

    def get_state(self) -> Dict[str, Any]:
        """Returns a thread-safe snapshot copy of current system state."""
        state = json.loads(json.dumps(self._state))
        state["system"]["uptime_seconds"] = int(time.time() - self._start_time)
        state["system"]["timestamp"] = datetime.now(timezone.utc).isoformat()
        return state

    async def update_sensors(
        self,
        tank_low: bool,
        tank_high: bool,
        reservoir_empty: bool,
        probes: Dict[str, float],
    ) -> None:
        """Update live sensor readings atomically."""
        async with self._lock:
            self._state["sensors"]["tank_low_float"] = tank_low
            self._state["sensors"]["tank_high_float"] = tank_high
            self._state["sensors"]["reservoir_low_float"] = reservoir_empty

            for zone, voltage in probes.items():
                v = round(voltage, 3)
                self._state["sensors"]["soil_probes"][f"{zone}_voltage"] = v
                # Calibration: 3.0V = 0% (Bone dry in air), 1.0V = 100% (Submerged in water)
                # Linear mapping clamped between 0% and 100%
                moisture_pct = max(0.0, min(100.0, round((3.0 - v) / (3.0 - 1.0) * 100.0, 1)))
                self._state["sensors"]["soil_percentages"][zone] = moisture_pct

    async def update_actuator(self, name: str, state: bool) -> None:
        """Set actuator state and update runtime tracking."""
        async with self._lock:
            now = time.time()
            prev_state = self._state["actuators"].get(name, False)
            self._state["actuators"][name] = state

            if name == "ac_contactor":
                if state and not prev_state:
                    self._state["runtime_tracking"]["ac_contactor_started_at"] = now
                elif not state:
                    self._state["runtime_tracking"]["ac_contactor_started_at"] = None

            elif name.startswith("zone_") and name.endswith("_pump"):
                zone_key = name.replace("_pump", "")
                if state and not prev_state:
                    self._state["runtime_tracking"]["zone_pumps_started_at"][zone_key] = now
                elif not state and prev_state:
                    self._state["runtime_tracking"]["zone_pumps_started_at"][zone_key] = None
                    self._state["runtime_tracking"]["zone_last_watered_at"][zone_key] = now
                    # Enforce cooldown lockout
                    lockout = now + settings.ZONE_COOLDOWN_SEC
                    self._state["runtime_tracking"]["zone_cooldown_until"][zone_key] = lockout
                    asyncio.create_task(self.persist_cooldown(zone_key, now, lockout))

    async def set_alarm(self, alarm_key: str, message: str) -> None:
        """Register an active alarm."""
        async with self._lock:
            self._state["alarms"][alarm_key] = message

    async def clear_alarm(self, alarm_key: str) -> None:
        """Clear an active alarm."""
        async with self._lock:
            self._state["alarms"].pop(alarm_key, None)

    async def update_failsafe_status(
        self,
        dry_run: bool,
        tank_high: bool,
        motor_timeout: bool,
        faulty_probes: List[str],
    ) -> None:
        """Updates safety interlock statuses."""
        async with self._lock:
            self._state["failsafe_status"]["dry_run_locked"] = dry_run
            self._state["failsafe_status"]["tank_high_locked"] = tank_high
            self._state["failsafe_status"]["motor_timeout_locked"] = motor_timeout
            self._state["failsafe_status"]["probe_faults"] = faulty_probes

    async def set_last_speech(self, text: str) -> None:
        """Update last voice speech alert."""
        async with self._lock:
            self._state["system"]["last_speech"] = text

    # -------------------------------------------------------------------------
    # SQLite Database Management & Wear Mitigation
    # -------------------------------------------------------------------------

    async def init_db(self) -> None:
        """Initializes database tables with WAL mode and memory PRAGMAs."""
        await asyncio.to_thread(self._init_db_sync)
        # Restore saved cooldown states from SQLite
        await self._restore_cooldowns()

    def _get_connection(self) -> sqlite3.Connection:
        """Creates a managed SQLite connection configured for RK3308 RAM limits."""
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        # Hardware optimization PRAGMAs
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute(f"PRAGMA cache_size = {settings.SQLITE_CACHE_SIZE_KB};")  # ~2MB cache
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db_sync(self) -> None:
        """Synchronous SQLite table schema initialization."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    zone_1_v REAL,
                    zone_2_v REAL,
                    zone_3_v REAL,
                    zone_4_v REAL,
                    tank_low INTEGER,
                    tank_high INTEGER,
                    reservoir_empty INTEGER,
                    ac_contactor INTEGER,
                    zone_1_pump INTEGER,
                    zone_2_pump INTEGER,
                    zone_3_pump INTEGER,
                    zone_4_pump INTEGER,
                    alarms_json TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data_json TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rules (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    rule_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS zone_cooldowns (
                    zone_id TEXT PRIMARY KEY,
                    last_watered_at REAL NOT NULL,
                    lockout_until REAL NOT NULL
                );
                """
            )
            # Create indexes for fast retrieval
            conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry_history(timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON system_events(timestamp);")
            conn.commit()
            logger.info("SQLite initialized successfully at %s with WAL mode.", self.db_path)

    async def log_telemetry_snapshot(self) -> None:
        """Persist current telemetry snapshot in SQLite asynchronously."""
        state = self.get_state()
        sensors = state["sensors"]
        actuators = state["actuators"]
        probes = sensors["soil_probes"]

        def _insert():
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO telemetry_history (
                        timestamp, zone_1_v, zone_2_v, zone_3_v, zone_4_v,
                        tank_low, tank_high, reservoir_empty,
                        ac_contactor, zone_1_pump, zone_2_pump, zone_3_pump, zone_4_pump,
                        alarms_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        datetime.now(timezone.utc).isoformat(),
                        probes.get("zone_1_voltage", 0.0),
                        probes.get("zone_2_voltage", 0.0),
                        probes.get("zone_3_voltage", 0.0),
                        probes.get("zone_4_voltage", 0.0),
                        int(sensors.get("tank_low_float", False)),
                        int(sensors.get("tank_high_float", False)),
                        int(sensors.get("reservoir_low_float", False)),
                        int(actuators.get("ac_contactor", False)),
                        int(actuators.get("zone_1_pump", False)),
                        int(actuators.get("zone_2_pump", False)),
                        int(actuators.get("zone_3_pump", False)),
                        int(actuators.get("zone_4_pump", False)),
                        json.dumps(state.get("alarms", {})),
                    ),
                )
                conn.commit()

        await asyncio.to_thread(_insert)

    async def log_event(self, event_type: str, severity: str, message: str, data: Optional[Dict] = None) -> None:
        """Record critical system events (failsafes, overrides, errors)."""
        data_json = json.dumps(data or {})
        ts = datetime.now(timezone.utc).isoformat()

        def _insert():
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO system_events (timestamp, event_type, severity, message, data_json)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (ts, event_type, severity, message, data_json),
                )
                conn.commit()

        await asyncio.to_thread(_insert)

    async def persist_cooldown(self, zone_id: str, last_watered_at: float, lockout_until: float) -> None:
        """Persists zone cooldown lockout timer."""
        def _upsert():
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO zone_cooldowns (zone_id, last_watered_at, lockout_until)
                    VALUES (?, ?, ?)
                    ON CONFLICT(zone_id) DO UPDATE SET
                        last_watered_at=excluded.last_watered_at,
                        lockout_until=excluded.lockout_until;
                    """,
                    (zone_id, last_watered_at, lockout_until),
                )
                conn.commit()

        await asyncio.to_thread(_upsert)

    async def _restore_cooldowns(self) -> None:
        """Restores cooldowns into memory on system boot."""
        def _read():
            with self._get_connection() as conn:
                cur = conn.execute("SELECT zone_id, last_watered_at, lockout_until FROM zone_cooldowns;")
                return cur.fetchall()

        rows = await asyncio.to_thread(_read)
        now = time.time()
        for row in rows:
            zone_id = row["zone_id"]
            if row["lockout_until"] > now:
                self._state["runtime_tracking"]["zone_last_watered_at"][zone_id] = row["last_watered_at"]
                self._state["runtime_tracking"]["zone_cooldown_until"][zone_id] = row["lockout_until"]
                logger.info("Restored active cooldown for %s until %s", zone_id, row["lockout_until"])

    async def prune_old_telemetry(self) -> None:
        """Periodically cleans up old history to prevent storage exhaustion."""
        def _prune():
            with self._get_connection() as conn:
                conn.execute(
                    """
                    DELETE FROM telemetry_history
                    WHERE timestamp < datetime('now', '-' || ? || ' hours');
                    """,
                    (str(settings.TELEMETRY_RETENTION_HOURS),),
                )
                conn.execute(
                    """
                    DELETE FROM system_events
                    WHERE timestamp < datetime('now', '-7 days');
                    """
                )
                conn.commit()

        await asyncio.to_thread(_prune)


state_manager = StateManager()
