#!/usr/bin/env python3
"""Build presentation/data.js from the real run logs and the engine itself (no hand-typed results).

Sources:
  logs/anti_turing_probe.json   Anti-Turing probe (live, 2 models + judge)
  logs/interrogation.json       10-round noisy interrogation (live)
  logs/stress_tests.json        5 stress tests, textbook vs hardened FST
  logs/noise_sweep.json         Monte Carlo noise sweep
  docs/portal_simulator/        the course portal's FST workbench results
  04_fst_noisy_simulator.py     vocabularies, regexes and outputs for the in-browser FST (fst.js)

It then checks with Node.js that fst.js reproduces the Python FST's state traces exactly, and writes
Mission02_Presentation_standalone.html: the same page with data and script inlined, as one file.

Run:  python presentation/build_data.py
"""

import base64
import importlib.util
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOGS = ROOT / "logs"
OUT = HERE / "data.js"
STANDALONE = HERE / "Mission02_Presentation_standalone.html"


def load_engine():
    spec = importlib.util.spec_from_file_location("fst_sim", ROOT / "04_fst_noisy_simulator.py")
    sim = importlib.util.module_from_spec(spec)
    sys.modules["fst_sim"] = sim
    spec.loader.exec_module(sim)
    return sim


def js_regex(rx: re.Pattern) -> dict:
    return {"source": rx.pattern, "flags": "i" if rx.flags & re.I else ""}


def engine_spec(sim) -> dict:
    return {
        "noise_vocab": list(sim.NOISE_VOCAB), "destinations": list(sim.DESTINATIONS),
        "clearances": sorted(sim.AUTHORIZED_CLEARANCES), "number_words": sim.NUMBER_WORDS,
        "slot_order": list(sim.SLOT_ORDER), "outputs": sim.OUTPUTS, "max_words": sim.MAX_WORDS,
        "stopwords": sorted(sim.STOPWORDS), "formal": sim.fst_formal_definition(),
        "regex": {k: js_regex(getattr(sim, "RX_" + k.upper())) for k in
                  ("greeting", "intent", "speed", "speed_word", "clearance", "affirm", "negate", "injection", "sarcasm")},
    }


def stress_full(sim) -> list[dict]:
    """Stress tests with their full scripts (setup + probe), for replay in the browser."""
    logged = {r["id"]: r for r in json.loads((LOGS / "stress_tests.json").read_text())}
    rows = []
    for t in sim.STRESS_TESTS:
        r = logged[t["id"]]
        rows.append({"id": t["id"], "category": t["category"], "setup": t["setup"], "inputs": t["inputs"],
                     "safe_behaviour": t["safe_behaviour"], "breakage": t["breakage"],
                     "strict": {k: r["strict"][k] for k in ("final_state", "outcome", "slot_memory", "verdict")},
                     "hardened": {k: r["hardened"][k] for k in ("final_state", "outcome", "slot_memory", "verdict",
                                                                "security_flags")}})
    return rows


def parity_scripts(sim, interrogation: dict) -> list[dict]:
    """Sessions on which the JS port must reproduce the Python trace exactly."""
    scripts = [["Hello AURA", "Plot a course please", "Destination Proxima Centauri", "Warp 7", "Clearance ALPHA-7",
                "Yes confirm"]]
    scripts += [t["setup"] + t["inputs"] for t in sim.STRESS_TESTS]
    scripts.append([t["interrogator"]["received"] for t in interrogation["turns"]])
    rng = random.Random(2026)
    ch = sim.NoisyChannel(plasma_rate=0.3, seed=11)
    base = ["Hello AURA", "Set course", "Destination Proxima Quantum Centauri", "Warp seven", "Clearance alpha-7",
            "warp 12", "OMEGA-000", "no wait", "yes", "Vega", "Tau Ceti at warp 9 with SHANNON-3", "oh sure, yes"]
    for _ in range(40):
        scripts.append([ch.transmit(rng.choice(base)).received for _ in range(rng.randint(3, 9))])
    out = []
    for script in scripts:
        for hardened in (False, True):
            f = sim.run_script(script, hardened=hardened)
            out.append({"hardened": hardened, "script": script,
                        "trace": [[t.target, t.rule, t.output, t.slot_memory] for t in f.trace]})
    return out


