"""
Production Configuration Loader for Radxa ROCK Pi S Controller.
Loads and strictly validates /etc/smart-water/config.yaml via Pydantic v2.
All hardware pin mappings default to null.
"""

from pathlib import Path
from typing import Dict, Optional
import os
import yaml
from pydantic import BaseModel, Field, field_validator


class HeadEndConfig(BaseModel):
    chip_path: Optional[str] = Field(default=None, description="Path to Linux gpiochip device, e.g. /dev/gpiochip1")
    tank_low_float_pin: Optional[int] = Field(default=None, description="IN1: Tank low float switch line offset")
    interlock_out_pin: Optional[int] = Field(default=None, description="IN2: Hardware interlock chain closed output")
    interlock_mid_pin: Optional[int] = Field(default=None, description="IN3: Mid-point between Full and Sump floats")
    leak_sensor_pin: Optional[int] = Field(default=None, description="IN4: Leak detection sensor line offset")
    contactor_relay_pin: Optional[int] = Field(default=None, description="Output: 25A contactor relay driver pin")
    contactor_relay_active_high: bool = Field(default=True, description="True if HIGH energizes the relay")
    debounce_ms: int = Field(default=100, ge=10, le=1000, description="Debounce filtering in milliseconds")


class GardenGatewayConfig(BaseModel):
    serial_port: str = Field(default="/dev/garden-gateway", description="Path to USB serial device for ESP32 gateway")
    baud_rate: int = Field(default=115200, description="UART baud rate")
    link_loss_timeout_s: int = Field(default=30, ge=5, le=300, description="Seconds without packet before raising LINK LOST")


class StaleThresholdsConfig(BaseModel):
    head_end_s: int = Field(default=15, ge=5, le=120, description="Max age in seconds for head-end float readings")
    garden_s: int = Field(default=90, ge=15, le=600, description="Max age in seconds for wireless garden telemetry")


class NetworkConfig(BaseModel):
    bind_address: str = Field(default="127.0.0.1", description="Bind IP address (Tailscale/Headscale IP or 127.0.0.1)")
    port: int = Field(default=8000, ge=1024, le=65535, description="HTTP API listening port")
    allow_all_interfaces: bool = Field(default=False, description="Explicit override required to bind 0.0.0.0")

    @field_validator("bind_address")
    @classmethod
    def validate_bind_address(cls, v: str, info) -> str:
        # Note: allow_all_interfaces check performed in SystemConfig validator
        return v.strip()


class SecurityConfig(BaseModel):
    auth_token_file: str = Field(default="/etc/smart-water/auth.json", description="Path storing hashed API token")
    rate_limit_per_minute: int = Field(default=60, ge=5, le=600, description="Rate limit for state-mutating endpoints")


class MotorConfig(BaseModel):
    auto_mode: bool = Field(default=False, description="Enable automatic replenishment when tank is low")
    max_runtime_s: int = Field(default=900, ge=30, le=3600, description="Maximum continuous motor run limit (15 min)")
    cooldown_s: int = Field(default=1800, ge=60, le=7200, description="Mandatory cooldown after max-runtime trip (30 min)")


class ZoneCalibration(BaseModel):
    dry_v: Optional[float] = Field(default=None, description="Calibration voltage in air (dry probe)")
    wet_v: Optional[float] = Field(default=None, description="Calibration voltage submerged in water")
    margin_v: float = Field(default=0.20, ge=0.05, le=1.0, description="Sanity band margin around calibrated extremes")


class ZoneConfig(BaseModel):
    enabled: bool = Field(default=False)
    soil_dry_threshold_pct: float = Field(default=35.0, ge=5.0, le=95.0)
    pulse_seconds: int = Field(default=15, ge=1, le=60)
    max_pulse_seconds: int = Field(default=60, ge=5, le=60)
    cooldown_hours: float = Field(default=6.0, ge=0.5, le=72.0)
    has_flow_meter: bool = Field(default=False)
    min_pulses_10s: int = Field(default=5, ge=0)
    calibration: ZoneCalibration = Field(default_factory=ZoneCalibration)


class AudioConfig(BaseModel):
    enabled: bool = Field(default=True)
    alsa_device: str = Field(default="default")
    voice: str = Field(default="en-us")
    speed: int = Field(default=145, ge=80, le=260)
    quiet_hours_start: Optional[int] = Field(default=None, ge=0, le=23)
    quiet_hours_end: Optional[int] = Field(default=None, ge=0, le=23)


class TelegramConfig(BaseModel):
    enabled: bool = Field(default=False)
    bot_token: Optional[str] = Field(default=None)
    chat_id: Optional[str] = Field(default=None)


class DatabaseConfig(BaseModel):
    path: str = Field(default="/opt/smart-water/data/system.db")
    retention_days: int = Field(default=90, ge=7, le=730)
    cache_size_kb: int = Field(default=-2000, description="PRAGMA cache_size (-2000 is ~2MB)")


class WatchdogConfig(BaseModel):
    device_path: str = Field(default="/dev/watchdog")
    timeout_s: int = Field(default=10, ge=3, le=60)
    pet_interval_s: int = Field(default=3, ge=1, le=30)


class SystemConfig(BaseModel):
    head_end: HeadEndConfig = Field(default_factory=HeadEndConfig)
    garden_gateway: GardenGatewayConfig = Field(default_factory=GardenGatewayConfig)
    stale_thresholds: StaleThresholdsConfig = Field(default_factory=StaleThresholdsConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    motor: MotorConfig = Field(default_factory=MotorConfig)
    zones: Dict[str, ZoneConfig] = Field(default_factory=lambda: {
        f"zone_{i}": ZoneConfig(has_flow_meter=(i == 1)) for i in range(1, 5)
    })
    audio: AudioConfig = Field(default_factory=AudioConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    watchdog: WatchdogConfig = Field(default_factory=WatchdogConfig)
    setup_complete: bool = Field(default=False)

    @field_validator("network")
    @classmethod
    def check_network_binding(cls, v: NetworkConfig) -> NetworkConfig:
        if v.bind_address in ("0.0.0.0", "::") and not v.allow_all_interfaces:
            raise ValueError(
                "Refusing to bind to 0.0.0.0 without explicit 'allow_all_interfaces: true'. "
                "Specify your Tailscale/Headscale IP or 127.0.0.1 in /etc/smart-water/config.yaml"
            )
        return v


def find_config_path() -> Path:
    """Locates the configuration file in standard filesystem locations."""
    env_path = os.getenv("SMART_WATER_CONFIG")
    if env_path and Path(env_path).exists():
        return Path(env_path)

    system_path = Path("/etc/smart-water/config.yaml")
    if system_path.exists():
        return system_path

    local_path = Path("./config.yaml")
    if local_path.exists():
        return local_path

    example_path = Path("./config.example.yaml")
    if example_path.exists():
        return example_path

    return system_path


def load_config(path: Optional[Path] = None) -> SystemConfig:
    """Loads and parses the YAML configuration file."""
    config_file = path or find_config_path()
    if not config_file.exists():
        return SystemConfig()

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return SystemConfig(**data)
    except Exception as exc:
        raise RuntimeError(f"Failed to load configuration from {config_file}: {exc}") from exc


def save_config(cfg: SystemConfig, path: Optional[Path] = None) -> None:
    """Saves updated configuration to file atomically."""
    config_file = path or find_config_path()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = config_file.with_suffix(".tmp")

    data = cfg.model_dump(mode="json")
    with open(temp_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    temp_file.replace(config_file)


# Global configuration instance
system_config = load_config()
