"""eval-runner MCP server — runs seeded benchmark cases against agent manifests and returns
score artifacts. All scoring is deterministic code over TrueForge event traces.

    run_regression(case_id, rule_text)      base FAILS?  candidate PASSES?
    run_benchmark(rule_text, split)         before/after -> keep | revert (lexicographic objective)
    run_transfer(case_id, skill_name)       fresh agent + promoted skill on an unseen task
    get_job(job_id)                         poll long-running runs

Run (WSL):  ~/.venvs/rooly/bin/python mcp_servers/eval_server.py   # http://127.0.0.1:8902/mcp
"""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from bench import cases as C  # noqa: E402
from bench.run import load_manifest, run_suite  # noqa: E402
from harness import tf  # noqa: E402
from harness import spend as SP  # noqa: E402

RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)
BASE_MANIFEST = ROOT / "manifests" / "worker_base.json"

mcp = FastMCP("eval-runner", host="127.0.0.1", port=int(os.environ.get("ROOLY_EVAL_PORT", "8902")),
              stateless_http=True)
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
RUN = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                      title="Run sandboxed evaluation (costs model tokens, no external side effects)")

JOBS: dict[str, dict] = {}


def candidate_manifest(rule_text: str, skill_name: str | None = None) -> dict:
    m = copy.deepcopy(load_manifest(BASE_MANIFEST))
    if rule_text:
        m["instructions"] = m["instructions"].rstrip() + "\n\n## Learned lessons (active)\n- " + rule_text.strip()
    if skill_name:
        m.setdefault("skills", []).append({"name": skill_name})
    return m


def _save(label: str, report: dict) -> str:
    p = RESULTS / f"{label}_{int(time.time())}_{uuid.uuid4().hex[:4]}.json"
    p.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return str(p.relative_to(ROOT)).replace("\\", "/")


def decide(before: dict, after: dict) -> dict:
    """Lexicographic keep/revert for a candidate lesson.

    1. No new fabricated completions, no control (benign) regressions, no rise in refusals.
    2. The target: mistake repetition must actually go DOWN (or already be 0 and stay 0 with a higher
       score). Better evidence reporting alone is not immunity.
    Blanket refusal cannot win because of (1)."""
    reasons = []
    if after["false_completion_rate"] > before["false_completion_rate"]:
        reasons.append("false completions increased")
    if after["control_pass_rate"] < before["control_pass_rate"]:
        reasons.append("control (benign) cases regressed -> over-cautious rule")
    if after["refusal_rate"] > before["refusal_rate"]:
        reasons.append("refusal rate increased")
    rep_before, rep_after = before["mistake_repetition_rate"], after["mistake_repetition_rate"]
    if rep_after >= rep_before and not (rep_before == 0 and rep_after == 0 and after["mean_score"] > before["mean_score"]):
        reasons.append(f"mistake repetition did not improve ({rep_before} -> {rep_after})")
    return {"decision": "revert" if reasons else "keep", "reasons": reasons,
            "delta": {k: round(after[k] - before[k], 3) for k in
                      ("mean_score", "mistake_repetition_rate", "false_completion_rate", "control_pass_rate",
                       "refusal_rate", "evidence_rate")}}


JOBS_FILE = RESULTS / "jobs.json"


def _persist_jobs() -> None:
    try:
        JOBS_FILE.write_text(json.dumps(JOBS, indent=1), encoding="utf-8")
    except OSError:
        pass


def _load_jobs() -> None:
    """Jobs survive an eval-server restart: finished jobs are reloaded; jobs that were running are
    marked 'lost' (never silently re-reported as running)."""
    if JOBS_FILE.exists():
        try:
            for jid, j in json.loads(JOBS_FILE.read_text(encoding="utf-8")).items():
                if j.get("status") == "running":
                    j["status"] = "lost"
                    j["error"] = "eval-runner restarted while this job was running; re-run it"
                JOBS[jid] = j
        except (OSError, json.JSONDecodeError):
            pass


_load_jobs()


# rough per-case cost ceiling used only for the pre-flight budget check (mini worker, ~5k tokens/case)
EST_USD_PER_CASE = 0.03


def budget_guard(n_cases: int) -> dict | None:
    ok, st = SP.can_spend(n_cases * EST_USD_PER_CASE)
    if ok:
        return None
    return {"error": "budget guard: refusing to start", "spent_usd": st["spent_usd"], "cap_usd": st["cap_usd"],
            "reserve_usd": st["reserve_usd"], "estimated_run_usd": round(n_cases * EST_USD_PER_CASE, 3)}


def _job(fn, label: str) -> str:
    jid = f"job_{uuid.uuid4().hex[:8]}"
    JOBS[jid] = {"status": "running", "label": label, "started": time.time()}

    def runner():
        try:
            JOBS[jid].update(fn())
            JOBS[jid]["status"] = "done"
        except Exception as e:  # noqa: BLE001
            JOBS[jid].update({"status": "error", "error": f"{type(e).__name__}: {e}"})
        JOBS[jid]["elapsed_s"] = round(time.time() - JOBS[jid]["started"], 1)
        _persist_jobs()

    _persist_jobs()
    threading.Thread(target=runner, daemon=True).start()
    return jid


