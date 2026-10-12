"""
Protocol v1.1 Serial & ESP-NOW Packet Serializer and Validator.
Shared JSON Lines binary/serial contract between ROCK Pi S and ESP32-C3 Garden Node.

Implements:
  - Fixed-order canonical JSON serialization (identical between C++ and Python)
  - Textual signature stripping and in-place byte verification without JSON re-serialization
  - Truncated 16-hex HMAC-SHA256 signature computation and constant-time verification
  - Session boot nonce tracking and strictly monotonic sequence validation
  - Pydantic packet schemas
"""

import hashlib
import hmac
import json
import logging
import re
import secrets
from typing import Any, Dict, List, Literal, Optional, Tuple, Union
from pydantic import BaseModel, Field

logger = logging.getLogger("smart_water.protocol")

PROTOCOL_VERSION = "1.1"

# Pi's persistent boot nonce for this process lifetime
PI_BOOT_NONCE: int = secrets.randbits(32)

# Regex to textually locate and strip ,"sig":"<16hex>"} from the raw line
SIG_PATTERN = re.compile(r'^(.*),"sig":"([0-9a-fA-F]{16})"\}$')


# ==============================================================================
# Canonical Formatters (Fixed Key Order, Compact Separators)
# ==============================================================================

def format_hb_unsigned(nonce: int, seq: int, uptime_s: int, res_wet: bool, ads_ok: bool,
                       pumps: List[int], reset_reason: int, rssi: int) -> str:
    p0 = 1 if pumps[0] else 0
    p1 = 1 if pumps[1] else 0
    p2 = 1 if pumps[2] else 0
    p3 = 1 if pumps[3] else 0
    rw = "true" if res_wet else "false"
    ao = "true" if ads_ok else "false"
    return (
        f'{{"v":"1.1","t":"hb","nonce":{nonce},"seq":{seq},"uptime_s":{uptime_s},'
        f'"res_wet":{rw},"ads_ok":{ao},"pumps":[{p0},{p1},{p2},{p3}],'
        f'"reset_reason":{reset_reason},"rssi":{rssi}}}'
    )


def format_soil_unsigned(nonce: int, seq: int, z: int, volts: float) -> str:
    return f'{{"v":"1.1","t":"soil","nonce":{nonce},"seq":{seq},"z":{z},"volts":{volts:.3f}}}'


def format_flow_unsigned(nonce: int, seq: int, z: int, delta_pulses: int, period_ms: int) -> str:
    return (
        f'{{"v":"1.1","t":"flow","nonce":{nonce},"seq":{seq},"z":{z},'
        f'"delta_pulses":{delta_pulses},"period_ms":{period_ms}}}'
    )


def format_pump_unsigned(nonce: int, seq: int, z: int, on: bool, reason: str, run_s: int) -> str:
    on_str = "true" if on else "false"
    return (
        f'{{"v":"1.1","t":"pump","nonce":{nonce},"seq":{seq},"z":{z},'
        f'"on":{on_str},"reason":"{reason}","run_s":{run_s}}}'
    )


def format_ack_unsigned(nonce: int, seq: int, id_str: str, status: str, err: str = "") -> str:
    return (
        f'{{"v":"1.1","t":"ack","nonce":{nonce},"seq":{seq},'
        f'"id":"{id_str}","status":"{status}","err":"{err}"}}'
    )


def format_cmd_unsigned(nonce: int, seq: int, id_str: str, z: int, run_s: int) -> str:
    return f'{{"v":"1.1","t":"cmd","nonce":{nonce},"seq":{seq},"id":"{id_str}","z":{z},"run_s":{run_s}}}'


def format_stop_unsigned(nonce: int, seq: int, id_str: str, z: int) -> str:
    return f'{{"v":"1.1","t":"stop","nonce":{nonce},"seq":{seq},"id":"{id_str}","z":{z}}}'


def format_stop_all_unsigned(nonce: int, seq: int, id_str: str) -> str:
    return f'{{"v":"1.1","t":"stop_all","nonce":{nonce},"seq":{seq},"id":"{id_str}"}}'


# ==============================================================================
# Signature Calculation & Verification
# ==============================================================================

def compute_sig16(unsigned_canonical_str: str, secret_key: str) -> str:
    """Computes first 16 hex characters of HMAC-SHA256 over exact canonical string."""
    k_bytes = secret_key.encode("utf-8")
    d_bytes = unsigned_canonical_str.encode("utf-8")
    return hmac.new(k_bytes, d_bytes, hashlib.sha256).hexdigest()[:16]


