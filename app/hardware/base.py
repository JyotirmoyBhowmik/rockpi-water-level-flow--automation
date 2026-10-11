"""
Abstract Base Interfaces for Hardware Drivers (Sensors, Actuators, Audio).
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class BaseGPIOController(ABC):
    """Abstract interface for Linux character device GPIO controllers."""

    @abstractmethod
    async def setup(self) -> None:
        """Initialize GPIO lines and hardware chips."""
        pass

    @abstractmethod
    async def read_pin(self, line_offset: int) -> bool:
        """Read boolean state of a digital input line."""
        pass

    @abstractmethod
    async def set_pin(self, line_offset: int, state: bool) -> None:
        """Drive a digital output line HIGH (True) or LOW (False)."""
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """Release claimed GPIO lines."""
        pass


class BaseADCController(ABC):
    """Abstract interface for 16-bit analog-to-digital converters (ADS1115)."""

    @abstractmethod
    async def setup(self) -> None:
        """Initialize I2C bus and ADC registers."""
        pass

    @abstractmethod
    async def read_channel_voltage(self, channel: int) -> float:
        """Read raw voltage in Volts from specified analog channel (0-3)."""
        pass

    @abstractmethod
    async def read_all_channels(self) -> Dict[str, float]:
        """Read all configured channels and return map of zone -> voltage."""
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """Release I2C bus handle."""
        pass


class BaseAudioController(ABC):
    """Abstract interface for voice synthesizer alerts."""

    @abstractmethod
    async def start(self) -> None:
        """Start audio playback worker."""
        pass

    @abstractmethod
    async def speak(self, text: str, priority: int = 1) -> None:
        """Enqueue speech alert for speech synthesis."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop worker and release audio handles."""
        pass

    @abstractmethod
    def get_recent_speech_logs(self, limit: int = 10) -> List[Dict[str, str]]:
        """Return history of voice announcements."""
        pass
