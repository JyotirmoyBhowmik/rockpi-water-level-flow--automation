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
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator, SecretStr
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
    tank_low_active_low: bool = Field(default=True, description="IN1: Tank low float switch active-low (PC817)")
    interlock_out_active_low: bool = Field(default=True, description="IN2: Interlock chain closed active-low (PC817)")
    interlock_mid_active_low: bool = Field(default=True, description="IN3: Interlock mid-tap active-low (PC817)")
    leak_active_low: bool = Field(default=True, description="IN4: Leak sensor active-low (PC817)")
    feedback_active_low: bool = Field(default=True, description="Auxiliary contact feedback active-low (PC817)")

    @field_validator("relay_idle_bias_confirmed")
    @classmethod
    def validate_polarity_safety(cls, v: Optional[str], info: Any) -> Optional[str]:
        # Polarity safety enforced in check method
        return v


class GardenGatewayConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable ESP32 wireless garden gateway")
    serial_port: str = Field(default="/dev/garden-gateway", description="Path to USB serial device for ESP32 gateway")
    baud_rate: int = Field(default=115200, description="UART baud rate")
    link_loss_timeout_s: int = Field(default=30, ge=5, le=300, description="Seconds without packet before raising LINK LOST")
    shared_key: Optional[SecretStr] = Field(default=None, description="HMAC-SHA256 shared secret key")


class StaleThresholdsConfig(BaseModel):
    head_end_s: int = Field(default=15, ge=5, le=120, description="Max age in seconds for head-end float readings")
    garden_s: int = Field(default=90, ge=15, le=600, description="Max age in seconds for wireless garden telemetry")
    soil_s: int = Field(default=60, ge=10, le=300, description="Max age for individual soil probe reading")
    reservoir_s: int = Field(default=20, ge=5, le=120, description="Max age for reservoir float status")
    heartbeat_s: int = Field(default=30, ge=10, le=120, description="Max age for node heartbeat")

    @model_validator(mode="before")
    @classmethod
    def migrate_deprecated_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            key_mapping = {
                "max_soil_age_s": "soil_s",
                "max_reservoir_age_s": "reservoir_s",
                "max_heartbeat_age_s": "heartbeat_s",
            }
            for old_key, new_key in key_mapping.items():
                if old_key in data:
                    val = data.pop(old_key)
                    logger.warning(
                        "Deprecated configuration key '%s' found in stale_thresholds; migrating to '%s'",
                        old_key,
                        new_key,
                    )
                    if new_key not in data:
                        data[new_key] = val
        return data


class NetworkConfig(BaseModel):
    bind_address: str = Field(default="127.0.0.1", description="Bind IP address (Tailscale/Headscale IP or 127.0.0.1)")
    port: int = Field(default=8000, ge=1024, le=65535, description="HTTP API listening port")
    allow_all_interfaces: bool = Field(default=False, description="Explicit override required to bind 0.0.0.0")

    @field_validator("bind_address")
    @classmethod
    def validate_bind_address(cls, v: str) -> str:
        return v.strip()


class SecurityConfig(BaseModel):
    auth_token_file: str = Field(default="/var/lib/smart-water/auth.json", description="Path storing hashed API token")
    rate_limit_per_minute: int = Field(default=60, ge=5, le=600, description="Rate limit for state-mutating endpoints")
    auth_failure_limit: int = Field(default=10, ge=3, le=50, description="Max failed auth per minute before IP block")
    webhook_allowlist: List[str] = Field(default_factory=list, description="Host allowlist for webhook action")


class MotorConfig(BaseModel):
    auto_mode: bool = Field(default=False, description="Enable automatic replenishment when tank is low")
    max_runtime_s: int = Field(default=900, ge=30, le=3600, description="Maximum continuous motor run limit (15 min)")
    cooldown_s: int = Field(default=1800, ge=60, le=7200, description="Mandatory cooldown after max-runtime trip (30 min)")
    min_off_time_s: int = Field(default=120, ge=10, le=1800, description="Minimum OFF time between motor stop and next start")
    pull_in_timeout_ms: int = Field(default=1000, ge=50, le=5000, description="Max milliseconds to wait for contactor pull-in")
    drop_out_timeout_ms: int = Field(default=1000, ge=50, le=5000, description="Max milliseconds to wait for contactor drop-out")