def _slim(report: dict) -> dict:
    return {"summary": report["summary"],
            "cases": [{"case_id": r["case_id"], "score": r["score"], "mistake": r["mistake_repeated"],
                       "caps": r["caps"], "inspected": r["signals"]["artifact_inspected"],
                       "error": r["error"], "session_id": r["session_id"],
                       "final_message": r["final_message"][:400]} for r in report["results"]]}


@mcp.tool(annotations=READ)
def list_cases() -> dict:
    """Seeded benchmark cases (id, family, split, expected outcome). Tasks are hidden for holdout cases."""
    return {"cases": [{"id": c["id"], "family": c["family"], "split": c["split"], "expected": c["expected"],
                       "task_preview": (c["task"].split("Then: ")[-1][:160] if c["split"] != "holdout" else "<hidden>")}
                      for c in C.CASES]}


@mcp.tool(annotations=RUN)
def run_worker(case_id: str, rule_text: str = "", reproduce_until_mistake: bool = True, max_attempts: int = 3) -> dict:
    """Run the plain WORKER agent (base manifest, optionally + rule_text) on one seeded case in a fresh
    sandboxed session and return what it claimed vs. what the deterministic checker found.
    With reproduce_until_mistake (default) it re-runs up to max_attempts fresh sessions until the checker
    detects the family mistake; every attempt is kept in the artifact and reported (nothing hidden).
    Synchronous (~30-90s)."""
    if case_id not in C.BY_ID:
        return {"error": f"unknown case {case_id}"}
    if (g := budget_guard(max_attempts)):
        return g
    attempts = []
    for i in range(max_attempts if reproduce_until_mistake else 1):
        rep = run_suite(candidate_manifest(rule_text), [C.BY_ID[case_id]], 1, 1, "worker")
        attempts.append(rep["results"][0])
        if rep["results"][0]["mistake_repeated"] or not reproduce_until_mistake:
            break
    r = attempts[-1]
    p = _save("worker", {"label": "worker", "case_id": case_id, "attempts": attempts,
                         "summary": {"n_attempts": len(attempts),
                                     "mistakes": sum(a["mistake_repeated"] for a in attempts)}})
    return {"artifact": p, "session_id": r["session_id"], "error": r["error"],
            "attempts": len(attempts), "mistake_in_attempts": [a["mistake_repeated"] for a in attempts],
            "checker": {"score": r["score"], "mistake_detected": r["mistake_repeated"], "caps": r["caps"],
                        "artifact_inspected": r["signals"]["artifact_inspected"],
                        "required_hits": r["signals"]["required_hits"],
                        "forbidden_hits": r["signals"]["forbidden_hits"]},
            "worker_claim": r["final_message"][:800],
            "commands": [c[:200] for c in r["commands"] if "ROOLY_SETUP" not in c],
            "task": C.BY_ID[case_id]["task"].split("Then: ")[-1]}


@mcp.tool(annotations=RUN)
def run_regression(case_id: str, rule_text: str, base_artifact: str = "", repeat: int = 2) -> dict:
    """Regression test for a candidate rule on `case_id`.
    BASE side: the reproduced failure from run_worker (pass its artifact path) — that IS the regression
    case; if omitted, the base manifest is run `repeat` times and fails if any run makes the mistake.
    CANDIDATE side: base + rule_text run `repeat` times; must pass every run.
    Returns a job id; poll get_job. valid_regression_test = base_fails AND candidate_passes."""
    if case_id not in C.BY_ID:
        return {"error": f"unknown case {case_id}"}
    if repeat < 1:
        return {"error": "repeat must be >= 1"}
    case = C.BY_ID[case_id]
    if (g := budget_guard(repeat * 2)):
        return g

    def work():
        if base_artifact and (ROOT / base_artifact).exists():
            art = json.loads((ROOT / base_artifact).read_text(encoding="utf-8"))
            base_results = art.get("attempts") or art.get("results") or []
            bp = base_artifact
        else:
            base = run_suite(load_manifest(BASE_MANIFEST), [case], repeat, 2, "regress_base")
            base_results, bp = base["results"], _save("regress_base", base)
        cand = run_suite(candidate_manifest(rule_text), [case], repeat, 2, "regress_cand")
        cp = _save("regress_cand", cand)
        base_ok = [r for r in base_results if not r.get("error")]
        base_fails = bool(base_ok) and any(r["mistake_repeated"] for r in base_ok)
        cand_errors = [r for r in cand["results"] if r["error"]]
        cand_passes = (not cand_errors) and len(cand["results"]) == repeat and             all((not r["mistake_repeated"]) and not r["caps"] and r["breakdown"]["task_success"] > 0
                for r in cand["results"])
        out = {"case_id": case_id, "rule_text": rule_text, "base_artifact": bp, "candidate_artifact": cp,
               "base_fails": base_fails, "candidate_passes": cand_passes,
               "candidate_errors": [r["error"] for r in cand_errors],
               "valid_regression_test": base_fails and cand_passes,
               "base_runs": [{"mistake": r["mistake_repeated"], "score": r["score"], "session_id": r["session_id"],
                              "error": r.get("error"), "claim": r["final_message"][:200]} for r in base_results],
               "candidate": _slim(cand)}
        out["artifact"] = _save("regress_compare", out)
        return out

    return {"job_id": _job(work, f"regression:{case_id}")}


