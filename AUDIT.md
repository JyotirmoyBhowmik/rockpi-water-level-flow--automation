# Codebase Security, Safety & Architecture Audit

**Target System:** Radxa ROCK Pi S (Rockchip RK3308, 512MB RAM) running Armbian Debian 12 (Bookworm)  
**Hybrid Node:** ESP32-C3 Super Mini (Unicast ESP-NOW + USB Serial Gateway, Protocol v1.1)  
**Date:** October 12, 2026  
**Test Suite Verification:** 43 passed in 3.68s (`python -m pytest -q`)  
**Linter Status:** All checks passed (`ruff check .`)  
**Working Tree Status:** Clean (`main` branch)  

---

## 1. Verification of Suspected Issues

### Issue 1: Watchdog `stop()` writing `'V'` during a health-stall exit
- **Status:** **CONFIRMED (Severity: High)**
- **File & Line:** `app/core/watchdog.py:134`, `app/core/watchdog.py:164`, `app/api/server.py:130`
- **Proof:**
  In `app/core/watchdog.py`:
  ```python
  # app/core/watchdog.py:154-164
  is_healthy, reason = health_monitor.check_health()
  if not is_healthy:
      logger.critical(
          "WATCHDOG HEALTH GATING FAILURE: %s. "
          "FORCING RELAY OFF AND EXITING FOR SYSTEMD RESTART!",
          reason,
      )
      await head_end_gpio.force_all_outputs_off()
      sys.exit(2)
  ```
  ```python
  # app/core/watchdog.py:132-136
  if self._dev_fd is not None:
      try:
          os.write(self._dev_fd, b"V")
          os.close(self._dev_fd)
          logger.info("Hardware watchdog cleanly disarmed with 'V'.")
  ```
  ```python
  # app/api/server.py:117-130
  yield
  ...
  await watchdog_manager.stop()
  ```
- **Analysis:**
  When a critical loop stalls, `HealthMonitor.check_health()` fails and invokes `sys.exit(2)`. This raises `SystemExit`, causing the ASGI application `lifespan` in `app/api/server.py` to enter its shutdown phase after `yield`. That shutdown unconditionally awaits `watchdog_manager.stop()`. Inside `stop()`, `os.write(self._dev_fd, b"V")` writes the Linux kernel "magic close" character `'V'` to `/dev/watchdog`. This disarms the hardware watchdog timer.
  If systemd fails to restart the process or hangs, the RK3308 hardware watchdog will NOT reboot the board. Furthermore, `server.py:137` touches `clean_marker` (`.clean_shutdown`), which fools the system on next boot into thinking the shutdown was normal rather than an emergency restart!
- **Fix:**
  Add a flag `is_emergency_exit: bool = False` or parameter `clean_disarm: bool = True` to `watchdog_manager.stop()`. On health-stall exit, close `_dev_fd` WITHOUT writing `'V'` (or immediately raise `os._exit(2)`), and do NOT create the `.clean_shutdown` marker.

---

### Issue 2: `notify_ready()` actually called after startup (`Type=notify`)
- **Status:** **REFUTED DEFECT / CONFIRMED PROPERLY CALLED (Severity: Info)**
- **File & Line:** `app/api/server.py:94`, `app/core/watchdog.py:191-203`, `tests/test_safety_pass2.py:63-83`
- **Proof:**
  ```python
  # app/api/server.py:93-95
  # 6. Notify systemd that service is fully started and ready
  watchdog_manager.notify_ready()
  ```
  ```python
  # app/core/watchdog.py:191-199
  def notify_ready(self) -> bool:
      """Sends READY=1 to systemd via $NOTIFY_SOCKET once all subsystems are online."""
      if self._notify_sock and self._notify_path:
          try:
              msg = b"READY=1\nSTATUS=Smart Water Controller online\n"
              path = ("\0" + self._notify_path[1:]) if self._notify_path.startswith("@") else self._notify_path
              self._notify_sock.sendto(msg, path)
              logger.info("Sent READY=1 to systemd notify socket.")
              return True
  ```
- **Analysis:**
  `watchdog_manager.notify_ready()` is called at line 94 of `server.py` immediately after DB initialization, token creation, GPIO setup, gateway startup, rules loading, and scheduler startup, but right before the server enters `yield`. Unit test `test_systemd_ready_notification_sent` explicitly verifies this contract. Matches `Type=notify` in `systemd/smart-water.service`.