class PowerMeterConfig(BaseModel):
    """
    Configuration for optional motor power and current monitoring via
    PZEM-004T v3 (Modbus RTU over USB-TTL) or SCT-013 CT + ADS1115 at head-end.
    NOTE: Software current monitoring NEVER replaces the physical thermal overload relay.
    """
    enabled: bool = Field(default=False, description="Enable optional motor AC power monitoring (PZEM-004T v3 or SCT-013 CT)")
    sensor_type: Literal["none", "pzem004t", "sct013_ads1115"] = Field(
        default="none", description="Power sensor interface: pzem004t (Modbus RTU) or sct013_ads1115 (CT clamp via ADS1115)"
    )
    serial_port: str = Field(default="/dev/pzem", description="Serial device for PZEM-004T v3 (e.g. /dev/pzem or /dev/ttyUSB1)")
    baud_rate: int = Field(default=9600, description="PZEM-004T baud rate (default 9600)")
    modbus_address: int = Field(default=1, ge=1, le=247, description="PZEM-004T Modbus slave address (1-247, default 1)")
    i2c_bus: int = Field(default=7, description="I2C bus number for ADS1115 CT input (default 7 on Rock Pi S)")
    i2c_address: int = Field(default=0x48, description="I2C address for ADS1115 (default 0x48)")
    ct_channel: int = Field(default=0, ge=0, le=3, description="ADS1115 analog input channel for CT clamp (0-3)")
    ct_ratio: float = Field(default=30.0, ge=1.0, le=100.0, description="CT current rating for 1V RMS output, e.g. 30.0 for SCT-013-030")
    voltage_min_v: float = Field(default=180.0, ge=100.0, le=250.0, description="Under-voltage cutoff threshold in Volts RMS (single-phase 230V window)")
    voltage_max_v: float = Field(default=260.0, ge=210.0, le=300.0, description="Over-voltage cutoff threshold in Volts RMS (single-phase 230V window)")
    voltage_fault_hold_s: float = Field(default=3.0, ge=0.5, le=30.0, description="Seconds voltage out-of-bounds must persist before tripping")
    dry_run_current_pct: float = Field(default=65.0, ge=10.0, le=95.0, description="Current below this % of learned baseline indicates dry run / unloaded impeller")
    dry_run_timeout_s: float = Field(default=5.0, ge=1.0, le=30.0, description="Seconds under-current must persist to trigger dry-run cutoff & lockout")
    overload_current_pct: float = Field(default=135.0, ge=105.0, le=250.0, description="Current above this % of learned baseline indicates overload or locked rotor")
    overload_timeout_s: float = Field(default=3.0, ge=0.5, le=30.0, description="Seconds over-current must persist to trigger overload cutoff & lockout")
    inrush_blanking_s: float = Field(default=3.0, ge=0.0, le=10.0, description="Seconds after contactor pull-in to blank current threshold evaluation for inrush")
    learning_runs_required: int = Field(default=5, ge=1, le=20, description="Number of healthy runs required to establish baseline current")
    baseline_current_a: Optional[float] = Field(default=None, ge=0.1, le=100.0, description="Manually configured or learned baseline current in Amperes")


