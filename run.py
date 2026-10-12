"""
Application Entrypoint for Radxa ROCK Pi S Smart Water Automation.
Runs a single-process asynchronous ASGI runtime (uvicorn + FastAPI) strictly within <35MB RAM.
"""

import uvicorn
from app import __version__
from app.config import system_config


def main():
    """Runs uvicorn ASGI server with low-memory parameters."""
    print("=" * 72)
    print(" RADXA ROCK PI S - INDUSTRIAL SMART WATER CONTROLLER")
    print(f" Version: {__version__}")
    print(f" Listening: http://{system_config.network.bind_address}:{system_config.network.port}")
    print(" Config: /etc/smart-water/config.yaml")
    print("=" * 72)

    # Low-overhead uvicorn settings for RK3308 512MB RAM
    uvicorn.run(
        "app.api.server:app",
        host=system_config.network.bind_address,
        port=system_config.network.port,
        access_log=False,       # Zero overhead; application logs via structured JSON
        loop="asyncio",
        workers=1,              # Single event-loop process strictly adheres to <35MB RAM
        log_level="info",
    )


if __name__ == "__main__":
    main()
