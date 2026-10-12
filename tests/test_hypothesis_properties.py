"""
Hypothesis-based property tests for:
1. Protocol v1.1 canonical serialization, bit-exact C++ <-> Python round-trip,
   HMAC-SHA256 signature verification, and field preservation across random values.
2. Interlock state machine: cause derivation truth table, active-low polarity invariance,
   software debouncer transient glitch rejection, and wiring fault latching.
"""

import os
from pathlib import Path
import shutil
import subprocess

import pytest
from hypothesis import given, settings, strategies as st

from app.core.state_manager import state_manager
from app.hardware.gpio_driver import SoftwareDebouncer, _decode
from app.hardware.protocol import (
    AckPacket,
    FlowPacket,
    HeartbeatPacket,
    PumpEventPacket,
    SequenceTracker,
    SoilPacket,
    compute_sig16,
    format_ack_unsigned,
    format_flow_unsigned,
    format_hb_unsigned,
    format_pump_unsigned,
    format_soil_unsigned,
    parse_and_validate_packet,
    sign_packet_string,
)

SHARED_KEY = "hypothesis-secret-key-32-chars-ok123"


def get_cpp_signer_exe() -> Path:
    """Finds or compiles tests/host_cpp_signer executable."""
    exe_name = "host_cpp_signer.exe" if os.name == "nt" else "host_cpp_signer"
    exe_path = Path("tests") / exe_name

    if not exe_path.exists():
        gpp = shutil.which("g++")
        if not gpp and os.name == "nt":
            scoop_gpp = Path(os.environ.get("USERPROFILE", "")) / "scoop" / "apps" / "gcc" / "current" / "bin" / "g++.exe"
            if scoop_gpp.exists():
                gpp = str(scoop_gpp)

        assert gpp is not None, f"g++ required to compile {exe_path}"
        src_path = Path("tests") / "host_cpp_signer.cpp"
        cmd = [gpp, "-O2", "-Wall", str(src_path), "-o", str(exe_path)]
        subprocess.run(cmd, check=True)

    return exe_path


class _CppWorker:
    _signer_proc: subprocess.Popen[str] | None = None
    _verifier_proc: subprocess.Popen[str] | None = None

    @classmethod
    def get_signer(cls, key: str = SHARED_KEY) -> subprocess.Popen[str]:
        if cls._signer_proc is None or cls._signer_proc.poll() is not None:
            exe = get_cpp_signer_exe()
            cls._signer_proc = subprocess.Popen(
                [str(exe), "--sign-line", key],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        return cls._signer_proc

    @classmethod
    def get_verifier(cls, key: str = SHARED_KEY) -> subprocess.Popen[str]:
        if cls._verifier_proc is None or cls._verifier_proc.poll() is not None:
            exe = get_cpp_signer_exe()
            cls._verifier_proc = subprocess.Popen(
                [str(exe), "--verify", key],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        return cls._verifier_proc

    @classmethod
    def sign(cls, line: str, key: str = SHARED_KEY) -> str:
        proc = cls.get_signer(key)
        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write(line.strip() + "\n")
        proc.stdin.flush()
        return proc.stdout.readline().strip()

    @classmethod
    def verify(cls, line: str, key: str = SHARED_KEY) -> str:
        proc = cls.get_verifier(key)
        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write(line.strip() + "\n")
        proc.stdin.flush()
        return proc.stdout.readline().strip()


def run_cpp_sign_line(unsigned_line: str, key: str = SHARED_KEY) -> str:
    """Invokes host_cpp_signer --sign-line via streaming pipe."""
    return _CppWorker.sign(unsigned_line, key)


def run_cpp_verify(signed_line: str, key: str = SHARED_KEY) -> str:
    """Invokes host_cpp_signer --verify via streaming pipe."""
    return _CppWorker.verify(signed_line, key)


# Safe string strategy for packet text fields (avoiding newlines or raw control chars)
safe_text_strat = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "P", "Zs"), max_codepoint=127),
    min_size=0,
    max_size=32,
).filter(lambda s: "\n" not in s and "\r" not in s)