class LevelSensorConfig(BaseModel):
    """
    Configuration for optional continuous overhead tank water level monitoring via:
      1. JSN-SR04T waterproof ultrasonic distance sensor (GPIO trigger/echo or UART serial)
      2. 4-20 mA submersible hydrostatic pressure transducer via ADS1115 + 250 ohm shunt
    NOTE: Electromechanical float switches remain the authoritative hardware interlock.
    """
    enabled: bool = Field(default=False, description="Enable continuous overhead tank water level monitoring")
    sensor_type: Literal["none", "jsn_sr04t", "pressure_4_20ma"] = Field(
        default="none", description="Level sensor interface: jsn_sr04t or pressure_4_20ma"
    )
    # JSN-SR04T parameters
    echo_pin: Optional[int] = Field(default=None, description="Echo input GPIO pin for JSN-SR04T (via PC817 or voltage divider)")
    trigger_pin: Optional[int] = Field(default=None, description="Trigger output GPIO pin for JSN-SR04T")
    serial_port: Optional[str] = Field(default=None, description="UART device path if using JSN-SR04T serial mode (e.g. /dev/ttyS2)")

    # 4-20 mA pressure transducer via ADS1115 parameters
    i2c_bus: int = Field(default=7, description="I2C bus number for ADS1115 level transducer (default 7 on Rock Pi S)")
    i2c_address: int = Field(default=0x48, description="I2C address for ADS1115 (default 0x48)")
    adc_channel: int = Field(default=1, ge=0, le=3, description="ADS1115 analog input channel for level transducer (0-3)")
    shunt_resistance_ohms: float = Field(default=250.0, ge=10.0, le=1000.0, description="Precision shunt resistor value in Ohms (default 250.0)")
    sensor_range_cm: float = Field(default=200.0, ge=10.0, le=2000.0, description="Full-scale depth range of 4-20 mA transducer in cm (at 20mA)")

    # Tank Geometry & Dimensions
    tank_shape: Literal["cylinder", "rectangular"] = Field(
        default="cylinder", description="Tank geometry: cylinder (vertical cylinder) or rectangular"
    )
    tank_height_cm: float = Field(default=150.0, ge=10.0, le=2000.0, description="Total internal water depth/height of tank in cm")
    tank_diameter_cm: Optional[float] = Field(default=100.0, ge=10.0, le=2000.0, description="Diameter in cm if cylindrical tank")
    tank_length_cm: Optional[float] = Field(default=None, ge=10.0, le=2000.0, description="Internal length in cm if rectangular tank")
    tank_width_cm: Optional[float] = Field(default=None, ge=10.0, le=2000.0, description="Internal width in cm if rectangular tank")
    sensor_offset_cm: float = Field(
        default=20.0, ge=0.0, le=200.0,
        description="Mounting distance between ultrasonic sensor face and 100% water mark (blind zone compensation)"
    )

    # Predictive stop & filling rate safety
    predictive_stop_enabled: bool = Field(default=True, description="Stop motor smoothly before Full float trips")
    predictive_stop_pct: float = Field(default=95.0, ge=50.0, le=99.0, description="Water level % to trigger predictive motor stop")
    slow_fill_min_run_s: float = Field(default=60.0, ge=10.0, le=600.0, description="Seconds motor must run before evaluating slow fill alarm")
    min_fill_rate_lpm: float = Field(default=5.0, ge=0.1, le=500.0, description="Minimum expected filling rate in L/min while motor is running")

    # Night-time leak detection
    night_leak_detection_enabled: bool = Field(default=True, description="Enable leak detection during night no-usage window")
    night_leak_window_start: int = Field(default=1, ge=0, le=23, description="Hour (0-23) when night zero-usage window begins (e.g. 1 = 01:00)")
    night_leak_window_end: int = Field(default=4, ge=0, le=23, description="Hour (0-23) when night zero-usage window ends (e.g. 4 = 04:00)")
    night_leak_max_drop_litres: float = Field(
        default=15.0, ge=1.0, le=500.0, description="Max allowable level drop in Litres during night window before raising leak alarm"
    )


class ZoneCalibration(BaseModel):
    dry_v: Optional[float] = Field(default=None, description="Calibration voltage in air (dry probe)")
    wet_v: Optional[float] = Field(default=None, description="Calibration voltage submerged in water")
    margin_v: float = Field(default=0.20, ge=0.05, le=1.0, description="Sanity band margin around calibrated extremes")


