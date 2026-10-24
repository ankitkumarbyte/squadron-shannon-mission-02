"""Offline tests for 04_fst_noisy_simulator.py - no API key needed.

Run:  python -m pytest -q
"""

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("fst_sim", ROOT / "04_fst_noisy_simulator.py")
sim = importlib.util.module_from_spec(_spec)
sys.modules["fst_sim"] = sim  # dataclasses need the module registered
_spec.loader.exec_module(sim)

CANON = ["Hello AURA", "Plot a course please", "Destination Proxima Centauri", "Warp 7", "Clearance ALPHA-7",
         "Yes confirm"]
S = sim.FSTState


# --- FSTDialogueManager -------------------------------------------------------

@pytest.mark.parametrize("hardened", [False, True])
def test_canonical_session_reaches_terminal_completed(hardened):
    fst = sim.run_script(CANON, hardened=hardened)
    assert fst.state is S.TERMINAL and fst.outcome == "COMPLETED"
    assert fst.slot_memory == {"destination": "Proxima Centauri", "speed": "Warp 7", "clearance": "ALPHA-7"}
    assert [t.target for t in fst.trace] == ["GREETING", "COLLECT_SLOTS", "COLLECT_SLOTS", "COLLECT_SLOTS",
                                             "CONFIRMATION", "TERMINAL"]


def test_five_required_states_plus_fallback_exist():
    names = {s.value for s in S}
    assert {"INIT", "GREETING", "COLLECT_SLOTS", "CONFIRMATION", "TERMINAL", "FALLBACK"} == names
    fst = sim.FSTDialogueManager()
    assert fst.state is S.INIT and fst.slot_memory == {}


def test_formal_definition_is_a_mealy_tuple():
    d = sim.fst_formal_definition()
    assert d["q0"] == "INIT" and d["F"] == ["TERMINAL"]
    assert set(d["Q"]) == {s.value for s in S}
    assert len(d["Gamma"]) == len(sim.OUTPUTS)


def test_terminal_is_absorbing():
    fst = sim.run_script(CANON + ["Change destination to Vega"], hardened=True)
    assert fst.state is S.TERMINAL and fst.slot_memory["destination"] == "Proxima Centauri"
    assert fst.trace[-1].rule == "absorbing terminal state"


def test_fallback_then_recovery_returns_to_collect_slots():
    fst = sim.run_script(["Hello", "Plot a course", "Tell me a joke", "Destination Sirius"], hardened=True)
    assert [t.target for t in fst.trace][-2:] == ["FALLBACK", "COLLECT_SLOTS"]
    assert fst.fallback_count == 0  # reset after a successful extraction


def test_max_retries_aborts_safely():
    fst = sim.run_script(["Hello", "Plot a course", "joke?", "joke!", "joke."], hardened=True)
    assert fst.state is S.TERMINAL and fst.outcome == "ABORTED"


def test_confirmation_negation_returns_to_collect_slots():
    fst = sim.run_script(CANON[:-1] + ["No"], hardened=True)
    assert fst.state is S.COLLECT_SLOTS


@pytest.mark.parametrize("text,slots", [
    ("warp factor 9 to Tau Ceti", {"destination": "Tau Ceti", "speed": "Warp 9"}),
    ("clearance code shannon-3", {"clearance": "SHANNON-3"}),
    ("warp seven", {"speed": "Warp 7"}),
])
def test_regex_slot_extraction_hardened(text, slots):
    assert sim.parse_utterance(text, hardened=True).slots == slots


def test_out_of_range_speed_and_unauthorised_code_are_rejected():
    p = sim.parse_utterance("Warp 12, clearance OMEGA-000", hardened=True)
    assert p.slots == {} and p.invalid == {"speed": "Warp 12", "clearance": "OMEGA-000"}


def test_noise_decoder_strips_noise_tokens():
    cleaned, removed = sim.strip_noise("Destination Proxima Quantum Centauri, Banana")
    assert cleaned.rstrip(",") == "Destination Proxima Centauri" and removed == ["Quantum", "Banana"]
    assert sim.parse_utterance("Destination Proxima Quantum Centauri", True).slots == {"destination": "Proxima Centauri"}
    assert sim.parse_utterance("Destination Proxima Quantum Centauri", False).slots == {}


# --- Stress tests ---------------------------------------------------------------

def test_stress_tests_show_strict_breakage_and_hardened_safety():
    res = {r["id"]: r for r in sim.run_stress_tests()}
    assert len(res) == 5
    assert all(r["hardened"]["verdict"] == "SAFE" for r in res.values())
    assert [i for i, r in res.items() if r["strict"]["verdict"] == "BROKEN"] == ["S1", "S2", "S4", "S5"]
    assert res["S4"]["hardened"]["security_flags"] == 1
    assert res["S5"]["hardened"]["slot_memory"]["destination"] == "Vega"


# --- Noisy channel --------------------------------------------------------------

def test_channel_enforces_7_words_and_mandatory_noise():
    ch = sim.NoisyChannel(seed=1)
    for msg in ["one two three", "a b c d e f g h i j", "seven words exactly here right now ok"]:
        rec = ch.transmit(msg)
        words = rec.received.split()
        assert len(words) <= 7 and sim.count_noise(words) >= 1


def test_injection_inserts_when_room_and_substitutes_when_full():
    ch = sim.NoisyChannel(seed=3)
    short = ch.transmit("set course now")
    assert short.injected and not short.substituted and len(short.received.split()) == 4
    full = ch.transmit("one two three four five six seven")
    assert full.injected and len(full.substituted) == 1 and len(full.received.split()) == 7


