"""
Comprehensive Tests for:
  1. Role-Based Access Control (RBAC): Viewer (read-only) vs Operator (control)
  2. Audit logging in SQLite events table with operator token name
  3. PWA Webmanifest and Service Worker static-only caching
  4. Local SSD1306 OLED display driver, status rendering, and mock I2C transport
  5. GPIO Piezo Buzzer alarm patterns, acoustic failover, and audio fallback
"""

import asyncio
import json
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.server import create_app
from app.config import system_config
from app.core.security import SecurityManager, security_manager
from app.hardware.audio_driver import audio_controller
from app.hardware.buzzer import BuzzerController
from app.hardware.gpio_backend import GPIOBackend
from app.hardware.oled_display import OLEDDisplayManager, SSD1306Display


# ==============================================================================
# 1. RBAC and Audit Logging Tests
# ==============================================================================

@pytest.fixture
def rbac_setup(tmp_path: Path):
    """Sets up an isolated SecurityManager with viewer and operator tokens."""
    auth_file = tmp_path / "auth_rbac.json"
    security_manager.auth_file_path = auth_file
    system_config.security.auth_token_file = str(auth_file)
    system_config.setup_complete = True
    security_manager._cached_tokens.clear()
    security_manager._cached_token_hash = None

    # Issue operator and viewer tokens
    op_token, _ = security_manager.issue_token("admin_op", role="operator", days=30)
    view_token, _ = security_manager.issue_token("guest_viewer", role="viewer", days=30)

    yield {
        "auth_file": auth_file,
        "op_token": op_token,
        "view_token": view_token,
        "op_headers": {"Authorization": f"Bearer {op_token}"},
        "view_headers": {"Authorization": f"Bearer {view_token}"},
    }


@pytest.mark.asyncio
async def test_rbac_viewer_can_read(rbac_setup):
    """Viewer tokens can freely access read-only endpoints (GET/HEAD)."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # GET /api/telemetry/live -> 200 OK
        res = await client.get("/api/telemetry/live", headers=rbac_setup["view_headers"])
        assert res.status_code == 200
        assert "head_end" in res.json()

        # GET /api/history -> 200 OK
        res_hist = await client.get("/api/history", headers=rbac_setup["view_headers"])
        assert res_hist.status_code == 200

        # GET /api/events -> 200 OK
        res_events = await client.get("/api/events", headers=rbac_setup["view_headers"])
        assert res_events.status_code == 200


@pytest.mark.asyncio
async def test_rbac_viewer_blocked_from_mutating_endpoints(rbac_setup):
    """Viewer tokens are blocked with 403 Forbidden on all state-mutating endpoints."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # POST /api/motor/start -> 403
        res = await client.post("/api/motor/start", headers=rbac_setup["view_headers"])
        assert res.status_code == 403
        body = res.json()
        assert body["error_code"] == "FORBIDDEN_ROLE"
        assert "require operator privileges" in body["message"]

        # POST /api/motor/stop -> 403
        res_stop = await client.post("/api/motor/stop", headers=rbac_setup["view_headers"])
        assert res_stop.status_code == 403
        assert res_stop.json()["error_code"] == "FORBIDDEN_ROLE"

        # POST /api/control/emergency-stop -> 403
        res_estop = await client.post("/api/control/emergency-stop", headers=rbac_setup["view_headers"])
        assert res_estop.status_code == 403

        # POST /api/zones/zone_1/start -> 403
        res_zone = await client.post("/api/zones/zone_1/start", json={"duration_seconds": 15}, headers=rbac_setup["view_headers"])
        assert res_zone.status_code == 403

        # PUT /api/config -> 403
        res_cfg = await client.put("/api/config", json={}, headers=rbac_setup["view_headers"])
        assert res_cfg.status_code == 403