---

### Issue 3: CSP header present and inline onclick handlers in `index.html` and `rules_builder.js`
- **Status:** **CONFIRMED (Severity: Medium)**
- **File & Line:** `app/api/server.py:251-254`, `app/ui/templates/index.html` (47 occurrences), `app/ui/static/js/rules_builder.js` (7 occurrences)
- **Proof:**
  In `app/api/server.py`:
  ```python
  # app/api/server.py:251-254
  response.headers["Content-Security-Policy"] = (
      "default-src 'self'; script-src 'self' 'unsafe-inline'; "
      "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
  )
  ```
  In `app/ui/templates/index.html`:
  ```html
  <!-- index.html:58 -->
  <button class="btn btn-danger btn-sm" onclick="triggerEmergencyStop()" style="font-weight: 700;">
    🛑 EMERGENCY STOP
  </button>
  <!-- index.html:77 -->
  <button class="btn btn-secondary btn-sm" onclick="resetMotorLockout()">Reset Lockout</button>
  ```
  In `app/ui/static/js/rules_builder.js`:
  ```javascript
  // rules_builder.js:190
  <button class="btn btn-secondary btn-sm" type="button" onclick="document.getElementById('${rowId}').remove()">✕</button>
  // rules_builder.js:205
  <select class="form-select act-target" style="flex: 2;" onchange="RulesBuilder.onTargetChanged('${rowId}', this.value)">
  ```
- **Analysis:**
  The server sets `Content-Security-Policy`, but because `index.html` uses 47 inline `onclick="..."` attributes and `rules_builder.js` interpolates 7 inline handlers into dynamic template strings, CSP currently must allow `'unsafe-inline'`. If `'unsafe-inline'` is removed to enforce enterprise production XSS standards, all buttons (Emergency Stop, Reset Lockout, tabs, modals, rule deletion) will immediately cease functioning.
- **Fix:**
  Refactor all 47 inline handlers in `index.html` and the 7 handlers in `rules_builder.js` to attach listeners via DOM `addEventListener` or event delegation (`data-action="..."`), then remove `'unsafe-inline'` from `script-src` in `server.py`.

---

### Issue 4: `gateway_reader` heartbeat when `garden_gateway.enabled=false` (Restart loop risk)
- **Status:** **CONFIRMED (Severity: High)**
- **File & Line:** `app/core/watchdog.py:30`, `app/config.py:81`, `app/hardware/gateway_driver.py:221-235`, `app/api/server.py:87`
- **Proof:**
  ```python
  # app/core/watchdog.py:27-31
  self._thresholds: Dict[str, float] = {
      "head_end_poll": 3.0,     # Must run every ~1s (max 3s)
      "rules_auto_loop": 15.0,  # Must run every ~5s (max 15s)
      "gateway_reader": 15.0,   # Must cycle at least every 15s
  }
  ```
  ```python
  # app/config.py:80-81
  class GardenGatewayConfig(BaseModel):
      enabled: bool = Field(default=False, description="Enable ESP32 wireless garden gateway")
  ```
  ```python
  # app/api/server.py:87
  await head_end_gpio.setup()
  await garden_gateway.start()
  ```
- **Analysis:**
  `GardenGatewayConfig.enabled` defaults to `False`. However, `server.py:87` calls `garden_gateway.start()` unconditionally. `gateway_driver.py` ignores `system_config.garden_gateway.enabled` entirely and runs `_gateway_loop()`, polling `/dev/garden-gateway` even when disabled.
  If any operator or conditional logic correctly skips starting `garden_gateway` when `enabled == False`, `HealthMonitor` will still require `gateway_reader` to beat every 15 seconds. After 15 seconds, `check_health()` fails:
  `"Critical loop 'gateway_reader' stalled"`, exits with code 2, and triggers an infinite crash-restart loop under systemd!
- **Fix:**
  In `HealthMonitor`, conditionally include `"gateway_reader"` in `self._thresholds` ONLY if `system_config.garden_gateway.enabled` is `True`. In `garden_gateway.start()`, return early if `not system_config.garden_gateway.enabled`.

---

