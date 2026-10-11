"""
Telemetry, Historical Logs, and SQLite Database Backup Endpoints.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, AsyncGenerator, Dict, Optional
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from app.config import system_config
from app.core.state_manager import state_manager

logger = logging.getLogger("smart_water.api.telemetry")
router = APIRouter(prefix="/api", tags=["telemetry"])


@router.get("/telemetry/live")
async def get_live_telemetry() -> Dict[str, Any]:
    """Returns instant snapshot of live state. No fake data: unread items are null."""
    return state_manager.get_state()


@router.get("/telemetry/stream")
async def sse_telemetry_stream(request: Request) -> StreamingResponse:
    """
    Real-time Server-Sent Events stream pushing live state every 1000ms.
    Sub-100KB RAM footprint.
    """
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
    limit: int = Query(default=100, ge=1, le=500, description="Hard cap <= 500 to protect RAM"),
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


@router.get("/backup")
async def backup_database():
    """
    Safely exports SQLite database using online 'VACUUM INTO' command.
    Generates downloadable .db snapshot.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    temp_dir = tempfile.gettempdir()
    backup_filename = f"smart_water_backup_{timestamp}.db"
    backup_path = os.path.join(temp_dir, backup_filename)

    def _vacuum():
        if os.path.exists(backup_path):
            os.remove(backup_path)
        with state_manager._get_connection() as conn:
            # Atomic online vacuum into destination file
            conn.execute(f"VACUUM INTO '{backup_path}';")

    try:
        await asyncio.to_thread(_vacuum)
        return FileResponse(
            backup_path,
            filename=backup_filename,
            media_type="application/vnd.sqlite3",
            background=None,
        )
    except Exception as exc:
        logger.error("Database backup failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database backup failed: {exc}",
        )