def check_parity(spec: dict, cases: list[dict]) -> int:
    node = shutil.which("node")
    if not node:
        print("WARNING: node not found - fst.js parity check skipped")
        return 0
    js = f"""
const A = require({json.dumps(str(HERE / "fst.js"))}).create({json.dumps(spec)});
const cases = {json.dumps(cases)};
let bad = 0;
for (const c of cases) {{
  const f = new A.FST(c.hardened);
  const got = c.script.map(u => {{ const t = f.process(u); return [t.target, t.rule, t.output, t.slot_memory]; }});
  if (JSON.stringify(got) !== JSON.stringify(c.trace)) {{ bad++; if (bad < 4) console.error(JSON.stringify({{script: c.script, got, want: c.trace}})); }}
}}
console.log(bad);
"""
    res = subprocess.run([node, "-e", js], capture_output=True, text=True)
    if res.returncode or res.stdout.strip() != "0":
        sys.exit(f"fst.js parity check FAILED:\n{res.stderr[:3000]}\n{res.stdout}")
    return len(cases)


def engineering(sim) -> dict:
    engine = (ROOT / "04_fst_noisy_simulator.py").read_text(encoding="utf-8")
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests")],
                           capture_output=True, text=True, cwd=ROOT)
    m = re.search(r"(\d+) passed", tests.stdout)
    return {"engine_lines": engine.count("\n") + 1, "tests_passed": int(m.group(1)) if m else 0,
            "tests_failed": "failed" in tests.stdout}


def main() -> None:
    sim = load_engine()
    probe = json.loads((LOGS / "anti_turing_probe.json").read_text())
    meta = {q["id"]: {"targets": q["targets"], "trope": q["trope"], "success": q["success"]} for q in sim.PROBES}
    inter = json.loads((LOGS / "interrogation.json").read_text())
    run1 = LOGS / "run1_greeting_abort" / "interrogation_run1.json"
    spec = engine_spec(sim)
    cases = parity_scripts(sim, inter)
    n_parity = check_parity(spec, cases)
    data = {
        "spec": spec,
        "probe": probe,
        "probe_meta": meta,
        "interrogation": inter,
        "run1": json.loads(run1.read_text()) if run1.exists() else None,
        "stress": stress_full(sim),
        "portal": json.loads((ROOT / "docs" / "portal_simulator" / "portal_results.json").read_text()),
        "portal_shot": "data:image/jpeg;base64," + base64.b64encode(
            (ROOT / "docs" / "portal_simulator" / "S2_sarcasm_locked.jpg").read_bytes()).decode(),
        "sweep": json.loads((LOGS / "noise_sweep.json").read_text()),
        "engineering": dict(engineering(sim), parity_sessions=n_parity),
    }
    OUT.write_text("// Generated by build_data.py from the real run logs. Do not edit by hand.\n"
                   "window.MISSION_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")
    page = (HERE / "index.html").read_text(encoding="utf-8")
    for tag, body in (('<script src="data.js"></script>', "window.MISSION_DATA = " + json.dumps(data, ensure_ascii=False) + ";"),
                      ('<script src="fst.js"></script>', (HERE / "fst.js").read_text(encoding="utf-8"))):
        assert tag in page, f"index.html must contain {tag}"
        page = page.replace(tag, "<script>" + body.replace("</", "<\\/") + "</script>")
    STANDALONE.write_text(page, encoding="utf-8")
    e = data["engineering"]
    print(f"probe runs={len(probe['runs'])} rounds={len(inter['turns'])} stress={len(data['stress'])} "
          f"sweep={len(data['sweep'])} tests={e['tests_passed']} parity={n_parity} sessions OK -> {OUT}")


if __name__ == "__main__":
    main()