### Issue 5: `can_water_zone(is_manual=True)` bypassing zone cooldown
- **Status:** **CONFIRMED (Severity: Medium)**
- **File & Line:** `app/engine/failsafes.py:287`, `app/engine/failsafes.py:298-301`, `app/engine/failsafes.py:315-320`
- **Proof:**
  ```python
  # app/engine/failsafes.py:287
  if not z_cfg.enabled and not is_manual:
      return False, f"Zone '{zone_key}' is disabled."
  ...
  # app/engine/failsafes.py:298-301
  if not is_manual:
      if z_last_ts is None or (now - z_last_ts) > max_soil_age:
          soil_age = int(now - z_last_ts) if z_last_ts else 999999
          return False, f"Zone '{zone_key}' soil reading is STALE ({soil_age}s > {max_soil_age}s limit). Auto-watering locked."
  ...
  # app/engine/failsafes.py:314-320
  # Cooldown Check (unless manual)
  if not is_manual:
      cooldown_until = z_state.get("cooldown_until_ts", 0.0)
      if cooldown_until > now:
          rem_h = round((cooldown_until - now) / 3600.0, 1)
          return False, f"Zone in cooldown protection ({rem_h}h remaining) to prevent root rot."
  ```
- **Analysis:**
  When `is_manual=True` (triggered by `POST /api/zones/{id}/water`), the engine bypasses:
  1. The 6-hour anti-root-rot cooldown (`cooldown_until_ts`).
  2. The zone disabled flag (`z_cfg.enabled`).
  3. The soil moisture staleness check (`max_soil_age`).
  While manual commands still enforce hardware link status (<30s), reservoir wet status, probe calibration sanity, and flow meter faults, an operator or automated script calling the manual API endpoint repeatedly can bypass cooldown and drown plants.
- **Fix:**
  Either enforce a minimum manual cooldown (e.g. 15 minutes between consecutive manual runs on the same zone), or require an explicit `force=true` query parameter with audited event logging when overriding an active cooldown.

---

### Issue 6: `SequenceTracker` behaviour on new nonce (replay window)
- **Status:** **CONFIRMED VULNERABILITY (Severity: High)**
- **File & Line:** `app/hardware/protocol.py:309-313`, `firmware/node/src/main.cpp:253-263`
- **Proof:**
  In `app/hardware/protocol.py`:
  ```python
  # app/hardware/protocol.py:308-313
  if nonce is not None:
      if self.last_nonce is None or nonce != self.last_nonce:
          # Fresh boot session detected: accept and reset
          self.last_nonce = nonce
          self.last_seq = seq
          return True, None
  ```
  In `firmware/node/src/main.cpp`:
  ```cpp
  // firmware/node/src/main.cpp:253-258
  if (!pi_session_active || cmd_nonce != pi_last_nonce) {
      // Fresh Pi session accepted
      pi_session_active = true;
      pi_last_nonce = cmd_nonce;
      pi_last_seq = cmd_seq;
  }
  ```
- **Analysis:**
  Both Python's `SequenceTracker` and the ESP32 node firmware only track a single `last_nonce` integer. If an attacker sniffs two valid past commands with different nonces (e.g., Session A: `nonce=0x1111`, `seq=1` and Session B: `nonce=0x2222`, `seq=1`), replaying Packet A causes `cmd_nonce != pi_last_nonce` (`0x1111 != 0x2222`), which evaluates to `true`. The receiver treats it as a "fresh boot session", resets sequence tracking, and executes the pump! Then replaying Packet B again resets tracking. By alternating packets from two past sessions, an attacker completely defeats monotonic sequence verification.
- **Fix:**
  Maintain a ring buffer or bounded set/cache of recently observed session nonces (e.g. `seen_nonces = set()`), rejecting any nonce seen within the last N hours or boot cycles. For the ESP32, store a small array of the last 16 seen session nonces and reject previously observed values.

---

### Issue 7: `install.sh` directory copying, `auth.json` path/ownership, and `rotate-token` permission
- **Status:** **AUDITED & CONFIRMED (Severity: Low / Enhancement)**
- **File & Line:** `install.sh:106-111, 144-156`
- **Proof:**
  ```bash
  # install.sh:106-111
  cp -ru "${CURRENT_DIR}/app" "${INSTALL_DIR}/"
  cp -ru "${CURRENT_DIR}/run.py" "${INSTALL_DIR}/"
  cp -ru "${CURRENT_DIR}/requirements.txt" "${INSTALL_DIR}/"
  cp -ru "${CURRENT_DIR}/systemd" "${INSTALL_DIR}/"
  rm -rf "${INSTALL_DIR}/tests"
  ```
  ```bash
  # install.sh:144-156
  chown -R smartwater:smartwater "${INSTALL_DIR}" "${DATA_DIR}" /run/smart-water
  chmod 750 "${INSTALL_DIR}"
  chmod 700 "${DATA_DIR}"

  AUTH_FILE="${DATA_DIR}/auth.json"
  if [[ ! -f "${AUTH_FILE}" ]]; then
      su -s /bin/bash smartwater -c \
          "${INSTALL_DIR}/venv/bin/python -m app.cli rotate-token"
  ```
