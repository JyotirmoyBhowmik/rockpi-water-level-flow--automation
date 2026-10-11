# Security Policy & Vulnerability Reporting

## 1. Safety & Security Philosophy

The Smart Water & Precision Irrigation Controller is designed for dual-layer safety operating in mixed AC high-voltage (230V) and low-voltage DC environments. Software failures must always fall back to passive hardware-level safe states:
- All motor contactor and pump outputs fail **OFF**.
- Any missing, stale, or ambiguous telemetry forces actuators into lockout.
- Physical float chain interrupts relay coil power independently of software state.

---

## 2. Threat Model & Defense-in-Depth

The controller operates under the following threat model:
1. **Network Isolation**: The application must NOT be bound to `0.0.0.0` or exposed directly to the public internet. Access is restricted to authenticated private mesh networks (Headscale / Tailscale) or loopback interface (`127.0.0.1`).
2. **Strict Single-Origin & CSP**: Strict Content Security Policy (`default-src 'self'`) is enforced with `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: strict-origin-when-cross-origin`.
3. **No Direct Tokens in URLs**: Telemetry SSE streams (`/api/telemetry/stream`) do NOT accept raw bearer tokens in query parameters. A short-lived (60s) single-use ticket must be minted via `POST /api/telemetry/ticket` over an authenticated channel.
4. **Brute-Force & Rate Limiting**: Authentication failures are tracked in-memory. 10 failed login attempts within 60 seconds trigger a 60-second client IP ban.
5. **SSRF Protection**: External integrations (e.g. Telegram webhooks) undergo strict IP validation. Private IP ranges (RFC 1918), loopbacks (127.0.0.0/8), link-local addresses (169.254.0.0/16, fe80::/10), and cloud metadata endpoints are strictly blocked.
6. **Shell Injection Prevention**: All external sub-processes (such as `espeak-ng` / `aplay`) use `asyncio.create_subprocess_exec` with piped stdin and strict argument regex validation (`^[a-zA-Z0-9_\-]+$`), strictly forbidding shell invocation (`shell=True`).
7. **HMAC-SHA256 ESP-NOW Authentication**: All wireless packets between the USB Gateway and the ESP32-C3 Garden Node are signed with HMAC-SHA256 over canonical JSON payloads with sequence monotonicity and boot-counter tracking to reject replay and tampering attacks.

---

## 3. Reporting a Vulnerability

If you discover a potential security or safety issue within this project:
1. **Do not create a public GitHub issue.**
2. Send an email with reproduction steps, environment details, and affected components to the repository maintainer or security contact.
3. Include relevant log snippets with sensitive identifiers (MAC addresses, tokens) masked.
4. We aim to acknowledge reports within 48 hours and provide a fix or mitigation plan within 7 days.
