"""
FastAPI Application Server Factory & Enterprise Production Middleware Pipeline.
Configured for <35MB RAM, structured logging, correlation IDs, and unified static serving.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
import time
import uuid
from typing import AsyncGenerator
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_control import router as control_router
from app.api.routes_rules import router as rules_router
from app.api.routes_telemetry import router as telemetry_router
from app.config import settings
from app.core.event_bus import bus
from app.core.state_manager import state_manager
from app.core.watchdog import watchdog_manager
from app.engine.rules_evaluator import rules_evaluator
from app.engine.scheduler import scheduler
from app.hardware.adc_driver import adc_controller
from app.hardware.audio_driver import audio_controller
from app.hardware.gpio_driver import gpio_controller

# -----------------------------------------------------------------------------
# Enterprise Structured JSON Logging (Rule 4.1)
# -----------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """Outputs standardized JSON logs with correlation IDs for headless journald/loki."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": "smart-water",
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "correlation_id"):
            log_obj["correlation_id"] = record.correlation_id
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)
log_handler = logging.StreamHandler(sys.stdout)
log_handler.setFormatter(JsonFormatter())
root_logger.handlers = [log_handler]

logger = logging.getLogger("smart_water.server")


# -----------------------------------------------------------------------------
# Lifespan Management
# -----------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Deterministically initializes hardware drivers and background services.
    Ensures safe shutdown and watchdog disarmament.
    """
    logger.info("Initializing Radxa Smart Water System on RK3308...")
    await state_manager.init_db()
    await bus.start()
    await audio_controller.start()
    await watchdog_manager.start()
    await gpio_controller.setup()
    await adc_controller.setup()
    await rules_evaluator.load_rules_from_db()
    await scheduler.start()

    logger.info(
        "Radxa Smart Water Controller online. MOCK_HARDWARE=%s, Listen=%s:%d",
        settings.MOCK_HARDWARE,
        settings.HOST,
        settings.PORT,
    )
    # Auditory boot confirmation
    await audio_controller.speak("Smart water system initialized. Status normal.")

    yield

    logger.info("Initiating graceful shutdown sequence...")
    await scheduler.stop()
    await gpio_controller.cleanup()
    await adc_controller.cleanup()
    await watchdog_manager.stop()
    await audio_controller.stop()
    await bus.stop()
    logger.info("All subsystems shutdown cleanly.")


# -----------------------------------------------------------------------------
# Application Factory
# -----------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Builds and configures the FastAPI application instance."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------------------------
    # Correlation ID & Tracing Middleware (Rule 4.2)
    # -------------------------------------------------------------------------
    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        start_time = time.time()

        response: Response = await call_next(request)

        duration_ms = (time.time() - start_time) * 1000.0
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
        return response

    # -------------------------------------------------------------------------
    # Global Exception Handler (Rule 2.4 - Sanitized Error Responses)
    # -------------------------------------------------------------------------
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        correlation_id = getattr(request.state, "correlation_id", "unknown")
        logger.error(
            "Unhandled server error on %s %s: %s",
            request.method,
            request.url.path,
            exc,
            exc_info=True,
            extra={"correlation_id": correlation_id},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status_code": 500,
                "error_code": "INTERNAL_SERVER_ERROR",
                "correlation_id": correlation_id,
                "message": "An internal hardware or system error occurred. Please consult system logs.",
            },
        )

    # Mount API routers
    app.include_router(telemetry_router)
    app.include_router(control_router)
    app.include_router(rules_router)

    # Static Assets & Web Dashboard
    ui_dir = Path(__file__).resolve().parent.parent / "ui"
    static_dir = ui_dir / "static"
    templates_dir = ui_dir / "templates"

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=FileResponse)
    async def serve_dashboard():
        index_file = templates_dir / "index.html"
        if not index_file.exists():
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"message": "Dashboard index.html not found"},
            )
        return FileResponse(str(index_file))

    @app.get("/healthz")
    async def health_check():
        state = state_manager.get_state()
        return {
            "status": "healthy",
            "uptime_seconds": state["system"]["uptime_seconds"],
            "mock_mode": settings.MOCK_HARDWARE,
            "version": settings.APP_VERSION,
        }

    return app


app = create_app()
