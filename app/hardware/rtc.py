"""
Hardware Clock (DS3231 RTC / /dev/rtc0) & Wall-Clock Validation for Rock Pi S.
Guarantees invalid or un-synchronized clocks block automatic watering.
Uses install-time timestamp threshold written by install.sh.
"""

import logging
import os
import time
from typing import Optional
from app.config import system_config

logger = logging.getLogger("smart_water.rtc")


def get_clock_epoch_threshold() -> float:
    """Returns minimum valid epoch threshold (install-time timestamp)."""
    return system_config.install_timestamp


def is_system_clock_valid() -> bool:
    """
    Checks if the system wall-clock time is valid.
    If the board booted without network NTP and RTC lost power,
    the clock will revert to 1970 or kernel build date.
    """
    now = time.time()
    threshold = get_clock_epoch_threshold()
    if now < threshold:
        logger.error(
            "SYSTEM CLOCK INVALID: Current epoch %f is prior to install timestamp threshold %f. "
            "All cooldowns forced active; automatic watering blocked.",
            now,
            threshold,
        )
        return False
    return True


def check_ds3231_rtc_present() -> bool:
    """Checks if /dev/rtc0 or hardware RTC character device exists."""
    return os.path.exists("/dev/rtc0") or os.path.exists("/dev/rtc")


def get_monotonic_now() -> float:
    """Returns monotonic time in seconds, immune to system clock adjustments."""
    return time.monotonic()