def sign_packet_string(unsigned_canonical_str: str, secret_key: str) -> str:
    """
    Appends signature to an unsigned canonical string ending in '}'
    Returns line terminated with '\\n'.
    """
    if not unsigned_canonical_str.endswith("}"):
        raise ValueError("Unsigned packet must end with '}'")
    sig = compute_sig16(unsigned_canonical_str, secret_key)
    return unsigned_canonical_str[:-1] + f',\"sig\":\"{sig}\"}}\n'


def canonicalize_packet_dict(data: Dict[str, Any]) -> str:
    """Serializes packet dictionary to exact fixed-order canonical string without sig."""
    t = data.get("t")
    nonce = data.get("nonce", PI_BOOT_NONCE)
    seq = data.get("seq", 0)

    if t == "hb":
        pumps = data.get("pumps", [0, 0, 0, 0])
        return format_hb_unsigned(
            nonce, seq, data.get("uptime_s", 0), bool(data.get("res_wet")),
            bool(data.get("ads_ok")), pumps, data.get("reset_reason", 0), data.get("rssi", -60)
        )
    elif t == "soil":
        return format_soil_unsigned(nonce, seq, data.get("z", 1), float(data.get("volts", 0.0)))
    elif t == "flow":
        return format_flow_unsigned(nonce, seq, data.get("z", 1), data.get("delta_pulses", 0), data.get("period_ms", 1000))
    elif t == "pump":
        return format_pump_unsigned(nonce, seq, data.get("z", 1), bool(data.get("on")), data.get("reason", ""), data.get("run_s", 0))
    elif t == "ack":
        return format_ack_unsigned(nonce, seq, data.get("id", ""), data.get("status", "ok"), data.get("err", ""))
    elif t == "cmd":
        return format_cmd_unsigned(nonce, seq, data.get("id", ""), data.get("z", 1), data.get("run_s", 15))
    elif t == "stop":
        return format_stop_unsigned(nonce, seq, data.get("id", ""), data.get("z", 1))
    elif t == "stop_all":
        return format_stop_all_unsigned(nonce, seq, data.get("id", ""))
    else:
        filtered = {k: v for k, v in data.items() if k != "sig"}
        return json.dumps(filtered, sort_keys=True, separators=(",", ":"))


def compute_packet_signature(data: Union[Dict[str, Any], str], secret_key: str) -> str:
    """Computes first 16 hex characters of HMAC-SHA256."""
    if isinstance(data, str):
        return compute_sig16(data, secret_key)
    return compute_sig16(canonicalize_packet_dict(data), secret_key)


def sign_packet(data: Dict[str, Any], secret_key: str) -> Dict[str, Any]:
    """Populates sig in packet dictionary and ensures nonce is present."""
    pkt = dict(data)
    if "nonce" not in pkt:
        pkt["nonce"] = PI_BOOT_NONCE
    pkt["sig"] = compute_packet_signature(pkt, secret_key)
    return pkt


def verify_and_strip_sig(
    raw_line: Union[str, bytes],
    secret_key: str
) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """
    Textually verifies signature and strips ,"sig":"<16hex>"} from the line.
    Returns:
      (is_valid: bool, unsigned_canonical_str: Optional[str], actual_sig: Optional[str], error_reason: Optional[str])
    """
    if isinstance(raw_line, bytes):
        try:
            line_str = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            return False, None, None, "Invalid UTF-8 bytes"
    else:
        line_str = raw_line

    line_str = line_str.rstrip("\r\n").strip()
    if not line_str:
        return False, None, None, "Empty packet"

    match = SIG_PATTERN.match(line_str)
    if not match:
        return False, None, None, "Missing or malformed ,'sig':'<16hex>'} suffix"

    prefix = match.group(1)
    actual_sig = match.group(2).lower()
    unsigned_str = prefix + "}"

    expected_sig = compute_sig16(unsigned_str, secret_key)
    if not hmac.compare_digest(actual_sig, expected_sig):
        return False, unsigned_str, actual_sig, f"Signature mismatch (got {actual_sig}, expected {expected_sig})"

    return True, unsigned_str, actual_sig, None


# ==============================================================================
# Pydantic Packet Models
# ==============================================================================

class BasePacket(BaseModel):
    v: Literal["1.1"] = PROTOCOL_VERSION
    t: str
    nonce: int = Field(..., ge=0)
    seq: int = Field(..., ge=0)
    sig: str = Field(..., pattern=r"^[0-9a-f]{16}$")


class HeartbeatPacket(BasePacket):
    t: Literal["hb"] = "hb"
    uptime_s: int = Field(..., ge=0)
    res_wet: bool
    ads_ok: bool
    pumps: List[int] = Field(..., min_length=4, max_length=4)
    reset_reason: int = 0
    rssi: int


