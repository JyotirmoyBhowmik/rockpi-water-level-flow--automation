"""
Application Entrypoint for Radxa ROCK Pi S Smart Water Automation.
Runs a single-process asynchronous ASGI runtime (uvicorn + FastAPI) strictly within <35MB RAM.
"""

import os
import sys
import uvicorn
from app.config import settings

def main():
    """Runs uvicorn ASGI server with low-memory parameters."""
    print("=" * 72)
    print(" RADXA ROCK PI S - INDUSTRIAL IOT WATER CONTROLLER")
    print(f" Version: {settings.APP_VERSION}")
    print(f" Mode: {'MOCK (Virtual Hardware)' if settings.MOCK_HARDWARE else 'PHYSICAL (RK3308 Linux /dev/gpiochip1, /dev/i2c-7)'}")
    print(f" Listening: http://{settings.HOST}:{settings.PORT}")
    print("=" * 72)

    # Low-overhead uvicorn settings for RK3308 512MB RAM
    uvicorn.run(
        "app.api.server:app",
        host=settings.HOST,
        port=settings.PORT,
        access_log=False,       # Zero overhead; application logs via structured JSON
        loop="asyncio",
        workers=1,              # Single event-loop process strictly adheres to <35MB RAM
        log_level="info" if not settings.DEBUG else "debug",
    )

if __name__ == "__main__":
    main()
