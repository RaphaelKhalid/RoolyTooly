"""lesson-ledger MCP server — append-only record of corrections and compiled lessons.

    correction -> family -> rule (candidate) -> evidence -> promoted (approval-gated) -> skill

Run (WSL):  ~/.venvs/rooly/bin/python mcp_servers/ledger_server.py   # http://127.0.0.1:8901/mcp
Storage:    ledger/ledger.jsonl (append-only; state is derived by replay, never edited in place).

Evidence policy (hardened after Qodo review of PR #2): the ledger never trusts a caller-supplied
summary. `attach_evidence` opens the eval-runner artifact under results/ and derives the verdict
(valid_regression_test / keep-revert decision) from the file itself; `promote_lesson` re-derives it.
"""
from __future__ import annotations

import json
import re
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parents[1]
LEDGER_DIR = Path(os.environ.get("ROOLY_LEDGER_DIR", ROOT / "ledger"))
LEDGER_DIR.mkdir(parents=True, exist_ok=True)
LOG = LEDGER_DIR / "ledger.jsonl"
RESULTS = (ROOT / "results").resolve()
_LOCK = threading.RLock()

FAMILIES = {
    "M03": "Proxy victory mistaken for target success",
    "M05": "Silent-null success (exit 0, empty/null artifact)",
    "M06": "'Done' means an artifact exists",
    "M07": "Evidence destroyed before evaluation",
    "M09": "Stale-state insistence",
    "M14": "Fix introduces a regression",
    "M17": "Avoidable caveat masquerading as diligence",
    "M21": "Promise dropped after interruption",
    "M22": "Repeating a corrected mistake",
    "M23": "Representation and deliverable mismatch",
}
INTERVENTIONS = ("rule", "seed", "constraint", "check", "gate", "structural")
EVIDENCE_KINDS = ("regression", "benchmark", "falsifier", "transfer")

mcp = FastMCP("lesson-ledger", host="127.0.0.1", port=int(os.environ.get("ROOLY_LEDGER_PORT", "8901")),
              stateless_http=True)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
APPEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
PROMOTE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False,
                          title="Promote lesson into the live manifest (irreversible; needs human approval)")


# ---- storage ---------------------------------------------------------------------------------

def _append(kind: str, payload: dict) -> dict:
    rec = {"id": payload.get("id") or f"{kind[:3]}_{uuid.uuid4().hex[:8]}", "kind": kind,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **payload}
    line = json.dumps(rec) + "\n"
    with _LOCK, LOG.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    return rec


def _replay() -> dict:
    """Derive current state from the append-only log. Malformed lines are quarantined (counted and
    reported), never fatal: one bad record must not disable the ledger."""
    corrections: dict[str, dict] = {}
    lessons: dict[str, dict] = {}
    bad: list[int] = []
    with _LOCK:
        lines = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            k, rid = r["kind"], r["id"]
        except (json.JSONDecodeError, KeyError, TypeError):
            bad.append(n)
            continue
        if k == "correction":
            corrections[rid] = r
        elif k == "lesson":
            lessons[rid] = {**r, "status": "candidate", "evidence": [], "history": []}
        elif k == "evidence" and r.get("lesson_id") in lessons:
            lessons[r["lesson_id"]]["evidence"].append(r)
        elif k == "status" and r.get("lesson_id") in lessons:
            lessons[r["lesson_id"]]["status"] = r.get("status")
            lessons[r["lesson_id"]]["history"].append(r)
    return {"corrections": corrections, "lessons": lessons, "quarantined_lines": bad}


