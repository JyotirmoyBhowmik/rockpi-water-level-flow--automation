# Industrial Safety Interlocks & Dual-Layer Failsafe Architecture

## 1. Safety Philosophy: Dual-Layer Defense

The Smart Water & Precision Irrigation Controller implements a **Dual-Layer Defense-in-Depth** safety architecture. Under no circumstances does the system rely solely on software, microcontrollers, or network connectivity to prevent catastrophic flooding, high-voltage contactor fire hazards, or pump burnout.

```
+--------------------------------------------------------------------------+
| PHYSICAL LAYER 1: Electromechanical Hardware Interlock Chain             |
| - Passive series switches break relay module VCC directly                |
| - Full Float (NC) opens on 100% full or cut cable                        |
| - Sump Float (NO) opens on dry ground well                               |
| - ZERO code execution, zero firmware dependency                          |
+--------------------------------------------------------------------------+
                                     |
                                     v
+--------------------------------------------------------------------------+
| SOFTWARE LAYER 2: Priority 0 Hardcoded Failsafe Guardian                 |
| - Interlock cause logic & wiring fault detection                         |
| - 15-minute continuous runtime hard stop & 30-minute cooldown             |
| - Stale data detection (<15s head-end, <90s garden node)                 |
| - Probe voltage sanity band filtering [0.8V - 3.0V]                      |
| - 10-second Zone 1 pulse flow verification                               |
| - Firmware-level 60-second hardware watchdog on ESP32-C3 node            |
+--------------------------------------------------------------------------+
```

---

## 2. Layer 1: Hardware Interlock Chain Electrical Theory

The primary interlock chain feeds fused 5V DC through two passive electromechanical float switches in series:
1. **Full Float (Overhead Tank):** Normally Closed (NC). Drops open when water reaches maximum safe level or if the sensor wiring is severed or disconnected.
2. **Sump Float (Ground Reservoir / Well):** Normally Open (NO). Held closed by the buoyancy of water in the sump. Drops open when water level is exhausted.

### Interlock Cause Logic & Truth Table:
Two taps feed optocoupler inputs `IN3` (`interlock_mid`) and `IN2` (`interlock_out`). Software monitors both pins simultaneously to determine the underlying root cause:

| `interlock_out` (IN2) | `interlock_mid` (IN3) | Hardware State | Cause Classification | System Response |
| :---: | :---: | :--- | :--- | :--- |
| **HIGH (1)** | **HIGH (1)** | Full float CLOSED, Sump float CLOSED | `healthy` | AC Contactor allowed to run |
| **LOW (0)** | **LOW (0)** | Full float OPEN (Top tank full or cut wire) | `tank_full` | Hardware cuts relay VCC. Software sets state OFF (`tank_full`) |
| **LOW (0)** | **HIGH (1)** | Sump float OPEN (Dry sump / low ground well) | `dry_sump` | Hardware cuts relay VCC. Software sets state OFF (`dry_sump`) |
| **HIGH (1)** | **LOW (0)** | **IMPOSSIBLE ELECTRICAL STATE** | `wiring_fault` | Immediate software lockout. Motor locked. Emergency alarm raised. |

> [!CAUTION]
> **Wiring Fault Condition:** If `interlock_out` reads HIGH while `interlock_mid` reads LOW, electricity is bypassing the primary Full float. This indicates a short circuit, crossed wire, or damaged optocoupler channel. The controller raises an immediate critical alarm and locks the motor permanently until verified.

---

## 3. Layer 2: Main AC Motor Failsafes

1. **Max Continuous Runtime Guard (15 Minutes):**
   - Submersible pump motors overheat rapidly when pumping against airlocks, jammed check valves, or cracked delivery pipes.
   - A monotonic timer (`get_monotonic_now()`) monitors continuous pump run duration.
   - If the pump runs for >= 900 seconds (15 minutes), the contactor relay is de-energized immediately.
   - State transitions to `COOLDOWN_LOCKED`.
