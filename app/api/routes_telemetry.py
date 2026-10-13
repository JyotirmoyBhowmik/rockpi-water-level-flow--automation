"""
Telemetry, Historical Logs, and SQLite Database Backup Endpoints.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from app.alerts.telegram_client import telegram_notifier
from app.config import RUNTIME_DIR, system_config
from app.core.security import security_manager
from app.core.state_manager import state_manager
from app.core.watchdog import health_monitor
from app.hardware.audio_driver import audio_controller
from app.hardware.gateway_driver import garden_gateway
from app.hardware.gpio_driver import head_end_gpio

logger = logging.getLogger("smart_water.api.telemetry")
router = APIRouter(prefix="/api", tags=["telemetry"])


@router.post("/telemetry/ticket")
async def create_telemetry_ticket() -> Dict[str, Any]:
    """
    Issues a short-lived (60s), single-use ticket for the SSE stream (Part 1.3).
    Endpoint is protected by Bearer authentication.
    """
    ticket = security_manager.create_sse_ticket(ttl_seconds=60)
    return {
        "status": "success",
        "ticket": ticket,
        "expires_in_s": 60,
    }


@router.get("/telemetry/live")
async def get_live_telemetry() -> Dict[str, Any]:
    """Returns instant snapshot of live state. Production data only: unread items are null."""
    return state_manager.get_state()


@router.get("/level/consumption")
async def get_level_consumption(
    hours: int = Query(default=24, ge=1, le=168, description="Number of past hours to retrieve"),
) -> Dict[str, Any]:
    """
    Returns household water consumption history (hourly breakdown and daily total)
    derived from continuous tank level monitoring for UI charting.
    """
    history = await state_manager.get_consumption_history(hours=hours)
    return {
        "status": "success",
        "data": history,
    }


@router.get("/telemetry/stream")
async def sse_telemetry_stream(
    request: Request,
    ticket: Optional[str] = Query(default=None, description="Short-lived single-use ticket"),
) -> StreamingResponse:
    """
    Real-time Server-Sent Events stream pushing live state every 1000ms.
    Accepts authentication either via:
      1. Single-use ticket query parameter (?ticket=...) (Part 1.3)
      2. Bearer Authorization header
    """
    auth_header = request.headers.get("Authorization")
    has_bearer = False
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        has_bearer = security_manager.verify_token(token)

    ticket_valid = False
    if ticket:
        ticket_valid = security_manager.consume_sse_ticket(ticket)

    if not has_bearer and not ticket_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid single-use ticket (?ticket=...) or Bearer token required for SSE stream.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        client_ip = request.client.host if request.client else "unknown"
        logger.info("SSE telemetry client connected: %s", client_ip)
        try:
            while True:
                if await request.is_disconnected():
                    break
                state = state_manager.get_state()
                payload = json.dumps(state)
                yield f"event: telemetry\ndata: {payload}\n\n"
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("SSE stream error: %s", e)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/events")
async def get_system_events(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    """Returns historical system events (failsafes, wiring faults, overrides)."""
    def _fetch():
        with state_manager._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT id, timestamp, event_type, severity, message, details_json
                FROM events
                ORDER BY id DESC
                LIMIT ? OFFSET ?;
                """,
                (limit, offset),
            )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                if r.get("details_json"):
                    try:
                        r["details"] = json.loads(r["details_json"])
                    except Exception:
                        r["details"] = {}
            return rows

    items = await asyncio.to_thread(_fetch)
    return {"status": "success", "count": len(items), "data": items}


