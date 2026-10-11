"""
Hardware Clock (DS3231 RTC / /dev/rtc0) & Wall-Clock Validation for Rock Pi S.
Guarantees invalid or un-synchronized clocks block automatic watering.
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger("smart_water.rtc")

# Build date epoch threshold: 2026-01-01 00:00:00 UTC
BUILD_DATE_EPOCH: float = 1767225600.0


def is_system_clock_valid() -> bool:
    """
    Checks if the system wall-clock time is valid.
    If the board booted without network NTP and RTC lost power,
    the clock will revert to 1970 or kernel build date.
    """
    now = time.time()
    if now < BUILD_DATE_EPOCH:
        logger.error(
            "SYSTEM CLOCK INVALID: Current epoch %f is prior to build date threshold %f. "
            "All cooldowns forced active; automatic watering blocked.",
            now,
            BUILD_DATE_EPOCH,
        )
        return False
    return True


def check_ds3231_rtc_present() -> bool:
    """Checks if /dev/rtc0 or hardware RTC character device exists."""
    if os.path.exists("/dev/rtc0") or os.path.exists("/dev/rtc"):
        return True
    return False


def get_monotonic_now() -> float:
    """Returns monotonic time in seconds, immune to system clock adjustments."""
    return time.monotonic()