2. **Mandatory Cooldown Period (30 Minutes):**
   - Prevents immediate restarting of hot pump windings.
   - Start requests are rejected until the 30-minute timer expires.
3. **Manual Reset Requirement:**
   - Once cooldown expires, the pump does NOT resume automatically.
   - An authorized operator must manually click "Reset Lockout" in the UI or call `/api/motor/reset` to acknowledge the condition.
4. **Conductive Leak Detection Guard:**
   - Monitored at `IN4` (PC817 Opto 4).
   - If water is detected around the pump manifold, the contactor and all garden pumps are immediately de-energized.
5. **Motor Electrical Sizing & Overload Protection:**
   - A qualified electrician must size the circuit breaker (MCB), supply cabling, and motor overload protection relay strictly from the pump motor nameplate Full Load Current (FLC).
   - The AC contactor switching contacts must be sized for inductive motor starting duty (AC-3 rating >= motor FLC).
6. **Optional AC Power & Motor Current Monitoring:**
   - **Dry-Run / Loss of Prime:** If motor current drops below `dry_run_current_pct` (default 65%) of learned baseline for > `dry_run_timeout_s` (default 5.0s), the contactor is shut off and locked out (`DRY_RUN_CURRENT`).
   - **Overload / Locked-Rotor:** If motor current exceeds `overload_current_pct` (default 135%) of learned baseline for > `overload_timeout_s` (default 3.0s), the contactor is shut off and locked out (`MOTOR_OVERLOAD_CURRENT`).
   - **Single-Phase AC Voltage Window:** Mains voltage is checked before starting and while running against a configurable safe operating window (`180V - 260V`). Out-of-window voltage trips and locks out the motor (`VOLTAGE_OUT_OF_BOUNDS`).
   - **Inrush Blanking Window:** Current evaluations are blanked during the first `inrush_blanking_s` (default 3.0s) to prevent false trips during induction motor starting inrush.
   - **Automated Baseline Learning:** Automatically learns healthy steady-state baseline current over the first 5 healthy completed runs (>=30s, normal stop), persisted to SQLite.
   - **CRITICAL SAFETY NOTICE:** Software current monitoring provides supplementary dry-run and locked-rotor protection. It **NEVER replaces the physical bimetallic/electronic thermal overload relay or MPCB** required by electrical wiring codes.
7. **Continuous Tank Level Sensing & Authoritative Float Hierarchy:**
   - **Predictive Motor Stop:** Software triggers a smooth stop when tank reaches `predictive_stop_pct` (default 95%), saving mechanical wear and stopping before the hardware float trips.
   - **Slow Fill Rate Alarm:** If the pump runs for > `slow_fill_min_run_s` (default 60s) and fill rate remains below `min_fill_rate_lpm` (default 5.0 L/min), a `SLOW_FILL_RATE` alarm is sounded and dispatched via Telegram to alert on delivery pipe leaks, failed check valves, or pump loss-of-prime.
   - **Night-Time Leak Detection:** If tank volume drops by > `night_leak_max_drop_litres` (default 15L) during the zero-usage night window (01:00 to 04:00) with no active irrigation, a `NIGHT_LEAK_DETECTED` alarm warns the homeowner of leaking toilets, dripping taps, or burst distribution lines.
   - **AUTHORITATIVE HARDWARE FLOATS:** Continuous level sensing is purely supplementary. The series electromechanical float switches (Full float NC, Sump float NO) remain the non-bypassable physical and software interlock layer. If the level sensor fails or disconnects, the physical Full float reliably interrupts the circuit at 100% capacity.

---

## 4. Layer 2: Wireless Garden Drip Irrigation Failsafes

1. **Dual Hardware Firmware Timer (60s Cap):**
   - In addition to software timers on the ROCK Pi S, the ESP32-C3 node firmware contains a hard-coded watchdog timer (`MAX_PUMP_HARD_TIMEOUT_S = 60`).
   - If the ROCK Pi S crashes, powers off, or wireless RF drops, the ESP32-C3 independently de-energizes the MOSFET gate after `duration_s` (max 60s).