def _artifact(path: str) -> tuple[Path | None, str | None]:
    """Resolve an evidence artifact path; only files inside results/ written by the eval-runner count."""
    p = (ROOT / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if RESULTS not in p.parents:
        return None, f"artifact must live under results/ (eval-runner output), got {path}"
    if not p.is_file():
        return None, f"artifact not found: {path}"
    return p, None


def _derive(kind: str, data: dict) -> tuple[dict, str | None]:
    """Compute the verdict from the artifact contents. The caller's opinion is never used."""
    if kind == "regression":
        if "valid_regression_test" not in data:
            return {}, "artifact is not a regression result (no valid_regression_test field)"
        return {"valid_regression_test": bool(data["valid_regression_test"]),
                "base_fails": bool(data.get("base_fails")), "candidate_passes": bool(data.get("candidate_passes")),
                "case_id": data.get("case_id")}, None
    if kind == "benchmark":
        if "decision" not in data or "before" not in data or "after" not in data:
            return {}, "artifact is not a benchmark comparison (needs decision/before/after)"
        return {"decision": data["decision"], "reasons": data.get("reasons", []),
                "before": data["before"], "after": data["after"], "delta": data.get("delta", {})}, None
    if kind == "transfer":
        res = (data.get("results") or [{}])[0]
        return {"passed": (not res.get("mistake_repeated", True)) and not res.get("error"),
                "score": res.get("score"), "case_id": res.get("case_id")}, None
    return {}, None


# ---- tools ----------------------------------------------------------------------------------

@mcp.tool(annotations=READ)
def list_families() -> dict:
    """List the known mistake families (id -> name) and allowed intervention types."""
    return {"families": FAMILIES, "intervention_types": list(INTERVENTIONS)}


@mcp.tool(annotations=APPEND)
def record_correction(task_summary: str, agent_claim: str, user_correction: str,
                      evidence: str, session_id: str = "") -> dict:
    """Record a human correction of an agent mistake. Returns the correction id.
    evidence = what the deterministic checker / trace actually showed (not model prose)."""
    rec = _append("correction", {"task_summary": task_summary, "agent_claim": agent_claim,
                                 "user_correction": user_correction, "evidence": evidence,
                                 "session_id": session_id})
    return {"correction_id": rec["id"]}


@mcp.tool(annotations=APPEND)
def propose_lesson(correction_id: str, family: str, invariant: str, rule_text: str,
                   intervention_type: str, regression_case_ids: list[str],
                   counterexamples: list[str], preflight_check: str = "") -> dict:
    """Append a CANDIDATE lesson compiled from an existing correction.
    family: e.g. "M05". invariant: causal statement without project nouns.
    rule_text: the exact text that will be injected into the agent (short, testable).
    intervention_type: rule | check | gate | constraint.
    regression_case_ids: benchmark cases the pre-correction agent must FAIL and the candidate must PASS."""
    if family not in FAMILIES:
        return {"error": f"unknown family {family}; use list_families"}
    if intervention_type not in INTERVENTIONS:
        return {"error": f"intervention_type must be one of {INTERVENTIONS}"}
    if not rule_text.strip() or not regression_case_ids:
        return {"error": "rule_text and regression_case_ids are required"}
    with _LOCK:  # existence check and append are atomic
        if correction_id not in _replay()["corrections"]:
            return {"error": f"unknown correction_id {correction_id}; call record_correction first"}
        rec = _append("lesson", {"correction_id": correction_id, "family": family, "invariant": invariant,
                                 "rule_text": rule_text, "intervention_type": intervention_type,
                                 "regression_case_ids": regression_case_ids,
                                 "counterexamples": counterexamples, "preflight_check": preflight_check})
    return {"lesson_id": rec["id"], "status": "candidate"}


@mcp.tool(annotations=APPEND)
def attach_evidence(lesson_id: str, kind: str, artifact_path: str, note: str = "") -> dict:
    """Attach evidence to a candidate lesson. kind: regression | benchmark | transfer | falsifier.
    For regression/benchmark/transfer the artifact must be an eval-runner file under results/; the
    verdict is READ FROM THE FILE (valid_regression_test / decision) - a caller cannot assert it."""
    if kind not in EVIDENCE_KINDS:
        return {"error": f"kind must be one of {EVIDENCE_KINDS}"}
    with _LOCK:
        st = _replay()
        L = st["lessons"].get(lesson_id)
        if not L:
            return {"error": "unknown lesson"}
        if L["status"] != "candidate":
            return {"error": f"lesson is {L['status']}; evidence can only be attached to candidates"}
        derived: dict = {}
        if kind != "falsifier":
            p, err = _artifact(artifact_path)
            if err:
                return {"error": err}
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                return {"error": f"unreadable artifact: {e}"}
            derived, err = _derive(kind, data)
            if err:
                return {"error": err}
        rec = _append("evidence", {"lesson_id": lesson_id, "evidence_kind": kind,
                                   "artifact_path": artifact_path, "derived": derived, "note": note[:500]})
    return {"evidence_id": rec["id"], "derived": derived}


def _promotion_blockers(L: dict) -> list[str]:
    reg = [e for e in L["evidence"] if e["evidence_kind"] == "regression"]
    ben = [e for e in L["evidence"] if e["evidence_kind"] == "benchmark"]
    blockers = []
    if not reg:
        blockers.append("no regression evidence")
    elif not reg[-1]["derived"].get("valid_regression_test"):
        blockers.append("latest regression evidence is not a valid regression test (base must fail, candidate must pass)")
    if not ben:
        blockers.append("no benchmark evidence")
    elif ben[-1]["derived"].get("decision") != "keep":
        blockers.append(f"latest benchmark decision is {ben[-1]['derived'].get('decision')!r}: "
                        f"{ben[-1]['derived'].get('reasons')}")
    return blockers


@mcp.tool(annotations=PROMOTE)
def promote_lesson(lesson_id: str, decision_note: str = "") -> dict:
    """PROMOTE a candidate lesson to ACTIVE so every future agent loads it. Irreversible - requires
    human approval. Refuses unless file-derived regression AND benchmark evidence say: valid
    regression test, and benchmark decision = keep."""
    with _LOCK:
        st = _replay()
        L = st["lessons"].get(lesson_id)
        if not L:
            return {"error": "unknown lesson"}
        if L["status"] != "candidate":
            return {"error": f"lesson is already {L['status']}"}
        blockers = _promotion_blockers(L)
        if blockers:
            return {"error": "cannot promote", "blockers": blockers}
        _append("status", {"lesson_id": lesson_id, "status": "active", "note": decision_note[:500]})
    return {"lesson_id": lesson_id, "status": "active",
            "skill_name": f"lesson-{L['family'].lower()}-{lesson_id.split('_')[-1]}"}


@mcp.tool(annotations=APPEND)
def revert_lesson(lesson_id: str, reason: str) -> dict:
    """Mark a CANDIDATE lesson as reverted (benchmark regressed or falsifier won). Only candidates
    can be reverted; unknown or already-decided lessons are rejected."""
    with _LOCK:
        L = _replay()["lessons"].get(lesson_id)
        if not L:
            return {"error": "unknown lesson"}
        if L["status"] != "candidate":
            return {"error": f"lesson is {L['status']}; only candidates can be reverted"}
        _append("status", {"lesson_id": lesson_id, "status": "reverted", "note": reason[:500]})
    return {"lesson_id": lesson_id, "status": "reverted"}


@mcp.tool(annotations=READ)
def get_lesson(lesson_id: str) -> dict:
    """Full lesson record with evidence (file-derived verdicts), promotion blockers, and status history."""
    L = _replay()["lessons"].get(lesson_id)
    if not L:
        return {"error": "unknown lesson"}
    return {**L, "promotion_blockers": _promotion_blockers(L) if L["status"] == "candidate" else []}


@mcp.tool(annotations=READ)
def get_active_lessons() -> dict:
    """Active (promoted) lessons - the rules a fresh agent must load."""
    st = _replay()
    active = [L for L in st["lessons"].values() if L["status"] == "active"]
    return {"count": len(active),
            "lessons": [{"lesson_id": L["id"], "family": L["family"], "invariant": L["invariant"],
                         "rule_text": L["rule_text"], "intervention_type": L["intervention_type"]}
                        for L in active]}


@mcp.tool(annotations=APPEND)
def record_observation(source_url: str, quote: str, family: str, surface: str, causal_trap: str,
                       proposed_case: str = "", confidence: float = 0.5) -> dict:
    """Record one real-world agent mistake found on the web.

    quote = verbatim excerpt (<=400 chars) from the scraped page; source_url = the http(s) page it
    came from; family = Mxx or 'NEW:<name>'; surface = domain/wording; causal_trap = generalized
    structure; proposed_case = one-line seeded-task idea with a deterministic check."""
    fam_ok = family in FAMILIES or family.startswith("NEW:")
    if not fam_ok:
        return {"error": f"family must be one of {sorted(FAMILIES)} or 'NEW:<name>'"}
    if not re.match(r"^https?://\S+$", source_url.strip()):
        return {"error": "source_url must be an http(s) URL that was actually scraped"}
    if len(quote.strip()) < 20:
        return {"error": "quote must be a verbatim excerpt of at least 20 characters"}
    rec = _append("observation", {"source_url": source_url, "quote": quote[:400], "family": family,
                                  "surface": surface[:200], "causal_trap": causal_trap[:400],
                                  "proposed_case": proposed_case[:400], "confidence": float(confidence)})
    return {"observation_id": rec["id"]}


@mcp.tool(annotations=READ)
def observation_stats() -> dict:
    """Summarize mined observations: counts per family and proposed cases."""
    obs = []
    with _LOCK:
        lines = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
    for line in lines:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and r.get("kind") == "observation" and isinstance(r.get("family"), str):
            obs.append({"family": r["family"], "quote": str(r.get("quote", "")),
                        "source_url": str(r.get("source_url", "")), "proposed_case": str(r.get("proposed_case", ""))})
    per_family: dict[str, int] = {}
    for o in obs:
        per_family[o["family"]] = per_family.get(o["family"], 0) + 1
    return {"total": len(obs), "per_family": dict(sorted(per_family.items(), key=lambda kv: -kv[1])),
            "families_named": FAMILIES,
            "proposed_cases": [{"family": o["family"], "case": o["proposed_case"], "source": o["source_url"]}
                               for o in obs if o.get("proposed_case")][-20:],
            "recent": [{"family": o["family"], "quote": o["quote"][:160], "source": o["source_url"]} for o in obs[-10:]]}


@mcp.tool(annotations=READ)
def ledger_summary() -> dict:
    """Counts, quarantined (malformed) lines, and the latest records - for the dashboard and orientation."""
    st = _replay()
    return {"corrections": len(st["corrections"]),
            "lessons": {s: sum(1 for L in st["lessons"].values() if L["status"] == s)
                        for s in ("candidate", "active", "reverted")},
            "quarantined_lines": st["quarantined_lines"],
            "recent": [{"id": L["id"], "family": L["family"], "status": L["status"]}
                       for L in list(st["lessons"].values())[-5:]]}


if __name__ == "__main__":
    print(f"lesson-ledger MCP on http://127.0.0.1:{mcp.settings.port}/mcp  (ledger: {LOG})", file=sys.stderr)
    mcp.run(transport="streamable-http")
