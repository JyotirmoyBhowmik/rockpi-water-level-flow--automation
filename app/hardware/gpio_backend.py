"""
Hardware Abstraction Layer for Linux libgpiod (v1 and v2 runtime selection).
Supports Debian 12 (python3-libgpiod / libgpiod 1.6.x) and newer libgpiod 2.x.
"""

from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("smart_water.gpio_backend")


class GPIOBackend(ABC):
    """Abstract interface for libgpiod access."""

    @abstractmethod
    def open(self, chip_path: str) -> bool:
        """Opens connection to the GPIO chip."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Releases all lines and closes chip handle."""
        pass

    @abstractmethod
    def request_input(self, line_offset: int, consumer: str = "smart-water", bias: str = "pull_up") -> bool:
        """Requests line as input with optional pull-up bias."""
        pass

    @abstractmethod
    def request_output(self, line_offset: int, consumer: str = "smart-water", initial_value: int = 0) -> bool:
        """Requests line as output with explicit initial level."""
        pass

    @abstractmethod
    def get_value(self, line_offset: int) -> int:
        """Reads logical value (0 or 1) of requested line."""
        pass

    @abstractmethod
    def set_value(self, line_offset: int, value: int) -> None:
        """Drives output line to logical level (0 or 1)."""
        pass


class LibgpiodV1Backend(GPIOBackend):
    """Implementation for libgpiod v1.x (Chip.get_line, line.request)."""

    def __init__(self):
        self._chip = None
        self._lines: Dict[int, Any] = {}
        self._chip_path: Optional[str] = None

    def open(self, chip_path: str) -> bool:
        try:
            import gpiod
            self._chip_path = chip_path
            # gpiod.Chip can accept path or name (e.g. "/dev/gpiochip1" or "gpiochip1")
            self._chip = gpiod.Chip(chip_path)
            logger.info("libgpiod v1 backend opened on %s (label=%s, lines=%d)",
                        chip_path, self._chip.label, self._chip.num_lines)
            return True
        except Exception as e:
            logger.warning("Failed to open %s with libgpiod v1: %s", chip_path, e)
            self._chip = None
            return False

    def close(self) -> None:
        for offset, line in list(self._lines.items()):
            try:
                line.release()
            except Exception:
                pass
        self._lines.clear()
        if self._chip:
            try:
                self._chip.close()
            except Exception:
                pass
            self._chip = None

    def request_input(self, line_offset: int, consumer: str = "smart-water", bias: str = "pull_up") -> bool:
        if not self._chip:
            return False
        try:
            import gpiod
            line = self._chip.get_line(line_offset)
            flags = 0
            if bias == "pull_up" and hasattr(gpiod, "LINE_REQ_FLAG_BIAS_PULL_UP"):
                flags = gpiod.LINE_REQ_FLAG_BIAS_PULL_UP
            elif bias == "pull_down" and hasattr(gpiod, "LINE_REQ_FLAG_BIAS_PULL_DOWN"):
                flags = gpiod.LINE_REQ_FLAG_BIAS_PULL_DOWN

            line.request(consumer=consumer, type=gpiod.LINE_REQ_DIR_IN, flags=flags)
            self._lines[line_offset] = line
            return True
        except Exception as e:
            logger.error("libgpiod v1 failed to request input line %d: %s", line_offset, e)
            return False

    def request_output(self, line_offset: int, consumer: str = "smart-water", initial_value: int = 0) -> bool:
        if not self._chip:
            return False
        try:
            import gpiod
            line = self._chip.get_line(line_offset)
            line.request(consumer=consumer, type=gpiod.LINE_REQ_DIR_OUT, default_val=initial_value)
            self._lines[line_offset] = line
            return True
        except Exception as e:
            logger.error("libgpiod v1 failed to request output line %d: %s", line_offset, e)
            return False

    def get_value(self, line_offset: int) -> int:
        line = self._lines.get(line_offset)
        if not line:
            raise RuntimeError(f"Line {line_offset} not requested on libgpiod v1")
        return int(line.get_value())

    def set_value(self, line_offset: int, value: int) -> None:
        line = self._lines.get(line_offset)
        if not line:
            raise RuntimeError(f"Line {line_offset} not requested on libgpiod v1")
        line.set_value(int(value))


