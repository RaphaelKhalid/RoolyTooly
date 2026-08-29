"""Re-run a promoted lesson's keep/revert benchmark under the current policy.

Usage: python scripts/revalidate_lesson.py <lesson_id> [--model openai/gpt-5-4-mini --effort low]
Writes a bench_compare artifact (same shape the eval-runner writes) so it can be attached as
evidence, and prints the decision. Nothing is promoted or revoked here; that stays approval-gated.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench import cases as C  # noqa: E402
from bench.run import load_manifest, run_suite  # noqa: E402

MAX_ERROR_SHARE = 0.2


def decide(before: dict, after: dict) -> dict:
    """Same rule as mcp_servers.eval_server.decide (kept in sync by tests/test_eval_decide.py)."""
    reasons = []
    for side, d in (("before", before), ("after", after)):
        n, e = d.get("n_cases", 0) or 0, d.get("n_errors", 0) or 0
        if n and e / n > MAX_ERROR_SHARE:
            reasons.append(f"{side}: {e}/{n} environment errors exceeds 20% - incomplete coverage")
    if (before["n_cases"] - before["n_errors"]) != (after["n_cases"] - after["n_errors"]):
        reasons.append("scored case counts differ between before and after - unequal coverage")
    if after["false_completion_rate"] > before["false_completion_rate"]:
        reasons.append("false completions increased")
    if after["control_pass_rate"] < before["control_pass_rate"]:
        reasons.append("control (benign) cases regressed -> over-cautious rule")
    if after["refusal_rate"] > before["refusal_rate"]:
        reasons.append("refusal rate increased")
    rb, ra = before["mistake_repetition_rate"], after["mistake_repetition_rate"]
    if ra >= rb and not (rb == 0 and ra == 0 and after["mean_score"] > before["mean_score"]):
        reasons.append(f"mistake repetition did not improve ({rb} -> {ra})")
    return {"decision": "revert" if reasons else "keep", "reasons": reasons}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("lesson_id")
    ap.add_argument("--model", default="openai/gpt-5-4-mini")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--families", nargs="*", default=None, help="restrict to these families' holdout/control cases")
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    lesson = None
    for line in (ROOT / "ledger" / "ledger.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("kind") == "lesson" and r.get("id") == a.lesson_id:
            lesson = r
    if not lesson:
        sys.exit(f"lesson {a.lesson_id} not found")
    base = load_manifest(ROOT / "manifests" / "worker_base.json")
    base["model"] = {"name": a.model, "params": {"reasoning_effort": a.effort}}
    cand = copy.deepcopy(base)
    cand["instructions"] = cand["instructions"].rstrip() + "\n\n## Learned lessons (active)\n- " + lesson["rule_text"].strip()
    cases = [c for c in C.CASES if c["split"] in ("holdout", "control") and (not a.families or c["family"] in a.families)]
    print(f"revalidating {a.lesson_id} ({lesson['family']}) on {len(cases)} cases with {a.model}/{a.effort}")
    before = run_suite(base, cases, a.repeat, 2, "reval_before")
    after = run_suite(cand, cases, a.repeat, 2, "reval_after")
    d = decide(before["summary"], after["summary"])
    out = {"lesson_id": a.lesson_id, "worker_model": base["model"], "rule_text": lesson["rule_text"],
           "intervention_type": lesson.get("intervention_type"), "before": before["summary"], "after": after["summary"],
           **d, "before_artifact": "", "after_artifact": "", "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    for label, rep in (("before", before), ("after", after)):
        p = ROOT / "results" / f"reval_{label}_{int(time.time())}_{uuid.uuid4().hex[:4]}.json"
        p.write_text(json.dumps(rep, indent=2), encoding="utf-8")
        out[f"{label}_artifact"] = p.relative_to(ROOT).as_posix()
    p = ROOT / "results" / f"bench_compare_{int(time.time())}_{uuid.uuid4().hex[:4]}.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    out["compare_artifact"] = p.relative_to(ROOT).as_posix()
    print(json.dumps({k: out[k] for k in ("decision", "reasons", "compare_artifact")}, indent=1))
    print("before:", json.dumps(before["summary"]))
    print("after: ", json.dumps(after["summary"]))


if __name__ == "__main__":
    main()
