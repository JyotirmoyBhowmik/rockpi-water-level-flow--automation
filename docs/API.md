# REST & SSE Telemetry API Specification

All API endpoints (except `/healthz`, static UI assets, and unauthenticated setup during first boot) require a Bearer token transmitted in the HTTP `Authorization` header:

```http
Authorization: Bearer <your-secret-token>
```

> **Network Binding & Port:** The HTTP server listens on port **8000** (default). By default, it binds to `127.0.0.1` (or your private Tailscale / Headscale 100.x.y.z IP). Binding to all interfaces (`0.0.0.0` or `::`) is explicitly refused unless `allow_all_interfaces: true` is configured in `/etc/smart-water/config.yaml`.

---

## 1. Standard Error Envelope
All error responses adhere strictly to enterprise production engineering standards:
```json
{
  "timestamp": "2026-10-12T01:50:00.000000Z",
  "status_code": 400,
  "error_code": "SAFETY_LOCKOUT",
  "correlation_id": "7f8b9a2c-1234-4567-89ab-cdef01234567",
  "message": "Overhead tank is already full (Full float open)."
}
```

---

## 2. Telemetry Endpoints

### 2.1 Health Check (Public)
`GET /healthz`
Returns system health, uptime, and firmware readiness without requiring an authentication token.
```json
{
  "status": "healthy",
  "version": "1.2.0",
  "system_status": "ONLINE",
  "hardware_ready": true,
  "uptime_seconds": 3600,
  "setup_complete": true
}
```

### 2.2 Live Telemetry Snapshot
`GET /api/telemetry/live`
Returns live state of head-end opto inputs, contactor motor, ESP-NOW wireless garden node, active alarms, and calibration states.
```json
{
  "system_status": "ONLINE",
  "hardware_ready": true,
  "uptime_seconds": 3600,
  "head_end": {
    "configured": true,
    "last_update_ts": 1728690000.0,
    "is_stale": false,
    "tank_low_float": false,
    "interlock_out": true,
    "interlock_mid": true,
    "leak_detected": false,
    "interlock_cause": "healthy",
    "contactor_relay": false
  },
  "motor": {
    "state": "OFF",
    "started_at_monotonic": null,
    "current_run_seconds": 0,
    "cooldown_until_ts": 0.0,
    "manual_locked": false,
    "last_stop_reason": "tank_full",
    "auto_mode": false
  },
  "garden": {
    "gateway_connected": true,
    "node_online": true,
    "last_packet_ts": 1728690002.5,
    "is_stale": false,
    "rssi": -65,
    "uptime_s": 7200,
    "reservoir_wet": true,
    "zones": {
      "zone_1": {
        "voltage": 1.84,
        "moisture_pct": 52.4,
        "calibrated": true,
        "pump_running": false,
        "flow_pulses": 1420,
        "flow_fault": false,
        "cooldown_until_ts": 0.0,
        "last_update_ts": 1728690001.0
      }
    }
  },
  "alarms": {}
}
```

### 2.3 Real-Time SSE Stream (Ticket-Authenticated)
1. **Acquire Streaming Ticket:**  
   `POST /api/telemetry/ticket` (authenticated via `Authorization: Bearer <token>`). Returns a single-use ticket valid for 60 seconds:
   ```json
   {
     "ticket": "9a8b7c6d-ticket-uuid-here",
     "expires_in_s": 60
   }
   ```
2. **Connect to SSE Stream:**  
   `GET /api/telemetry/stream?ticket=<ticket_id>`  
   Server-Sent Events (SSE) streaming live telemetry at 1Hz (`Content-Type: text/event-stream`). Disconnects automatically if client stops consuming to conserve RAM.

### 2.4 Event Journal (Paginated)
`GET /api/events?limit=50&offset=0`
Returns timestamped historical security interlock trips, pump starts, stops, and calibration changes.

### 2.5 Historical Telemetry (Paginated)
`GET /api/history?limit=100&offset=0`
Returns 60s downsampled sensor readings for charting and analysis.

### 2.6 Atomic Database Backup & Download
- `POST /api/backup`: Uses SQLite `VACUUM INTO` to create a zero-downtime atomic backup file on disk.
- `GET /api/backup/download`: Downloads the latest database snapshot as a binary stream (`application/vnd.sqlite3`). Authenticated via `Authorization: Bearer <token>`. The Web UI downloads this via `fetch()`, builds an in-memory `Blob`, and triggers download.
- CLI Restoration: `python -m app.cli restore <backup.db>` (validates SQLite magic header before replacing `/var/lib/smart-water/system.db`).

---

## 3. Motor & Actuator Control Endpoints

### 3.1 Start 25A AC Contactor
`POST /api/motor/start`
Energizes the 230V AC pump contactor. Requires explicit operator confirmation and verifies all interlocks.
**Request Body:**
```json
{
  "confirm": true
}
```

### 3.2 Stop AC Contactor
`POST /api/motor/stop`
De-energizes the contactor immediately. Always permitted.

### 3.3 Clear Motor Lockout
`POST /api/motor/reset`
Clears safety lockout flags following a max-runtime trip (after cooldown period has elapsed).

### 3.4 Toggle Automated Replenishment
`POST /api/motor/auto-mode`
**Request Body:**
```json
{
  "enabled": true
}
```

---

## 4. Wireless Garden Zone Control Endpoints

### 4.1 Trigger Precision Drip Pulse
`POST /api/zones/{zone_id}/water`
Energizes low-voltage DC pump for specified seconds (clamped to 60s max).
**Request Body:**
```json
{
  "duration_seconds": 20
}
```

### 4.2 Stop Active Zone Pulse
`POST /api/zones/{zone_id}/stop`

### 4.3 Stop All Zones
`POST /api/zones/stop-all`

### 4.4 Calibrate Soil Probe
`POST /api/zones/{zone_id}/calibrate`
Captures current live voltage as dry or wet calibration anchor.
**Request Body:**
```json
{
  "step": "dry" // "dry" (air) or "wet" (water cup)
}
```

---

## 5. Panic & Voice Control Endpoints

### 5.1 Emergency Stop
`POST /api/control/emergency-stop`
Dispatches instant kill signal to AC contactor and all wireless garden pumps.

### 5.2 Voice Announcement Test
`POST /api/control/speak`
**Request Body:**
```json
{
  "text": "Irrigation system operating normally."
}
```

---

## 6. Automation Rules Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/rules` | List all user-defined automation rules |
| `POST` | `/api/rules` | Create a new automation rule |
| `PUT` | `/api/rules/{id}` | Update an existing rule |
| `DELETE` | `/api/rules/{id}` | Permanently delete a rule |
| `POST` | `/api/rules/{id}/toggle` | Enable or disable a rule |
| `GET` | `/api/rules/templates` | List user-saved rule templates |
| `POST` | `/api/rules/{id}/save-template` | Save an active rule as a reusable template |