class SoilPacket(BasePacket):
    t: Literal["soil"] = "soil"
    z: int = Field(..., ge=1, le=4)
    volts: float = Field(..., ge=0.0, le=3.6)


class FlowPacket(BasePacket):
    t: Literal["flow"] = "flow"
    z: int = Field(..., ge=1, le=4)
    delta_pulses: int = Field(..., ge=0)
    period_ms: int = Field(1000, ge=100)


class PumpEventPacket(BasePacket):
    t: Literal["pump"] = "pump"
    z: int = Field(..., ge=1, le=4)
    on: bool
    reason: str
    run_s: int = Field(0, ge=0)


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
    err: str = ""


# Gateway internal messages (over USB serial)
class GatewayInitPacket(BaseModel):
    v: Literal["1.1"] = PROTOCOL_VERSION
    t: Literal["gw_init"] = "gw_init"
    uptime_s: int = 0
    mac: str = ""


class GatewayHbPacket(BaseModel):
    v: Literal["1.1"] = PROTOCOL_VERSION
    t: Literal["gw_hb"] = "gw_hb"
    uptime_s: int = 0
    free_heap: int = 0


class GatewayErrPacket(BaseModel):
    v: Literal["1.1"] = PROTOCOL_VERSION
    t: Literal["gw_err"] = "gw_err"
    err: str
    detail: str = ""


PacketModel = Union[
    HeartbeatPacket, SoilPacket, FlowPacket, PumpEventPacket,
    CommandPacket, StopPacket, StopAllPacket, AckPacket,
    GatewayInitPacket, GatewayHbPacket, GatewayErrPacket
]


# ==============================================================================
# Monotonic Sequence & Boot Nonce Tracker (Part 2.1)
# ==============================================================================

class SequenceTracker:
    """Tracks sequence numbers and random 32-bit boot nonces per sender session."""

    def __init__(self):
        self.last_nonce: Optional[int] = None
        self.last_seq: int = -1

    def validate_and_update(self, seq: int, nonce: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        """
        Validates monotonic sequence number.
        If a new boot nonce is received, recognizes a fresh session and resets sequence tracking.
        """
        if nonce is not None:
            if self.last_nonce is None or nonce != self.last_nonce:
                # Fresh boot session detected: accept and reset
                self.last_nonce = nonce
                self.last_seq = seq
                return True, None

        # Same session: sequence must be strictly increasing
        if self.last_seq >= 0 and seq <= self.last_seq:
            return False, f"Replay or out-of-order sequence (got {seq}, expected > {self.last_seq})"

        self.last_seq = seq
        return True, None


# ==============================================================================
# High-Level Packet Parser & Validator
# ==============================================================================

def parse_and_validate_packet(
    raw_line: Union[str, bytes],
    secret_key: str,
    seq_tracker: Optional[SequenceTracker] = None,
) -> Tuple[Optional[PacketModel], Optional[str]]:
    """
    Parses a raw line string into a validated PacketModel:
      1. Signature verification & stripping over raw bytes
      2. Protocol version check (1.1)
      3. Monotonic sequence check with boot nonce handling
      4. Pydantic schema validation
    """
    valid, unsigned_str, actual_sig, err = verify_and_strip_sig(raw_line, secret_key)
    if not valid:
        # Check if it's a gateway-local message (which has no sig field)
        try:
            line_str = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            data = json.loads(line_str.strip())
            t = data.get("t")
            if t == "gw_init":
                return GatewayInitPacket.model_validate(data), None
            elif t == "gw_hb":
                return GatewayHbPacket.model_validate(data), None
            elif t == "gw_err":
                return GatewayErrPacket.model_validate(data), None
        except Exception:
            pass
        return None, err or "Signature verification failed"

    # Signature is valid! Parse JSON from the verified canonical string
    try:
        data = json.loads(unsigned_str)
    except Exception as exc:
        return None, f"Malformed JSON in signed payload: {exc}"

    if data.get("v") != PROTOCOL_VERSION:
        return None, f"Unsupported protocol version {data.get('v')} (expected {PROTOCOL_VERSION})"

    # Re-insert validated signature into data dict for model validation
    data["sig"] = actual_sig

    # Validate sequence number and boot nonce
    if seq_tracker is not None:
        seq = data.get("seq")
        nonce = data.get("nonce")
        if seq is None or not isinstance(seq, int):
            return None, "Missing integer sequence number"
        ok, reason = seq_tracker.validate_and_update(seq, nonce)
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
        elif t == "pump":
            return PumpEventPacket.model_validate(data), None
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
