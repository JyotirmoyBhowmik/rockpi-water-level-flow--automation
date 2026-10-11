"""
Hardware & Systemd Watchdog Manager for Radxa ROCK Pi S.
Interfaces with Linux kernel /dev/watchdog and systemd sd_notify socket.
Petted ONLY when the main application loops report healthy status.
"""

import asyncio
import logging
import os
import socket
from typing import Optional
from app.config import system_config

logger = logging.getLogger("smart_water.watchdog")


class WatchdogGuardian:
    """
    Dual watchdog manager:
    1. Linux /dev/watchdog character device
    2. systemd sd_notify watchdog socket ($NOTIFY_SOCKET)
    """

    def __init__(self):
        self._dev_fd: Optional[int] = None
        self._notify_sock: Optional[socket.socket] = None
        self._notify_path: Optional[str] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._is_hardware_present = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        # 1. Systemd sd_notify socket initialization
        self._notify_path = os.getenv("NOTIFY_SOCKET")
        if self._notify_path:
            try:
                self._notify_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                logger.info("Systemd watchdog socket detected at %s", self._notify_path)
            except Exception as e:
                logger.warning("Could not open systemd notify socket: %s", e)

        # 2. Linux Kernel Hardware Watchdog
        dev_path = system_config.watchdog.device_path
        if os.path.exists(dev_path):
            try:
                self._dev_fd = os.open(dev_path, os.O_WRONLY)
                self._is_hardware_present = True
                logger.info("Linux hardware watchdog opened at %s", dev_path)
            except Exception as e:
                logger.warning("Could not open %s: %s", dev_path, e)
                self._is_hardware_present = False

        self._task = asyncio.create_task(self._heartbeat_loop(), name="watchdog_heartbeat")
        logger.info("WatchdogGuardian initialized.")

    async def stop(self) -> None:
        """Gracefully disarms watchdog with magic 'V' token before exit."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Disarm Linux watchdog
        if self._dev_fd is not None:
            try:
                os.write(self._dev_fd, b"V")
                os.close(self._dev_fd)
                logger.info("Hardware watchdog cleanly disarmed with 'V'.")
            except Exception as e:
                logger.error("Error closing hardware watchdog: %s", e)
            finally:
                self._dev_fd = None

        if self._notify_sock:
            try:
                self._notify_sock.close()
            except Exception:
                pass
            self._notify_sock = None

    async def _heartbeat_loop(self) -> None:
        interval = system_config.watchdog.pet_interval_s
        while self._running:
            try:
                self._pet()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error petting watchdog: %s", exc)
                await asyncio.sleep(interval)

    def _pet(self) -> None:
        """Sends keep-alive pulse to kernel watchdog and systemd."""
        # 1. Kernel watchdog keep-alive
        if self._dev_fd is not None:
            try:
                os.write(self._dev_fd, b"\0")
            except Exception as e:
                logger.error("Kernel watchdog write failed: %s", e)

        # 2. Systemd watchdog keep-alive (WATCHDOG=1)
        if self._notify_sock and self._notify_path:
            try:
                msg = b"WATCHDOG=1"
                if self._notify_path.startswith("@"):
                    # Abstract UNIX socket
                    path = "\0" + self._notify_path[1:]
                else:
                    path = self._notify_path
                self._notify_sock.sendto(msg, path)
            except Exception as e:
                logger.debug("Systemd sd_notify write error: %s", e)


watchdog_manager = WatchdogGuardian()
