"""
Health-Gated Watchdog Manager for Radxa ROCK Pi S Controller.
Monitors critical application loops (head-end poll, rules/auto loop, gateway reader).
Pets Linux /dev/watchdog and systemd sd_notify ONLY when all critical loops are fresh.
If a critical task dies, forces relay OFF and terminates non-zero so systemd restarts it.
"""

import asyncio
import logging
import os
import socket
import sys
import time
from typing import Dict, Optional, Tuple
from app.config import system_config
from app.hardware.gpio_driver import head_end_gpio
from app.hardware.rtc import get_monotonic_now

logger = logging.getLogger("smart_water.watchdog")


class HealthMonitor:
    """Tracks heartbeat timestamps from critical loops."""

    def __init__(self):
        self._beats: Dict[str, float] = {}
        # Maximum allowed silent intervals
        self._thresholds: Dict[str, float] = {
            "head_end_poll": 3.0,     # Must run every ~1s (max 3s)
            "rules_auto_loop": 15.0,  # Must run every ~5s (max 15s)
            "gateway_reader": 15.0,   # Must cycle at least every 15s
        }
        self._enabled = False

    def enable(self) -> None:
        """Enables gating once all critical tasks have started."""
        now = get_monotonic_now()
        for name in self._thresholds:
            self._beats[name] = now
        self._enabled = True
        logger.info("HealthMonitor active with thresholds: %s", self._thresholds)

    def disable(self) -> None:
        self._enabled = False

    def beat(self, name: str) -> None:
        """Records healthy progress from a critical task."""
        self._beats[name] = get_monotonic_now()

    def get_beat_ages(self) -> Dict[str, float]:
        """Returns age in seconds of each monitored loop."""
        now = get_monotonic_now()
        return {name: round(now - self._beats.get(name, 0.0), 2) for name in self._thresholds}

    def check_health(self) -> Tuple[bool, Optional[str]]:
        """
        Verifies all registered critical loops have reported within their max age.
        Returns (is_healthy, failure_reason).
        """
        if not self._enabled:
            return True, None

        now = get_monotonic_now()
        for name, max_age in self._thresholds.items():
            last = self._beats.get(name)
            if last is None:
                return False, f"Critical loop '{name}' has never reported a heartbeat"
            age = now - last
            if age > max_age:
                return False, f"Critical loop '{name}' stalled: last beat {age:.2f}s ago (> {max_age}s limit)"

        return True, None


health_monitor = HealthMonitor()


class WatchdogGuardian:
    """
    Health-gated watchdog manager:
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
                logger.info("Systemd notify socket detected at %s", self._notify_path)
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
        """Gracefully disarms watchdog with magic 'V' token before intentional exit."""
        self._running = False
        health_monitor.disable()

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

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
                # 1. Health gating check
                is_healthy, reason = health_monitor.check_health()
                if not is_healthy:
                    logger.critical(
                        "WATCHDOG HEALTH GATING FAILURE: %s. "
                        "FORCING RELAY OFF AND EXITING FOR SYSTEMD RESTART!",
                        reason,
                    )
                    # Unconditionally de-energize relay
                    await head_end_gpio.force_all_outputs_off()
                    # Exit non-zero so systemd triggers Restart=always
                    sys.exit(2)

                # 2. Pet watchdog handles
                self._pet()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in watchdog loop: %s", exc)
                await asyncio.sleep(interval)

    def _pet(self) -> None:
        """Sends keep-alive pulse to kernel watchdog and systemd."""
        if self._dev_fd is not None:
            try:
                os.write(self._dev_fd, b"\0")
            except Exception as e:
                logger.error("Kernel watchdog write failed: %s", e)

        if self._notify_sock and self._notify_path:
            try:
                msg = b"WATCHDOG=1"
                path = ("\0" + self._notify_path[1:]) if self._notify_path.startswith("@") else self._notify_path
                self._notify_sock.sendto(msg, path)
            except Exception as e:
                logger.debug("Systemd sd_notify write error: %s", e)


watchdog_manager = WatchdogGuardian()