- **Analysis:**
  1. **Copied Directories:** Only `app`, `run.py`, `requirements.txt`, and `systemd` are copied to `/opt/smart-water`. `tests/` is explicitly purged. `protocol/messages.json` is not copied (acceptable as `protocol.py` is self-contained). `firmware/` is excluded.
  2. **Auth file path & ownership:** `AUTH_FILE` is `/var/lib/smart-water/auth.json`. Directory has 0700 permissions owned by `smartwater:smartwater`.
  3. **Rotate-token permission:** `install.sh` uses `su -s /bin/bash smartwater -c ...` to execute token rotation as `smartwater`, ensuring `auth.json` is owned by `smartwater:smartwater` with mode 0600.
  - *Risk:* If an administrator runs `python -m app.cli rotate-token` directly as `root` after installation, `auth.json` will be overwritten with `root:root` ownership, preventing the unprivileged `smartwater` service user from reading or modifying it on restart.
- **Fix:**
  In `app/cli.py`, check the current UID or drop privileges to the owner of `DATA_DIR` before writing `auth.json`, or document that CLI commands must run under `sudo -u smartwater`.

---

### Issue 8: `ci.yml` steps analysis
- **Status:** **AUDITED & CONFIRMED (Severity: Low)**
- **File & Line:** `.github/workflows/ci.yml:13-57`
- **Proof:**
  ```yaml
  # .github/workflows/ci.yml
  steps:
    - name: Checkout repository
    - name: Set up Python 3.11
    - name: Install dependencies
    - name: Run Ruff Lint Checks
    - name: Run ShellCheck on installation scripts
    - name: Run Python safety & protocol regression tests with coverage
    - name: Prepare test secrets for PlatformIO firmware builds
    - name: Build Gateway Firmware with PlatformIO
    - name: Build Node Firmware with PlatformIO
  ```
- **Analysis:**
  The CI workflow correctly runs:
  1. Ruff linting on Python code.
  2. ShellCheck on `install.sh` and `uninstall.sh`.
  3. Pytest with test coverage reporting.
  4. Automatic PlatformIO compilation of both ESP32-C3 firmware targets (`gateway` and `node`).
  - *Observation:* The host C++ signer test (`host_cpp_signer.cpp`) is invoked inside `tests/test_protocol_contract.py` via `subprocess`, which compiles the binary using `g++`. In `ubuntu-latest`, `g++` is pre-installed via `build-essential`. However, `ci.yml` does not explicitly verify `g++` presence or add it to dependencies.

---

## 2. Additional Uncovered Findings

### Issue 9: Gateway firmware emits integer `v: 1` but Python expects string `"1.1"`
- **Status:** **CONFIRMED CRITICAL PROTOCOL MISMATCH (Severity: High)**
- **File & Line:** `firmware/gateway/src/main.cpp:83, 141, 154`, `app/hardware/protocol.py:265, 272, 279, 346-353`
- **Proof:**
  In `firmware/gateway/src/main.cpp`:
  ```cpp
  // main.cpp:141, 154
  Serial.print("{\"v\":1,\"t\":\"gw_init\",\"channel\":");
  Serial.print("{\"v\":1,\"t\":\"gw_hb\",\"uptime_s\":");
  ```
  In `app/hardware/protocol.py`:
  ```python
  class GatewayInitPacket(BaseModel):
      v: Literal["1.1"] = PROTOCOL_VERSION  # PROTOCOL_VERSION = "1.1"
      t: Literal["gw_init"] = "gw_init"

  class GatewayHbPacket(BaseModel):
      v: Literal["1.1"] = PROTOCOL_VERSION
      t: Literal["gw_hb"] = "gw_hb"
  ```
  ```python
  # protocol.py:346-354
  if t == "gw_init":
      return GatewayInitPacket.model_validate(data), None
  ...
  except Exception:
      pass
  return None, err or "Signature verification failed"
  ```
