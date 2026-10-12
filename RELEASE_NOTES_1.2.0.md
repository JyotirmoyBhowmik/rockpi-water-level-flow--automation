# Release Notes - Version 1.2.0

**Release Date:** October 13, 2026  
**Target Hardware:** Radxa ROCK Pi S (Rockchip RK3308, 512 MB RAM, Debian 12 Bookworm)  
**Wireless Node Hardware:** ESP32-C3 Super Mini (Unicast ESP-NOW, Protocol v1.1)  
**License:** MIT / Industrial Production Standard  

---

## Executive Summary

Version 1.2.0 of the **Radxa ROCK Pi S Smart Water & Precision Irrigation Controller** represents a major production release delivering enterprise-grade safety, continuous level sensing, power monitoring, weather-aware irrigation scheduling, and hardened operations under a strict 512 MB RAM budget.

All 35 industrial diagnostic failure modes identified in the system safety audit have been formally resolved, verified, and sealed with 188 automated unit/integration tests and property-based regression suites maintaining **85% code coverage** across `app/core` and `app/engine`.

---

## Key Subsystems & Major Enhancements

### 1. Motor Current & Power Monitoring (`app/hardware/power_meter.py`)
- **Dual Hardware Support**: Seamless integration with Modbus RTU PZEM-004T v3 (over USB-TTL) or SCT-013 current transformer via ADS1115 head-end ADC.
- **Dry-Run Detection**: Monitors active current; trips and locks out motor if current drops below configurable percentage (default 70%) of learned baseline for >10 s.
- **Overload & Locked-Rotor Protection**: Stops motor immediately if current exceeds 130% of nameplate rating for >5 s.
- **Single-Phase Voltage Window**: Asserts line voltage remains within 207 V - 253 V window (230 V ±10%).
- **Self-Learning Baseline**: Automatically samples and averages steady-state current across the first 5 healthy pumping runs.
- **Per-Run Energy Logging**: Records precise kWh consumption per run into SQLite `runs` table.
- **Physical Safety Invariant**: Power monitoring functions strictly as a diagnostic supervisory layer and *never* replaces the physical bimetallic thermal overload relay or circuit breaker.

### 2. Continuous Tank Level Sensing (`app/hardware/level_sensor.py`)
- **Sensor Types**: Waterproof JSN-SR04T ultrasonic transceiver or 4-20 mA submersible hydrostatic pressure transducer (via ADS1115 with 250 Ω precision shunt).
- **Tank Geometry**: Supports rectangular and cylindrical tanks with configurable dimensions, dead-zones, and capacity limits.
- **Volumetric Tracking**: Reports real-time water level % and capacity in litres.
- **Fill-Rate & ETA Estimation**: Dynamic estimation of fill rate (L/min) and projected completion time.
- **Filling-Too-Slowly Alarm**: Triggers an alarm if fill rate is unexpectedly low, detecting pipe leaks, burst mains, or pump impeller cavitation.
- **Predictive Stop**: Gracefully de-energizes the motor before the mechanical Full float switch is engaged.
- **Night-Time Leak Detection**: Asserts volumetric stability during quiet hours with no active usage windows.
- **Hardware Interlock Invariant**: Float switches remain the authoritative hardware interlock; continuous sensor telemetry never overrides physical float contacts.

### 3. Weather-Aware Precision Irrigation (`app/engine/weather_service.py`)
- **Open-Meteo Integration**: Automated forecast retrieval every 3 hours via asynchronous HTTP with strict connection/read timeouts (zero API keys required).
- **Rain Skip Logic**: Automatically suspends scheduled irrigation if rain probability exceeds threshold (default 70%) or precipitation in the past 24 hours exceeded threshold (default 10 mm).
- **Microclimate Telemetry**: Integrates optional BME280 sensor on garden node (reporting ambient temperature, relative humidity, and barometric pressure via signed Protocol v1.1 `env` packets).
- **Soak Cycles**: Executes irrigation in pulse-and-wait intervals (e.g., 30 s run, 60 s soak) preventing runoff and soil erosion.
- **Volumetric Daily Budgets**: Enforces strict daily water volume caps per zone (litres/day) calculated using calibrated flow meter K-factors.
- **Safety Gate**: All automatic watering passes unconditionally through `failsafe_engine.can_water_zone()`.

