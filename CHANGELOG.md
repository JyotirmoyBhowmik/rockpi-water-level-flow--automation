# Changelog

All notable changes to the Radxa ROCK Pi S Smart Water & Precision Irrigation Controller are documented in this file.

## [1.2.0] - 2026-10-12

### Added
- **Automated Configuration Documentation**:
  - Implemented `tools/gen_config_docs.py` to auto-generate `docs/CONFIG.md` directly from Pydantic models.
  - Added CI workflow drift check (`python tools/gen_config_docs.py --check`) failing automatically if docs drift from schema.
- **Unified Single Version Source**:
  - Centralized application version in `app.__version__ = "1.2.0"`.
  - Harmonized version reporting across `run.py`, FastAPI application metadata, `/healthz` endpoint, and CLI `version`.

### Changed
- **Stale Thresholds Consolidation**:
  - Removed duplicate stale threshold fields (`max_soil_age_s`, `max_reservoir_age_s`, `max_heartbeat_age_s`) in favor of canonical `_s` fields (`soil_s`, `reservoir_s`, `heartbeat_s`).
  - Added backward-compatibility configuration migration in `load_config` that translates deprecated keys and logs explicit deprecation warnings.
- **Framework Pinning & Monkey-Patch Removal**:
  - Removed Starlette router monkey-patch shims from `app/__init__.py` and `app/api/__init__.py`.
  - Pinned mutually compatible `fastapi==0.143.0`, `starlette==1.7.0`, `uvicorn==0.54.0`, and `pydantic==2.14.0` in `requirements.txt` for Python 3.11 aarch64.
- **Security & Resilience Hardening**:
  - Strict Content Security Policy (CSP) with zero inline scripts/styles across all UI templates and components.
  - Webhook SSRF validation with asynchronous DNS offloading, IP pinning transport, and strict range filtering (blocking CGNAT, link-local, cloud metadata, and IPv6 ULA).
  - Non-disarming watchdog stall exit policy to prevent hardware runaway.
  - Strict nonce and monotonic boot count tracking in sequence replay defense.

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
