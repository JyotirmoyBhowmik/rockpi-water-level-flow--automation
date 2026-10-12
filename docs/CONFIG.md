# System Configuration Guide (`/etc/smart-water/config.yaml`)

The Smart Water Controller is configured via `/etc/smart-water/config.yaml`.
All hardware pin mappings default to `null` on first installation. The system enforces strict Pydantic v2 validation at startup. If hardware pins are `null`, the app boots cleanly into `SAFE-NO-HARDWARE` mode with all outputs held OFF.

---

## 1. Full Configuration Reference

```yaml
# ==============================================================================
# Radxa ROCK Pi S Controller Configuration
# File Path: /etc/smart-water/config.yaml
# ==============================================================================

# Head-End Hardwired GPIO Mapping (PC817 Opto Board & Contactor Relay)
head_end:
  chip_path: null                  # e.g. "/dev/gpiochip1" (Radxa Rock Pi S)
  tank_low_float_pin: null         # IN1: Line offset for Tank Low float (Active LOW)
  interlock_out_pin: null          # IN2: Line offset for Interlock Out (HIGH = OK)
  interlock_mid_pin: null          # IN3: Line offset for Interlock Mid-point tap
  leak_sensor_pin: null            # IN4: Line offset for Leak sensor (Active LOW)
  contactor_relay_pin: null        # Output: Line offset for 25A AC contactor driver
  contactor_relay_active_high: true # true if HIGH energizes relay, false if LOW
  contactor_feedback_pin: null     # Optional: Auxiliary contact feedback line offset
  debounce_ms: 100                 # Hardware debounce filtering (10 - 1000 ms)
  tank_low_active_low: true        # IN1: true = LOW when float closed (PC817 open-collector)
  interlock_out_active_low: true   # IN2: true = LOW when chain energized (PC817 open-collector)
  interlock_mid_active_low: true   # IN3: true = LOW when mid-tap energized (PC817 open-collector)
  leak_active_low: true            # IN4: true = LOW when leak detected (PC817 open-collector)
  feedback_active_low: true        # Aux: true = LOW when aux contact closed (PC817 open-collector)

# Wireless Garden ESP32-C3 USB Gateway Serial Link
garden_gateway:
  serial_port: "/dev/garden-gateway" # Stable udev symlink to USB Serial CDC
  baud_rate: 115200                  # UART Baud Rate
  link_loss_timeout_s: 30            # Seconds without packet before raising LINK LOST

# Telemetry Staleness Thresholds (Stale Data is Unsafe Data)
stale_thresholds:
  head_end_s: 15                   # Max age (seconds) for head-end float readings
  garden_s: 90                     # Max age (seconds) for wireless garden readings

# Network Binding & Tailscale VPN Configuration
network:
  bind_address: "127.0.0.1"        # Set to your 100.x.y.z Tailscale IP or 127.0.0.1
  port: 8000                       # HTTP REST and SSE listening port
  allow_all_interfaces: false      # Refuses 0.0.0.0 unless explicitly true

# API Security & Rate Limiting
security:
  auth_token_file: "/etc/smart-water/auth.json"
  rate_limit_per_minute: 60        # Sliding window rate limit per client IP

# Overhead Tank Main Pump AC Motor Safeguards
motor:
  auto_mode: false                 # Enable automated replenishment when tank is low
  max_runtime_s: 900               # 15 minutes max continuous runtime limit
  cooldown_s: 1800                 # 30 minutes mandatory cooldown after max-runtime trip

# Wireless Garden Drip Zones (1-4)
zones:
  zone_1:
    name: "Zone 1 - Vegetables"
    enabled: false                 # Must be explicitly enabled
    pulse_seconds: 15              # Default watering pulse duration
    max_pulse_seconds: 60          # Server-side hard cap (<= 60s)
    cooldown_seconds: 21600        # 6 hours cooldown to prevent root rot
    has_flow_meter: true           # Hall-effect pulse flow meter connected on Zone 1
    min_pulses_10s: 4              # Minimum pulses expected within 10s of pumping
    calibration:
      dry_v: null                  # Air voltage (e.g. 2.65V)
      wet_v: null                  # Water voltage (e.g. 1.15V)
      margin_v: 0.4                # Sanity band margin [wet - margin .. dry + margin]

  zone_2:
    name: "Zone 2 - Herbs"
    enabled: false
    pulse_seconds: 15
    max_pulse_seconds: 60
    cooldown_seconds: 21600
    has_flow_meter: false
    min_pulses_10s: 0
    calibration:
      dry_v: null
      wet_v: null
      margin_v: 0.4

  zone_3:
    name: "Zone 3 - Perennials"
    enabled: false
    pulse_seconds: 20
    max_pulse_seconds: 60
    cooldown_seconds: 21600
    has_flow_meter: false
    min_pulses_10s: 0
    calibration:
      dry_v: null
      wet_v: null
      margin_v: 0.4

  zone_4:
    name: "Zone 4 - Shrubs"
    enabled: false
    pulse_seconds: 25
    max_pulse_seconds: 60
    cooldown_seconds: 21600
    has_flow_meter: false
    min_pulses_10s: 0
    calibration:
      dry_v: null
      wet_v: null
      margin_v: 0.4

# Synthesized Speech Audio Alerts (ALSA + espeak-ng)
audio:
  alsa_device: "default"           # ALSA device identifier (e.g. "hw:1,0" for CM108)
  volume_percent: 85
  voice_speed: 155                 # Words per minute
  quiet_hours_enabled: true
  quiet_hours_start: "22:00"       # Silence low-priority alerts at night
  quiet_hours_end: "06:00"

# Telegram Bot Critical Notifications
telegram:
  enabled: false
  bot_token: ""
  chat_id: ""

# Commissioning State
setup_complete: false              # Set to true once Setup Wizard is completed
```

---

## 2. Validation & Safety Constraints

1. **Zero Invented Pins:** Pin offsets cannot default to imaginary integers. If unassigned (`null`), outputs remain permanently OFF.
2. **Strict Network Binding:** If `bind_address` is set to `0.0.0.0` or `::` while `allow_all_interfaces` is `false`, application startup will intentionally crash with an explicit safety exception.
3. **Zone Pulse Cap:** `max_pulse_seconds` cannot exceed 60 seconds under any circumstances.
4. **Stale Thresholds:** Cannot be configured below 5s or above 600s to avoid unsafe automation decisions on dead telemetry.
