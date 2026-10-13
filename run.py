import os
import sys
import uvicorn
from app import __version__
from app.config import system_config


def set_process_memory_limit(max_mb: int) -> None:
    """Enforces an OS-level virtual memory ceiling using POSIX setrlimit.

    If the process attempts to allocate beyond max_mb, malloc returns NULL
    and Python raises MemoryError rather than starving system RAM.
    Graceful no-op on non-POSIX / Windows environments.
    """
    if sys.platform == "win32":
        return
    try:
        import resource
        if hasattr(resource, "RLIMIT_AS"):
            limit_bytes = int(max_mb * 1024 * 1024)
            soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            target_hard = hard if hard != resource.RLIM_INFINITY and hard < limit_bytes else limit_bytes
            resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, target_hard))
            print(f"[MEMORY] Hard virtual memory ceiling enforced: {max_mb} MB (RLIMIT_AS)")
    except (ImportError, AttributeError, ValueError, OSError) as exc:
        print(f"[MEMORY] Notice: Could not set RLIMIT_AS ({exc})")


def main():
    """Runs uvicorn ASGI server with low-memory parameters."""
    ram_limit_env = os.getenv("SMART_WATER_MAX_RAM_MB")
    if ram_limit_env:
        try:
            set_process_memory_limit(int(ram_limit_env))
        except ValueError:
            pass

    print("=" * 72)
    print(" RADXA ROCK PI S - INDUSTRIAL SMART WATER CONTROLLER")
    print(f" Version: {__version__}")
    print(f" Listening: http://{system_config.network.bind_address}:{system_config.network.port}")
    print(" Config: /etc/smart-water/config.yaml")
    if ram_limit_env:
        print(f" Memory Limit: {ram_limit_env} MB")
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

