"""
Linux Kernel Hardware Watchdog Driver for Radxa ROCK Pi S (/dev/watchdog).
Ensures unrecoverable OS or process lockups result in automatic hardware reboot.
"""

import asyncio
import logging
import os
from typing import Optional
from app.config import settings

logger = logging.getLogger("smart_water.watchdog")


class WatchdogManager:
    """
    Manages periodic petting of Linux /dev/watchdog.
    Falls back cleanly to mock emulation in sandbox/development mode.
    """

    def __init__(
        self,
        dev_path: str = settings.WATCHDOG_DEV_PATH,
        pet_interval: int = settings.WATCHDOG_PET_INTERVAL_SEC,
        is_mock: bool = settings.MOCK_HARDWARE,
    ):
        self.dev_path = dev_path
        self.pet_interval = pet_interval
        self.is_mock = is_mock or not os.path.exists(dev_path)
        self._fd: Optional[int] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._pet_count = 0

    async def start(self) -> None:
        """Opens watchdog device and begins heartbeat loop."""
        if self._running:
            return

        if not self.is_mock:
            try:
                # Open watchdog character device in write-only mode
                self._fd = os.open(self.dev_path, os.O_WRONLY)
                logger.info("Kernel hardware watchdog opened at %s.", self.dev_path)
            except Exception as e:
                logger.warning(
                    "Unable to open %s (%s). Falling back to virtualized watchdog.",
                    self.dev_path,
                    e,
                )
                self.is_mock = True
        else:
            logger.info("Watchdog running in virtualized mock mode.")

        self._running = True
        self._task = asyncio.create_task(self._pet_loop(), name="watchdog_pet_loop")

    async def stop(self) -> None:
        """
        Gracefully disables the watchdog before shutdown.
        Linux watchdog convention: write 'V' magic character before close to prevent reboot.
        """
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._fd is not None:
            try:
                # Send 'V' to inform kernel watchdog this is a clean, intentional stop
                os.write(self._fd, b"V")
                os.close(self._fd)
                logger.info("Watchdog cleanly disarmed with magic 'V' token.")
            except Exception as e:
                logger.error("Error closing watchdog fd: %s", e)
            finally:
                self._fd = None

    async def _pet_loop(self) -> None:
        """Heartbeat loop that periodically pets the watchdog."""
        while self._running:
            try:
                self._pet()
                self._pet_count += 1
                await asyncio.sleep(self.pet_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Failed to pet watchdog: %s", e)
                await asyncio.sleep(self.pet_interval)

    def _pet(self) -> None:
        """Performs a single keep-alive write."""
        if not self.is_mock and self._fd is not None:
            os.write(self._fd, b"\0")
        else:
            # Mock mode heartbeat
            if self._pet_count % 20 == 0:
                logger.debug("Mock watchdog pet tick #%d", self._pet_count)

    @property
    def pet_count(self) -> int:
        return self._pet_count


watchdog_manager = WatchdogManager()
