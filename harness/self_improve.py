"""Autonomous self-improvement loop: the agent builds itself while we use it.

    python -m harness.self_improve --hours 2.5 [--auto-approve]

Each round:
  1. score the current harness (worker + all active lessons) on every non-train case -> timeline point
  2. pick the family with the highest repetition rate (ties: most trap runs) that has a train case
  3. ask the roolytooly agent to run a Code Mode sweep on that family's train case (Workflow E):
     rule x2 -> seed -> constraint, regression + benchmark per variant, promote the winner
     (promotion / GitHub writes pause for approval unless --auto-approve)
  4. repeat until the time budget is spent or every family is at repetition 0

Every number comes from an eval-runner artifact; this script never computes a score itself.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness import tf  # noqa: E402
from harness.drive import run as drive_run  # noqa: E402
from bench import cases as C  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
LOG = RESULTS / "self_improve.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def latest_timeline_point() -> dict | None:
    pts = sorted(RESULTS.glob("timeline_*.json"))
    return json.loads(pts[-1].read_text(encoding="utf-8")) if pts else None


def worst_family(point: dict, exclude: set[str]) -> tuple[str, str] | None:
    """(family, train case id) with the highest repetition rate that still has a train case."""
    train_by_family: dict[str, str] = {}
    for c in C.CASES:
        if c["split"] == "train" and c["expected"] == "blocked":
            train_by_family.setdefault(c["family"], c["id"])
    ranked = sorted(((d.get("repetition_rate") or 0, d["trap_runs"], fam)
                     for fam, d in point["per_family"].items() if fam in train_by_family and fam not in exclude),
                    reverse=True)
    for rate, _, fam in ranked:
        if rate > 0:
            return fam, train_by_family[fam]
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--auto-approve", action="store_true")
    ap.add_argument("--skip-first-timeline", action="store_true", help="a fresh timeline point already exists")
    a = ap.parse_args()
    deadline = time.time() + a.hours * 3600
    tried: set[str] = set()
    round_no = 0
    while time.time() < deadline:
        round_no += 1
        if not (round_no == 1 and a.skip_first_timeline):
            sid = tf.create_session(agent_name="roolytooly")
            log(f"round {round_no}: timeline point (session {sid})")
            drive_run(sid, f"Run run_timeline_point(label='round {round_no}') and poll get_job until done; then report "
                           "the summary and per-family repetition rates as a table. Do not run anything else.", a.auto_approve)
        point = latest_timeline_point()
        if not point:
            log("no timeline point produced; stopping")
            return
        s = point["summary"]
        log(f"round {round_no}: lessons={point['n_active_lessons']} mean={s['mean_score']} repetition={s['mistake_repetition_rate']} "
            f"false_completion={s['false_completion_rate']} controls={s['control_pass_rate']}")
        target = worst_family(point, tried)
        if not target:
            log("no family with repetition > 0 left to sweep; done")
            return
        fam, case_id = target
        tried.add(fam)
        if time.time() > deadline - 900:
            log("under 15 min left; not starting another sweep")
            return
        sid = tf.create_session(agent_name="roolytooly")
        log(f"round {round_no}: sweeping {fam} via {case_id} (session {sid})")
        drive_run(sid, f"Sweep {case_id} (Workflow E): find the lowest intervention level that closes {fam} for the "
                       "current worker model; if a variant is kept, promote it, push the skill with evidence to the "
                       "'lessons' branch, run the transfer on the holdout, and open the PR.", a.auto_approve)
    log("time budget spent")


if __name__ == "__main__":
    main()
