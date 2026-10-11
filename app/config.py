"""
Production Configuration & Settings Loader for Radxa ROCK Pi S Controller.
Splits configuration into:
  1. /etc/smart-water/config.yaml: Bootstrap-only, read-only to service (root:smartwater 0640).
  2. /var/lib/smart-water/settings.yaml: Runtime UI-edited settings (0600).
Honours SMART_WATER_DATA_DIR environment variable.
"""

import logging
import os
from pathlib import Path
import stat
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
import yaml

logger = logging.getLogger("smart_water.config")

# ==============================================================================
# Filesystem Hierarchy Paths
# ==============================================================================

def get_data_dir() -> Path:
    """Returns primary persistent data directory, respecting environment variable."""
    env_dir = os.getenv("SMART_WATER_DATA_DIR")
    if env_dir:
        p = Path(env_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    system_data = Path("/var/lib/smart-water")
    try:
        system_data.mkdir(parents=True, exist_ok=True)
        return system_data
    except (PermissionError, OSError):
        # Development / non-root local fallback
        local_data = Path("./data")
        local_data.mkdir(parents=True, exist_ok=True)
        return local_data


def get_runtime_dir() -> Path:
    """Returns runtime ephemeral directory (for temp backups, sockets)."""
    system_run = Path("/run/smart-water")
    try:
        system_run.mkdir(parents=True, exist_ok=True)
        return system_run
    except (PermissionError, OSError):
        local_run = Path("./data/run")
        local_run.mkdir(parents=True, exist_ok=True)
        return local_run


DATA_DIR: Path = get_data_dir()
RUNTIME_DIR: Path = get_runtime_dir()


# ==============================================================================
# Pydantic Schemas
# ==============================================================================

class HeadEndConfig(BaseModel):
    chip_path: Optional[str] = Field(default=None, description="Path to Linux gpiochip device, e.g. /dev/gpiochip1")
    tank_low_float_pin: Optional[int] = Field(default=None, description="IN1: Tank low float switch line offset")
    interlock_out_pin: Optional[int] = Field(default=None, description="IN2: Hardware interlock chain closed output")
    interlock_mid_pin: Optional[int] = Field(default=None, description="IN3: Mid-point between Full and Sump floats")
    leak_sensor_pin: Optional[int] = Field(default=None, description="IN4: Leak detection sensor line offset")
    contactor_relay_pin: Optional[int] = Field(default=None, description="Output: 25A contactor relay driver pin")
    contactor_relay_active_high: bool = Field(default=True, description="True if HIGH energizes the relay")
    relay_idle_bias_confirmed: Optional[str] = Field(default=None, description="Must be 'pull_up' if active-low used")
    contactor_feedback_pin: Optional[int] = Field(default=None, description="Optional: Contactor aux contact feedback")
    debounce_ms: int = Field(default=100, ge=10, le=1000, description="Debounce filtering in milliseconds")

    @field_validator("relay_idle_bias_confirmed")
    @classmethod
    def validate_polarity_safety(cls, v: Optional[str], info) -> Optional[str]:
        # Polarity safety enforced in check method
        return v


class GardenGatewayConfig(BaseModel):
    serial_port: str = Field(default="/dev/garden-gateway", description="Path to USB serial device for ESP32 gateway")
    baud_rate: int = Field(default=115200, description="UART baud rate")
    link_loss_timeout_s: int = Field(default=30, ge=5, le=300, description="Seconds without packet before raising LINK LOST")
    shared_key: str = Field(default="rockpi-garden-v1.1-key", description="HMAC-SHA256 shared secret key")


class StaleThresholdsConfig(BaseModel):
    head_end_s: int = Field(default=15, ge=5, le=120, description="Max age in seconds for head-end float readings")
    garden_s: int = Field(default=90, ge=15, le=600, description="Max age in seconds for wireless garden telemetry")
    soil_s: int = Field(default=60, ge=10, le=300, description="Max age for individual soil probe reading")
    reservoir_s: int = Field(default=20, ge=5, le=120, description="Max age for reservoir float status")
    heartbeat_s: int = Field(default=30, ge=10, le=120, description="Max age for node heartbeat")


class NetworkConfig(BaseModel):
    bind_address: str = Field(default="127.0.0.1", description="Bind IP address (Tailscale/Headscale IP or 127.0.0.1)")
    port: int = Field(default=8000, ge=1024, le=65535, description="HTTP API listening port")
    allow_all_interfaces: bool = Field(default=False, description="Explicit override required to bind 0.0.0.0")

    @field_validator("bind_address")
    @classmethod
    def validate_bind_address(cls, v: str) -> str:
        return v.strip()


class SecurityConfig(BaseModel):
    auth_token_file: str = Field(default="/etc/smart-water/auth.json", description="Path storing hashed API token")
    rate_limit_per_minute: int = Field(default=60, ge=5, le=600, description="Rate limit for state-mutating endpoints")
    auth_failure_limit: int = Field(default=10, ge=3, le=50, description="Max failed auth per minute before IP block")
    webhook_allowlist: List[str] = Field(default_factory=list, description="Host allowlist for webhook action")


class MotorConfig(BaseModel):
    auto_mode: bool = Field(default=False, description="Enable automatic replenishment when tank is low")
    max_runtime_s: int = Field(default=900, ge=30, le=3600, description="Maximum continuous motor run limit (15 min)")
    cooldown_s: int = Field(default=1800, ge=60, le=7200, description="Mandatory cooldown after max-runtime trip (30 min)")
    min_off_time_s: int = Field(default=120, ge=10, le=1800, description="Minimum OFF time between motor stop and next start")


class ZoneCalibration(BaseModel):
    dry_v: Optional[float] = Field(default=None, description="Calibration voltage in air (dry probe)")
    wet_v: Optional[float] = Field(default=None, description="Calibration voltage submerged in water")
    margin_v: float = Field(default=0.20, ge=0.05, le=1.0, description="Sanity band margin around calibrated extremes")


class ZoneConfig(BaseModel):
    name: str = Field(default="")
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
    path: str = Field(default_factory=lambda: str(DATA_DIR / "system.db"))
    retention_days: int = Field(default=90, ge=7, le=730)
    cache_size_kb: int = Field(default=-2000, description="PRAGMA cache_size (-2000 is ~2MB)")


class WatchdogConfig(BaseModel):
    device_path: str = Field(default="/dev/watchdog")
    timeout_s: int = Field(default=15, ge=3, le=60)
    pet_interval_s: int = Field(default=2, ge=1, le=10)


class SetupChecks(BaseModel):
    input_test: bool = False
    relay_test: bool = False
    gateway_pairing: bool = False
    calibration: bool = False


class SystemConfig(BaseModel):
    head_end: HeadEndConfig = Field(default_factory=HeadEndConfig)
    garden_gateway: GardenGatewayConfig = Field(default_factory=GardenGatewayConfig)
    stale_thresholds: StaleThresholdsConfig = Field(default_factory=StaleThresholdsConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    motor: MotorConfig = Field(default_factory=MotorConfig)
    zones: Dict[str, ZoneConfig] = Field(default_factory=lambda: {
        f"zone_{i}": ZoneConfig(name=f"Zone {i}", has_flow_meter=(i == 1)) for i in range(1, 5)
    })
    audio: AudioConfig = Field(default_factory=AudioConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    watchdog: WatchdogConfig = Field(default_factory=WatchdogConfig)
    setup_complete: bool = Field(default=False)
    setup_checks: SetupChecks = Field(default_factory=SetupChecks)
    restart_required: bool = Field(default=False)
    install_timestamp: float = Field(default=1767225600.0)

    @field_validator("network")
    @classmethod
    def check_network_binding(cls, v: NetworkConfig) -> NetworkConfig:
        if v.bind_address in ("0.0.0.0", "::") and not v.allow_all_interfaces:
            raise ValueError(
                "Refusing to bind to 0.0.0.0 without explicit 'allow_all_interfaces: true'. "
                "Specify your Tailscale/Headscale IP or 127.0.0.1 in /etc/smart-water/config.yaml"
            )
        return v

    def is_relay_polarity_safe(self) -> bool:
        """Enforces that active-low relay must have confirmed pull-up idle bias."""
        if not self.head_end.contactor_relay_active_high:
            return self.head_end.relay_idle_bias_confirmed == "pull_up"
        return True


# ==============================================================================
# Configuration Loading & Settings Persistence
# ==============================================================================

def find_bootstrap_config_path() -> Path:
    """Finds read-only bootstrap config (/etc/smart-water/config.yaml)."""
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


def get_settings_path() -> Path:
    """Returns /var/lib/smart-water/settings.yaml (UI-edited runtime settings)."""
    return DATA_DIR / "settings.yaml"


def get_install_timestamp() -> float:
    """Reads install-time epoch from /var/lib/smart-water/install_timestamp."""
    ts_file = DATA_DIR / "install_timestamp"
    if ts_file.exists():
        try:
            content = ts_file.read_text(encoding="utf-8").strip()
            return float(content)
        except Exception as e:
            logger.warning("Could not parse %s: %s", ts_file, e)
    # Default fallback threshold: 2026-01-01 UTC
    return 1767225600.0


def load_config() -> SystemConfig:
    """
    Loads unified configuration:
      1. Base / bootstrap from /etc/smart-water/config.yaml
      2. Overlaid with /var/lib/smart-water/settings.yaml
    """
    merged: Dict[str, Any] = {}

    # 1. Load bootstrap config
    bootstrap_file = find_bootstrap_config_path()
    if bootstrap_file.exists():
        try:
            with open(bootstrap_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if isinstance(data, dict):
                merged.update(data)
        except Exception as e:
            logger.error("Failed to load bootstrap config %s: %s", bootstrap_file, e)

    # 2. Overlay settings.yaml
    settings_file = get_settings_path()
    if settings_file.exists():
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                s_data = yaml.safe_load(f) or {}
            if isinstance(s_data, dict):
                for k, v in s_data.items():
                    if isinstance(v, dict) and isinstance(merged.get(k), dict):
                        merged[k].update(v)
                    else:
                        merged[k] = v
        except Exception as e:
            logger.error("Failed to load settings file %s: %s", settings_file, e)

    # Always enforce database path to DATA_DIR/system.db unless explicitly set
    if "database" not in merged:
        merged["database"] = {}
    if "path" not in merged["database"]:
        merged["database"]["path"] = str(DATA_DIR / "system.db")

    # Install timestamp
    merged["install_timestamp"] = get_install_timestamp()

    try:
        cfg = SystemConfig(**merged)
    except Exception as e:
        logger.error("Error validating configuration: %s. Using safe defaults.", e)
        cfg = SystemConfig()

    return cfg


def save_settings(cfg: SystemConfig) -> bool:
    """
    Persists UI-edited settings exclusively to /var/lib/smart-water/settings.yaml (0600).
    Never overwrites /etc/smart-water/config.yaml.
    """
    settings_file = get_settings_path()
    temp_file = settings_file.with_suffix(".tmp")

    # Extract UI-editable subset
    payload = {
        "head_end": cfg.head_end.model_dump(mode="json"),
        "motor": cfg.motor.model_dump(mode="json"),
        "zones": {k: v.model_dump(mode="json") for k, v in cfg.zones.items()},
        "telegram": cfg.telegram.model_dump(mode="json"),
        "setup_complete": cfg.setup_complete,
        "setup_checks": cfg.setup_checks.model_dump(mode="json"),
        "restart_required": cfg.restart_required,
    }

    try:
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, default_flow_style=False, sort_keys=False)

        # Set permission 0600 on Linux
        try:
            os.chmod(temp_file, 0o600)
        except OSError:
            pass

        temp_file.replace(settings_file)
        logger.info("Saved settings to %s", settings_file)
        return True
    except Exception as e:
        logger.error("Failed to save settings to %s: %s", settings_file, e)
        if temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass
        return False


# Global configuration singleton
system_config: SystemConfig = load_config()

# Alias for backwards compatibility
save_config = save_settings

# Startup hardware pin signature to detect if a restart is required
_STARTUP_PIN_SIGNATURE = (
    system_config.head_end.chip_path,
    system_config.head_end.tank_low_float_pin,
    system_config.head_end.interlock_out_pin,
    system_config.head_end.interlock_mid_pin,
    system_config.head_end.leak_sensor_pin,
    system_config.head_end.contactor_relay_pin,
    system_config.head_end.contactor_relay_active_high,
    system_config.head_end.relay_idle_bias_confirmed,
    system_config.head_end.contactor_feedback_pin,
)


def check_if_restart_required(updated_head_end: HeadEndConfig) -> bool:
    """Returns True if pin or polarity configurations differ from boot values."""
    new_sig = (
        updated_head_end.chip_path,
        updated_head_end.tank_low_float_pin,
        updated_head_end.interlock_out_pin,
        updated_head_end.interlock_mid_pin,
        updated_head_end.leak_sensor_pin,
        updated_head_end.contactor_relay_pin,
        updated_head_end.contactor_relay_active_high,
        updated_head_end.relay_idle_bias_confirmed,
        updated_head_end.contactor_feedback_pin,
    )
    return new_sig != _STARTUP_PIN_SIGNATURE