- **Analysis:**
  When `firmware/gateway/src/main.cpp` emits `{"v":1,"t":"gw_hb","uptime_s":...}`, Python parses the JSON and passes it to `GatewayHbPacket.model_validate(data)`. Because `v` is `1` (integer) instead of `"1.1"` (string), Pydantic raises a `ValidationError`. Line 352 catches the exception silently and returns:
  `None, "Missing or malformed ,'sig':'<16hex>'} suffix"`.
  As a consequence, **all gateway heartbeats and gateway init packets from hardware are silently rejected by the host controller!**
- **Fix:**
  In `firmware/gateway/src/main.cpp`, change `{\"v\":1,` to `{\"v\":\"1.1\",` across all status lines, or allow `v: Union[Literal["1.1"], Literal[1], Literal["1"]]` in `GatewayInitPacket`, `GatewayHbPacket`, and `GatewayErrPacket`.

---

### Issue 10: `save_settings` wipes `garden_gateway` block from `settings.yaml`
- **Status:** **CONFIRMED CONFIG TRUNCATION (Severity: Medium)**
- **File & Line:** `app/config.py:325-333`
- **Proof:**
  ```python
  # app/config.py:325-333
  payload = {
      "head_end": cfg.head_end.model_dump(mode="json"),
      "motor": cfg.motor.model_dump(mode="json"),
      "zones": {k: v.model_dump(mode="json") for k, v in cfg.zones.items()},
      "telegram": cfg.telegram.model_dump(mode="json"),
      "setup_complete": cfg.setup_complete,
      "setup_checks": cfg.setup_checks.model_dump(mode="json"),
      "restart_required": cfg.restart_required,
  }
  ```
- **Analysis:**
  `app/cli.py:98-101` instructs operators to paste `garden_gateway` configuration into `/var/lib/smart-water/settings.yaml`. However, `save_settings` in `app/config.py` only dumps the hardcoded dictionary keys listed above. Whenever the UI or API saves settings, it overwrites `settings.yaml` without `garden_gateway`, destroying the wireless gateway credentials and enabled status!
- **Fix:**
  Include `"garden_gateway": cfg.garden_gateway.model_dump(mode="json")` in `save_settings()`.

---

### Issue 11: `VoiceAlertSynthesizer._playback_worker` subprocess await lacks timeout
- **Status:** **CONFIRMED HANG RISK (Severity: Medium)**
- **File & Line:** `app/hardware/audio_driver.py:140`
- **Proof:**
  ```python
  # app/hardware/audio_driver.py:140
  await asyncio.gather(espeak_proc.wait(), aplay_proc.wait())
  ```
- **Analysis:**
  If the ALSA subsystem or USB audio device stalls or deadlocks on audio buffer underrun, `aplay_proc.wait()` will wait indefinitely. Because there is no timeout, the `audio_playback_worker` task hangs permanently, blocking all subsequent voice announcements (including emergency audio alerts).
- **Fix:**
  Wrap the gather in `asyncio.wait_for(asyncio.gather(...), timeout=10.0)`, and kill the subprocesses on timeout.

---

## 3. Comprehensive Audit Table

