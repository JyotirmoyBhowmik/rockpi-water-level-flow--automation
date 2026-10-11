# Radxa ROCK Pi S Smart Water & Precision Irrigation Controller

[![Linux](https://img.shields.io/badge/Platform-Armbian%20%7C%20Debian-blue.svg)](https://armbian.com)
[![Hardware](https://img.shields.io/badge/SoC-Rockchip%20RK3308-orange.svg)](https://radxa.com)
[![RAM Budget](https://img.shields.io/badge/RAM%20Budget-%3C35MB-green.svg)](#)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-lightgrey.svg)](#)

An industrial-grade, dual-layer, hybrid IoT automation controller designed for the **Radxa ROCK Pi S** (Rockchip RK3308, 512MB RAM) running headless Armbian/Debian Linux. 

The controller orchestrates domestic overhead water tank replenishment (via a 25A AC contactor) and a 4-zone wireless plant drip irrigation system (via an ESP32-C3 Super Mini running ESP-NOW), featuring physical electromechanical interlocks, hardware-timed safety cutoffs, synthesized voice alerts, and zero-trust mesh networking.

---

## Table of Contents
1. [System Architecture & Dual-Layer Safety](#1-system-architecture--dual-layer-safety)
2. [Bill of Materials (BOM) & Sourcing](#2-bill-of-materials-bom--sourcing)
3. [Rock Pi S Pinout & Hardware Pin Mapping](#3-rock-pi-s-pinout--hardware-pin-mapping)
4. [ESP32-C3 Super Mini Pinout & Hardware Pin Mapping](#4-esp32-c3-super-mini-pinout--hardware-pin-mapping)
5. [Interlock Chain Electrical Theory & Truth Table](#5-interlock-chain-electrical-theory--truth-table)
6. [Calibration Guide (Soil Moisture & Pulse Flow Meter)](#6-calibration-guide-soil-moisture--pulse-flow-meter)
7. [Headscale / Tailscale Mesh VPN Configuration](#7-headscale--tailscale-mesh-vpn-configuration)
8. [Prerequisites & System Requirements](#8-prerequisites--system-requirements)
9. [One-Click Installation & Systemd Service](#9-one-click-installation--systemd-service)
10. [First Boot & Setup Wizard Flow](#10-first-boot--setup-wizard-flow)
11. [Offline Operation & RTC Battery Backup](#11-offline-operation--rtc-battery-backup)
12. [Audio Alerts & Speech Synthesis (ALSA / espeak-ng)](#12-audio-alerts--speech-synthesis-alsa--espeak-ng)
13. [Low-Code Rule Builder & Automation Examples](#13-low-code-rule-builder--automation-examples)
14. [Serial Gateway Protocol (JSON Lines v1 Specification)](#14-serial-gateway-protocol-json-lines-v1-specification)
15. [REST & SSE Telemetry API Reference](#15-rest--sse-telemetry-api-reference)
16. [Database Schema & WAL Storage Architecture](#16-database-schema--wal-storage-architecture)
17. [Troubleshooting & Diagnostic Runbook (Quick Links)](#17-troubleshooting--diagnostic-runbook-quick-links)
18. [Production Hardening & Security Audit Checklist](#18-production-hardening--security-audit-checklist)
19. [Maintenance, Backup & Disaster Recovery](#19-maintenance-backup--disaster-recovery)

---

## 1. System Architecture & Dual-Layer Safety

The system implements a **Dual-Layer Defense-in-Depth** architecture across two isolated physical domains:

```
[ Head-End: ROCK Pi S (Armbian Headless) ]
  ├── Hardware Interlock Layer 1: Passive electromechanical float switches directly break contactor VCC
  ├── Software Failsafe Layer 2: Priority 0 Failsafe Guardian (15m runtime guard, 30m cooldown, leak trip)
  ├── Hardwired PC817 Opto Inputs (IN1: Tank Low, IN2: Interlock Out, IN3: Interlock Mid, IN4: Leak)
  ├── 1x Contactor Relay Driver (25A AC contactor switching 230V submersible pump)
  ├── DS3231 I2C RTC (0x68) & Optional SSD1306 OLED (0x3C)
  ├── C-Media CM108 USB Audio DAC (ALSA voice alerts via espeak-ng)
  └── Linux Kernel Watchdog (/dev/watchdog) & systemd sd_notify

[ USB Gateway: ESP32-C3 Super Mini on USB Hub ]
  └── Bridges USB Serial (/dev/garden-gateway at 115200 baud) <---> ESP-NOW (2.4GHz Wireless)

[ Wireless Garden Node: ESP32-C3 Super Mini (Solar / Battery) ]
  ├── ADS1115 16-Bit I2C ADC (0x48) reading 4x Capacitive Soil Probes (A0-A3)
  ├── 4x Logic-Level N-MOSFET drivers for 12V DC Drip Pumps (Hardware 60s firmware timeout)
  ├── 1x Reed Float Switch (Reservoir low-level dry-run protection)
  └── 1x Hall-Effect Pulse Flow Meter on Zone 1 (10s pulse verification)
```

---

## 2. Bill of Materials (BOM) & Sourcing

| Component | Quantity | Specification / Model | Est. Cost (USD) | Function |
| :--- | :---: | :--- | :---: | :--- |
| **SBC** | 1 | Radxa ROCK Pi S (512MB RAM, RK3308B) | $18.00 | Head-End Controller |
| **Microcontroller** | 2 | ESP32-C3 Super Mini (RISC-V, Type-C) | $6.00 | Gateway & Garden Node |
| **Optoisolator Board** | 1 | 4-Channel PC817 Optocoupler Isolation Board | $2.50 | Float & Leak Galvanic Isolation |
| **AC Contactor** | 1 | 25A 2-Pole AC Contactor (230V Coil or 12V Coil) | $12.00 | Main Overhead AC Pump Switching |
| **Contactor Relay Board** | 1 | 1-Channel 5V/12V Optocoupled Relay Module | $1.50 | Pilot relay driving contactor coil |
| **ADC Module** | 1 | ADS1115 16-Bit 4-Channel I2C ADC Module | $3.00 | Precision Soil Moisture Digitization |
| **Soil Probes** | 4 | Capacitive Soil Moisture Sensor v1.2 / v2.0 | $6.00 | Corrosion-free soil impedance probe |
| **MOSFET Module** | 1 | 4-Channel Logic-Level N-MOSFET (LR7843 / AO3400) | $3.50 | Low-side switching of 12V DC pumps |
| **Submersible Pumps** | 4 | 12V DC 2A Micro Submersible Water Pumps | $14.00 | Precision drip delivery for Zones 1-4 |
| **Flow Meter** | 1 | YF-S201 Hall-Effect Pulse Flow Meter | $4.50 | Zone 1 pulse monitoring & leak check |
| **Float Switches** | 3 | Stainless Steel / PP Vertical Float Switches | $6.00 | Full Float (NC), Sump (NO), Reservoir (NO) |
| **RTC Module** | 1 | DS3231 I2C Precision RTC + CR2032 Battery | $2.50 | Monotonic time during internet loss |
| **USB Audio** | 1 | CM108 USB Audio DAC Adapter + Powered Speaker | $4.50 | Synthesized voice alerts |
| **Total** | - | - | **~$81.50** | Complete Industrial Automation |

---

## 3. Rock Pi S Pinout & Hardware Pin Mapping

Operating strictly at **3.3V logic levels** (Line voltages > 3.3V will permanently destroy the RK3308).

```
                      Radxa ROCK Pi S - Header 1 (26-Pin)
                   +---------------------------------------+
   3.3V Power (1)  | [01] [02] | (2)  5V Power (Fused)
   I2C7_SDA   (3)  | [03] [04] | (4)  5V Power
   I2C7_SCL   (5)  | [05] [06] | (6)  Ground
  GPIO1_B2/IN1 (7) | [07] [08] | (8)  UART0_TX
  GPIO1_B3/IN2 (9) | [09] [10] | (10) UART0_RX
 GPIO1_B4/IN3 (11) | [11] [12] | (12) GPIO1_B5/IN4
              (13) | [13] [14] | (14) Ground
GPIO1_C0/Relay(15) | [15] [16] | (16) GPIO1_C1
              (17) | [17] [18] | (18) GPIO
                   +---------------------------------------+
```

*Note: All pins are defined in `/etc/smart-water/config.yaml`. No hardcoded pin numbers exist in the source code.*

---

## 4. ESP32-C3 Super Mini Pinout & Hardware Pin Mapping

```
Pin 1  (5V IN)        -> 5V DC Supply Rail
Pin 2  (GND)          -> Common DC Ground
Pin 3  (GPIO 2)       -> Zone 1 Flow Meter (Pulse Interrupt)
Pin 4  (GPIO 3)       -> Reservoir Float Switch (Reed Switch to GND)
Pin 5  (GPIO 4)       -> ADS1115 I2C SDA
Pin 6  (GPIO 5)       -> ADS1115 I2C SCL
Pin 11 (GPIO 6)       -> Zone 1 Pump MOSFET Gate
Pin 12 (GPIO 7)       -> Zone 2 Pump MOSFET Gate
Pin 13 (GPIO 10)      -> Zone 3 Pump MOSFET Gate
Pin 14 (GPIO 20)      -> Zone 4 Pump MOSFET Gate
Pin 9  (GPIO 8)       -> Onboard Status LED (Active Low)
```

---

## 5. Interlock Chain Electrical Theory & Truth Table

A fused 5V source is wired through two electromechanical switches in series:
`+5V Fused -> Full Float (NC) -> Tap (IN3: interlock_mid) -> Sump Float (NO) -> Tap (IN2: interlock_out) -> Relay Module VCC`

```
+----------------+----------------+---------------------+-------------------+---------------------+
| interlock_out  | interlock_mid  | Physical Condition  | Hardware Reaction | Software State      |
+----------------+----------------+---------------------+-------------------+---------------------+
| HIGH (1)       | HIGH (1)       | Chain Intact        | Motor Allowed     | healthy             |
| LOW (0)        | LOW (0)        | Full Float Tripped  | VCC Cutoff        | tank_full           |
| LOW (0)        | HIGH (1)       | Sump Float Open     | VCC Cutoff        | dry_sump            |
| HIGH (1)       | LOW (0)        | IMPOSSIBLE STATE    | FORCED LOCKOUT    | wiring_fault        |
+----------------+----------------+---------------------+-------------------+---------------------+
```

---

## 6. Calibration Guide (Soil Moisture & Pulse Flow Meter)

### 6.1 Capacitive Soil Moisture Calibration
Capacitive soil probes read inverted analog voltage: dry soil = high voltage (~2.6V), saturated wet soil = low voltage (~1.2V).
1. **Dry Step:** Expose probe to dry ambient air. Trigger dry capture:
   `POST /api/zones/zone_1/calibrate {"step": "dry"}`
2. **Wet Step:** Submerge probe into a glass of tap water up to the silkscreen depth line. Trigger wet capture:
   `POST /api/zones/zone_1/calibrate {"step": "wet"}`
3. The software writes reference voltages and sanity margin (`margin_v: 0.4V`) to `/etc/smart-water/config.yaml`.
4. Readings outside `[wet_v - margin .. dry_v + margin]` trigger an immediate sanity band alarm.

### 6.2 Zone 1 Pulse Flow Meter Calibration
1. The YF-S201 flow meter outputs pulses proportional to water volume (~450 pulses per liter).
2. `min_pulses_10s` is configured to 4 pulses. If Zone 1 runs for 10 seconds and accumulates fewer than 4 pulses, the system declares a `NO FLOW` fault and cuts the pump immediately.

---

## 7. Headscale / Tailscale Mesh VPN Configuration

To satisfy security isolation constraints without exposing public ports:
1. Install Tailscale:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   ```
2. Connect to your private Headscale / Tailscale mesh:
   ```bash
   sudo tailscale up --login-server=https://headscale.yourdomain.com --accept-routes
   ```
3. Verify assigned IP:
   ```bash
   tailscale ip -4
   # Example: 100.64.0.15
   ```
4. Set `network.bind_address: "100.64.0.15"` in `/etc/smart-water/config.yaml`.

---

## 8. Prerequisites & System Requirements

- **Target Board:** Radxa ROCK Pi S (512MB RAM, RK3308).
- **Operating System:** Armbian / Debian 12 (Bookworm) or Debian 13 (Trixie) Minimal Headless.
- **Micro-SD Card:** High Endurance Class 10 (8GB or larger).
- **Kernel Watchdog:** `/dev/watchdog` enabled in device tree.
- **I2C7 Overlay:** Enabled via `overlays=i2c7` in `/boot/armbianEnv.txt`.

---

## 9. One-Click Installation & Systemd Service

Deploy to target board in one command:
```bash
sudo ./install.sh
```

The script sets up:
1. Dedicated unprivileged system user `smartwater` (groups: `gpio, i2c, dialout, audio`).
2. Virtualenv in `/opt/smart-water/venv` with zero-compile pip wheels.
3. Stable udev symlink `/dev/garden-gateway` for ESP32-C3 USB CDC.
4. Cryptographic Bearer token in `/etc/smart-water/auth.json`.
5. Hardened systemd service with memory cgroups (`MemoryHigh=35M`, `MemoryMax=45M`) and 15s watchdog keepalive.

> [!NOTE]
> **Production Exclusion:** The `tests/` directory is strictly excluded from `install.sh` and production deployment. Unit tests run only in developer test environments and may be deleted on the physical board to conserve disk space.

---

## 10. First Boot & Setup Wizard Flow

On first boot with unconfigured pins, the system boots into `SAFE-NO-HARDWARE` mode. Point your browser to `http://<tailscale-ip>:8000`:
1. **Welcome:** Explains dual-layer safety architecture.
2. **PIN Offset Selection:** Displays detected pins from `/api/gpioinfo`.
3. **PC817 Opto Input Testing:** Live test of float switch states.
4. **Relay Pilot Test:** 2-second safe relay click verification.
5. **Gateway Pairing:** Verifies serial packets on `/dev/garden-gateway`.
6. **Probe Calibration:** Step-by-step air/water calibration.
7. **Complete:** Generates final `/etc/smart-water/config.yaml` and starts normal operation.

---

## 11. Offline Operation & RTC Battery Backup

1. A DS3231 RTC on `/dev/i2c-7` (address `0x68`) provides hardware battery-backed timekeeping.
2. If internet connection is lost, system maintains monotonic time across power cycles.
3. If RTC battery dies and system boots with un-synced clock (earlier than `BUILD_DATE_EPOCH`), automated watering is blocked until time is established.

---

## 12. Audio Alerts & Speech Synthesis (ALSA / espeak-ng)

The controller speaks synthesized voice notifications over a USB audio interface:
- **Priority 0 (Immediate):** Emergency leak detection, wiring faults, max runtime cutoffs. (Never silenced).
- **Priority 1 (Warning):** Motor started, tank full, sump dry.
- **Priority 2 (Informational):** Zone watering pulses. (Silenced during Quiet Hours 22:00-06:00).

---

## 13. Low-Code Rule Builder & Automation Examples

The built-in Rule Studio allows visual, block-based rule creation stored directly in SQLite:
- **Condition Sources:** `tank.low`, `motor.state`, `interlock.state`, `zone_N.moisture`, `zone_N.volts`, `reservoir.state`, `leak.state`, `time.clock`, `link.state`.
- **Action Targets:** `pump.zone_N`, `motor.start`, `motor.stop`, `voice.announce`, `webhook.post`, `telegram.send`.

---

## 14. Serial Gateway Protocol (JSON Lines v1 Specification)

Every message is a single JSON object terminated by `\n` with `"v": 1`.

### Node / Gateway -> Pi:
```json
{"v":1,"t":"hb","seq":142,"uptime_s":710,"rssi":-64}
{"v":1,"t":"soil","z":1,"volts":1.842,"ts_ms":710200}
{"v":1,"t":"res","wet":true,"ts_ms":710250}
{"v":1,"t":"flow","z":1,"pulses":48,"flow_lpm":0.0,"ts_ms":710300}
{"v":1,"t":"gw_hb","uptime_s":1420}
```

### Pi -> Gateway / Node:
```json
{"v":1,"cmd":"pump","z":1,"state":1,"duration_s":15}
{"v":1,"cmd":"stop_all"}
```

---

## 15. REST & SSE Telemetry API Reference

See complete documentation in [docs/API.md](docs/API.md).
- `GET /healthz` - Unauthenticated health check.
- `GET /api/telemetry/live` - Instant snapshot of live state.
- `GET /api/telemetry/stream` - 1Hz Server-Sent Events stream.
- `POST /api/motor/start` - Energize 25A contactor (`{"confirm": true}`).
- `POST /api/motor/reset` - Clear safety lockout after cooldown.
- `POST /api/zones/{id}/water` - Energize precision drip pump.
- `POST /api/control/emergency-stop` - Emergency shutdown of all outputs.

---

## 16. Database Schema & WAL Storage Architecture

SQLite database is located at `/var/lib/smart-water/state.db`.
- **WAL Mode:** Write-Ahead-Logging with `-2000` cache size (~2MB RAM footprint).
- **Zero Seeded Rows:** Database initializes completely empty on fresh install.
- **Tables:** `readings` (downsampled 60s telemetry), `events` (safety interlock logs), `runs` (actuator runtime history), `rules` (user automation rules), `user_rule_templates`.

---

## 17. Troubleshooting & Diagnostic Runbook (Quick Links)

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for 28 diagnostic scenarios covering wiring faults, link drops, audio errors, and safety lockouts.

---

## 18. Production Hardening & Security Audit Checklist

- [x] Unprivileged service execution under `smartwater` system user.
- [x] Memory limit cgroup enforced (`MemoryHigh=35M`, `MemoryMax=45M`).
- [x] Bearer token constant-time SHA-256 verification.
- [x] Rate limiting on control endpoints (60 actions/min).
- [x] Refusal to bind `0.0.0.0` without explicit configuration.
- [x] Kernel hardware watchdog keepalive ping loop.
- [x] All outputs forced OFF on exit or unhandled crash.

---

## 19. Maintenance, Backup & Disaster Recovery

### 19.1 Atomic Database Backup
Generate a zero-downtime atomic database snapshot:
```bash
curl -X POST -H "Authorization: Bearer $AUTH_TOKEN" http://localhost:8000/api/backup
```

### 19.2 Token Rotation
Rotate the API token at any time:
```bash
sudo -u smartwater /opt/smart-water/venv/bin/python -m app.cli rotate-token
```

### 19.3 Clean Uninstallation
To completely remove the controller and restore default OS state:
```bash
sudo ./uninstall.sh
```