# ==============================================================================
# 1. PROTOCOL HYPOTHESIS PROPERTY TESTS (Python <-> C++ Bit-Identical Parity)
# ==============================================================================

class TestProtocolHypothesisProperties:

    @settings(max_examples=30, deadline=None)
    @given(
        nonce=st.integers(min_value=0, max_value=2**32 - 1),
        seq=st.integers(min_value=1, max_value=2**31 - 1),
        uptime_s=st.integers(min_value=0, max_value=10_000_000),
        res_wet=st.booleans(),
        ads_ok=st.booleans(),
        p0=st.integers(min_value=0, max_value=1),
        p1=st.integers(min_value=0, max_value=1),
        p2=st.integers(min_value=0, max_value=1),
        p3=st.integers(min_value=0, max_value=1),
        reset_reason=st.integers(min_value=0, max_value=10),
        rssi=st.integers(min_value=-120, max_value=0),
    )
    def test_heartbeat_roundtrip_parity(
        self, nonce, seq, uptime_s, res_wet, ads_ok, p0, p1, p2, p3, reset_reason, rssi
    ):
        pumps = [p0, p1, p2, p3]
        unsigned_py = format_hb_unsigned(nonce, seq, uptime_s, res_wet, ads_ok, pumps, reset_reason, rssi)
        signed_py = sign_packet_string(unsigned_py, SHARED_KEY).strip()

        # 1. Byte-identical signature generation by C++
        signed_cpp = run_cpp_sign_line(unsigned_py)
        assert signed_py == signed_cpp, "C++ and Python generated different signed packets!"

        # 2. C++ verifies Python signed packet
        cpp_verify_res = run_cpp_verify(signed_py)
        expected_sig = compute_sig16(unsigned_py, SHARED_KEY)
        assert cpp_verify_res == f"OK {expected_sig}"

        # 3. Python parses C++ signed packet and validates fields
        tracker = SequenceTracker()
        pkt, err = parse_and_validate_packet(signed_cpp, SHARED_KEY, tracker)
        assert err is None
        assert isinstance(pkt, HeartbeatPacket)
        assert pkt.nonce == nonce
        assert pkt.seq == seq
        assert pkt.uptime_s == uptime_s
        assert pkt.res_wet == res_wet
        assert pkt.ads_ok == ads_ok
        assert pkt.pumps == pumps
        assert pkt.reset_reason == reset_reason
        assert pkt.rssi == rssi

    @settings(max_examples=30, deadline=None)
    @given(
        nonce=st.integers(min_value=0, max_value=2**32 - 1),
        seq=st.integers(min_value=1, max_value=2**31 - 1),
        z=st.integers(min_value=1, max_value=4),
        volts=st.floats(min_value=0.0, max_value=3.3, allow_nan=False, allow_infinity=False),
    )
    def test_soil_roundtrip_parity(self, nonce, seq, z, volts):
        volts = round(volts, 3)
        unsigned_py = format_soil_unsigned(nonce, seq, z, volts)
        signed_py = sign_packet_string(unsigned_py, SHARED_KEY).strip()

        # C++ signs unsigned line -> must be identical
        signed_cpp = run_cpp_sign_line(unsigned_py)
        assert signed_py == signed_cpp

        # C++ verifies Python signed packet
        assert run_cpp_verify(signed_py) == f"OK {compute_sig16(unsigned_py, SHARED_KEY)}"

        # Python validates packet
        tracker = SequenceTracker()
        pkt, err = parse_and_validate_packet(signed_cpp, SHARED_KEY, tracker)
        assert err is None
        assert isinstance(pkt, SoilPacket)
        assert pkt.z == z
        assert abs(pkt.volts - volts) < 0.001

    @settings(max_examples=30, deadline=None)
    @given(
        nonce=st.integers(min_value=0, max_value=2**32 - 1),
        seq=st.integers(min_value=1, max_value=2**31 - 1),
        z=st.integers(min_value=1, max_value=4),
        delta_pulses=st.integers(min_value=0, max_value=10000),
        period_ms=st.integers(min_value=100, max_value=5000),
    )
    def test_flow_roundtrip_parity(self, nonce, seq, z, delta_pulses, period_ms):
        unsigned_py = format_flow_unsigned(nonce, seq, z, delta_pulses, period_ms)
        signed_py = sign_packet_string(unsigned_py, SHARED_KEY).strip()

        signed_cpp = run_cpp_sign_line(unsigned_py)
        assert signed_py == signed_cpp

        assert run_cpp_verify(signed_py) == f"OK {compute_sig16(unsigned_py, SHARED_KEY)}"

        tracker = SequenceTracker()
        pkt, err = parse_and_validate_packet(signed_cpp, SHARED_KEY, tracker)
        assert err is None
        assert isinstance(pkt, FlowPacket)
        assert pkt.delta_pulses == delta_pulses
        assert pkt.period_ms == period_ms

    @settings(max_examples=30, deadline=None)
    @given(
        nonce=st.integers(min_value=0, max_value=2**32 - 1),
        seq=st.integers(min_value=1, max_value=2**31 - 1),
        z=st.integers(min_value=1, max_value=4),
        on=st.booleans(),
        reason=safe_text_strat,
        run_s=st.integers(min_value=0, max_value=60),
    )
    def test_pump_event_roundtrip_parity(self, nonce, seq, z, on, reason, run_s):
        unsigned_py = format_pump_unsigned(nonce, seq, z, on, reason, run_s)
        signed_py = sign_packet_string(unsigned_py, SHARED_KEY).strip()

        signed_cpp = run_cpp_sign_line(unsigned_py)
        assert signed_py == signed_cpp

        assert run_cpp_verify(signed_py) == f"OK {compute_sig16(unsigned_py, SHARED_KEY)}"

        tracker = SequenceTracker()
        pkt, err = parse_and_validate_packet(signed_cpp, SHARED_KEY, tracker)
        assert err is None
        assert isinstance(pkt, PumpEventPacket)
        assert pkt.z == z
        assert pkt.on == on
        assert pkt.reason == reason
        assert pkt.run_s == run_s

    @settings(max_examples=30, deadline=None)
    @given(
        nonce=st.integers(min_value=0, max_value=2**32 - 1),
        seq=st.integers(min_value=1, max_value=2**31 - 1),
        cmd_id=safe_text_strat.filter(lambda s: len(s) > 0),
        status=st.sampled_from(["ok", "err", "rejected"]),
        err_msg=safe_text_strat,
    )
    def test_ack_roundtrip_parity(self, nonce, seq, cmd_id, status, err_msg):
        unsigned_py = format_ack_unsigned(nonce, seq, cmd_id, status, err_msg)
        signed_py = sign_packet_string(unsigned_py, SHARED_KEY).strip()

        signed_cpp = run_cpp_sign_line(unsigned_py)
        assert signed_py == signed_cpp

        assert run_cpp_verify(signed_py) == f"OK {compute_sig16(unsigned_py, SHARED_KEY)}"

        tracker = SequenceTracker()
        pkt, err = parse_and_validate_packet(signed_cpp, SHARED_KEY, tracker)
        assert err is None
        assert isinstance(pkt, AckPacket)
        assert pkt.id == cmd_id
        assert pkt.status == status
        assert pkt.err == err_msg


