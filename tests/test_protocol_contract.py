"""
Protocol v1.1 Contract Verification Test.
Validates bi-directional serialization and parsing against protocol/messages.json schema:
  - Canonical JSON fixed-order serialization
  - Golden test vector verification against protocol/messages.json
  - Bi-directional interoperability between native C++ signer (host_cpp_signer) and Python verifier
  - Monotonic sequence numbers and random boot nonce session reset
  - Signature tampering rejection
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

from app.hardware.protocol import (
    AckPacket,
    CommandPacket,
    FlowPacket,
    HeartbeatPacket,
    PumpEventPacket,
    SequenceTracker,
    SoilPacket,
    StopAllPacket,
    StopPacket,
    compute_sig16,
    format_ack_unsigned,
    format_cmd_unsigned,
    format_flow_unsigned,
    format_hb_unsigned,
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
    assert len(lines) == 8, f"Expected 8 packets from C++ signer, got {len(lines)}"

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


def test_python_generated_packets_verified_by_cpp():
    """
    Generates signed packets using Python's formatters and pipes them into
    the native C++ verifier (host_cpp_signer --verify).
    """
    cpp_exe = get_or_compile_cpp_binary()

    # Create one packet of each type in Python
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
    from app.hardware.gateway_driver import garden_gateway

    system_config.garden_gateway.shared_key = SHARED_KEY
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
        check=True
    )

    results = [r.strip() for r in proc.stdout.splitlines() if r.strip()]
    assert len(results) == 3
    for r in results:
        assert r.startswith("OK "), f"C++ rejected packet from gateway_driver: {r}"
