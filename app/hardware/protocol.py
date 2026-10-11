"""
Protocol v1.1 Serial & ESP-NOW Packet Serializer and Validator.
Shared JSON Lines binary/serial contract between ROCK Pi S and ESP32-C3 Garden Node.
Implements:
  - Canonical JSON serialization
  - HMAC-SHA256 truncated (16-char) signature computation and verification
  - Monotonic sequence tracking with boot counter rollover detection
  - Pydantic packet schemas
"""

import hashlib
import hmac
import json
import logging
from typing import Any, Dict, List, Literal, Optional, Tuple, Union
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("smart_water.protocol")

PROTOCOL_VERSION = "1.1"


def canonicalize_packet_dict(data: Dict[str, Any]) -> bytes:
    """
    Serializes packet dictionary into canonical JSON bytes.
    Excludes 'sig' field, sorts keys lexicographically, uses compact separators without spaces.
    """
    filtered = {k: v for k, v in data.items() if k != "sig"}
    canonical_str = json.dumps(filtered, sort_keys=True, separators=(",", ":"))
    return canonical_str.encode("utf-8")


def compute_packet_signature(data: Dict[str, Any], secret_key: str) -> str:
    """Computes first 16 hex characters of HMAC-SHA256 over canonical JSON."""
    canonical_bytes = canonicalize_packet_dict(data)
    key_bytes = secret_key.encode("utf-8")
    full_hex = hmac.new(key_bytes, canonical_bytes, hashlib.sha256).hexdigest()
    return full_hex[:16]


def sign_packet(data: Dict[str, Any], secret_key: str) -> Dict[str, Any]:
    """Signs packet in-place or returns copy with 'sig' field populated."""
    pkt = dict(data)
    pkt["sig"] = compute_packet_signature(pkt, secret_key)
    return pkt


# ==============================================================================
# Pydantic Packet Models
# ==============================================================================

class BasePacket(BaseModel):
    v: Literal["1.1"] = PROTOCOL_VERSION
    t: str
    seq: int = Field(..., ge=0)
    sig: str = Field(..., pattern=r"^[0-9a-f]{16}$")


class HeartbeatPacket(BasePacket):
    t: Literal["hb"] = "hb"
    boot: int = Field(..., ge=0)
    uptime_s: int = Field(..., ge=0)
    res_wet: bool
    ads_ok: bool
    pumps: List[int] = Field(..., min_length=4, max_length=4)
    rssi: int


class SoilPacket(BasePacket):
    t: Literal["soil"] = "soil"
    z: int = Field(..., ge=1, le=4)
    volts: float = Field(..., ge=0.0, le=3.6)


class FlowPacket(BasePacket):
    t: Literal["flow"] = "flow"
    z: int = Field(..., ge=1, le=4)
    pulses_delta: int = Field(..., ge=0)


class CommandPacket(BasePacket):
    t: Literal["cmd"] = "cmd"
    id: str
    z: int = Field(..., ge=1, le=4)
    run_s: int = Field(..., ge=1, le=60)


class StopPacket(BasePacket):
    t: Literal["stop"] = "stop"
    id: str
    z: int = Field(..., ge=1, le=4)


class StopAllPacket(BasePacket):
    t: Literal["stop_all"] = "stop_all"
    id: str


class AckPacket(BasePacket):
    t: Literal["ack"] = "ack"
    id: str
    status: Literal["ok", "err", "rejected"]
    message: Optional[str] = None


PacketModel = Union[HeartbeatPacket, SoilPacket, FlowPacket, CommandPacket, StopPacket, StopAllPacket, AckPacket]


class SequenceTracker:
    """Tracks sequence numbers and boot cycles for incoming packets."""

    def __init__(self):
        self.last_boot: int = -1
        self.last_seq: int = -1

    def validate_and_update(self, seq: int, boot: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        """
        Validates monotonic sequence number.
        If boot count increases, resets sequence tracking to allow fresh boot start.
        """
        if boot is not None:
            if boot > self.last_boot:
                # Fresh node boot detected
                self.last_boot = boot
                self.last_seq = seq
                return True, None
            elif boot < self.last_boot:
                return False, f"Stale boot cycle (got {boot}, expected >= {self.last_boot})"

        # Within same boot session: strictly monotonic
        if self.last_seq >= 0 and seq <= self.last_seq:
            return False, f"Replay or out-of-order sequence (got {seq}, expected > {self.last_seq})"

        self.last_seq = seq
        return True, None


def parse_and_validate_packet(
    raw_line: Union[str, bytes],
    secret_key: str,
    seq_tracker: Optional[SequenceTracker] = None,
) -> Tuple[Optional[PacketModel], Optional[str]]:
    """
    Parses a raw line string into a validated PacketModel:
      1. JSON parse
      2. Protocol version check (1.1)
      3. HMAC-SHA256 signature verification
      4. Sequence number monotonicity check
      5. Pydantic schema validation
    """
    if isinstance(raw_line, bytes):
        try:
            line_str = raw_line.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None, "Packet bytes not valid UTF-8"
    else:
        line_str = raw_line.strip()

    if not line_str:
        return None, "Empty packet"

    try:
        data = json.loads(line_str)
    except Exception as exc:
        return None, f"Malformed JSON: {exc}"

    if not isinstance(data, dict):
        return None, "Packet payload must be a JSON object"

    if data.get("v") != PROTOCOL_VERSION:
        return None, f"Unsupported protocol version {data.get('v')} (expected {PROTOCOL_VERSION})"

    actual_sig = data.get("sig")
    if not actual_sig or not isinstance(actual_sig, str):
        return None, "Missing or invalid signature"

    expected_sig = compute_packet_signature(data, secret_key)
    if not hmac.compare_digest(actual_sig, expected_sig):
        return None, f"HMAC signature mismatch (got {actual_sig}, computed {expected_sig})"

    # Sequence monotonicity check
    if seq_tracker is not None:
        seq = data.get("seq")
        boot = data.get("boot")
        if seq is None or not isinstance(seq, int):
            return None, "Missing integer sequence number"
        ok, reason = seq_tracker.validate_and_update(seq, boot)
        if not ok:
            return None, f"Sequence validation failed: {reason}"

    # Schema parse
    t = data.get("t")
    try:
        if t == "hb":
            return HeartbeatPacket.model_validate(data), None
        elif t == "soil":
            return SoilPacket.model_validate(data), None
        elif t == "flow":
            return FlowPacket.model_validate(data), None
        elif t == "cmd":
            return CommandPacket.model_validate(data), None
        elif t == "stop":
            return StopPacket.model_validate(data), None
        elif t == "stop_all":
            return StopAllPacket.model_validate(data), None
        elif t == "ack":
            return AckPacket.model_validate(data), None
        else:
            return None, f"Unknown packet type '{t}'"
    except Exception as exc:
        return None, f"Schema validation error: {exc}"
