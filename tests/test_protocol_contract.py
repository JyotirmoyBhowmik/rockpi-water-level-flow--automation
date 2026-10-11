"""
Protocol v1.1 Contract Verification Test.
Validates bi-directional serialization and parsing against protocol/messages.json schema:
  - Canonical JSON serialization
  - HMAC-SHA256 signature generation and validation
  - Monotonic sequence numbers and boot cycle reset
  - Python model of Node's parser and Pi's driver interoperability
"""

import hashlib
import hmac
import json
from pathlib import Path
import pytest
from app.hardware.protocol import (
    AckPacket,
    CommandPacket,
    FlowPacket,
    HeartbeatPacket,
    SequenceTracker,
    SoilPacket,
    StopAllPacket,
    StopPacket,
    canonicalize_packet_dict,
    compute_packet_signature,
    parse_and_validate_packet,
    sign_packet,
)

SHARED_KEY = "test-secret-garden-v1.1-key"


def test_schema_file_exists_and_valid_json():
    """Verifies shared schema file protocol/messages.json is present and valid JSON Schema."""
    schema_path = Path("protocol/messages.json")
    assert schema_path.exists(), "protocol/messages.json must exist"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    assert schema.get("title") == "SmartWaterProtocolV1_1"
    assert "oneOf" in schema
    assert "required" in schema
    assert "properties" in schema


def test_canonical_json_and_signature_computation():
    """Verifies that dict key ordering does not alter canonical representation or signature."""
    pkt1 = {"v": "1.1", "t": "hb", "seq": 42, "boot": 1, "uptime_s": 100}
    pkt2 = {"uptime_s": 100, "t": "hb", "boot": 1, "v": "1.1", "seq": 42}

    canon1 = canonicalize_packet_dict(pkt1)
    canon2 = canonicalize_packet_dict(pkt2)
    assert canon1 == canon2

    sig1 = compute_packet_signature(pkt1, SHARED_KEY)
    sig2 = compute_packet_signature(pkt2, SHARED_KEY)
    assert sig1 == sig2
    assert len(sig1) == 16


def test_pi_to_node_command_packet_contract():
    """
    Contract test: feeds exact bytes emitted by Pi's driver into Node's parser logic.
    """
    cmd_dict = {
        "v": "1.1",
        "t": "cmd",
        "id": "cmd-test-uuid-1234",
        "z": 2,
        "run_s": 25,
        "seq": 1,
    }
    signed_dict = sign_packet(cmd_dict, SHARED_KEY)
    raw_line_bytes = (json.dumps(signed_dict, separators=(",", ":")) + "\n").encode("utf-8")

    # Node's parser model
    node_seq_tracker = SequenceTracker()
    parsed_pkt, err = parse_and_validate_packet(raw_line_bytes, SHARED_KEY, node_seq_tracker)

    assert err is None
    assert isinstance(parsed_pkt, CommandPacket)
    assert parsed_pkt.v == "1.1"
    assert parsed_pkt.t == "cmd"
    assert parsed_pkt.id == "cmd-test-uuid-1234"
    assert parsed_pkt.z == 2
    assert parsed_pkt.run_s == 25
    assert parsed_pkt.seq == 1


def test_node_to_pi_telemetry_packet_contract():
    """
    Contract test: feeds exact bytes emitted by Node into Pi driver parser.
    """
    # 1. Heartbeat
    hb_dict = {
        "v": "1.1",
        "t": "hb",
        "seq": 10,
        "boot": 3,
        "uptime_s": 120,
        "res_wet": True,
        "ads_ok": True,
        "pumps": [0, 1, 0, 0],
        "rssi": -65,
    }
    signed_hb = sign_packet(hb_dict, SHARED_KEY)
    hb_bytes = json.dumps(signed_hb, separators=(",", ":")).encode("utf-8")

    pi_tracker = SequenceTracker()
    pkt, err = parse_and_validate_packet(hb_bytes, SHARED_KEY, pi_tracker)
    assert err is None
    assert isinstance(pkt, HeartbeatPacket)
    assert pkt.res_wet is True
    assert pkt.pumps == [0, 1, 0, 0]

    # 2. Flow Delta
    flow_dict = {
        "v": "1.1",
        "t": "flow",
        "seq": 11,
        "z": 1,
        "pulses_delta": 18,
    }
    signed_flow = sign_packet(flow_dict, SHARED_KEY)
    flow_bytes = json.dumps(signed_flow, separators=(",", ":")).encode("utf-8")

    pkt_flow, err_flow = parse_and_validate_packet(flow_bytes, SHARED_KEY, pi_tracker)
    assert err_flow is None
    assert isinstance(pkt_flow, FlowPacket)
    assert pkt_flow.z == 1
    assert pkt_flow.pulses_delta == 18

    # 3. Command Ack
    ack_dict = {
        "v": "1.1",
        "t": "ack",
        "seq": 12,
        "id": "cmd-test-uuid-1234",
        "status": "ok",
    }
    signed_ack = sign_packet(ack_dict, SHARED_KEY)
    ack_bytes = json.dumps(signed_ack, separators=(",", ":")).encode("utf-8")

    pkt_ack, err_ack = parse_and_validate_packet(ack_bytes, SHARED_KEY, pi_tracker)
    assert err_ack is None
    assert isinstance(pkt_ack, AckPacket)
    assert pkt_ack.id == "cmd-test-uuid-1234"
    assert pkt_ack.status == "ok"


def test_signature_tampering_rejected():
    """Verifies that modified payload or incorrect key is rejected."""
    pkt_dict = {
        "v": "1.1",
        "t": "soil",
        "seq": 5,
        "z": 3,
        "volts": 2.15,
    }
    signed = sign_packet(pkt_dict, SHARED_KEY)

    # Tamper with voltage
    tampered = dict(signed)
    tampered["volts"] = 1.05
    tampered_bytes = json.dumps(tampered).encode("utf-8")

    _, err = parse_and_validate_packet(tampered_bytes, SHARED_KEY)
    assert err is not None
    assert "signature mismatch" in err

    # Wrong key
    valid_bytes = json.dumps(signed).encode("utf-8")
    _, err_key = parse_and_validate_packet(valid_bytes, "wrong-key")
    assert err_key is not None
    assert "signature mismatch" in err_key


def test_sequence_monotonicity_and_boot_reset():
    """Verifies sequence numbers must strictly increase, but resets on new boot cycle."""
    tracker = SequenceTracker()

    # Initial packet seq=5, boot=1 -> OK
    ok, err = tracker.validate_and_update(seq=5, boot=1)
    assert ok is True

    # Next packet seq=6, boot=1 -> OK
    ok, err = tracker.validate_and_update(seq=6, boot=1)
    assert ok is True

    # Replay packet seq=6, boot=1 -> Rejected
    ok, err = tracker.validate_and_update(seq=6, boot=1)
    assert ok is False
    assert "Replay or out-of-order" in err

    # Out-of-order packet seq=4, boot=1 -> Rejected
    ok, err = tracker.validate_and_update(seq=4, boot=1)
    assert ok is False

    # Node rebooted: boot=2, seq=0 -> Accepted!
    ok, err = tracker.validate_and_update(seq=0, boot=2)
    assert ok is True

    # Next packet on boot=2
    ok, err = tracker.validate_and_update(seq=1, boot=2)
    assert ok is True
