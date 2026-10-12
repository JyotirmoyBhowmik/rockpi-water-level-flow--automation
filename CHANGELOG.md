# Changelog

All notable changes to the Radxa ROCK Pi S Smart Water & Precision Irrigation Controller are documented in this file.

## [1.2.0] - 2026-10-13

### Added
- **Motor Current & Power Monitoring (`app/hardware/power_meter.py`)**:
  - PZEM-004T v3 (Modbus RTU over USB-TTL) and SCT-013 CT (ADS1115 ADC) monitoring.
  - Dry-run protection, overload / locked-rotor lockout, and 230V voltage window verification.
  - Self-learning current baseline calibration across first 5 healthy runs.
  - Per-run energy consumption tracking in kWh logged to `runs` table.
- **Continuous Tank Level Sensing (`app/hardware/level_sensor.py`)**:
  - JSN-SR04T ultrasonic and 4-20 mA submersible pressure transducer support.
  - Volumetric capacity estimation (litres and percentage) with cylindrical/rectangular tank geometry.
  - Dynamic fill-rate estimation, projected fill ETA, and "filling too slowly" alarm.
  - Predictive motor de-energization before Full float trip and night-time leak detection.
- **Weather-Aware Irrigation & Scheduling (`app/engine/weather_service.py`)**:
  - Open-Meteo forecast integration with automated rain probability and precipitation skip.
  - BME280 microclimate telemetry support over Protocol v1.1 `env` packet.
  - Per-zone irrigation time windows, soak cycles (pulse + pause), and volumetric daily budgets.
- **Observability & Integrations**:
  - Prometheus `/metrics` exporter in native text format behind authentication.
  - MQTT integration with Home Assistant auto-discovery and failsafe-validated command execution.
  - ntfy.sh push notifications as alternative to Telegram alerts.
  - Pure in-browser SVG historical telemetry charts from `/api/history`.
  - Automated daily diagnostic summaries covering runtimes, water usage, and events.
- **Resilience, Updates & Storage**:
  - `overlayroot` read-only root compatibility with separate `/var/lib/smart-water` writable partition.
  - SQLite WAL hardening with `busy_timeout=5000` and automated nightly `VACUUM INTO` backups (7-day retention).
  - Safe A/B atomic software updates (`python -m app.cli update --from <tarball>`) with SHA-256 verification and automatic rollback.
- **RBAC, Security & UI Hardening**:
  - Role-Based Access Control (`operator` vs `viewer` tokens) with CLI token management.
  - SQLite operator audit logging in `events` table for all state mutations.
  - Progressive Web App (PWA) manifest and Service Worker strictly bypassing API caching.
  - Mobile-first dashboard layout with persistent sticky E-STOP button and industrial dark mode.
  - Standalone SSD1306 OLED status display and piezo buzzer acoustic failover.
- **Automated Configuration Documentation**:
  - Auto-generated `docs/CONFIG.md` via `tools/gen_config_docs.py` with zero-drift CI gate.
- **Single Source of Truth Versioning**:
  - Centralized application version in `app.__version__ = "1.2.0"`.

### Changed
- **Stale Thresholds Consolidation**:
  - Removed duplicate stale threshold fields in favor of canonical `_s` naming with transparent migration shims.
- **Framework Pinning & Architecture Cleanup**:
  - Pinned modern, mutually compatible framework dependencies (`fastapi==0.143.0`, `starlette==1.7.0`, `pydantic==2.14.0`).
- **Security & Safety Invariants**:
  - Strict Content Security Policy (CSP) eliminating `'unsafe-inline'`.
  - SSRF protection on outbound webhooks with asynchronous DNS pinning.
  - Watchdog stall exit via immediate termination preserving hardware reset capabilities.

## [1.1.0] - 2026-10-12

### Added
- **Protocol v1.1 Implementation & Contract Testing**:
  - Shared canonical JSON schema (`protocol/messages.json`) verifying packets across SBC and Node.
  - HMAC-SHA256 16-hex digital signatures on all wireless commands and telemetry.
  - Sequence monotonicity tracking with boot cycle counter reset support.
  - Flow rate reporting migrated from arbitrary instantaneous L/min to verifiable `delta_pulses` over `period_ms`.
  - Command acknowledgment and retry loop with exponential backoff (2s timeout, 3 attempts).
- **Dual GPIO Runtime Backend (`app/hardware/gpio_backend.py`)**:
  - Transparent dynamic selection between libgpiod v2 (`request_lines`, `LineSettings`) and v1 (`Chip.get_line`).
  - Strict 1.0-second async timeout (`asyncio.wait_for`) on all GPIO read/write operations.
  - Startup polarity verification ensuring outputs default to OFF.
  - Contactor auxiliary feedback verification (`read_contactor_feedback`) for pull-in and welded contact checks.
- **Health-Gated Watchdog Subsystem (`app/core/watchdog.py`)**:
  - Multi-loop heartbeat monitoring (`head_end_poll` < 3s, `rules_auto_loop` < 15s, `gateway_reader` < 15s).
  - Gated `/dev/watchdog` and systemd `sd_notify` petting only when all loops are healthy.
  - Hard de-energization of contactor relay and exit with error code 2 upon loop stall.
- **State Persistence & Fault Safety**:
  - SQLite `lockouts` table tracking motor cooldowns, manual lockouts, and active alarms across power cycles.
  - Automatic invalid wall-clock detection forcing indefinite cooldown locks until clock synchronization.
  - Continuous head-end input lost guard stopping motor if PC817 polling becomes stale (>3s) while motor is running.
  - Minimum motor OFF time enforcement (120 seconds) preventing short-cycling.
- **Security Hardening**:
  - Single-use short-lived (60s) tickets for `/api/telemetry/stream` SSE endpoints (`POST /api/telemetry/ticket`).
  - SSRF protection on webhook destinations rejecting loopback, link-local, and private subnets.
  - Subprocess refactoring in speech alerts: switched from shell string evaluation to `asyncio.create_subprocess_exec` stdin piping with regex whitelisting for voices and ALSA device IDs.
  - Authentication brute-force protection rate-limiting failed logins (10 failed attempts/min triggers 60s IP block).
- **Setup Wizard & Configuration Split**:
  - Read-only `/etc/smart-water/config.yaml` bootstrap configuration separated from runtime `/var/lib/smart-water/settings.yaml`.
  - 4-part setup verification (PC817 input test, contactor click test, ESP-NOW gateway pairing, soil probe calibration) before finalizing `setup_complete`.

### Changed
- **Packaging & OS Compatibility**:
  - Explicit platform validation for Armbian Debian 12 (Bookworm).
  - Venv created with `--system-site-packages` utilizing distribution `python3-libgpiod` without pip compiling.
  - Separated production `requirements.txt` from developer test suite `requirements-dev.txt`.
  - Memory bounds relaxed to `MemoryHigh=128M` and `MemoryMax=200M`.
  - Consolidated runtime state to single directory `/var/lib/smart-water/system.db`.

### Removed
- Removed legacy mock layers and simulated stochastic sensor generators.
- Eliminated all fake seeded demo records and template rows from database initialization.