class WeatherConfig(BaseModel):
    """
    Configuration for Open-Meteo weather forecast polling and smart irrigation skips.
    Free, open API (no API key required).
    """
    enabled: bool = Field(default=False, description="Enable Open-Meteo weather-aware irrigation skips")
    latitude: float = Field(default=0.0, ge=-90.0, le=90.0, description="Latitude for Open-Meteo weather forecast")
    longitude: float = Field(default=0.0, ge=-180.0, le=180.0, description="Longitude for Open-Meteo weather forecast")
    poll_interval_hours: float = Field(default=3.0, ge=1.0, le=24.0, description="Forecast polling frequency in hours (default 3h)")
    forecast_lookahead_hours: int = Field(default=12, ge=1, le=48, description="Lookahead window in hours to evaluate rain probability")
    rain_prob_threshold_pct: float = Field(default=60.0, ge=0.0, le=100.0, description="Rain probability threshold (%) to skip watering")
    past_rain_threshold_mm: float = Field(default=5.0, ge=0.0, le=200.0, description="Rainfall in last 24h (mm) threshold to skip watering")
    timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0, description="HTTP request timeout in seconds")


class ZoneConfig(BaseModel):
    name: str = Field(default="")
    enabled: bool = Field(default=False)
    soil_dry_threshold_pct: float = Field(default=35.0, ge=5.0, le=95.0)
    pulse_seconds: int = Field(default=15, ge=1, le=60)
    max_pulse_seconds: int = Field(default=60, ge=5, le=60)
    cooldown_hours: float = Field(default=6.0, ge=0.5, le=72.0)
    has_flow_meter: bool = Field(default=False)
    flow_meter_model: str = Field(default="YF-S201", description="Flow sensor hardware model, e.g. YF-S201")
    flow_k_factor: float = Field(default=7.5, ge=0.1, le=100.0, description="K-factor in Hz per L/min (7.5 for YF-S201 = 450 pulses/L)")
    min_pulses_10s: int = Field(default=5, ge=0)
    calibration: ZoneCalibration = Field(default_factory=ZoneCalibration)
    # Schedule Window (Time Windows)
    schedule_window_start: Optional[int] = Field(default=None, ge=0, le=23, description="Allowed watering window start hour (0-23)")
    schedule_window_end: Optional[int] = Field(default=None, ge=0, le=23, description="Allowed watering window end hour (0-23)")
    # Soak Cycles (pulse + wait + pulse)
    soak_cycle_enabled: bool = Field(default=False, description="Enable soak cycles (pulse + wait + pulse) to prevent runoff")
    soak_pulse_seconds: int = Field(default=15, ge=1, le=60, description="Duration in seconds of each pulse in soak cycle")
    soak_wait_seconds: int = Field(default=120, ge=5, le=1800, description="Rest/soak interval between pulses in seconds")
    soak_pulse_count: int = Field(default=2, ge=1, le=10, description="Number of pulses per soak cycle")
    # Daily Budget
    daily_budget_litres: Optional[float] = Field(default=None, ge=0.1, le=1000.0, description="Maximum water budget per zone per day in Litres")

    def is_within_schedule(self, current_hour: int) -> bool:
        """Determines whether current hour is within allowed watering schedule window."""
        if self.schedule_window_start is None or self.schedule_window_end is None:
            return True
        start = self.schedule_window_start
        end = self.schedule_window_end
        if start <= end:
            return start <= current_hour < end
        else:  # Midnight crossing, e.g. 22:00 to 04:00
            return current_hour >= start or current_hour < end



class AudioConfig(BaseModel):
    enabled: bool = Field(default=True)
    alsa_device: str = Field(default="default")
    voice: str = Field(default="en-us")
    speed: int = Field(default=145, ge=80, le=260)
    quiet_hours_start: Optional[int] = Field(default=None, ge=0, le=23)
    quiet_hours_end: Optional[int] = Field(default=None, ge=0, le=23)


