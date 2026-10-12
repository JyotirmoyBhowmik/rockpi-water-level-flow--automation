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
