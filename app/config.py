"""
System Configuration & Hardware Pinout Mapping for Radxa ROCK Pi S (RK3308).
Loads environment variables with strict validation via Pydantic Settings.
"""

from pathlib import Path
from typing import Dict, List, Optional
import os
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Runtime Mode
    APP_NAME: str = "Radxa Smart Water & Precision Irrigation Controller"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field(default="production", description="Environment mode: production or development")
    DEBUG: bool = Field(default=False, description="Enable debug logging")
    MOCK_HARDWARE: bool = Field(
        default_factory=lambda: os.getenv("MOCK_HARDWARE", "false").lower() in ("true", "1", "yes")
        or not os.path.exists("/dev/gpiochip1")
    )

    # Web & API Server
    HOST: str = Field(default="0.0.0.0", description="Bind address. Secured by Headscale/Tailscale mesh")
    PORT: int = Field(default=8000, description="Web API server port")
    CORS_ORIGINS: List[str] = Field(default_factory=lambda: ["*"])

    # Linux Device Paths
    GPIO_CHIP_PATH: str = Field(default="/dev/gpiochip1", description="ROCK Pi S Header 1 gpiochip")
    I2C_BUS: int = Field(default=7, description="ROCK Pi S I2C7 (Pin 3 SDA, Pin 5 SCL)")
    I2C_DEV_PATH: str = Field(default="/dev/i2c-7", description="I2C device file")
    WATCHDOG_DEV_PATH: str = Field(default="/dev/watchdog", description="Linux hardware watchdog timer")
    WATCHDOG_TIMEOUT_SEC: int = Field(default=10, description="Watchdog keep-alive timeout")
    WATCHDOG_PET_INTERVAL_SEC: int = Field(default=3, description="Watchdog petting heartbeat")

    # Hardware Pin Allocation (ROCK Pi S Header 1, gpiochip1)
    # Inputs (Active LOW, internal pull-up)
    PIN_TANK_LOW_FLOAT: int = Field(default=10, description="Line 10: Tank Low Float (Active LOW)")
    PIN_TANK_HIGH_FLOAT: int = Field(default=11, description="Line 11: Tank High Float (Active LOW)")
    PIN_RESERVOIR_LOW_FLOAT: int = Field(default=12, description="Line 12: Reservoir Low Float (Active LOW)")

    # Outputs (Active HIGH, 10k pull-down)
    PIN_AC_CONTACTOR: int = Field(default=13, description="Line 13: Overhead AC Pump 25A Contactor")
    PIN_ZONE_1_MOSFET: int = Field(default=15, description="Line 15: Zone 1 12V DC Pump MOSFET")
    PIN_ZONE_2_MOSFET: int = Field(default=22, description="Line 22: Zone 2 12V DC Pump MOSFET")
    PIN_ZONE_3_MOSFET: int = Field(default=23, description="Line 23: Zone 3 12V DC Pump MOSFET")
    PIN_ZONE_4_MOSFET: int = Field(default=24, description="Line 24: Zone 4 12V DC Pump MOSFET")

    # ADS1115 16-Bit I2C ADC
    ADS1115_I2C_ADDR: int = Field(default=0x48, description="ADS1115 default I2C address")
    ADC_PROBE_CHANNELS: Dict[str, int] = Field(
        default_factory=lambda: {
            "zone_1": 0,
            "zone_2": 1,
            "zone_3": 2,
            "zone_4": 3,
        }
    )

    # Audio & Voice Alerts (ALSA + espeak-ng over USB audio dongle)
    AUDIO_ENABLED: bool = Field(default=True, description="Enable voice announcements")
    ALSA_DEVICE: str = Field(default="default", description="ALSA playback device (e.g. hw:1,0 or default)")
    ESPEAK_VOICE: str = Field(default="en-us", description="espeak-ng voice model")
    ESPEAK_SPEED: int = Field(default=155, description="espeak-ng speech rate in wpm")

    # Failsafe Interlock Parameters (Non-bypassable Priority 0)
    MAX_AC_MOTOR_RUNTIME_SEC: int = Field(
        default=900, description="Maximum continuous AC pump runtime before emergency cutoff (15 min)"
    )
    MAX_DC_PUMP_RUNTIME_SEC: int = Field(
        default=120, description="Maximum continuous single zone DC pump runtime (2 min)"
    )
    SENSOR_VOLTAGE_MIN_V: float = Field(default=0.5, description="Probe short-circuit or disconnect threshold")
    SENSOR_VOLTAGE_MAX_V: float = Field(default=3.0, description="Probe air / out-of-bounds threshold")
    ZONE_COOLDOWN_SEC: int = Field(default=21600, description="Minimum lockout between waterings per zone (6 hours)")

    # Storage & SQLite Settings (Strictly optimized for <35MB RAM footprint)
    DATA_DIR: Path = Field(
        default_factory=lambda: Path(os.getenv("SMART_WATER_DATA_DIR", "./data")).resolve()
    )
    SQLITE_CACHE_SIZE_KB: int = Field(default=-2000, description="SQLite PRAGMA cache_size (-2000 is ~2MB)")
    TELEMETRY_RETENTION_HOURS: int = Field(default=48, description="Prune telemetry older than 48h to preserve disk")

    # Scheduler & Evaluator Timers
    RULES_EVALUATION_INTERVAL_SEC: float = Field(default=5.0, description="Low-code rule evaluator tick interval")
    TELEMETRY_SAMPLE_INTERVAL_SEC: float = Field(default=1.0, description="Hardware sampling & SSE broadcast interval")

    @property
    def db_path(self) -> Path:
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        return self.DATA_DIR / "system.db"


settings = Settings()
