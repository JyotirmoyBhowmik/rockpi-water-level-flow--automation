"""
Protocol v1.1 Contract Verification Test.
Validates bi-directional serialization and parsing against protocol/messages.json schema:
  - Canonical JSON fixed-order serialization
  - Golden test vector verification against protocol/messages.json
  - Bi-directional interoperability between native C++ signer (host_cpp_signer) and Python verifier
  - Monotonic sequence numbers and random boot nonce session reset
  - Signature tampering rejection
"""

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

from app.core.state_manager import state_manager
from app.engine.plant_controller import plant_controller
from app.engine.scheduler import scheduler
from app.hardware.gateway_driver import garden_gateway
from app.hardware.protocol import (
    AckPacket,
    CommandPacket,
    FlowPacket,
    GatewayErrPacket,
    GatewayHbPacket,
    GatewayInitPacket,
    HeartbeatPacket,
    PumpEventPacket,
    SequenceTracker,
    SoilPacket,
    StopAllPacket,
    StopPacket,
    EnvPacket,
    compute_sig16,
    format_ack_unsigned,
    format_cmd_unsigned,
    format_flow_unsigned,
    format_hb_unsigned,
    format_hb_env_unsigned,
    format_env_unsigned,
    format_pump_unsigned,
    format_soil_unsigned,
    format_stop_all_unsigned,
    format_stop_unsigned,
    parse_and_validate_packet,
    sign_packet_string,
    verify_and_strip_sig,
)

SHARED_KEY = "test-secret-key-32-chars-long12345"


