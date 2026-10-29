"""
benchmarks/check_drift.py — fail when any benchmark case changes outcome.

    python benchmarks/check_drift.py            # compare with baseline.json
    python benchmarks/check_drift.py --update   # accept the current outcomes

A changed outcome is not automatically wrong — it may be an intended
improvement — but it must be a deliberate, reviewed change: update the
baseline in the same pull request and say why. The cases are synthetic;
this guards behavior, not real-world accuracy.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
BASELINE = HERE / "baseline.json"


def current() -> dict:
    import run_benchmark
    out = run_benchmark.run()
    return {"metrics": {k: out["metrics"][k] for k in ("cases", "tp", "fp", "tn", "fn")},
            "outcomes": {c["id"]: c["outcome"] for c in out["cases"]}}


def diff(base: dict, now: dict) -> list[str]:
    b, n = base["outcomes"], now["outcomes"]
    out = [f"{cid}: {b.get(cid, 'absent')} -> {n.get(cid, 'absent')}"
           for cid in sorted(set(b) | set(n)) if b.get(cid) != n.get(cid)]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args(argv)
    now = current()
    if a.update:
        BASELINE.write_text(json.dumps(now, indent=2, sort_keys=True) + "\n")
        print(f"baseline updated: {now['metrics']}")
        return 0
    base = json.loads(BASELINE.read_text())
    changes = diff(base, now)
    print(f"baseline {base['metrics']}\ncurrent  {now['metrics']}")
    if changes:
        print("OUTCOME DRIFT (update benchmarks/baseline.json deliberately if intended):")
        for c in changes:
            print("  " + c)
        return 1
    print("no drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