class LibgpiodV2Backend(GPIOBackend):
    """Implementation for libgpiod v2.x (gpiod.request_lines, LineSettings)."""

    def __init__(self):
        self._chip_path: Optional[str] = None
        self._line_offsets: Dict[int, Any] = {}
        self._output_lines: set[int] = set()

    def open(self, chip_path: str) -> bool:
        try:
            import gpiod
            # In v2, Chip takes filesystem path e.g. /dev/gpiochip1
            chip = gpiod.Chip(chip_path)
            chip.close()
            self._chip_path = chip_path
            logger.info("libgpiod v2 verified on %s", chip_path)
            return True
        except Exception as e:
            logger.warning("Failed to open %s with libgpiod v2: %s", chip_path, e)
            return False

    def close(self) -> None:
        for req in set(self._line_offsets.values()):
            try:
                req.release()
            except Exception:
                pass
        self._line_offsets.clear()
        self._output_lines.clear()
        self._chip_path = None

    def request_input(self, line_offset: int, consumer: str = "smart-water", bias: str = "pull_up") -> bool:
        if not self._chip_path:
            return False
        # Release existing line if already requested
        if line_offset in self._line_offsets:
            try:
                self._line_offsets[line_offset].release()
            except Exception:
                pass
            del self._line_offsets[line_offset]

        try:
            import gpiod
            from gpiod.line import Direction, Bias

            settings = gpiod.LineSettings()
            settings.direction = Direction.INPUT
            if bias == "pull_up":
                settings.bias = Bias.PULL_UP
            elif bias == "pull_down":
                settings.bias = Bias.PULL_DOWN

            req = gpiod.request_lines(
                self._chip_path,
                consumer=consumer,
                config={line_offset: settings},
            )
            # Store request reference
            self._line_offsets[line_offset] = req
            return True
        except Exception as e:
            logger.error("libgpiod v2 failed to request input line %d: %s", line_offset, e)
            return False

    def request_output(self, line_offset: int, consumer: str = "smart-water", initial_value: int = 0) -> bool:
        if not self._chip_path:
            return False
        # Release existing line if already requested
        if line_offset in self._line_offsets:
            try:
                self._line_offsets[line_offset].release()
            except Exception:
                pass
            del self._line_offsets[line_offset]

        try:
            import gpiod
            from gpiod.line import Direction, Value

            settings = gpiod.LineSettings()
            settings.direction = Direction.OUTPUT
            settings.output_value = Value.ACTIVE if initial_value else Value.INACTIVE

            req = gpiod.request_lines(
                self._chip_path,
                consumer=consumer,
                config={line_offset: settings},
            )
            self._line_offsets[line_offset] = req
            self._output_lines.add(line_offset)
            return True
        except Exception as e:
            logger.error("libgpiod v2 failed to request output line %d: %s", line_offset, e)
            return False

    def get_value(self, line_offset: int) -> int:
        req = self._line_offsets.get(line_offset)
        if not req:
            raise RuntimeError(f"Line {line_offset} not requested on libgpiod v2")
        val = req.get_value(line_offset)
        # Value enum or int
        return int(val.value if hasattr(val, "value") else val)

    def set_value(self, line_offset: int, value: int) -> None:
        req = self._line_offsets.get(line_offset)
        if not req:
            raise RuntimeError(f"Line {line_offset} not requested on libgpiod v2")
        from gpiod.line import Value
        req.set_value(line_offset, Value.ACTIVE if value else Value.INACTIVE)


def create_gpio_backend() -> Optional[GPIOBackend]:
    """
    Selects libgpiod backend version at runtime.
    Returns LibgpiodV2Backend if gpiod v2 is present,
    else LibgpiodV1Backend if gpiod v1 is present,
    else None if gpiod is not available.
    """
    try:
        import gpiod
    except ImportError:
        logger.info("libgpiod library not installed; physical GPIO backend unavailable.")
        return None

    if hasattr(gpiod, "request_lines") or hasattr(gpiod, "LineSettings"):
        logger.info("Detected libgpiod v2 API.")
        return LibgpiodV2Backend()
    elif hasattr(gpiod, "Chip"):
        logger.info("Detected libgpiod v1 API.")
        return LibgpiodV1Backend()
    else:
        logger.warning("Unrecognized gpiod API structure.")
        return None