def get_or_compile_cpp_binary() -> Path:
    """Finds g++ compiler and ensures tests/host_cpp_signer is compiled."""
    exe_name = "host_cpp_signer.exe" if os.name == "nt" else "host_cpp_signer"
    exe_path = Path("tests") / exe_name

    gpp = shutil.which("g++")
    if not gpp and os.name == "nt":
        # Check scoop path
        scoop_gpp = Path(os.environ.get("USERPROFILE", "")) / "scoop" / "apps" / "gcc" / "current" / "bin" / "g++.exe"
        if scoop_gpp.exists():
            gpp = str(scoop_gpp)

    if not exe_path.exists():
        assert gpp is not None, f"g++ not found on PATH or scoop to compile {exe_path}"
        src_path = Path("tests") / "host_cpp_signer.cpp"
        cmd = [gpp, "-O2", "-Wall", str(src_path), "-o", str(exe_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"Compilation failed: {res.stderr}"

    return exe_path


def test_schema_file_exists_and_golden_vectors():
    """Verifies protocol/messages.json is present and all golden vectors match."""
    schema_path = Path("protocol/messages.json")
    assert schema_path.exists(), "protocol/messages.json must exist"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    assert schema.get("title") == "SmartWaterProtocolV1_1"
    assert "canonical_rules" in schema
    assert "fixed_key_orders" in schema
    golden_vectors = schema.get("golden_vectors", [])
    assert len(golden_vectors) >= 8

    for gv in golden_vectors:
        key = gv["secret_key"]
        unsigned = gv["unsigned"]
        expected_sig = gv["sig"]
        signed_line = gv["signed_line"]

        # 1. Compute sig in Python
        computed_sig = compute_sig16(unsigned, key)
        assert computed_sig == expected_sig, f"Vector {gv['name']} signature mismatch"

        # 2. Verify signature stripping
        valid, extracted_unsigned, actual_sig, err = verify_and_strip_sig(signed_line, key)
        assert valid is True, f"Vector {gv['name']} failed verification: {err}"
        assert extracted_unsigned == unsigned
        assert actual_sig == expected_sig


def test_cpp_generated_packets_verified_by_python():
    """
    Runs native C++ implementation (host_cpp_signer --generate) and passes
    emitted bytes into Python's parse_and_validate_packet.
    """
    cpp_exe = get_or_compile_cpp_binary()
    proc = subprocess.run(
        [str(cpp_exe), "--generate", SHARED_KEY],
        capture_output=True,
        text=True,
        check=True
    )
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 12, f"Expected 12 packets from C++ signer, got {len(lines)}"

    seq_tracker = SequenceTracker()
    parsed_packets = []
    for line in lines:
        pkt, err = parse_and_validate_packet(line, SHARED_KEY, seq_tracker)
        assert err is None, f"Python failed to validate C++ packet: {line} (error: {err})"
        assert pkt is not None
        parsed_packets.append(pkt)

    # Validate model types received from C++
    assert isinstance(parsed_packets[0], HeartbeatPacket)
    assert parsed_packets[0].uptime_s == 120
    assert parsed_packets[0].rssi == -65

    assert isinstance(parsed_packets[1], SoilPacket)
    assert parsed_packets[1].z == 1
    assert abs(parsed_packets[1].volts - 1.842) < 0.001

    assert isinstance(parsed_packets[2], FlowPacket)
    assert parsed_packets[2].delta_pulses == 15

    assert isinstance(parsed_packets[3], PumpEventPacket)
    assert parsed_packets[3].on is True
    assert parsed_packets[3].reason == "cmd"

    assert isinstance(parsed_packets[4], AckPacket)
    assert parsed_packets[4].status == "ok"

    assert isinstance(parsed_packets[5], CommandPacket)
    assert parsed_packets[5].z == 1
    assert parsed_packets[5].run_s == 15

    assert isinstance(parsed_packets[6], StopPacket)
    assert parsed_packets[6].z == 1

    assert isinstance(parsed_packets[7], StopAllPacket)
    assert parsed_packets[7].id == "cmd-003"

    assert isinstance(parsed_packets[8], PumpEventPacket)
    assert parsed_packets[8].on is False
    assert parsed_packets[8].reason == 'fault: "sensor" \\ timeout'

    assert isinstance(parsed_packets[9], AckPacket)
    assert parsed_packets[9].id == 'cmd:"001"\\test'
    assert parsed_packets[9].status == "err"
    assert parsed_packets[9].err == 'err: "timeout" \\ fault'

    assert isinstance(parsed_packets[10], HeartbeatPacket)
    assert parsed_packets[10].env is not None
    assert abs(parsed_packets[10].env.temp - 24.5) < 0.001
    assert abs(parsed_packets[10].env.rh - 55.2) < 0.001
    assert abs(parsed_packets[10].env.press - 1013.25) < 0.001

    assert isinstance(parsed_packets[11], EnvPacket)
    assert abs((parsed_packets[11].temp or 0.0) - 24.5) < 0.001
    assert abs((parsed_packets[11].rh or 0.0) - 55.2) < 0.001
    assert abs((parsed_packets[11].press or 0.0) - 1013.25) < 0.001


def test_python_generated_packets_verified_by_cpp():
    """
    Generates signed packets using Python's formatters and pipes them into
    the native C++ verifier (host_cpp_signer --verify).
    """
    cpp_exe = get_or_compile_cpp_binary()

    # Create one packet of each type in Python (including escaped quotes/slashes)
    nonce = 5555
    python_packets = [
        sign_packet_string(format_hb_unsigned(nonce, 1, 300, True, True, [0, 1, 0, 0], 0, -60), SHARED_KEY),
        sign_packet_string(format_soil_unsigned(nonce, 2, 2, 2.155), SHARED_KEY),
        sign_packet_string(format_flow_unsigned(nonce, 3, 1, 8, 1000), SHARED_KEY),
        sign_packet_string(format_pump_unsigned(nonce, 4, 2, False, "done", 25), SHARED_KEY),
        sign_packet_string(format_ack_unsigned(nonce, 5, "cmd-99", "ok", ""), SHARED_KEY),
        sign_packet_string(format_cmd_unsigned(nonce, 6, "cmd-100", 3, 20), SHARED_KEY),
        sign_packet_string(format_stop_unsigned(nonce, 7, "cmd-101", 3), SHARED_KEY),
        sign_packet_string(format_stop_all_unsigned(nonce, 8, "cmd-102"), SHARED_KEY),
        sign_packet_string(format_pump_unsigned(nonce, 9, 1, False, 'fault: "sensor" \\ timeout', 10), SHARED_KEY),
        sign_packet_string(format_ack_unsigned(nonce, 10, 'cmd:"001"\\test', "err", 'err: "timeout" \\ fault'), SHARED_KEY),
        sign_packet_string(format_hb_env_unsigned(nonce, 11, 300, True, True, [0, 0, 0, 0], 0, -60, 24.5, 55.2, 1013.25), SHARED_KEY),
        sign_packet_string(format_env_unsigned(nonce, 12, 24.5, 55.2, 1013.25), SHARED_KEY),
    ]

    stdin_input = "".join(python_packets)
    proc = subprocess.run(
        [str(cpp_exe), "--verify", SHARED_KEY],
        input=stdin_input,
        capture_output=True,
        text=True,
        check=True
    )

    results = [r.strip() for r in proc.stdout.splitlines() if r.strip()]
    assert len(results) == len(python_packets)
    for r in results:
        assert r.startswith("OK "), f"C++ verification failed for Python packet: {r}"


def test_signature_tampering_rejected_both_sides():
    """Verifies that tampering with any character invalidates signature in Python and C++."""
    cpp_exe = get_or_compile_cpp_binary()

    valid_packet = sign_packet_string(format_soil_unsigned(1001, 1, 1, 1.500), SHARED_KEY)
    # Tamper with voltage
    tampered_packet = valid_packet.replace('"volts":1.500', '"volts":2.500')

    # Python check
    valid, _, _, err = verify_and_strip_sig(tampered_packet, SHARED_KEY)
    assert valid is False
    assert "mismatch" in err.lower()

    # C++ check
    proc = subprocess.run(
        [str(cpp_exe), "--verify", SHARED_KEY],
        input=tampered_packet,
        capture_output=True,
        text=True,
        check=True
    )
    assert proc.stdout.strip() == "FAIL"


def test_sequence_monotonicity_and_boot_nonce_reset():
    """Verifies sequence rejection on replay and session reset on new boot nonce."""
    tracker = SequenceTracker()

    # Session 1 (nonce 1001)
    ok, err = tracker.validate_and_update(1, nonce=1001)
    assert ok is True
    assert err is None

    ok, err = tracker.validate_and_update(2, nonce=1001)
    assert ok is True

    # Replay sequence 2 -> rejected
    ok, err = tracker.validate_and_update(2, nonce=1001)
    assert ok is False
    assert "Replay" in err

    # Out of order sequence 1 -> rejected
    ok, err = tracker.validate_and_update(1, nonce=1001)
    assert ok is False

    # Node reboots: new nonce 9999 -> accepted and sequence tracking resets to 1
    ok, err = tracker.validate_and_update(1, nonce=9999)
    assert ok is True
    assert err is None


@pytest.mark.asyncio
async def test_gateway_driver_write_packet_verified_by_cpp(monkeypatch):
    """
    Verifies that packets sent by garden_gateway._write_packet are byte-exact
    canonical JSON lines accepted and verified by the native C++ verifier.
    """
    from app.config import system_config
    from pydantic import SecretStr

    old_key = system_config.garden_gateway.shared_key
    system_config.garden_gateway.shared_key = SecretStr(SHARED_KEY)
    try:
        cpp_exe = get_or_compile_cpp_binary()

        captured_lines = []

        class MockSerial:
            def write(self, data: bytes):
                captured_lines.append(data.decode("utf-8"))

            def flush(self):
                pass

        garden_gateway._serial_port = MockSerial()

        # 1. Cmd packet
        await garden_gateway._write_packet({
            "v": "1.1",
            "t": "cmd",
            "nonce": 4321,
            "seq": 1,
            "id": "cmd-gw-1",
            "z": 1,
            "run_s": 15,
        })

        # 2. Stop packet
        await garden_gateway._write_packet({
            "v": "1.1",
            "t": "stop",
            "nonce": 4321,
            "seq": 2,
            "id": "cmd-gw-2",
            "z": 1,
        })

        # 3. Stop All packet
        await garden_gateway._write_packet({
            "v": "1.1",
            "t": "stop_all",
            "nonce": 4321,
            "seq": 3,
            "id": "cmd-gw-3",
        })

        garden_gateway._serial_port = None
        assert len(captured_lines) == 3

        stdin_input = "".join(captured_lines)
        proc = subprocess.run(
            [str(cpp_exe), "--verify", SHARED_KEY],
            input=stdin_input,
            capture_output=True,
            text=True,
            check=True,
        )

        results = [r.strip() for r in proc.stdout.splitlines() if r.strip()]
        assert len(results) == 3
        for r in results:
            assert r.startswith("OK "), f"C++ rejected packet from gateway_driver: {r}"
    finally:
        garden_gateway._serial_port = None
        system_config.garden_gateway.shared_key = old_key


@pytest.mark.asyncio
async def test_golden_vectors_gateway_routing_to_scheduler():
    """
    Feeds golden-vector signed lines from protocol/messages.json through
    parse_and_validate_packet -> scheduler._on_gateway_packet and asserts
    state is updated:
      - heartbeat updates timestamps, node_online, uptime_s, and nonce
      - soil updates voltage
      - flow pulses accumulate with real period_ms
      - pump on updates running state, and pump off completes the run
      - command ack resolves pending future in garden_gateway
      - dict compatibility branch routes through identical functions
    """
    spec_path = Path("protocol") / "messages.json"
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)

    vectors_by_name = {v["name"]: v for v in spec["golden_vectors"]}
    seq_tracker = SequenceTracker()

    # 1. Heartbeat packet
    hb_vec = vectors_by_name["heartbeat_packet"]
    hb_pkt, err = parse_and_validate_packet(hb_vec["signed_line"], hb_vec["secret_key"], seq_tracker)
    assert err is None
    assert isinstance(hb_pkt, HeartbeatPacket)
    assert hb_pkt.nonce == 1001

    await scheduler._on_gateway_packet(hb_pkt)
    st = state_manager.get_state()
    assert st["garden"]["node_online"] is True
    assert st["garden"]["last_heartbeat_ts"] is not None
    assert st["garden"]["uptime_s"] == 120
    assert st["garden"]["boot_count"] == 1001
    assert st["garden"]["reservoir_wet"] is True

    # 2. Soil probe packet
    soil_vec = vectors_by_name["soil_probe_packet"]
    soil_pkt, err = parse_and_validate_packet(soil_vec["signed_line"], soil_vec["secret_key"], seq_tracker)
    assert err is None
    assert isinstance(soil_pkt, SoilPacket)

    await scheduler._on_gateway_packet(soil_pkt)
    st = state_manager.get_state()
    assert st["garden"]["zones"]["zone_1"]["voltage"] == 1.842

    # 3. Flow delta packet (accumulates pulses with real period_ms)
    flow_vec = vectors_by_name["flow_delta_packet"]
    flow_pkt, err = parse_and_validate_packet(flow_vec["signed_line"], flow_vec["secret_key"], seq_tracker)
    assert err is None
    assert isinstance(flow_pkt, FlowPacket)
    assert flow_pkt.delta_pulses == 15
    assert flow_pkt.period_ms == 1000

    prev_flow_pulses = st["garden"]["zones"]["zone_1"].get("flow_pulses", 0)
    await scheduler._on_gateway_packet(flow_pkt)
    st = state_manager.get_state()
    assert st["garden"]["zones"]["zone_1"]["flow_pulses"] == prev_flow_pulses + 15
    assert st["garden"]["zones"]["zone_1"]["flow_delta"] == 15
    assert st["garden"]["zones"]["zone_1"]["flow_period_ms"] == 1000

    # 4. Pump event packet (on=True)
    pump_vec = vectors_by_name["pump_event_packet"]
    pump_pkt, err = parse_and_validate_packet(pump_vec["signed_line"], pump_vec["secret_key"], seq_tracker)
    assert err is None
    assert isinstance(pump_pkt, PumpEventPacket)
    assert pump_pkt.on is True

    await scheduler._on_gateway_packet(pump_pkt)
    st = state_manager.get_state()
    assert st["garden"]["zones"]["zone_1"]["pump_running"] is True

    # 5. Command Ack packet (seq=5) resolves pending command future in garden_gateway
    loop = asyncio.get_running_loop()
    ack_fut = loop.create_future()
    garden_gateway._pending_acks["cmd-001"] = ack_fut

    ack_vec = vectors_by_name["command_ack_packet"]
    ack_pkt, err = parse_and_validate_packet(ack_vec["signed_line"], ack_vec["secret_key"], seq_tracker)
    assert err is None
    assert isinstance(ack_pkt, AckPacket)

    await scheduler._on_gateway_packet(ack_pkt)
    assert ack_fut.done() is True
    resolved_ack = ack_fut.result()
    assert resolved_ack.id == "cmd-001"
    assert resolved_ack.status == "ok"

    # 6. Pump off event (seq=6) completes active run in plant_controller
    plant_controller._active_runs["zone_1"] = {
        "running": True,
        "cmd_id": "test-run-cmd-1",
        "duration_s": 15,
        "started_mono": 1000.0,
        "started_iso": "2026-10-12T12:00:00Z",
        "source": "test",
    }
    assert "zone_1" in plant_controller._active_runs

    unsigned_off = format_pump_unsigned(1001, 6, 1, False, "done", 15)
    signed_off = sign_packet_string(unsigned_off, SHARED_KEY)
    pump_off_pkt, err = parse_and_validate_packet(signed_off, SHARED_KEY, seq_tracker)
    assert err is None
    assert isinstance(pump_off_pkt, PumpEventPacket)
    assert pump_off_pkt.on is False

    await scheduler._on_gateway_packet(pump_off_pkt)
    st = state_manager.get_state()
    assert st["garden"]["zones"]["zone_1"]["pump_running"] is False
    assert "zone_1" not in plant_controller._active_runs  # Completed!

    # 7. Dict branch routed through the same functions
    dict_flow = {
        "t": "flow",
        "z": 2,
        "delta_pulses": 25,
        "period_ms": 2500,
    }
    prev_z2_pulses = st["garden"]["zones"]["zone_2"].get("flow_pulses", 0)
    await scheduler._on_gateway_packet(dict_flow)
    st = state_manager.get_state()
    assert st["garden"]["zones"]["zone_2"]["flow_pulses"] == prev_z2_pulses + 25
    assert st["garden"]["zones"]["zone_2"]["flow_delta"] == 25
    assert st["garden"]["zones"]["zone_2"]["flow_period_ms"] == 2500


def test_firmware_gateway_error_and_status_validation():
    """
    Verifies that locally generated JSON lines from firmware/gateway/src/main.cpp
    (using 'v':'1.1' string) validate as GatewayErrPacket, GatewayInitPacket, GatewayHbPacket.
    """
    # 1. Delivery failed error
    raw_err1 = b'{"v":"1.1","t":"gw_err","err":"esp_now_delivery_failed"}\n'
    pkt1, err1 = parse_and_validate_packet(raw_err1, "dummy_key")
    assert err1 is None
    assert isinstance(pkt1, GatewayErrPacket)
    assert pkt1.err == "esp_now_delivery_failed"
    assert str(pkt1.v) == "1.1"

    # 2. Send error with code
    raw_err2 = b'{"v":"1.1","t":"gw_err","err":"esp_now_send_error","code":1}\n'
    pkt2, err2 = parse_and_validate_packet(raw_err2, "dummy_key")
    assert err2 is None
    assert isinstance(pkt2, GatewayErrPacket)
    assert pkt2.err == "esp_now_send_error"
    assert pkt2.code == 1

    # 3. Broadcast MAC error
    raw_err3 = b'{"v":"1.1","t":"gw_err","err":"broadcast_mac_not_allowed"}\n'
    pkt3, err3 = parse_and_validate_packet(raw_err3, "dummy_key")
    assert err3 is None
    assert isinstance(pkt3, GatewayErrPacket)

    # 4. Gateway init announce
    raw_init = b'{"v":"1.1","t":"gw_init","channel":1}\n'
    pkt4, err4 = parse_and_validate_packet(raw_init, "dummy_key")
    assert err4 is None
    assert isinstance(pkt4, GatewayInitPacket)
    assert pkt4.channel == 1

    # 5. Gateway periodic heartbeat
    raw_hb = b'{"v":"1.1","t":"gw_hb","uptime_s":45}\n'
    pkt5, err5 = parse_and_validate_packet(raw_hb, "dummy_key")
    assert err5 is None
    assert isinstance(pkt5, GatewayHbPacket)
    assert pkt5.uptime_s == 45
