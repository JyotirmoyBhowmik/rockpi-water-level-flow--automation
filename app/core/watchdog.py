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
from typing import Dict, Optional, Tuple
from app.config import system_config
from app.hardware.gpio_driver import head_end_gpio
from app.hardware.rtc import get_monotonic_now

logger = logging.getLogger("smart_water.watchdog")


class HealthMonitor:
    """Tracks heartbeat timestamps from critical loops."""

    def __init__(self) -> None:
        self._beats: Dict[str, float] = {}
        self._thresholds: Dict[str, float] = {}
        self._enabled = False
        self._init_thresholds()

    def _init_thresholds(self) -> None:
        """Configures monitored loop thresholds, excluding gateway if disabled."""
        self._thresholds = {
            "head_end_poll": 3.0,     # Must run every ~1s (max 3s)
            "rules_auto_loop": 15.0,  # Must run every ~5s (max 15s)
        }
        if system_config.garden_gateway.enabled:
            self._thresholds["gateway_reader"] = 15.0

    def enable(self) -> None:
        """Enables gating once all critical tasks have started."""
        self._init_thresholds()
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

    def __init__(self) -> None:
        self._dev_fd: Optional[int] = None
        self._notify_sock: Optional[socket.socket] = None
        self._notify_path: Optional[str] = None
        self._running = False
        self._task: Optional[asyncio.Task[None]] = None
        self._is_hardware_present = False
        self.is_stall_exit: bool = False
        self._ready_sent: bool = False
        self._exit_func = os._exit

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.is_stall_exit = False

        # 1. Systemd sd_notify socket initialization
        self._notify_path = os.getenv("NOTIFY_SOCKET")
        af_unix = getattr(socket, "AF_UNIX", None)
        if self._notify_path and af_unix is not None:
            try:
                self._notify_sock = socket.socket(af_unix, socket.SOCK_DGRAM)
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

    async def stop(self, disarm: bool = True) -> None:
        """Gracefully disarms watchdog with magic 'V' token before intentional exit.
        If disarm is False or is_stall_exit is True, skips writing 'V' so watchdog hardware remains armed.
        """
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
                if disarm and not self.is_stall_exit:
                    os.write(self._dev_fd, b"V")
                    logger.info("Hardware watchdog cleanly disarmed with 'V'.")
                else:
                    logger.warning("Hardware watchdog closed WITHOUT disarm ('V' skipped due to stall/unclean exit).")
                os.close(self._dev_fd)
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
                    self.is_stall_exit = True
                    logger.critical(
                        "WATCHDOG HEALTH GATING FAILURE: %s. "
                        "FORCING RELAY OFF AND EXITING FOR SYSTEMD RESTART (NO 'V' DISARM)!",
                        reason,
                    )
                    # Unconditionally de-energize all outputs
                    try:
                        await head_end_gpio.force_all_outputs_off()
                    except Exception as e:
                        logger.critical("Error forcing outputs off during watchdog stall: %s", e)

                    # Flush all logs before hard exit
                    try:
                        for h in logging.getLogger().handlers:
                            h.flush()
                        for h in logger.handlers:
                            h.flush()
                        sys.stdout.flush()
                        sys.stderr.flush()
                    except Exception:
                        pass

                    # Hard exit non-zero without writing 'V' so systemd restarts it
                    self._exit_func(2)
                    return

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

    def notify_ready(self) -> bool:
        """Sends READY=1 to systemd via $NOTIFY_SOCKET once all subsystems are online."""
        if self._ready_sent:
            return True
        if self._notify_sock and self._notify_path:
            try:
                msg = b"READY=1\nSTATUS=Smart Water Controller online\n"
                path = ("\0" + self._notify_path[1:]) if self._notify_path.startswith("@") else self._notify_path
                self._notify_sock.sendto(msg, path)
                self._ready_sent = True
                logger.info("Sent READY=1 to systemd notify socket.")
                return True
            except Exception as e:
                logger.warning("Failed to send READY=1 to systemd: %s", e)
                return False
        return False


watchdog_manager = WatchdogGuardian()