| ID | file:line | severity | finding | proof (code excerpt) | fix |
|---|---|---|---|---|---|
| **SEC-01** | `app/core/watchdog.py:134` | **High** | Watchdog `stop()` writes `'V'` on health-stall exit, disarming hardware watchdog timer. | `if self._dev_fd is not None: os.write(self._dev_fd, b"V")` | Differentiate graceful stop from stall exit; close FD without `'V'` on stall. |
| **SEC-02** | `app/api/server.py:137` | **Medium** | Clean shutdown marker `.clean_shutdown` created even after fatal health-stall exit. | `clean_marker.touch()` | Do not write `.clean_shutdown` on `sys.exit(2)` or health failure. |
| **SEC-03** | `app/hardware/protocol.py:309` | **High** | Sequence tracker resets on any different nonce, allowing alternating replay attacks. | `if self.last_nonce is None or nonce != self.last_nonce: self.last_nonce = nonce` | Store a cache/ring-buffer of seen nonces; reject historical nonces. |
| **SEC-04** | `firmware/node/src/main.cpp:253` | **High** | Node firmware resets session tracking on any new nonce, enabling replay vulnerability. | `if (!pi_session_active \|\| cmd_nonce != pi_last_nonce) { pi_last_nonce = cmd_nonce; }` | Keep ring buffer of last 16 seen nonces; reject nonces seen recently. |
| **SEC-05** | `app/api/server.py:252` | **Medium** | CSP uses `'unsafe-inline'` in `script-src` to support 47 inline `onclick` handlers in `index.html`. | `"script-src 'self' 'unsafe-inline'"` | Refactor `index.html` and `rules_builder.js` to `addEventListener`; remove `'unsafe-inline'`. |
| **SEC-06** | `app/core/watchdog.py:30` | **High** | `gateway_reader` loop monitored unconditionally; if gateway disabled, causes crash-restart loop. | `self._thresholds = { ... "gateway_reader": 15.0 }` | Only monitor `"gateway_reader"` when `system_config.garden_gateway.enabled == True`. |
| **SEC-07** | `app/hardware/gateway_driver.py:87` | **Medium** | Gateway driver loop starts unconditionally even when `garden_gateway.enabled == False`. | `await garden_gateway.start()` in `server.py:87` | Check `system_config.garden_gateway.enabled` before starting serial reader. |
| **SEC-08** | `app/engine/failsafes.py:315` | **Medium** | `can_water_zone(is_manual=True)` bypasses 6h zone cooldown and root-rot protection. | `if not is_manual: if cooldown_until > now: return False, ...` | Enforce minimum manual cooldown (e.g. 15 min) or require explicit override parameter. |
| **SEC-09** | `firmware/gateway/src/main.cpp:141` | **High** | Gateway firmware emits integer `v: 1` but Python schema requires string `"1.1"`, dropping all packets. | `Serial.print("{\"v\":1,\"t\":\"gw_init\"...");` | Change `{\"v\":1` to `{\"v\":\"1.1\"` in gateway firmware and accept both in Pydantic. |
| **SEC-10** | `app/config.py:325` | **Medium** | `save_settings()` omits `garden_gateway`, wiping gateway credentials from `settings.yaml`. | `payload = { "head_end": ..., "motor": ..., "zones": ... }` | Add `"garden_gateway": cfg.garden_gateway.model_dump(mode="json")` to payload. |
| **SEC-11** | `app/hardware/audio_driver.py:140` | **Medium** | Subprocess await in audio worker lacks timeout, risking permanent hang on ALSA freeze. | `await asyncio.gather(espeak_proc.wait(), aplay_proc.wait())` | Wrap with `asyncio.wait_for(..., timeout=10.0)` and kill hung processes. |
| **SEC-12** | `app/hardware/audio_driver.py:57` | **Low** | Audio queue has unbounded capacity; high-frequency events could leak memory. | `self._queue = asyncio.Queue()` | Use bounded queue `asyncio.Queue(maxsize=32)`. |
| **OPS-01** | `install.sh:106` | **Low** | `protocol/messages.json` not copied to `/opt/smart-water`. | `cp -ru "${CURRENT_DIR}/app" "${INSTALL_DIR}/"` | Not strictly required for runtime (`protocol.py` is standalone), but useful for contract tests. |
| **OPS-02** | `app/cli.py:122` | **Low** | Running `app.cli rotate-token` as root leaves `auth.json` unreadable by `smartwater`. | `security_manager.rotate_token()` | Warn or chown `auth.json` to `smartwater:smartwater` if invoked under root. |
| **OPS-03** | `.github/workflows/ci.yml:28` | **Low** | `ci.yml` does not explicitly declare `g++` dependency for `tests/host_cpp_signer.cpp`. | `pip install ruff platformio` | Add step verifying `g++` availability or include in test setup. |

---

## 4. Test Suite Execution Proof

```text
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-8.4.2, pluggy-1.6.0
rootdir: C:\Users\TEST\rockpi-water-level-flow -automation
configfile: pyproject.toml
plugins: anyio-4.11.0, asyncio-1.1.0, cov-7.0.0
asyncio: mode=Mode.STRICT, default_loop_scope=None
collected 43 items

tests/test_api_endpoints.py ......                                       [ 13%]
tests/test_failsafes.py .............                                    [ 44%]
tests/test_protocol_contract.py ........                                 [ 62%]
tests/test_rules_engine.py ....                                          [ 72%]
tests/test_safety_pass2.py ............                                  [100%]

============================== 43 passed in 3.68s ==============================
```
