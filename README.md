# Industrial IoT Water Level & Precision Irrigation Controller
### Embedded Linux Architecture for Radxa ROCK Pi S (Rockchip RK3308, 512MB RAM)

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Armbian%20%2F%20Debian%20Headless-red.svg)](https://armbian.com)
[![RAM Budget](https://img.shields.io/badge/RAM%20Budget-%3C35%20MB-brightgreen.svg)]()
[![Hardware](https://img.shields.io/badge/SBC-Radxa%20ROCK%20Pi%20S%20(RK3308)-orange.svg)](https://radxa.com/products/rockpi/s)
[![Tests](https://img.shields.io/badge/Tests-18%20Passed%20(100%25)-green.svg)]()

Industrial-grade, ultra-lightweight IoT Automation and Precision Irrigation Controller engineered specifically for the resource-constrained **Radxa ROCK Pi S** (Quad-Core ARM Cortex-A35, 512MB RAM).

---

## 🏗️ System Architecture & Memory Engineering

```
                      +------------------------------------------+
                      |         Tailscale / Headscale Mesh        |
                      +--------------------+---------------------+
                                           | Private Mesh IP
                                           v
+-----------------------------------------------------------------------------------+
| Radxa ROCK Pi S (Rockchip RK3308 Headless Armbian, RAM Budget < 35 MB)            |
|                                                                                   |
|  FastAPI + Uvicorn Async Process                                                 |
|  ├── Core: In-Memory Pub/Sub EventBus & State Store                               |
|  ├── DB: SQLite in WAL Mode (PRAGMA cache_size = -2000, ~2MB)                     |
|  ├── Hardware: libgpiod (/dev/gpiochip1) + smbus2 I2C7 (/dev/i2c-7)               |
|  ├── Audio: espeak-ng + ALSA over USB DAC (/dev/snd)                              |
|  ├── Watchdog: Linux Kernel Hardware Keep-Alive (/dev/watchdog)                   |
|  └── Web Engine: Real-Time SSE Stream (/api/telemetry/stream) + Dynamic SVG UI   |
+-------------------+----------------------+--------------------+-------------------+
                    |                      |                    |
                    v                      v                    v
          +------------------+   +-------------------+   +----------------+
          |  ADS1115 16-Bit  |   | 4-CH MOSFET Board |   | 25A Contactor  |
          |     I2C7 ADC     |   |   (Optocoupled)   |   | (AC Motor 230V)|
          +--------+---------+   +---------+---------+   +--------+-------+
                   |                       |                      |
                   v                       v                      v
          4x Capacitive Probes     4x 12V DC Pumps       Overhead Water Tank
          (Zone 1-4 Soil)         (Precision Drip)        (1000L Domestic)
```

### ⚡ Strict Resource Hardening (<35MB Total Runtime Budget)
1. **Zero Client Bundlers**: No Node.js, Webpack, or npm runtime on the target board.
2. **Alpine.js & Clean Standalone CSS**: Sub-100KB client footprint served directly by FastAPI.
3. **Optimized Single Process**: Single Python 3.11+ asynchronous process serving REST, SSE, and static files.
4. **SQLite WAL Tuning**: Strict hardware PRAGMAs (`cache_size = -2000`, `synchronous = NORMAL`, `temp_store = MEMORY`, `auto_vacuum = INCREMENTAL`) preventing SD card wear and keeping database memory strictly under 2MB.
5. **Systemd Cgroups**: Process memory bounded by `MemoryHigh=35M` and `MemoryMax=45M`.

---

## 📌 Hardware Pin Allocation (ROCK Pi S Header 1, `gpiochip1`)

| Signal Description | Line Offset | Pin # | Electrical Spec | Direction | Safe Initial State |
|--------------------|-------------|-------|-----------------|-----------|--------------------|
| **Tank Low Float** | Line 10 | Pin 16 | Active LOW (Pull-up) | INPUT | Closed (Tripped) when water < 250L |
| **Tank High Float** | Line 11 | Pin 18 | Active LOW (Pull-up) | INPUT | Closed (Tripped) when tank is full (>=900L) |
| **Reservoir Empty Float** | Line 12 | Pin 22 | Active LOW (Pull-up) | INPUT | Closed (Tripped) when reservoir <= 30L |
| **AC Contactor (25A)** | Line 13 | Pin 7 | Active HIGH (10k pull-down) | OUTPUT | LOW (Pump De-energized) |
| **Zone 1 MOSFET** | Line 15 | Pin 8 | Active HIGH (10k pull-down) | OUTPUT | LOW (Pump 1 De-energized) |
| **Zone 2 MOSFET** | Line 22 | Pin 10 | Active HIGH (10k pull-down) | OUTPUT | LOW (Pump 2 De-energized) |
| **Zone 3 MOSFET** | Line 23 | Pin 11 | Active HIGH (10k pull-down) | OUTPUT | LOW (Pump 3 De-energized) |
| **Zone 4 MOSFET** | Line 24 | Pin 12 | Active HIGH (10k pull-down) | OUTPUT | LOW (Pump 4 De-energized) |
| **I2C7 SDA** | Bus 7 | Pin 3 | 3.3V I2C Bus | I/O | ADS1115 Address 0x48 |
| **I2C7 SCL** | Bus 7 | Pin 5 | 3.3V I2C Bus | I/O | ADS1115 Address 0x48 |

---

## 🛡️ Priority 0 Non-Bypassable Hardcoded Failsafes

Safety interlocks execute before any user rule, scheduled task, or manual override:

1. **Dry-Run Lockout**: If Ground Reservoir float indicates EMPTY, all 4 DC plant pumps are forced OFF and locked.
2. **Overhead Tank High Cutoff**: If Overhead Tank High float trips, AC Contactor is forced OFF immediately.
3. **Max Motor Runtime Guard**: If AC Contactor is ON continuously for >15 minutes, emergency cutoff is triggered, motor is locked out, and voice alert is sounded. Requires operator reset.
4. **Out-of-Bounds Sensor Guard**: If capacitive probe voltage reads `< 0.5V` (short circuit) or `> 3.0V` (disconnected), probe is flagged, irrigation is locked for that zone, and voice warning is spoken.
5. **Zone Cooldown Lockout**: Enforces a strict 6-hour lockout between watering cycles per zone to prevent root rot.

---

## 🧩 Low-Code / No-Code JSON Rule Specification

Automations are defined via clean JSON schemas evaluated every 5 seconds:

```json
{
  "id": "tpl-morning-drip",
  "name": "Morning Precision Drip",
  "enabled": true,
  "combinator": "AND",
  "conditions": [
    {
      "source": "sensor.zone_1_moisture",
      "operator": "<",
      "value": 35.0
    },
    {
      "source": "reservoir.state",
      "operator": "==",
      "value": "ok"
    }
  ],
  "actions": [
    {
      "target": "pump.zone_1",
      "duration_seconds": 20
    },
    {
      "target": "voice.announce",
      "speech_text": "Executing morning precision drip for Zone 1."
    }
  ]
}
```

---

## 🎨 Interactive Circuit Schematic Web UI

The dashboard renders an interactive SVG schematic with dynamic animations:
- **Wire Traces**: Animated SVG `stroke-dasharray` pulse when electrical current or signals are flowing.
- **Pin Status**: Digital inputs/outputs illuminate in green, blue, or red.
- **Fluid Levels**: Animated water level rectangles reflect live tank percentages.
- **Pump Impellers**: Impellers rotate with CSS transforms when pumps are energized.
- **Tooltips**: Hovering or tapping any node displays live hardware line offsets, logic levels, voltages, and failsafe states.

---

## 🚀 Installation & Deployment

### 1. One-Click Setup on Armbian / Debian
```bash
sudo git clone https://github.com/radxa/smart-water-controller.git /opt/smart-water
cd /opt/smart-water
sudo bash install.sh
```

### 2. Manual Development & Sandbox Run
```bash
# Clone and enter directory
cd rockpi-water-level-flow-automation

# Install Python requirements
pip install -r requirements.txt

# Run in virtualized mock mode
MOCK_HARDWARE=true python run.py
```

### 3. Running Verification Test Suite
```bash
pytest -v
```

All 18 unit, failsafe, mock, and API integration tests pass with 100% pass rate.