class BuzzerConfig(BaseModel):
    """
    Configuration for optional piezo buzzer connected to SBC GPIO.
    Provides audible alerts for critical alarms when voice audio is unavailable or silent.
    """
    enabled: bool = Field(default=False, description="Enable hardware piezo buzzer on SBC GPIO")
    gpio_pin: Optional[int] = Field(default=None, description="GPIO line offset for piezo buzzer driver")
    active_high: bool = Field(default=True, description="True if HIGH energizes the buzzer")
    beep_on_alarm: bool = Field(default=True, description="Sound audible alert on critical alarms when voice audio is unavailable")


class DisplayConfig(BaseModel):
    """
    Configuration for local SSD1306 OLED status display over I2C.
    Displays tank levels, pump status, wireless garden link, and active alarms.
    """
    enabled: bool = Field(default=False, description="Enable local I2C SSD1306 OLED status display")
    i2c_bus: int = Field(default=7, ge=0, le=10, description="I2C bus number on ROCK Pi S (default 7 on Header 1)")
    i2c_address: int = Field(default=0x3C, ge=0, le=0x7F, description="I2C address for SSD1306 (default 0x3C)")
    width: int = Field(default=128, ge=64, le=256, description="Display width in pixels (typically 128)")
    height: int = Field(default=64, ge=16, le=128, description="Display height in pixels (64 or 32)")
    refresh_interval_s: float = Field(default=2.0, ge=0.5, le=30.0, description="Interval in seconds for display refresh")
    rotate: int = Field(default=0, ge=0, le=270, description="Display rotation: 0, 90, 180, or 270 degrees")


class TelegramConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable Telegram bot notifications")
    bot_token: Optional[str] = Field(default=None, description="Telegram bot API token")
    chat_id: Optional[str] = Field(default=None, description="Telegram chat ID for alert delivery")


class NtfyConfig(BaseModel):
    """
    Configuration for optional ntfy.sh push notification client.
    Lightweight, privacy-respecting alternative/supplement to Telegram.
    """
    enabled: bool = Field(default=False, description="Enable ntfy.sh push notifications")
    server_url: str = Field(default="https://ntfy.sh", description="ntfy server URL (e.g. https://ntfy.sh or self-hosted)")
    topic: Optional[str] = Field(default=None, description="ntfy topic name for alert delivery")
    access_token: Optional[SecretStr] = Field(default=None, description="Optional Bearer token for protected topics")
    priority_default: int = Field(default=3, ge=1, le=5, description="Default priority level: 1 (min) to 5 (urgent)")


class MQTTConfig(BaseModel):
    """
    Configuration for optional MQTT client with Home Assistant discovery.
    Allows telemetry publishing and remote control through strict failsafe gates.
    """
    enabled: bool = Field(default=False, description="Enable optional MQTT publisher and remote control")
    broker_host: str = Field(default="127.0.0.1", description="MQTT broker hostname or IP address")
    broker_port: int = Field(default=1883, ge=1, le=65535, description="MQTT broker port (default 1883)")
    username: Optional[str] = Field(default=None, description="MQTT broker authentication username")
    password: Optional[SecretStr] = Field(default=None, description="MQTT broker authentication password")
    client_id: str = Field(default="rockpi_smart_water", description="MQTT client ID")
    topic_prefix: str = Field(default="smart_water", description="Base topic prefix for MQTT publications")
    ha_discovery_enabled: bool = Field(default=True, description="Publish Home Assistant MQTT discovery payloads on connect")
    ha_discovery_prefix: str = Field(default="homeassistant", description="Home Assistant MQTT discovery topic prefix")
    publish_interval_s: float = Field(default=5.0, ge=1.0, le=60.0, description="Interval in seconds for state telemetry publishing")
    tls_enabled: bool = Field(default=False, description="Enable TLS connection to MQTT broker")
    tls_ca_cert: Optional[str] = Field(default=None, description="Path to custom CA certificate for TLS verification")
    command_auth_required: bool = Field(default=True, description="Require valid Bearer token in command payload")