@pytest.mark.asyncio
async def test_rbac_operator_allowed_and_audit_logged(rbac_setup):
    """Operator tokens can execute mutations and generate an audit log entry in SQLite events."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # POST /api/control/emergency-stop -> 200 OK
        res = await client.post("/api/control/emergency-stop", headers=rbac_setup["op_headers"])
        assert res.status_code == 200
        assert res.json()["status"] == "success"

        # Check audit log via GET /api/events
        res_events = await client.get("/api/events?limit=10", headers=rbac_setup["op_headers"])
        assert res_events.status_code == 200
        events = res_events.json().get("data", [])
        audit_events = [e for e in events if e.get("event_type") == "AUDIT_CONTROL"]
        assert len(audit_events) >= 1
        audit = audit_events[0]
        assert "admin_op" in audit["message"]
        assert "POST" in audit["message"]
        assert "/api/control/emergency-stop" in audit["message"]
        assert audit.get("details", {}).get("operator") == "admin_op"


@pytest.mark.asyncio
async def test_token_revocation_and_expiration(tmp_path: Path):
    """Revoked and expired tokens are rejected with 401 Unauthorized."""
    auth_file = tmp_path / "auth_tokens.json"
    sec_mgr = SecurityManager(auth_file_path=auth_file)

    # 1. Active token
    tok, _ = sec_mgr.issue_token("test_user", role="operator", days=10)
    assert sec_mgr.authenticate(tok) is not None

    # 2. Revoked token
    assert sec_mgr.revoke_token("test_user") is True
    assert sec_mgr.authenticate(tok) is None

    # 3. Expired token
    expired_tok, _ = sec_mgr.issue_token("old_user", role="viewer", days=-1)
    assert sec_mgr.authenticate(expired_tok) is None

    # 4. List tokens
    tokens = sec_mgr.list_tokens()
    assert any(t.name == "test_user" and t.revoked is True for t in tokens)
    assert any(t.name == "old_user" and not t.is_valid() for t in tokens)


@pytest.mark.asyncio
async def test_legacy_auth_json_backward_compatibility(tmp_path: Path):
    """A legacy auth.json containing only token_hash defaults to operator role."""
    import hashlib
    legacy_file = tmp_path / "legacy_auth.json"
    raw_token = "legacy_secret_token_12345"
    h = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    legacy_file.write_text(json.dumps({"token_hash": h}), encoding="utf-8")

    sec_mgr = SecurityManager(auth_file_path=legacy_file)
    record = sec_mgr.authenticate(raw_token)
    assert record is not None
    assert record.role == "operator"
    assert record.name == "default_admin"


# ==============================================================================
# 2. PWA Manifest and Service Worker Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_pwa_manifest_endpoint():
    """Serves valid web app manifest for standalone mobile home-screen install."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # /manifest.webmanifest
        res = await client.get("/manifest.webmanifest")
        assert res.status_code == 200
        assert "application/manifest+json" in res.headers["content-type"]
        data = res.json()
        assert data["name"] == "Smart Water Controller"
        assert data["display"] == "standalone"
        assert len(data["icons"]) >= 1

        # /manifest.json alias
        res_alias = await client.get("/manifest.json")
        assert res_alias.status_code == 200