@router.get("/history")
async def get_readings_history(
    source: Optional[str] = Query(default=None, description="Filter source (e.g. z1_volts)"),
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
    limit: int = Query(default=300, ge=1, le=3000, description="Cap <= 3000 to protect RAM"),
) -> Dict[str, Any]:
    """
    Returns historical downsampled sensor readings with strict pagination.
    """
    def _fetch():
        with state_manager._get_connection() as conn:
            query = "SELECT * FROM readings WHERE 1=1"
            params = []
            if from_date:
                query += " AND timestamp >= ?"
                params.append(from_date)
            if to_date:
                query += " AND timestamp <= ?"
                params.append(to_date)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(query, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    data = await asyncio.to_thread(_fetch)
    return {"status": "success", "count": len(data), "data": data}


@router.get("/summary/daily")
async def get_daily_summary(
    hours: int = Query(default=24, ge=1, le=168, description="Number of past hours for summary"),
) -> Dict[str, Any]:
    """Returns 24h operational summary digest (motor runtime, starts, litres, irrigation per zone, alarms)."""
    from app.engine.daily_summary import daily_summary_service
    data = await daily_summary_service.get_summary_data(hours=hours)
    formatted = daily_summary_service.format_summary_message(data)
    return {
        "status": "success",
        "data": data,
        "message": formatted,
    }


@router.post("/summary/daily/send")
async def dispatch_daily_summary(
    hours: int = Query(default=24, ge=1, le=168, description="Number of past hours for summary"),
) -> Dict[str, Any]:
    """Dispatches 24h summary digest to Telegram and ntfy alerting channels."""
    from app.engine.daily_summary import daily_summary_service
    data = await daily_summary_service.dispatch_daily_summary(hours=hours)
    return {
        "status": "success",
        "message": "Daily summary dispatched to configured notification channels.",
        "data": data,
    }


@router.get("/backup")
async def backup_database():
    """
    Safely exports SQLite database using online 'VACUUM INTO' command (Part 3.2).
    Generates downloadable .db snapshot in /run/smart-water (mode 0600),
    and deletes the temporary file immediately after streaming via BackgroundTask.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_filename = f"smart_water_backup_{timestamp}.db"
    backup_path = RUNTIME_DIR / backup_filename

    def _cleanup(path: Path):
        try:
            if path.exists():
                path.unlink()
                logger.info("Cleaned up temporary backup file %s", path)
        except Exception as e:
            logger.warning("Failed to clean up backup file %s: %s", path, e)

    def _vacuum():
        if backup_path.exists():
            backup_path.unlink()
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with state_manager._get_connection() as conn:
            conn.execute(f"VACUUM INTO '{backup_path}';")
        try:
            os.chmod(backup_path, 0o600)
        except OSError:
            pass

    try:
        await asyncio.to_thread(_vacuum)
        return FileResponse(
            str(backup_path),
            filename=backup_filename,
            media_type="application/vnd.sqlite3",
            background=BackgroundTask(_cleanup, backup_path),
        )
    except Exception as exc:
        logger.error("Database backup failed: %s", exc)
        if backup_path.exists():
            try:
                backup_path.unlink()
            except OSError:
                pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database backup failed: {exc}",
        )


@router.get("/health/detail")
async def get_health_detail() -> Dict[str, Any]:
    """
    Returns granular health metrics across all subsystems (Part 5.2):
      - Watchdog loop ages
      - Hardware presence and link loss
      - Database metrics
    """
    state = state_manager.get_state()
    watchdog_ages = health_monitor.get_beat_ages()
    is_healthy, reason = health_monitor.check_health()

    return {
        "status": "healthy" if is_healthy else "degraded",
        "health_gating_ok": is_healthy,
        "health_failure_reason": reason,
        "watchdog_loop_ages_s": watchdog_ages,
        "hardware": {
            "head_end_ready": head_end_gpio.is_hardware_available,
            "gateway_serial_online": garden_gateway.is_link_online,
            "node_last_seen_ts": garden_gateway.node_last_seen,
            "gateway_last_seen_ts": garden_gateway.gateway_last_seen,
            "failed_sig_count": garden_gateway.failed_sig_count,
            "last_gw_err": garden_gateway.last_gw_err,
        },
        "system_status": state["system_status"],
        "setup_complete": system_config.setup_complete,
        "database": {
            "path": str(state_manager.db_path),
            "exists": state_manager.db_path.exists(),
        },
    }


@router.post("/alerts/test")
async def test_alert_notification() -> Dict[str, Any]:
    """
    Triggers test alert notifications via Telegram and local audio (Part 5.2).
    Protected by Bearer authentication.
    """
    msg = "Test alert from Smart Water Controller. Notification subsystem operational."
    await audio_controller.speak("Test alert: notification system operational.", priority=1)
    await telegram_notifier.send_alert("TEST_ALERT", msg)
    return {
        "status": "success",
        "message": "Test alert dispatched to audio queue and Telegram.",
    }
