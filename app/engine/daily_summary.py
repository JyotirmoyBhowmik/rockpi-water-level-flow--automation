"""
Daily Summary Notification Service for Radxa ROCK Pi S Smart Water Controller.
Compiles a 24-hour operational digest:
  - Motor runtime, starts count, estimated litres pumped, electrical energy (kWh)
  - Irrigation delivered per zone (runs count, duration, litres)
  - Safety alarms, interlock trips, and wiring faults triggered
Dispatches report via Telegram and ntfy.sh.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict

from app.alerts.ntfy_client import ntfy_notifier
from app.alerts.telegram_client import telegram_notifier
from app.config import system_config
from app.core.state_manager import state_manager

logger = logging.getLogger("smart_water.daily_summary")


def format_duration_human(seconds: int) -> str:
    """Formats seconds into human-readable e.g. 1h 12m 30s or 4m 15s."""
    if seconds <= 0:
        return "0s"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    parts = []
    if h > 0:
        parts.append(f"{h}h")
    if m > 0:
        parts.append(f"{m}m")
    if s > 0 or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


class DailySummaryService:
    """Compiles 24-hour summary metrics and dispatches alerts."""

    async def get_summary_data(self, hours: int = 24) -> Dict[str, Any]:
        """Queries database and state to compute 24h operational recap."""
        window_start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        live_state = state_manager.get_state()

        def _query() -> Dict[str, Any]:
            with state_manager._get_connection() as conn:
                # 1. Motor statistics
                try:
                    motor_cur = conn.execute(
                        """
                        SELECT COUNT(*), COALESCE(SUM(duration_s), 0), COALESCE(SUM(energy_kwh), 0.0)
                        FROM runs
                        WHERE target = 'motor' AND started_at >= ?;
                        """,
                        (window_start,),
                    )
                    m_starts, m_runtime_s, m_energy = motor_cur.fetchone()
                except Exception:
                    motor_cur = conn.execute(
                        """
                        SELECT COUNT(*), COALESCE(SUM(duration_s), 0)
                        FROM runs
                        WHERE target = 'motor' AND started_at >= ?;
                        """,
                        (window_start,),
                    )
                    m_starts, m_runtime_s = motor_cur.fetchone()
                    m_energy = 0.0

                # 2. Per-zone statistics
                zones_stats: Dict[str, Dict[str, Any]] = {}
                for i in range(1, 5):
                    z_key = f"zone_{i}"
                    z_cur = conn.execute(
                        """
                        SELECT COUNT(*), COALESCE(SUM(duration_s), 0)
                        FROM runs
                        WHERE target = ? AND started_at >= ?;
                        """,
                        (z_key, window_start),
                    )
                    z_runs, z_duration = z_cur.fetchone()

                    # Query flow pulses logged in readings (Zone 1 has dedicated flow meter)
                    z_pulses = 0
                    if i == 1:
                        try:
                            pulses_cur = conn.execute(
                                """
                                SELECT COALESCE(SUM(z1_flow), 0)
                                FROM readings
                                WHERE timestamp >= ?;
                                """,
                                (window_start,),
                            )
                            z_pulses = pulses_cur.fetchone()[0]
                        except Exception:
                            z_pulses = 0

                    z_cfg = system_config.zones.get(z_key)
                    zone_label = z_cfg.name if (z_cfg and z_cfg.name) else f"Zone {i}"
                    k_factor = z_cfg.flow_k_factor if z_cfg else 7.5
                    pulses_per_l = k_factor * 60.0
                    flow_litres = round(z_pulses / max(1.0, pulses_per_l), 1)

                    # Fall back to live daily_litres if higher
                    live_litres = live_state.get("garden", {}).get("zones", {}).get(z_key, {}).get("daily_litres", 0.0)
                    total_litres = max(flow_litres, live_litres)

                    zones_stats[z_key] = {
                        "name": zone_label,
                        "runs": z_runs,
                        "duration_s": z_duration,
                        "litres": round(total_litres, 1),
                    }

                # 3. Alarms and critical events
                events_cur = conn.execute(
                    """
                    SELECT event_type, severity, message, timestamp
                    FROM events
                    WHERE severity IN ('WARNING', 'CRITICAL', 'ALARM') AND timestamp >= ?
                    ORDER BY id DESC;
                    """,
                    (window_start,),
                )
                raw_events = [dict(r) for r in events_cur.fetchall()]

                return {
                    "motor": {
                        "starts": m_starts,
                        "runtime_s": m_runtime_s,
                        "energy_kwh": round(m_energy, 3),
                    },
                    "zones": zones_stats,
                    "alarms": raw_events,
                }

        db_data = await asyncio.to_thread(_query)

        # Estimate motor litres pumped:
        # If continuous level sensor has consumption or fill rate, calculate accordingly
        runtime_s = db_data["motor"]["runtime_s"]
        ls_state = live_state.get("level_sensor", {})
        est_fill_rate_lpm = ls_state.get("fill_rate_lpm") or system_config.level_sensor.min_fill_rate_lpm or 15.0
        est_litres = round((runtime_s / 60.0) * est_fill_rate_lpm, 1)

        db_data["motor"]["est_litres"] = est_litres
        db_data["system_status"] = live_state.get("system_status", "UNKNOWN")
        db_data["uptime_seconds"] = live_state.get("uptime_seconds", 0)
        db_data["generated_at"] = datetime.now(timezone.utc).isoformat()
        db_data["period_hours"] = hours

        return db_data

    def format_summary_message(self, data: Dict[str, Any]) -> str:
        """Formats summary dictionary into clean notification text."""
        m = data["motor"]
        zones = data["zones"]
        alarms = data["alarms"]

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        runtime_str = format_duration_human(m["runtime_s"])

        lines = [
            "📊 [SMART WATER DAILY RECAP]",
            f"Period: Past {data.get('period_hours', 24)}h ending {now_str}",
            "----------------------------------------",
            "🚰 Motor Operations:",
            f"  • Starts: {m['starts']}",
            f"  • Total Runtime: {runtime_str} ({m['runtime_s']}s)",
            f"  • Est. Pumped: ~{m['est_litres']} L",
            f"  • Energy Used: {m['energy_kwh']} kWh",
            "",
            "🌱 Irrigation Per Zone:",
        ]

        for z_key, z in sorted(zones.items()):
            dur_str = format_duration_human(z["duration_s"])
            lines.append(f"  • {z['name']}: {z['runs']} runs ({dur_str}), {z['litres']} L")

        lines.append("")
        lines.append("⚠️ Safety Alarms & Trips:")
        if not alarms:
            lines.append("  • None (All interlocks & hardware healthy)")
        else:
            lines.append(f"  • Total Alarms Triggered: {len(alarms)}")
            for al in alarms[:5]:  # show up to 5 latest
                lines.append(f"    - [{al.get('event_type')}] {al.get('message', '')}")
            if len(alarms) > 5:
                lines.append(f"    ... and {len(alarms) - 5} more")

        lines.append("----------------------------------------")
        uptime_str = format_duration_human(data.get("uptime_seconds", 0))
        lines.append(f"Status: {data.get('system_status')} | Controller Uptime: {uptime_str}")

        return "\n".join(lines)

    async def dispatch_daily_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Compiles and dispatches daily summary to all configured alerting channels."""
        data = await self.get_summary_data(hours=hours)
        msg = self.format_summary_message(data)

        # Dispatch to Telegram and ntfy
        await telegram_notifier.send_alert("DAILY_SUMMARY", msg)
        await ntfy_notifier.send_alert("DAILY_SUMMARY", msg, priority=2, tags=["chart_with_upwards_trend", "clipboard"])

        logger.info("Daily summary digest dispatched successfully.")
        return data


daily_summary_service = DailySummaryService()
