# SmartWater Protocol v1.1 Specification

## 1. Overview
Protocol v1.1 is the binary/serial communications contract between the **ROCK Pi S Head-End Controller** (Linux, Python 3.11) and the **ESP32-C3 Garden Node** (ESP-IDF/Arduino C++).

Messages are formatted as **single-line JSON (JSON-Lines)** terminated by `\n`. Every packet exchanged across the RF / serial boundary is cryptographically authenticated using **truncated HMAC-SHA256**.

---

## 2. Canonical Serialization Form

Both Python and C++ must serialize and deserialize packets using a single byte-exact canonical form. To guarantee cross-platform bit-parity without relying on non-deterministic JSON serializer key order:

1. **Separators**: Compact separators `,` and `:` with **no whitespace** around keys or values.
2. **Key Ordering**: Strictly fixed per message type (`fixed_key_orders`).
3. **Numeric Formatting**:
   - **Integers**: Plain decimal representations (e.g., `1001`, `0`), never floating-point or scientific notation.
   - **Floats**: Fixed-point string with exactly 3 decimal places (`%.3f`, e.g., `1.842`).
   - **Booleans**: Literal `true` or `false` (lowercase).
4. **String Escaping**:
   - Double quotes (`"`) and backslashes (`\`) inside string values MUST be escaped as `\"` and `\\`.
   - Control characters `\n`, `\r`, `\t` are escaped respectively.
5. **No `sig` Field in Unsigned String**: The payload to be signed terminates with `}` and contains no `sig` key.

### Canonical Key Orders

| Type (`t`) | Fixed Key Order |
|---|---|
| `hb` | `v`, `t`, `nonce`, `seq`, `uptime_s`, `res_wet`, `ads_ok`, `pumps`, `reset_reason`, `rssi` |
| `soil` | `v`, `t`, `nonce`, `seq`, `z`, `volts` |
| `flow` | `v`, `t`, `nonce`, `seq`, `z`, `delta_pulses`, `period_ms` |
| `pump` | `v`, `t`, `nonce`, `seq`, `z`, `on`, `reason`, `run_s` |
| `ack` | `v`, `t`, `nonce`, `seq`, `id`, `status`, `err` |
| `cmd` | `v`, `t`, `nonce`, `seq`, `id`, `z`, `run_s` |
| `stop` | `v`, `t`, `nonce`, `seq`, `id`, `z` |
| `stop_all` | `v`, `t`, `nonce`, `seq`, `id` |

---

## 3. Cryptographic Signature & Verification

1. **Algorithm**: HMAC-SHA256 computed over the UTF-8 bytes of the unsigned canonical string.
2. **Truncation**: The hex digest is truncated to the first 16 lowercase hex characters (64 bits of security).
3. **Appending**: The trailing `}` of the unsigned string is replaced with `,"sig":"<16hex>"}\n`.
4. **Verification**:
   - The verifier scans the line textually from the right for `,"sig":"([0-9a-fA-F]{16})"}`.
   - It strips this suffix and restores `}` to reconstruct the exact unsigned string.
   - It computes the expected HMAC-SHA256(16) over the reconstructed string and compares it in constant time.

---

## 4. Replay & Session Defense (Choice B)

To protect against packet replay attacks across power cycles and network drops:

1. **Random Boot Nonce**:
   - Generated at boot using hardware RNG (32-bit unsigned integer).
   - Identifies the current active boot session.
2. **Strictly Monotonic Sequence (`seq`)**:
   - Increments by 1 for each emitted packet during a boot session.
   - Packets with `seq <= last_seq` within the same session are rejected as replays.
3. **Session Reset & Boot Verification (Choice B)**:
   - When a packet with a new `nonce` arrives:
     - Under `strict_nonce_verification = False` (default backward compatibility): The tracker accepts the new session and resets sequence tracking to the packet's `seq`.
     - Under `strict_nonce_verification = True` (Choice B hardening): A new nonce is rejected unless it is accompanied by `seq == 1` (a boot announcement) or a strictly increasing `boot_count` read from non-volatile storage (NVS). Replayed mid-session packets from previous reboots with `seq > 1` are dropped.

---

## 5. Golden Packet Vectors (from `protocol/messages.json`)

All examples use test secret key `"test-secret-key-32-chars-long12345"`.

### 5.1 Telemetry Packets (Node -> Pi)

- **Heartbeat (`hb`):**
  ```json
  {"v":"1.1","t":"hb","nonce":1001,"seq":1,"uptime_s":120,"res_wet":true,"ads_ok":true,"pumps":[0,0,0,0],"reset_reason":1,"rssi":-65,"sig":"3d4a199760f47ebf"}
  ```

- **Soil Probe Moisture (`soil`):**
  ```json
  {"v":"1.1","t":"soil","nonce":1001,"seq":2,"z":1,"volts":1.842,"sig":"7dd8f26ab85b860e"}
  ```

- **Flow Meter Pulses (`flow`):**
  ```json
  {"v":"1.1","t":"flow","nonce":1001,"seq":3,"z":1,"delta_pulses":15,"period_ms":1000,"sig":"0e8e872241b8db70"}
  ```

- **Pump Event (`pump`):**
  ```json
  {"v":"1.1","t":"pump","nonce":1001,"seq":4,"z":1,"on":true,"reason":"cmd","run_s":15,"sig":"5fe9711f9473028a"}
  ```

- **Command Acknowledgment (`ack`):**
  ```json
  {"v":"1.1","t":"ack","nonce":1001,"seq":5,"id":"cmd-001","status":"ok","err":"","sig":"0a2d092007f31086"}
  ```

### 5.2 Control Commands (Pi -> Node)

- **Pump Run Command (`cmd`):**
  ```json
  {"v":"1.1","t":"cmd","nonce":2002,"seq":1,"id":"cmd-001","z":1,"run_s":15,"sig":"756580f2703f2006"}
  ```

- **Stop Zone Pump Command (`stop`):**
  ```json
  {"v":"1.1","t":"stop","nonce":2002,"seq":2,"id":"cmd-002","z":1,"sig":"92b186b1f5e489fa"}
  ```

- **Emergency Stop All Pumps Command (`stop_all`):**
  ```json
  {"v":"1.1","t":"stop_all","nonce":2002,"seq":3,"id":"cmd-003","sig":"73b8a1e4c99759a6"}
  ```

### 5.3 Escaped Strings Vectors

- **Pump with escaped reason string:**
  ```json
  {"v":"1.1","t":"pump","nonce":1001,"seq":6,"z":2,"on":false,"reason":"fault: \"sensor\" \\ timeout","run_s":10,"sig":"4e8cbb79bffe9a54"}
  ```

- **Ack with escaped ID and error string:**
  ```json
  {"v":"1.1","t":"ack","nonce":1001,"seq":7,"id":"cmd:\"001\"\\test","status":"err","err":"err: \"timeout\" \\ fault","sig":"576a5e905e4bbf41"}
  ```