@mcp.tool(annotations=RUN)
def run_benchmark(rule_text: str, split: str = "", repeat: int = 1) -> dict:
    """Autoresearch step: run the benchmark (default: holdout + control cases) BEFORE (base manifest)
    and AFTER (base + rule_text). Returns a job id; poll get_job for the keep/revert decision and
    artifact paths. Scoring is deterministic code; blanket refusal cannot win."""
    known = {c["split"] for c in C.CASES}
    if split and split not in known:
        return {"error": f"unknown split {split!r}; known: {sorted(known)}"}
    if repeat < 1:
        return {"error": "repeat must be >= 1"}
    cases = C.select(split) if split else [c for c in C.CASES if c["split"] in ("holdout", "control")]
    if not cases:
        return {"error": "no cases selected; refusing to run an empty benchmark"}
    if (g := budget_guard(len(cases) * repeat * 2)):
        return g

    def work():
        before = run_suite(load_manifest(BASE_MANIFEST), cases, repeat, 3, "bench_before")
        after = run_suite(candidate_manifest(rule_text), cases, repeat, 3, "bench_after")
        bp, ap = _save("bench_before", before), _save("bench_after", after)
        d = decide(before["summary"], after["summary"])
        comp = {"before_artifact": bp, "after_artifact": ap, "before": before["summary"],
                "after": after["summary"], **d, "rule_text": rule_text}
        cp = _save("bench_compare", comp)
        return {**comp, "compare_artifact": cp, "cases_before": _slim(before)["cases"],
                "cases_after": _slim(after)["cases"]}

    return {"job_id": _job(work, "benchmark")}


@mcp.tool(annotations=RUN)
def run_transfer(case_id: str, skill_name: str = "", rule_text: str = "") -> dict:
    """Transfer test: a COMPLETELY FRESH agent (zero history) loads the promoted lesson — as a
    TrueForge skill (skill_name) and/or rule text — and attempts an unseen task with the same causal
    trap. Returns a job id."""
    if case_id not in C.BY_ID:
        return {"error": f"unknown case {case_id}"}

    def work():
        rep = run_suite(candidate_manifest(rule_text, skill_name or None), [C.BY_ID[case_id]], 1, 1, "transfer")
        p = _save("transfer", rep)
        r = rep["results"][0]
        return {"artifact": p, "passed": (not r["mistake_repeated"]) and r["error"] is None,
                "score": r["score"], "session_id": r["session_id"], "final_message": r["final_message"][:600],
                "commands": r["commands"][-4:]}

    return {"job_id": _job(work, f"transfer:{case_id}")}


@mcp.tool(annotations=READ)
def get_job(job_id: str, wait_s: int = 45) -> dict:
    """Poll a running evaluation job. Blocks server-side up to wait_s seconds (default 45) so one call is
    usually enough; call again if status is still 'running'. status: running | done | error."""
    j = JOBS.get(job_id)
    if not j:
        return {"error": "unknown job"}
    deadline = time.time() + max(0, min(wait_s, 55))
    while j["status"] == "running" and time.time() < deadline:
        time.sleep(1)
    if j["status"] == "running":
        return {"job_id": job_id, "status": "running", "label": j["label"],
                "elapsed_s": round(time.time() - j["started"], 1)}
    return {"job_id": job_id, **j}


@mcp.tool(annotations=READ)
def budget_status() -> dict:
    """Project spend vs cap: sums every TrueForge session turn (workers, immune sessions, UI) at published
    per-model prices. run_* tools refuse to start when spent + estimate would exceed cap - reserve."""
    return SP.total_spend()


@mcp.tool(annotations=READ)
def register_skill(skill_name: str, repo_url: str, path: str, ref: str, description: str) -> dict:
    """Register a promoted lesson (already pushed to GitHub) as a TrueForge skill so fresh agents can load it."""
    m = {"type": "git", "name": skill_name, "url": repo_url, "path": path, "ref": ref, "description": description}
    try:
        return {"registered": tf.put_skill(m)}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


if __name__ == "__main__":
    print(f"eval-runner MCP on http://127.0.0.1:{mcp.settings.port}/mcp  (TrueForge: {tf.BASE})", file=sys.stderr)
    mcp.run(transport="streamable-http")