class DatabaseConfig(BaseModel):
    path: str = Field(default_factory=lambda: str(DATA_DIR / "system.db"), description="SQLite database file path")
    retention_days: int = Field(default=90, ge=7, le=730, description="Retention window for historical telemetry in days")
    cache_size_kb: int = Field(default=-2000, description="PRAGMA cache_size (-2000 is ~2MB)")
    busy_timeout_ms: int = Field(default=5000, ge=100, le=60000, description="SQLite busy_timeout in milliseconds (default 5000)")
    backup_dir: str = Field(default="/var/lib/smart-water/backups", description="Directory storing nightly VACUUM INTO database backups")
    backup_retention_days: int = Field(default=7, ge=1, le=90, description="Retention window for rotated SQLite database backups in days (default 7)")
    rclone_remote: Optional[str] = Field(default=None, description="Optional rclone destination (e.g. 'gdrive:smart-water-backups') for remote backup sync")


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
    power_meter: PowerMeterConfig = Field(default_factory=PowerMeterConfig)
    level_sensor: LevelSensorConfig = Field(default_factory=LevelSensorConfig)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    zones: Dict[str, ZoneConfig] = Field(default_factory=lambda: {
        f"zone_{i}": ZoneConfig(name=f"Zone {i}", has_flow_meter=(i == 1)) for i in range(1, 5)
    })
    audio: AudioConfig = Field(default_factory=AudioConfig)
    buzzer: BuzzerConfig = Field(default_factory=BuzzerConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    ntfy: NtfyConfig = Field(default_factory=NtfyConfig)
    mqtt: MQTTConfig = Field(default_factory=MQTTConfig)
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

    @model_validator(mode="after")
    def validate_gateway_secrets(self) -> "SystemConfig":
        if self.garden_gateway.enabled and (not self.garden_gateway.shared_key or not self.garden_gateway.shared_key.get_secret_value()):
            raise ValueError(
                "garden_gateway.enabled is true but garden_gateway.shared_key is missing or empty. "
                "Run 'python -m app.cli gen-espnow-secrets' and configure shared_key in /var/lib/smart-water/settings.yaml"
            )
        return self

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

    # Migrate deprecated stale threshold keys in load_config
    if "stale_thresholds" in merged and isinstance(merged["stale_thresholds"], dict):
        key_mapping = {
            "max_soil_age_s": "soil_s",
            "max_reservoir_age_s": "reservoir_s",
            "max_heartbeat_age_s": "heartbeat_s",
        }
        for old_key, new_key in key_mapping.items():
            if old_key in merged["stale_thresholds"]:
                val = merged["stale_thresholds"].pop(old_key)
                logger.warning(
                    "Deprecated configuration key '%s' found in stale_thresholds; migrating to '%s'",
                    old_key,
                    new_key,
                )
                if new_key not in merged["stale_thresholds"]:
                    merged["stale_thresholds"][new_key] = val

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
        "garden_gateway": cfg.garden_gateway.model_dump(mode="json"),
        "power_meter": cfg.power_meter.model_dump(mode="json"),
        "level_sensor": cfg.level_sensor.model_dump(mode="json"),
        "weather": cfg.weather.model_dump(mode="json"),
        "buzzer": cfg.buzzer.model_dump(mode="json"),
        "display": cfg.display.model_dump(mode="json"),
        "telegram": cfg.telegram.model_dump(mode="json"),
        "ntfy": cfg.ntfy.model_dump(mode="json"),
        "mqtt": cfg.mqtt.model_dump(mode="json"),
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
    system_config.head_end.tank_low_active_low,
    system_config.head_end.interlock_out_active_low,
    system_config.head_end.interlock_mid_active_low,
    system_config.head_end.leak_active_low,
    system_config.head_end.feedback_active_low,
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
        updated_head_end.tank_low_active_low,
        updated_head_end.interlock_out_active_low,
        updated_head_end.interlock_mid_active_low,
        updated_head_end.leak_active_low,
        updated_head_end.feedback_active_low,
    )
    return new_sig != _STARTUP_PIN_SIGNATURE