@pytest.mark.asyncio
async def test_pwa_service_worker_endpoint():
    """Serves service worker script with Service-Worker-Allowed header and API bypass."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/sw.js")
        assert res.status_code == 200
        assert "application/javascript" in res.headers["content-type"]
        assert res.headers.get("service-worker-allowed") == "/"

        content = res.text
        # Critical security invariant: Service Worker must NEVER cache /api/*, /metrics, or /healthz
        assert "url.pathname.startsWith('/api')" in content
        assert "url.pathname.startsWith('/metrics')" in content
        assert "url.pathname === '/healthz'" in content


# ==============================================================================
# 3. SSD1306 OLED Display Driver Tests
# ==============================================================================

class MockI2CBus:
    """Mock I2C bus tracking writes for SSD1306 display commands and data."""

    def __init__(self):
        self.commands: list[int] = []
        self.data_blocks: list[list[int]] = []
        self.closed: bool = False

    def write_byte_data(self, addr: int, cmd_reg: int, val: int) -> None:
        if cmd_reg == 0x00:
            self.commands.append(val)

    def write_i2c_block_data(self, addr: int, data_reg: int, chunk: list[int]) -> None:
        if data_reg == 0x40:
            self.data_blocks.append(chunk)

    def close(self) -> None:
        self.closed = True


def test_oled_ssd1306_init_with_mock_bus():
    """Initializes SSD1306 via I2C commands on 128x64 display."""
    mock_bus = MockI2CBus()
    disp = SSD1306Display(bus_num=7, address=0x3C, width=128, height=64, mock_bus=mock_bus)

    assert disp.init_display() is True
    assert disp.is_hardware_available is True
    # Verify essential SSD1306 commands were sent
    assert 0xAE in mock_bus.commands  # Display OFF
    assert 0x8D in mock_bus.commands  # Charge pump
    assert 0xAF in mock_bus.commands  # Display ON
    disp.close()
    assert mock_bus.closed is True


def test_oled_render_status_normal():
    """Renders normal status screen with Tank, Motor, Garden, and Power readings."""
    mock_bus = MockI2CBus()
    disp = SSD1306Display(bus_num=7, width=128, height=64, mock_bus=mock_bus)
    disp.init_display()

    state = {
        "head_end": {"tank_low_float": False, "interlock_out": True},
        "motor": {"state": "RUNNING", "current_run_seconds": 125},
        "garden": {"link_state": "CONNECTED", "zones": {}},
        "level_sensor": {"available": True, "level_pct": 82.5, "volume_litres": 1650},
        "power_meter": {"available": True, "voltage_v": 231.2, "current_a": 4.15, "power_w": 960},
        "alarms": {},
    }

    img = disp.render_status_image(state)
    assert img is not None
    assert img.size == (128, 64)

    # Convert to buffer and display
    disp.display_image(img)
    assert disp.last_rendered_buffer is not None
    assert len(disp.last_rendered_buffer) == 1024  # 128 * (64 / 8)
    assert len(mock_bus.data_blocks) > 0


def test_oled_render_status_with_active_alarm():
    """Renders prominent inverted banner when a critical alarm is active."""
    mock_bus = MockI2CBus()
    disp = SSD1306Display(bus_num=7, width=128, height=64, mock_bus=mock_bus)
    disp.init_display()

    state = {
        "head_end": {},
        "motor": {"state": "LOCKOUT"},
        "garden": {"link_state": "OFFLINE", "zones": {}},
        "level_sensor": {"available": False},
        "power_meter": {"available": False},
        "alarms": {"dry_sump": {"reason": "Sump float open"}},
    }

    img = disp.render_status_image(state)
    assert img is not None
    # Verify image has white pixels in the top banner area (inverted bar)
    pix = img.load()
    assert any(pix[x, 5] for x in range(10, 50))


def test_oled_compact_32px_layout():
    """Renders compact 2-line layout for 128x32 OLED displays."""
    mock_bus = MockI2CBus()
    disp = SSD1306Display(bus_num=7, width=128, height=32, mock_bus=mock_bus)
    disp.init_display()

    state = {
        "head_end": {},
        "motor": {"state": "STOPPED"},
        "garden": {"link_state": "CONNECTED", "zones": {}},
        "level_sensor": {"available": True, "level_pct": 90.0},
        "power_meter": {"available": False},
        "alarms": {},
    }

    img = disp.render_status_image(state)
    assert img is not None
    assert img.size == (128, 32)
    disp.display_image(img)
    assert len(disp.last_rendered_buffer) == 512  # 128 * (32 / 8)


def test_oled_graceful_when_hardware_absent():
    """Driver operates safely in simulated mode when physical I2C is absent."""
    disp = SSD1306Display(bus_num=99, address=0x3C, width=128, height=64)
    # Bus 99 does not exist
    success = disp.init_display()
    assert success is False
    assert disp.is_hardware_available is False

    # Calling display methods does not raise exception
    disp.clear()
    img = disp.render_status_image({})
    if img:
        disp.display_image(img)
    disp.close()


@pytest.mark.asyncio
async def test_oled_manager_background_loop():
    """Tests OLEDDisplayManager start, periodic refresh, and clean shutdown."""
    mock_bus = MockI2CBus()
    disp = SSD1306Display(bus_num=7, mock_bus=mock_bus)
    mgr = OLEDDisplayManager(display=disp)

    system_config.display.enabled = True
    system_config.display.refresh_interval_s = 0.1

    await mgr.start()
    assert mgr._running is True
    await asyncio.sleep(0.25)
    assert len(mock_bus.data_blocks) > 0

    await mgr.stop()
    assert mgr._running is False
    system_config.display.enabled = False


# ==============================================================================
# 4. GPIO Piezo Buzzer Controller Tests
# ==============================================================================

class MockGPIOBackend(GPIOBackend):
    """Mock GPIO backend for verifying buzzer pin driving."""

    def __init__(self):
        self.opened = False
        self.requested_pin: Optional[int] = None
        self.pin_values: dict[int, int] = {}
        self.history: list[tuple[int, int]] = []

    def open(self, chip_path: str) -> bool:
        self.opened = True
        return True

    def close(self) -> None:
        self.opened = False

    def request_input(self, line_offset: int, consumer: str = "smart-water", bias: str = "pull_up") -> bool:
        return True

    def request_output(self, line_offset: int, consumer: str = "smart-water", initial_value: int = 0) -> bool:
        self.requested_pin = line_offset
        self.pin_values[line_offset] = initial_value
        return True

    def get_value(self, line_offset: int) -> int:
        return self.pin_values.get(line_offset, 0)

    def set_value(self, line_offset: int, value: int) -> None:
        self.pin_values[line_offset] = value
        self.history.append((line_offset, value))


@pytest.mark.asyncio
async def test_buzzer_setup_and_beep():
    """Tests buzzer initialization and pulse train execution."""
    backend = MockGPIOBackend()
    buzzer = BuzzerController(backend=backend)

    system_config.buzzer.enabled = True
    system_config.buzzer.gpio_pin = 13
    system_config.buzzer.active_high = True

    assert await buzzer.setup() is True
    assert buzzer.is_available is True
    assert backend.requested_pin == 13
    assert backend.pin_values[13] == 0  # Initial silent level

    # Play 2 quick pulses
    await buzzer.beep(duration_s=0.05, count=2, interval_s=0.03)
    await asyncio.sleep(0.2)

    # Check pulse history: HIGH -> LOW -> HIGH -> LOW
    pin_transitions = [v for pin, v in backend.history if pin == 13]
    assert 1 in pin_transitions
    assert pin_transitions[-1] == 0  # Restored to 0 after beep
    assert buzzer.is_beeping is False

    await buzzer.stop()
    assert buzzer.is_available is False
    system_config.buzzer.enabled = False


@pytest.mark.asyncio
async def test_buzzer_alarm_patterns():
    """Distinct alarm keys produce appropriate pulse parameters."""
    backend = MockGPIOBackend()
    buzzer = BuzzerController(backend=backend)

    system_config.buzzer.enabled = True
    system_config.buzzer.gpio_pin = 13
    await buzzer.setup()

    # 1. Emergency stop pattern (5 bursts)
    with patch.object(buzzer, "beep", new_callable=AsyncMock) as mock_beep:
        await buzzer.alarm_beep("emergency_stop", "E-Stop pressed")
        mock_beep.assert_called_once_with(duration_s=0.08, count=5, interval_s=0.08)

    # 2. Overload pattern (4 bursts)
    with patch.object(buzzer, "beep", new_callable=AsyncMock) as mock_beep:
        await buzzer.alarm_beep("overload_trip", "Locked rotor")
        mock_beep.assert_called_once_with(duration_s=0.15, count=4, interval_s=0.10)

    # 3. Dry run pattern (3 bursts)
    with patch.object(buzzer, "beep", new_callable=AsyncMock) as mock_beep:
        await buzzer.alarm_beep("dry_run", "Loss of prime")
        mock_beep.assert_called_once_with(duration_s=0.20, count=3, interval_s=0.15)

    # 4. Leak pattern (2 long bursts)
    with patch.object(buzzer, "beep", new_callable=AsyncMock) as mock_beep:
        await buzzer.alarm_beep("leak_detected", "Pipe burst")
        mock_beep.assert_called_once_with(duration_s=0.40, count=2, interval_s=0.20)

    await buzzer.stop()
    system_config.buzzer.enabled = False


@pytest.mark.asyncio
async def test_buzzer_audio_failover_trigger():
    """Buzzer automatically triggers when voice audio is absent or disabled."""
    backend = MockGPIOBackend()
    buzzer = BuzzerController(backend=backend)

    system_config.buzzer.enabled = True
    system_config.buzzer.gpio_pin = 13
    system_config.buzzer.beep_on_alarm = True
    await buzzer.setup()

    # Case A: Audio hardware absent -> Triggers buzzer
    with patch.object(audio_controller, "_has_audio_hardware", False), \
         patch.object(buzzer, "alarm_beep", new_callable=AsyncMock) as mock_alarm:
        triggered = await buzzer.trigger_alarm_if_audio_unavailable("wiring_fault", "Interlock open", priority=0)
        assert triggered is True
        mock_alarm.assert_called_once()

    # Case B: Voice audio disabled in config -> Triggers buzzer
    with patch.object(system_config.audio, "enabled", False), \
         patch.object(buzzer, "alarm_beep", new_callable=AsyncMock) as mock_alarm:
        triggered = await buzzer.trigger_alarm_if_audio_unavailable("emergency_stop", "E-Stop", priority=0)
        assert triggered is True
        mock_alarm.assert_called_once()

    # Case C: Voice audio hardware present and enabled -> Buzzer does not trigger
    with patch.object(audio_controller, "_has_audio_hardware", True), \
         patch.object(system_config.audio, "enabled", True), \
         patch.object(audio_controller, "_is_in_quiet_hours", return_value=False), \
         patch.object(buzzer, "alarm_beep", new_callable=AsyncMock) as mock_alarm:
        triggered = await buzzer.trigger_alarm_if_audio_unavailable("dry_run", priority=0)
        assert triggered is False
        mock_alarm.assert_not_called()

    await buzzer.stop()
    system_config.buzzer.enabled = False


@pytest.mark.asyncio
async def test_audio_driver_speak_falls_back_to_buzzer():
    """VoiceAlertSynthesizer.speak() falls back to buzzer when audio is disabled."""
    from app.hardware.buzzer import buzzer_controller

    with patch.object(system_config.audio, "enabled", False), \
         patch.object(buzzer_controller, "alarm_beep", new_callable=AsyncMock) as mock_alarm:
        await audio_controller.speak("Emergency: Water leak detected.", priority=0)
        mock_alarm.assert_called_once()
