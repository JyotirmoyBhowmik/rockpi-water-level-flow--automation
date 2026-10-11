"""
Telemetry Endpoints: Live State, Historical Time-Series, and Real-Time SSE Streams.
Optimized for low-overhead client push directly over Headscale/Tailscale mesh.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from app.config import settings
from app.core.state_manager import state_manager

logger = logging.getLogger("smart_water.api.telemetry")
router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


@router.get("/live", response_model=Dict[str, Any])
async def get_live_telemetry() -> Dict[str, Any]:
    """Returns instant snapshot of in-memory system state."""
    return state_manager.get_state()


@router.get("/history")
async def get_telemetry_history(
    limit: int = Query(default=100, ge=1, le=500, description="Pagination item limit"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> Dict[str, Any]:
    """
    Returns bounded historical telemetry records from SQLite.
    Guarantees pagination controls to protect RK3308 RAM.
    """
    def _query():
        with state_manager._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT id, timestamp, zone_1_v, zone_2_v, zone_3_v, zone_4_v,
                       tank_low, tank_high, reservoir_empty,
                       ac_contactor, zone_1_pump, zone_2_pump, zone_3_pump, zone_4_pump,
                       alarms_json
                FROM telemetry_history
                ORDER BY id DESC
                LIMIT ? OFFSET ?;
                """,
                (limit, offset),
            )
            rows = [dict(row) for row in cur.fetchall()]
            for r in rows:
                if r.get("alarms_json"):
                    try:
                        r["alarms"] = json.loads(r["alarms_json"])
                    except Exception:
                        r["alarms"] = {}
            return rows

    items = await asyncio.to_thread(_query)
    return {
        "status": "success",
        "count": len(items),
        "limit": limit,
        "offset": offset,
        "data": items,
    }


@router.get("/events")
async def get_system_events(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    """Returns recent system events and failsafe triggers."""
    def _query():
        with state_manager._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT id, timestamp, event_type, severity, message, data_json
                FROM system_events
                ORDER BY id DESC
                LIMIT ? OFFSET ?;
                """,
                (limit, offset),
            )
            rows = [dict(row) for row in cur.fetchall()]
            for r in rows:
                if r.get("data_json"):
                    try:
                        r["data"] = json.loads(r["data_json"])
                    except Exception:
                        r["data"] = {}
            return rows

    items = await asyncio.to_thread(_query)
    return {
        "status": "success",
        "count": len(items),
        "data": items,
    }


@router.get("/stream")
async def sse_telemetry_stream(request: Request) -> StreamingResponse:
    """
    Server-Sent Events (SSE) stream pushing live telemetry every 1000ms.
    Significantly lower RAM footprint than WebSockets on 512MB SBC.
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        client_ip = request.client.host if request.client else "unknown"
        logger.info("Client connected to SSE stream: %s", client_ip)
        try:
            while True:
                # Disconnect check
                if await request.is_disconnected():
                    logger.info("SSE client disconnected: %s", client_ip)
                    break

                # Get state snapshot
                state = state_manager.get_state()
                payload = json.dumps(state)

                # Format SSE data frame
                yield f"event: telemetry\ndata: {payload}\n\n"

                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("SSE connection cleanly closed for %s", client_ip)
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