# ==============================================================================
# 2. INTERLOCK STATE MACHINE HYPOTHESIS PROPERTY TESTS
# ==============================================================================

class TestInterlockStateMachineProperties:

    @settings(max_examples=20)
    @given(
        raw_val=st.sampled_from([0, 1]),
        active_low=st.booleans(),
    )
    def test_property_decode_logic_inversion(self, raw_val: int, active_low: bool):
        """
        Property: _decode(raw, active_low) is guaranteed to invert when active_low=True
        and preserve when active_low=False.
        """
        decoded = _decode(raw_val, active_low)
        if active_low:
            assert decoded == (raw_val == 0)
        else:
            assert decoded == (raw_val == 1)

    def test_property_decode_none_is_none(self):
        assert _decode(None, True) is None
        assert _decode(None, False) is None

    @pytest.mark.asyncio
    @settings(max_examples=40, deadline=None)
    @given(
        out_state=st.booleans(),
        mid_state=st.booleans(),
    )
    async def test_property_interlock_truth_table_cause(self, out_state: bool, mid_state: bool):
        """
        Property: With no latched fault, interlock cause must strictly follow:
          (True, True)   -> healthy
          (False, False) -> tank_full
          (False, True)  -> dry_sump
          (True, False)  -> wiring_fault
        """
        state_manager.reset_wiring_fault()
        await state_manager.update_head_end(
            tank_low=False,
            interlock_out=out_state,
            interlock_mid=mid_state,
            leak_detected=False,
            hardware_available=True,
        )
        cause = state_manager.get_state()["head_end"]["interlock_cause"]

        if out_state is True and mid_state is True:
            assert cause == "healthy"
        elif out_state is False and mid_state is False:
            assert cause == "tank_full"
        elif out_state is False and mid_state is True:
            assert cause == "dry_sump"
        elif out_state is True and mid_state is False:
            assert cause == "wiring_fault"

    @pytest.mark.asyncio
    @settings(max_examples=30, deadline=None)
    @given(
        history=st.lists(
            st.tuples(st.booleans(), st.booleans()),
            min_size=3,
            max_size=15,
        )
    )
    async def test_property_wiring_fault_latching_invariance(self, history):
        """
        Property: Once an invalid state (out=True, mid=False) is encountered anywhere
        in the sequence of readings, the state machine MUST latch into wiring_fault,
        and never return to healthy/tank_full/dry_sump until reset_wiring_fault() is called.
        """
        state_manager.reset_wiring_fault()
        fault_ever_seen = False

        for out_val, mid_val in history:
            if out_val is True and mid_val is False:
                fault_ever_seen = True

            await state_manager.update_head_end(
                tank_low=False,
                interlock_out=out_val,
                interlock_mid=mid_val,
                leak_detected=False,
                hardware_available=True,
            )
            state = state_manager.get_state()["head_end"]

            if fault_ever_seen:
                assert state["wiring_fault_latched"] is True
                assert state["interlock_cause"] == "wiring_fault"

        # Explicit reset must clear the latched fault
        if fault_ever_seen:
            state_manager.reset_wiring_fault()
            assert state_manager.get_state()["head_end"]["wiring_fault_latched"] is False

    @settings(max_examples=40)
    @given(
        initial_val=st.booleans(),
        debounce_ms=st.integers(min_value=50, max_value=500),
        poll_interval_ms=st.integers(min_value=20, max_value=100),
    )
    def test_property_software_debouncer_glitch_rejection(
        self, initial_val: bool, debounce_ms: int, poll_interval_ms: int
    ):
        """
        Property: SoftwareDebouncer MUST NEVER change debounced state on a single
        transient glitch sample. It requires N consecutive opposite samples to flip.
        """
        debouncer = SoftwareDebouncer(debounce_ms=debounce_ms, poll_interval_ms=poll_interval_ms)
        required_n = debouncer.get_required_samples()
        assert required_n >= 2

        # 1. Initialize channel
        val = debouncer.update("test_channel", initial_val)
        assert val == initial_val

        # 2. Feed (N - 1) glitch samples of the opposite value
        opposite_val = not initial_val
        for _ in range(required_n - 1):
            debounced = debouncer.update("test_channel", opposite_val)
            # Must remain at initial_val! Glitch rejected.
            assert debounced == initial_val

        # 3. Restore to initial_val: glitch canceled
        debounced = debouncer.update("test_channel", initial_val)
        assert debounced == initial_val

        # 4. Now feed required_n consecutive opposite samples: must transition
        for step in range(1, required_n + 1):
            debounced = debouncer.update("test_channel", opposite_val)
            if step < required_n:
                assert debounced == initial_val
            else:
                assert debounced == opposite_val