### 4. Observability, Metrics & Telemetry Publishing
- **Prometheus `/metrics`**: Hand-crafted Prometheus text-format exporter (zero external client dependencies) protected by Bearer token authentication.
- **MQTT with Home Assistant Auto-Discovery**: Optional MQTT publisher (paho-mqtt) broadcasting tank levels, motor status, zone status, and alarms. Commands accepted only via signed/authorized topics and routed through the failsafe engine.
- **ntfy.sh Push Notifications**: Fast, self-hostable push notifications as a lightweight alternative to Telegram.
- **In-Browser SVG Historical Charts**: Pure SVG chart rendering without heavy JavaScript charting libraries.
- **Automated Daily Summaries**: Nightly diagnostic reports summarizing motor runtime, start count, water delivered, zone irrigation volumes, and system events.

### 5. Storage, Backup, Update & Operational Resilience
- **Read-Only Root Filesystem**: Support for `overlayroot` read-only root with `/var/lib/smart-water` mounted on a dedicated writable partition (or `log2ram` alternative).
- **Hardened SQLite Concurrency**: Configured with `PRAGMA busy_timeout=5000` and `PRAGMA synchronous=NORMAL` in WAL mode.
- **Automated Atomic Backups**: Nightly `VACUUM INTO` snapshots with 7-day retention in `/var/lib/smart-water/backups/` and optional rclone cloud sync.
- **A/B Atomic Software Updates**: CLI tool `python -m app.cli update --from <tarball>` with SHA-256 verification, dual install slots (`/opt/smart-water-a` and `/opt/smart-water-b`), automated post-restart health check, and automatic rollback upon failure.

### 6. RBAC, Security & User Experience
- **Role-Based Access Control**: Strict segregation between `operator` (read-write control) and `viewer` (read-only telemetry) tokens.
- **Token Management CLI**: Issue tokens with explicit expiry, revoke tokens, and inspect audit logs.
- **Audit Logging**: Every state-mutating operation logs operator identity, timestamp, IP address, and parameters into SQLite.
- **Progressive Web App (PWA)**: Web App Manifest and Service Worker (`sw.js`) caching static UI assets while strictly never caching `/api/*`, `/metrics`, or `/healthz`.
- **Mobile-First Industrial UI**: Responsive dashboard cards, persistent high-visibility Emergency Stop (E-STOP) button, and industrial dark mode.
- **Local OLED & Buzzer Drivers**: Standalone SSD1306 OLED display for offline monitoring and piezo buzzer acoustic failover for critical alarms.

---

## Verification & Quality Assurance Gate

| Verification Suite | Target | Status | Notes |
| :--- | :--- | :--- | :--- |
| **Ruff Linter** | Entire Codebase | **PASS** | Zero linting or formatting defects. |
| **Mypy Strict Typecheck** | `app/core` & `app/engine` | **PASS** | Strict mode passing across all 13 core files. |
| **Pytest Suite** | 188 Unit/Integration Tests | **PASS** | 188 passed in 61.5 s. |
| **Code Coverage** | `app/core` & `app/engine` | **85%** | Meets strict >=85% enterprise coverage gate. |
| **Protocol Contract Tests** | Protocol v1.1 Schema | **PASS** | Bi-directional C++ <-> Python round-trip byte-identical. |
| **PlatformIO Gateway** | ESP32-C3 Gateway | **PASS** | PlatformIO release build successful (`firmware.bin` verified). |
| **PlatformIO Node** | ESP32-C3 Node | **PASS** | PlatformIO release build successful (`firmware.bin` verified). |
| **ShellCheck** | `install.sh`, `uninstall.sh` | **PASS** | Zero shell warnings or syntax issues. |
| **Config Documentation** | `tools/gen_config_docs.py` | **PASS** | Schema and `docs/CONFIG.md` synchronized (0 drift). |

---

## Upgrade & Installation Instructions

To install on a fresh Armbian Debian 12 (Bookworm) board:
```bash
sudo ./install.sh
```

To update an existing installation using the A/B update manager:
```bash
sudo -u smartwater /opt/smart-water/venv/bin/python -m app.cli update --from /tmp/smart-water-v1.2.0.tar.gz
```