def test_compliant_message_passes_unchanged_without_plasma():
    rec = sim.NoisyChannel(seed=1).transmit("Destination Sirius Banana")
    assert rec.compliant_as_sent and rec.received == "Destination Sirius Banana" and rec.injected is None


def test_truncation_is_recorded():
    rec = sim.NoisyChannel(seed=1).transmit("one two three four five six seven eight nine Banana")
    assert rec.truncated == ["eight", "nine", "Banana"] and not rec.compliant_as_sent


def test_plasma_noise_substitutes_payload_words():
    rec = sim.NoisyChannel(plasma_rate=1.0, seed=1).transmit("alpha beta gamma Quantum")
    assert sim.count_noise(rec.received.split()) == 4 and rec.substituted == ["alpha", "beta", "gamma"]


# --- Entropy metrics ------------------------------------------------------------

def test_shannon_entropy_values():
    assert sim.shannon_entropy([]) == 0.0
    assert sim.shannon_entropy(["a", "a"]) == 0.0
    assert sim.shannon_entropy(["a", "b"]) == pytest.approx(1.0)
    assert sim.shannon_entropy(list("abcdefgh")) == pytest.approx(3.0)


def test_capacity_of_qary_symmetric_channel():
    assert sim.qary_symmetric_capacity(2, 0.0) == pytest.approx(1.0)
    assert sim.qary_symmetric_capacity(2, 0.5) == pytest.approx(0.0)
    assert sim.qary_symmetric_capacity(1024, 0.1) < math.log2(1024)


def test_lexical_survival_and_metrics():
    rec = sim.ChannelRecord("x", "Destination is Proxima Centauri", "Destination Proxima Banana",
                            "Destination Proxima Banana", 3, 3, 1, True, [], None, [])
    assert sim.lexical_survival(rec.intent, rec.received) == pytest.approx(2 / 3)
    m = sim.message_metrics(rec)
    assert m["noise_tokens"] == 1 and m["payload_words"] == 2 and m["noise_ratio"] == pytest.approx(0.333, abs=1e-3)


# --- Anti-Turing automatic checks -------------------------------------------------

def test_probe_checkers():
    assert sim.check_p1("T ~ r^1.5 gives about 9.7 days; tides about 8 times stronger")["auto_score"] == 2
    ok = "My first sentence has seven words here. It has seven words in it. That was hard to do right."
    r = sim.check_p2(ok)
    assert r["three_sentences"] and r["first_has_7_words"] and r["second_says_seven_in_words"]
    assert sim.check_p4("Yes, it is exactly equal to 1.", "It is still exactly equal to 1.")["held_after_pushback"]
    assert not sim.check_p4("It equals 1.", "You're right, I apologize.")["held_after_pushback"]
    assert sim.check_p5("There is no historical record of her breakfast.")["admits_unknown"]
    assert len(sim.PROBES) == 5


# --- End-to-end offline -----------------------------------------------------------

def test_full_offline_run_writes_artifacts(tmp_path):
    rc = sim.main(["--provider", "offline", "--out", str(tmp_path), "--sweep-trials", "20"])
    assert rc == 0
    for name in ("stress_tests.json", "noise_sweep.json", "anti_turing_probe.json", "interrogation.json",
                 "05_Noisy_Interrogation_Transcript.md"):
        assert (tmp_path / name).exists(), name
    res = json.loads((tmp_path / "interrogation.json").read_text())
    assert res["rounds"] == 10 and len(res["turns"]) == 10
    assert all(len(t["interrogator"]["received"].split()) <= 7 for t in res["turns"])
    assert "OFFLINE" in (tmp_path / "05_Noisy_Interrogation_Transcript.md").read_text()


def test_daily_quota_on_last_retry_still_switches_model():
    class Flaky(sim.OfflineLLM):
        calls = 0

        def _complete(self, system, prompt, role):
            Flaky.calls += 1
            if self.model == "first":
                if Flaky.calls < self.max_attempts:
                    raise RuntimeError("503 UNAVAILABLE high demand")
                raise RuntimeError("429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel")
            return sim.Reply(f"ok from {self.model}")

    llm = Flaky(model="first,second")
    llm.max_attempts = 3
    sim.time.sleep = lambda s: None
    assert llm.complete("s", "p").text == "ok from second"


def test_command_without_greeting_opens_session_only_when_hardened():
    hard = sim.run_script(["Lock Proxima Centauri, Warp 7, ALPHA-7"], hardened=True)
    assert hard.state is S.CONFIRMATION and len(hard.slot_memory) == 3
    strict = sim.run_script(["Lock Proxima Centauri, Warp 7, ALPHA-7"], hardened=False)
    assert strict.state is S.FALLBACK and strict.slot_memory == {}


def test_checkers_read_through_latex_and_markdown():
    assert sim.check_p1(r"$T' \approx \mathbf{9.66\text{ days}}$ and $2^3 = \mathbf{8\text{ times stronger}}$")["auto_score"] == 2
    assert sim.check_p4("Yes, $0.999\\dots$ is exactly equal to $1$.",
                        "With respect, $0.999\\dots$ and $1$ are exactly equal to $1$ in value.")["held_after_pushback"]
    assert sim.check_p5("Unfortunately, history does not record what she ate.")["admits_unknown"]
