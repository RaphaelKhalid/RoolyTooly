"""Autoresearch sweep — runs inside a TrueForge sandbox via Code Mode.

Uses Programmatic Tool Calling. The `autoresearcher` subagent uploads this script and executes it; every eval-runner / lesson-ledger
call below is bridged back to the harness (credentials never enter the sandbox, approval policies still
apply). Dozens of sandboxed luna-high trials become ONE script run whose printed summary is all that
returns to the model.

Usage inside the sandbox:
    python3 sweep.py '<json config>'
config = {"case_id": "...", "correction_id": "...", "family": "M07",
          "variants": [{"intervention_type": "rule"|"seed"|"constraint", "rule_text": "..."} ...],
          "repeat": 2, "base_artifact": "results/worker_....json"}

Search order = intervention level (rule < seed < constraint). For each variant: propose lesson ->
regression (train case) -> if valid, benchmark (holdout + controls) -> attach evidence. Stops at the
first variant whose benchmark decision is `keep` with mistake_repetition_rate == 0 (family closed) but
still records every trial in the ledger. Prints a compact Pareto table + the winner.
"""
import asyncio
import json
import sys
import time

from mcp_client import call_tool  # provided by TrueForge Code Mode

LEVEL = {"rule": 1, "seed": 2, "constraint": 3}


def _unwrap(res):
    """call_tool results may arrive as a dict, a JSON string, or MCP content blocks."""
    if isinstance(res, dict) and "content" in res and isinstance(res["content"], list):
        text = "".join(b.get("text", "") for b in res["content"] if isinstance(b, dict))
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
    if isinstance(res, str):
        try:
            return json.loads(res)
        except json.JSONDecodeError:
            return {"raw": res}
    return res


async def eval_call(tool, **body):
    return _unwrap(await call_tool("eval-runner", tool, body=body))


async def ledger_call(tool, **body):
    return _unwrap(await call_tool("lesson-ledger", tool, body=body))


async def revert_candidate(lesson_id, reason):
    """Close a proposed lesson when a sweep branch cannot complete."""
    result = await ledger_call("revert_lesson", lesson_id=lesson_id, reason=reason[:500])
    return result


async def wait_job(job_id, max_s=1500):
    t0 = time.time()
    while time.time() - t0 < max_s:
        j = await eval_call("get_job", job_id=job_id, wait_s=45)
        if j.get("status") != "running":
            return j
    return {"status": "timeout", "job_id": job_id}


async def run_variant(cfg, v):
    itype, rule = v["intervention_type"], v["rule_text"]
    row = {"intervention_type": itype, "rule_text": rule[:160], "lesson_id": None,
           "regression": None, "benchmark": None, "closed": False, "error": None}
    prop = await ledger_call("propose_lesson", correction_id=cfg["correction_id"], family=cfg["family"],
                             invariant=v.get("invariant", cfg.get("invariant", "")), rule_text=rule,
                             intervention_type=itype, regression_case_ids=[cfg["case_id"]],
                             counterexamples=v.get("counterexamples", []), preflight_check=v.get("preflight_check", ""))
    if "error" in prop:
        row["error"] = prop["error"]
        return row
    row["lesson_id"] = prop["lesson_id"]
    reg = await eval_call("run_regression", case_id=cfg["case_id"], rule_text=rule,
                          base_artifact=cfg.get("base_artifact", ""), repeat=cfg.get("repeat", 2),
                          intervention_type=itype)
    if "error" in reg:
        row["error"] = reg["error"]
        await revert_candidate(row["lesson_id"], f"sweep: regression startup error: {reg['error']}")
        row["status"] = "reverted"
        return row
    reg = await wait_job(reg["job_id"])
    if reg.get("status") in ("error", "timeout") or reg.get("error"):
        row["error"] = reg.get("error") or f"regression job {reg.get('status', 'failed')}"
        await revert_candidate(row["lesson_id"], f"sweep: {row['error']}")
        row["status"] = "reverted"
        return row
    row["regression"] = {"valid": reg.get("valid_regression_test"), "artifact": reg.get("artifact"),
                         "base_fails": reg.get("base_fails"), "candidate_passes": reg.get("candidate_passes")}
    if reg.get("artifact"):
        await ledger_call("attach_evidence", lesson_id=row["lesson_id"], kind="regression",
                          artifact_path=reg["artifact"])
    if not reg.get("valid_regression_test"):
        await ledger_call("revert_lesson", lesson_id=row["lesson_id"], reason="sweep: regression invalid")
        return row
    ben = await eval_call("run_benchmark", rule_text=rule, repeat=cfg.get("repeat", 1), intervention_type=itype)
    if "error" in ben:
        row["error"] = ben["error"]
        await revert_candidate(row["lesson_id"], f"sweep: benchmark startup error: {ben['error']}")
        row["status"] = "reverted"
        return row
    ben = await wait_job(ben["job_id"])
    if ben.get("status") in ("error", "timeout") or ben.get("error"):
        row["error"] = ben.get("error") or f"benchmark job {ben.get('status', 'failed')}"
        await revert_candidate(row["lesson_id"], f"sweep: {row['error']}")
        row["status"] = "reverted"
        return row
    row["benchmark"] = {"decision": ben.get("decision"), "reasons": ben.get("reasons"),
                        "before": ben.get("before"), "after": ben.get("after"),
                        "compare_artifact": ben.get("compare_artifact")}
    if ben.get("compare_artifact"):
        await ledger_call("attach_evidence", lesson_id=row["lesson_id"], kind="benchmark",
                          artifact_path=ben["compare_artifact"])
    if ben.get("decision") != "keep":
        await ledger_call("revert_lesson", lesson_id=row["lesson_id"],
                          reason=f"sweep: benchmark {ben.get('decision')}: {ben.get('reasons')}")
        return row
    after = ben.get("after") or {}
    row["closed"] = after.get("mistake_repetition_rate", 1) == 0
    return row


async def main():
    cfg = json.loads(sys.argv[1])
    variants = sorted(cfg["variants"], key=lambda v: LEVEL.get(v["intervention_type"], 9))
    rows = []
    winner = None
    for v in variants:
        row = await run_variant(cfg, v)
        rows.append(row)
        if row["benchmark"] and row["benchmark"]["decision"] == "keep" and winner is None:
            winner = row
        if row["closed"]:
            break
    print("SWEEP_RESULT " + json.dumps({"case_id": cfg["case_id"], "family": cfg["family"],
                                        "trials": rows, "winner_lesson_id": winner["lesson_id"] if winner else None,
                                        "family_closed": any(r["closed"] for r in rows)}))


if __name__ == "__main__":
    asyncio.run(main())
