"""
FastAPI Server Factory with Bearer Token Security, Rate Limiting, and Safe Shutdown.
Ensures outputs are forced OFF on any exit path or unhandled exception.
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
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_config import router as config_router
from app.api.routes_control import router as control_router
from app.api.routes_rules import router as rules_router
from app.api.routes_telemetry import router as telemetry_router
from app.alerts.telegram_client import telegram_notifier
from app.config import system_config
from app.core.event_bus import bus
from app.core.security import security_manager
from app.core.state_manager import state_manager
from app.core.watchdog import watchdog_manager
from app.engine.rules_evaluator import rules_evaluator
from app.engine.scheduler import scheduler
from app.hardware.audio_driver import audio_controller
from app.hardware.gateway_driver import garden_gateway
from app.hardware.gpio_driver import head_end_gpio


# -----------------------------------------------------------------------------
# Structured JSON Logging
# -----------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
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
root_logger.setLevel(logging.INFO)
log_handler = logging.StreamHandler(sys.stdout)
log_handler.setFormatter(JsonFormatter())
root_logger.handlers = [log_handler]

logger = logging.getLogger("smart_water.server")


# -----------------------------------------------------------------------------
# Lifespan Management (Outputs forced OFF on startup and shutdown)
# -----------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Initializing Smart Water System on RK3308...")

    # 1. Initialize SQLite tables (zero-seeded data)
    await state_manager.init_db()

    # 2. Check/Generate secure API token
    security_manager.get_or_create_token()

    # 3. Start Core Services
    await bus.start()
    await audio_controller.start()
    await telegram_notifier.start()
    await watchdog_manager.start()

    # 4. Initialize Hardware (Outputs start forced OFF)
    await head_end_gpio.setup()
    await garden_gateway.start()

    # 5. Load user rules and start scheduler
    await rules_evaluator.load_rules_from_db()
    await scheduler.start()

    logger.info(
        "Smart Water Controller online. Listen=%s:%d. Hardware Ready=%s",
        system_config.network.bind_address,
        system_config.network.port,
        head_end_gpio.is_hardware_available,
    )

    yield

    logger.info("Shutting down. FORCING ALL OUTPUTS OFF...")
    # Safe shutdown: force all outputs OFF unconditionally
    try:
        await head_end_gpio.force_all_outputs_off()
        await garden_gateway.send_stop_all()
    except Exception as e:
        logger.error("Error killing outputs during shutdown: %s", e)

    await scheduler.stop()
    await head_end_gpio.cleanup()
    await garden_gateway.stop()
    await watchdog_manager.stop()
    await telegram_notifier.stop()
    await audio_controller.stop()
    await bus.stop()
    logger.info("All subsystems safely de-energized.")


# -----------------------------------------------------------------------------
# Application Factory
# -----------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="Radxa Smart Water Controller",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # In-memory sliding rate limiter: ip -> list of timestamps
    rate_limit_history: dict[str, list[float]] = {}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------------------------
    # Correlation ID & Bearer Token Authentication Middleware
    # -------------------------------------------------------------------------
    @app.middleware("http")
    async def security_and_tracing_middleware(request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id

        # Public endpoints that bypass authentication
        public_prefixes = (
            "/static",
            "/healthz",
            "/favicon.ico",
        )
        is_public = (
            request.url.path == "/"
            or any(request.url.path.startswith(p) for p in public_prefixes)
            or (not system_config.setup_complete and request.url.path.startswith("/api/setup"))
            or (not system_config.setup_complete and request.url.path == "/api/telemetry/live")
            or (not system_config.setup_complete and request.url.path == "/api/telemetry/stream")
            or (not system_config.setup_complete and request.url.path == "/api/gpioinfo")
        )

        if not is_public and request.url.path.startswith("/api"):
            auth_header = request.headers.get("Authorization")
            token = None
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()

            if not security_manager.verify_token(token):
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status_code": 401,
                        "error_code": "UNAUTHORIZED",
                        "correlation_id": correlation_id,
                        "message": "Invalid or missing Bearer token.",
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Rate Limiting on control endpoints (Part 2.4)
            if request.method in ("POST", "PUT", "DELETE"):
                client_ip = request.client.host if request.client else "unknown"
                now = time.time()
                history = rate_limit_history.setdefault(client_ip, [])
                # Prune older than 60s
                rate_limit_history[client_ip] = [ts for ts in history if now - ts < 60.0]
                if len(rate_limit_history[client_ip]) >= system_config.security.rate_limit_per_minute:
                    return JSONResponse(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        content={
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "status_code": 429,
                            "error_code": "RATE_LIMITED",
                            "correlation_id": correlation_id,
                            "message": f"Rate limit exceeded (max {system_config.security.rate_limit_per_minute} actions/min).",
                        },
                    )
                rate_limit_history[client_ip].append(now)

        response: Response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response

    # Global Exception Handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        cid = getattr(request.state, "correlation_id", "unknown")
        logger.error("Unhandled error on %s: %s", request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status_code": 500,
                "error_code": "INTERNAL_ERROR",
                "correlation_id": cid,
                "message": "Internal hardware or system failure.",
            },
        )

    # Mount API routers
    app.include_router(telemetry_router)
    app.include_router(control_router)
    app.include_router(rules_router)
    app.include_router(config_router)

    # Static assets and template dashboard
    ui_dir = Path(__file__).resolve().parent.parent / "ui"
    static_dir = ui_dir / "static"
    templates_dir = ui_dir / "templates"

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=FileResponse)
    async def serve_dashboard():
        index_file = templates_dir / "index.html"
        return FileResponse(str(index_file))

    @app.get("/healthz")
    async def health_check():
        state = state_manager.get_state()
        return {
            "status": "healthy",
            "version": "1.0.0",
            "system_status": state["system_status"],
            "hardware_ready": state["hardware_ready"],
            "uptime_seconds": state["uptime_seconds"],
            "setup_complete": system_config.setup_complete,
        }

    return app


app = create_app()
