"""
Hand-written Prometheus / OpenMetrics 0.0.4 text format generator.
Zero external dependencies (no prometheus-client library required).
Adheres strictly to the Prometheus exposition format specification.
"""

from typing import Any, Dict, List, Optional
import time
from app.config import system_config
from app.core.state_manager import state_manager


def _format_metric(
    name: str,
    metric_type: str,
    help_text: str,
    value: Any,
    labels: Optional[Dict[str, str]] = None,
) -> str:
    """Formats a single metric line with HELP and TYPE metadata."""
    if value is None:
        return ""

    num_val: str
    if isinstance(value, bool):
        num_val = "1" if value else "0"
    elif isinstance(value, (int, float)):
        num_val = f"{value:.4f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
    else:
        try:
            num_val = str(float(value))
        except (ValueError, TypeError):
            return ""

    label_str = ""
    if labels:
        pairs = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        label_str = "{" + ",".join(pairs) + "}"

    lines = [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} {metric_type}",
        f"{name}{label_str} {num_val}",
    ]
    return "\n".join(lines) + "\n"


def generate_prometheus_metrics() -> str:
    """
    Introspects live state from state_manager and formats a Prometheus 0.0.4 text payload.
    Emits system, motor, IO, garden, zone, power, tank level, weather, and alarm metrics.
    """
    state = state_manager.get_state()
    lines: List[str] = []
    now = time.time()

    # 1. System Metrics
    lines.append(_format_metric(
        "smart_water_uptime_seconds", "counter",
        "Total controller uptime in seconds",
        state.get("uptime_seconds", 0)
    ))
    lines.append(_format_metric(
        "smart_water_system_clock_valid", "gauge",
        "Whether system RTC / chrony clock has valid synchronized time",
        state.get("system_clock_valid", True)
    ))
    lines.append(_format_metric(
        "smart_water_hardware_ready", "gauge",
        "Whether hardwired GPIO hardware interface is initialized and accessible",
        state.get("hardware_ready", False)
    ))
    lines.append(_format_metric(
        "smart_water_setup_complete", "gauge",
        "Whether first-run commissioning wizard has completed",
        system_config.setup_complete
    ))

    # 2. Motor & Contactor Metrics
    motor = state.get("motor", {})
    is_motor_running = motor.get("state") == "RUNNING"
    lines.append(_format_metric(
        "smart_water_motor_running", "gauge",
        "Whether the 25A AC motor contactor is energized",
        1 if is_motor_running else 0
    ))
    lines.append(_format_metric(
        "smart_water_motor_current_run_seconds", "gauge",
        "Current continuous run duration in seconds of the active motor cycle",
        motor.get("current_run_seconds", 0)
    ))
    lines.append(_format_metric(
        "smart_water_motor_auto_mode", "gauge",
        "Whether automatic overhead tank replenishment is enabled",
        motor.get("auto_mode", False)
    ))
    lines.append(_format_metric(
        "smart_water_motor_manual_locked", "gauge",
        "Whether motor is under manual maintenance lockout",
        motor.get("manual_locked", False)
    ))
    cooldown_until = motor.get("cooldown_until_ts", 0.0)
    lines.append(_format_metric(
        "smart_water_motor_cooldown_active", "gauge",
        "Whether motor is currently held in mandatory cooldown lockout",
        1 if (cooldown_until and cooldown_until > now) else 0
    ))

    # 3. Head-End Hardwired Inputs
    he = state.get("head_end", {})
    lines.append(_format_metric(
        "smart_water_head_end_configured", "gauge",
        "Whether head-end GPIO pin configuration is loaded",
        he.get("configured", False)
    ))
    lines.append(_format_metric(
        "smart_water_head_end_stale", "gauge",
        "Whether head-end sensor telemetry has exceeded stale timeout",
        he.get("is_stale", True)
    ))
    if he.get("tank_low_float") is not None:
        lines.append(_format_metric(
            "smart_water_tank_low_float", "gauge",
            "Logical state of IN1 overhead tank low float switch (1 = low/dry, 0 = submerged)",
            he.get("tank_low_float")
        ))
    if he.get("interlock_out") is not None:
        lines.append(_format_metric(
            "smart_water_interlock_out", "gauge",
            "Hardware interlock chain closed output (1 = closed/safe to run, 0 = open)",
            he.get("interlock_out")
        ))
    if he.get("interlock_mid") is not None:
        lines.append(_format_metric(
            "smart_water_interlock_mid", "gauge",
            "Hardware interlock mid-point tap reading between Full and Sump floats",
            he.get("interlock_mid")
        ))
    if he.get("leak_detected") is not None:
        lines.append(_format_metric(
            "smart_water_leak_detected", "gauge",
            "Head-end bund / pump room moisture sensor status (1 = leak detected, 0 = dry)",
            he.get("leak_detected")
        ))
    if he.get("contactor_relay") is not None:
        lines.append(_format_metric(
            "smart_water_contactor_relay", "gauge",
            "Head-end GPIO contactor driver output line state",
            he.get("contactor_relay")
        ))
    lines.append(_format_metric(
        "smart_water_wiring_fault_latched", "gauge",
        "Whether impossible interlock chain condition latched safety shutdown",
        he.get("wiring_fault_latched", False)
    ))

    # 4. Wireless Garden Node (ESP32-C3)
    garden = state.get("garden", {})
    lines.append(_format_metric(
        "smart_water_garden_gateway_connected", "gauge",
        "Whether serial CDC connection to ESP32 gateway is healthy",
        garden.get("gateway_connected", False)
    ))
    lines.append(_format_metric(
        "smart_water_garden_node_online", "gauge",
        "Whether garden node is actively transmitting ESP-NOW telemetry",
        garden.get("node_online", False)
    ))
    lines.append(_format_metric(
        "smart_water_garden_stale", "gauge",
        "Whether garden node telemetry has exceeded staleness threshold",
        garden.get("is_stale", True)
    ))
    if garden.get("rssi") is not None:
        lines.append(_format_metric(
            "smart_water_garden_rssi_dbm", "gauge",
            "ESP-NOW wireless packet Received Signal Strength Indicator in dBm",
            garden.get("rssi")
        ))
    if garden.get("uptime_s") is not None:
        lines.append(_format_metric(
            "smart_water_garden_uptime_seconds", "gauge",
            "Garden node remote microcontroller uptime in seconds",
            garden.get("uptime_s")
        ))
    lines.append(_format_metric(
        "smart_water_garden_boot_count", "counter",
        "Garden node boot announcement count persisted in NVS",
        garden.get("boot_count", 0)
    ))
    if garden.get("reservoir_wet") is not None:
        lines.append(_format_metric(
            "smart_water_garden_reservoir_wet", "gauge",
            "Garden rainwater/irrigation reservoir float status (1 = wet/submerged, 0 = dry)",
            garden.get("reservoir_wet")
        ))

    # BME280 Environment telemetry
    env = garden.get("env", {})
    if env.get("temp_c") is not None:
        lines.append(_format_metric(
            "smart_water_garden_env_temperature_celsius", "gauge",
            "Garden ambient temperature from BME280 sensor in Celsius",
            env.get("temp_c")
        ))
    if env.get("rh_pct") is not None:
        lines.append(_format_metric(
            "smart_water_garden_env_relative_humidity_percent", "gauge",
            "Garden ambient relative humidity from BME280 sensor in percent",
            env.get("rh_pct")
        ))
    if env.get("pressure_hpa") is not None:
        lines.append(_format_metric(
            "smart_water_garden_env_pressure_hpa", "gauge",
            "Garden barometric atmospheric pressure from BME280 sensor in hPa",
            env.get("pressure_hpa")
        ))

    # 5. Plant Irrigation Zones
    zones = garden.get("zones", {})
    for z_id, z_data in sorted(zones.items()):
        lbl = {"zone": z_id}
        if z_data.get("moisture_pct") is not None:
            lines.append(_format_metric(
                "smart_water_zone_moisture_percent", "gauge",
                "Calibrated soil moisture percentage (0-100%)",
                z_data.get("moisture_pct"), labels=lbl
            ))
        if z_data.get("voltage") is not None:
            lines.append(_format_metric(
                "smart_water_zone_voltage_volts", "gauge",
                "Raw analog voltage reading from capacitive soil probe",
                z_data.get("voltage"), labels=lbl
            ))
        lines.append(_format_metric(
            "smart_water_zone_pump_running", "gauge",
            "Whether 12V submersible pump is active for this zone",
            1 if z_data.get("pump_running") else 0, labels=lbl
        ))
        lines.append(_format_metric(
            "smart_water_zone_flow_fault", "gauge",
            "Whether flow sensor detected zero pulses during pump pulse",
            1 if z_data.get("flow_fault") else 0, labels=lbl
        ))
        lines.append(_format_metric(
            "smart_water_zone_daily_litres", "gauge",
            "Cumulative litres of water delivered today to this zone",
            z_data.get("daily_litres", 0.0), labels=lbl
        ))

    # 6. Motor Power Meter (PZEM-004T / CT Clamp)
    pm = state.get("power_meter", {})
    lines.append(_format_metric(
        "smart_water_power_meter_available", "gauge",
        "Whether motor power and current sensor is active and communicating",
        pm.get("available", False)
    ))
    if pm.get("voltage_v") is not None:
        lines.append(_format_metric(
            "smart_water_mains_voltage_volts", "gauge",
            "Single-phase AC mains voltage RMS in Volts",
            pm.get("voltage_v")
        ))
    if pm.get("current_a") is not None:
        lines.append(_format_metric(
            "smart_water_motor_current_amperes", "gauge",
            "Motor running current RMS in Amperes",
            pm.get("current_a")
        ))
    if pm.get("power_w") is not None:
        lines.append(_format_metric(
            "smart_water_motor_power_watts", "gauge",
            "Motor active real power in Watts",
            pm.get("power_w")
        ))
    if pm.get("energy_kwh") is not None:
        lines.append(_format_metric(
            "smart_water_motor_energy_kwh", "counter",
            "Cumulative motor energy consumption in kWh",
            pm.get("energy_kwh")
        ))
    if pm.get("frequency_hz") is not None:
        lines.append(_format_metric(
            "smart_water_mains_frequency_hertz", "gauge",
            "AC mains electrical frequency in Hertz",
            pm.get("frequency_hz")
        ))
    if pm.get("power_factor") is not None:
        lines.append(_format_metric(
            "smart_water_motor_power_factor", "gauge",
            "Motor electrical power factor (0.00 - 1.00)",
            pm.get("power_factor")
        ))

    # 7. Continuous Overhead Tank Level Sensor
    ls = state.get("level_sensor", {})
    lines.append(_format_metric(
        "smart_water_level_sensor_available", "gauge",
        "Whether continuous water level sensor is initialized and online",
        ls.get("available", False)
    ))
    if ls.get("level_pct") is not None:
        lines.append(_format_metric(
            "smart_water_tank_level_percent", "gauge",
            "Continuous overhead tank water level percentage (0-100%)",
            ls.get("level_pct")
        ))
    if ls.get("water_depth_cm") is not None:
        lines.append(_format_metric(
            "smart_water_tank_water_depth_cm", "gauge",
            "Calculated tank internal water depth in centimeters",
            ls.get("water_depth_cm")
        ))
    if ls.get("volume_litres") is not None:
        lines.append(_format_metric(
            "smart_water_tank_volume_litres", "gauge",
            "Calculated overhead tank stored water volume in Litres",
            ls.get("volume_litres")
        ))
    if ls.get("fill_rate_lpm") is not None:
        lines.append(_format_metric(
            "smart_water_tank_fill_rate_lpm", "gauge",
            "Estimated tank replenishment rate in Litres per minute",
            ls.get("fill_rate_lpm")
        ))
    lines.append(_format_metric(
        "smart_water_tank_daily_consumption_litres", "gauge",
        "Estimated household water consumption today derived from tank level drop",
        ls.get("daily_consumption_litres", 0.0)
    ))

    # 8. Weather Forecast & Smart Skip
    weather = state.get("weather", {})
    lines.append(_format_metric(
        "smart_water_weather_skip_active", "gauge",
        "Whether automated irrigation is currently skipped due to rain forecast",
        weather.get("skip_active", False)
    ))
    if weather.get("rain_prob_max_pct") is not None:
        lines.append(_format_metric(
            "smart_water_weather_rain_prob_max_percent", "gauge",
            "Maximum rain probability in forecast window from Open-Meteo",
            weather.get("rain_prob_max_pct")
        ))
    if weather.get("rainfall_past_24h_mm") is not None:
        lines.append(_format_metric(
            "smart_water_weather_rainfall_past_24h_mm", "gauge",
            "Past 24 hours precipitation in millimeters from Open-Meteo",
            weather.get("rainfall_past_24h_mm")
        ))

    # 9. Active Alarms & Interlock Faults
    alarms = state.get("alarms", {})
    lines.append(_format_metric(
        "smart_water_active_alarms_total", "gauge",
        "Total count of currently active safety alarms and interlock trips",
        len(alarms)
    ))
    for alarm_name in sorted(alarms.keys()):
        lines.append(_format_metric(
            "smart_water_alarm_active", "gauge",
            "Active state of a specific safety interlock alarm (1 = active)",
            1, labels={"alarm": str(alarm_name)}
        ))

    # Join non-empty metric sections with single newline
    content = "".join([m for m in lines if m])
    return content