2. **Concurrency Limit (Max 2 Pumps Simultaneously):**
   - Prevents DC bus voltage drops and battery brownouts on solar/battery installations.
   - Starting a third pump is rejected until an active pump finishes.
3. **Reservoir Dry-Run Lockout:**
   - Sourced from reed float switch on GPIO 5 of the garden node.
   - If `reservoir_wet == false`, DC pumps are locked out to protect impellers.
4. **Probe Sanity Band Filtering:**
   - Capacitive probes must read within `[wet_v - margin .. dry_v + margin]` (typically 0.8V to 3.0V).
   - Voltages outside this band indicate disconnected probes, corroded traces, or shorts to 3.3V/GND. Irrigation is blocked.
5. **Zone 1 Pulse Flow Meter Verification (10-Second Check):**
   - If Zone 1 runs for 10 seconds and accumulates fewer than `min_pulses_10s` (configured in config, default 5 pulses), the system declares `NO FLOW DETECTED`.
   - The pump is killed immediately, the zone is locked, and a voice alert sounds.
6. **Root Rot Cooldown Protection (6 Hours):**
   - To prevent over-watering and root rot, automated watering cannot trigger on the same zone more than once every 6 hours.

---

## 5. Telemetry Freshness & Clock Validity Guards

1. **Stale Data is Unsafe Data:**
   - Head-End inputs older than **15 seconds** are marked `STALE`. Contactor start is locked.
   - Garden Node telemetry older than **90 seconds** is marked `STALE`. Drip irrigation is locked.
   - Gateway USB link lost for > **30 seconds** raises `LINK LOST`.
2. **Un-Synced Clock Guard:**
   - During internet blackouts, an un-synced system clock might report epoch `1970-01-01`, which would corrupt interval calculations and schedule morning watering at midnight.
   - The system checks DS3231 RTC status and compares system clock against `BUILD_DATE_EPOCH`.
   - If clock is un-synced, all automatic rule-based watering is blocked. Manual operator pulses remain available.

---

## 6. Deterministic Output State on Shutdown

Upon receiving `SIGINT`, `SIGTERM`, or an unhandled Python exception:
1. `head_end_gpio.force_all_outputs_off()` drives the contactor relay LOW immediately.
2. `garden_gateway.send_stop_all()` transmits an emergency stop packet over ESP-NOW.
3. Watchdog loop terminates cleanly.

---

## 7. Storage Safety & Firmware Flashing Policy

### 7.1 Database Integrity & Power-Cut Resilience
- **SQLite WAL Mode**: `PRAGMA journal_mode = WAL;` with `PRAGMA synchronous = NORMAL;` and `PRAGMA busy_timeout = 5000;`. Guarantees zero file corruption on unexpected power outages while reducing flash wear.
- **Nightly Atomic Backups**: Every 24 hours, `VACUUM INTO` creates an atomic backup with a 7-day rotation window and optional cloud sync via `rclone`.
- **Read-Only Root**: Supported via `overlayroot` and `log2ram` with `/var/lib/smart-water` placed on a separate writable partition. See [STORAGE_READONLY.md](file:///c:/Users/TEST/rockpi-water-level-flow -automation/docs/STORAGE_READONLY.md).

### 7.2 Garden Node ESP-NOW Firmware OTA Policy
> [!CAUTION]
> **FIRMWARE OTA OVER ESP-NOW IS STRICTLY OUT OF SCOPE.**
> ESP-NOW action frames have a 250-byte payload limit. Unsigned or single-partition OTA introduces severe risks of bricked nodes or runaway irrigation actuators in the event of frame loss or tampering.
> Node firmware flashing is restricted exclusively to physical USB-UART programming using PlatformIO. Any future OTA proposal must mandate `esp_https_ota`-style signed ECDSA binaries verified by the hardware bootloader with dual rollback partitions (`ota_0`, `ota_1`).

