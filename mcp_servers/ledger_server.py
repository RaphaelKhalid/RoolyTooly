"""lesson-ledger MCP server — append-only record of corrections and compiled lessons.

    correction -> family -> rule (candidate) -> evidence -> promoted (approval-gated) -> skill

Run (WSL):  ~/.venvs/rooly/bin/python mcp_servers/ledger_server.py   # http://127.0.0.1:8901/mcp
Storage:    ledger/*.jsonl (append-only; state is derived by replay, never edited in place).
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parents[1]
LEDGER_DIR = Path(os.environ.get("ROOLY_LEDGER_DIR", ROOT / "ledger"))
LEDGER_DIR.mkdir(parents=True, exist_ok=True)
LOG = LEDGER_DIR / "ledger.jsonl"

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
INTERVENTIONS = ("rule", "check", "gate", "constraint")

mcp = FastMCP("lesson-ledger", host="127.0.0.1", port=int(os.environ.get("ROOLY_LEDGER_PORT", "8901")),
              stateless_http=True)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
APPEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
PROMOTE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False,
                          title="Promote lesson into the live manifest (irreversible; needs human approval)")


def _append(kind: str, payload: dict) -> dict:
    rec = {"id": payload.get("id") or f"{kind[:3]}_{uuid.uuid4().hex[:8]}", "kind": kind,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **payload}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def _replay() -> dict:
    """Derive current state from the append-only log."""
    corrections: dict[str, dict] = {}
    lessons: dict[str, dict] = {}
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            k = r["kind"]
            if k == "correction":
                corrections[r["id"]] = r
            elif k == "lesson":
                lessons[r["id"]] = {**r, "status": "candidate", "evidence": [], "history": []}
            elif k == "evidence" and r["lesson_id"] in lessons:
                lessons[r["lesson_id"]]["evidence"].append(r)
            elif k == "status" and r["lesson_id"] in lessons:
                lessons[r["lesson_id"]]["status"] = r["status"]
                lessons[r["lesson_id"]]["history"].append(r)
    return {"corrections": corrections, "lessons": lessons}


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
    """Append a CANDIDATE lesson compiled from a correction.
    family: e.g. "M05". invariant: causal statement without project nouns.
    rule_text: the exact text that will be injected into the agent (must be short and testable).
    intervention_type: rule | check | gate | constraint.
    regression_case_ids: benchmark cases the pre-correction agent must FAIL and the candidate must PASS."""
    if family not in FAMILIES:
        return {"error": f"unknown family {family}; use list_families"}
    if intervention_type not in INTERVENTIONS:
        return {"error": f"intervention_type must be one of {INTERVENTIONS}"}
    rec = _append("lesson", {"correction_id": correction_id, "family": family, "invariant": invariant,
                             "rule_text": rule_text, "intervention_type": intervention_type,
                             "regression_case_ids": regression_case_ids,
                             "counterexamples": counterexamples, "preflight_check": preflight_check})
    return {"lesson_id": rec["id"], "status": "candidate"}


@mcp.tool(annotations=APPEND)
def attach_evidence(lesson_id: str, kind: str, artifact_path: str, summary: dict) -> dict:
    """Attach benchmark/regression/falsifier evidence to a lesson. kind: regression | benchmark | falsifier.
    artifact_path must point at a real score artifact written by the eval-runner (code, not prose)."""
    st = _replay()
    if lesson_id not in st["lessons"]:
        return {"error": "unknown lesson"}
    p = Path(artifact_path)
    if kind in ("regression", "benchmark") and not (p.is_absolute() and p.exists() or (ROOT / p).exists()):
        return {"error": f"artifact not found: {artifact_path} — evidence must be a real file"}
    rec = _append("evidence", {"lesson_id": lesson_id, "evidence_kind": kind,
                               "artifact_path": artifact_path, "summary": summary})
    return {"evidence_id": rec["id"]}


@mcp.tool(annotations=PROMOTE)
def promote_lesson(lesson_id: str, decision_note: str = "") -> dict:
    """PROMOTE a candidate lesson to ACTIVE so every future agent loads it. Irreversible — requires
    human approval. Refuses unless regression + benchmark evidence is attached and the benchmark
    did not regress."""
    st = _replay()
    L = st["lessons"].get(lesson_id)
    if not L:
        return {"error": "unknown lesson"}
    kinds = {e["evidence_kind"] for e in L["evidence"]}
    if not {"regression", "benchmark"} <= kinds:
        return {"error": f"cannot promote: evidence kinds present={sorted(kinds)}, need regression+benchmark"}
    bench = [e for e in L["evidence"] if e["evidence_kind"] == "benchmark"][-1]["summary"]
    if bench.get("decision") not in ("keep", "KEEP"):
        return {"error": f"cannot promote: benchmark decision was {bench.get('decision')!r}"}
    _append("status", {"lesson_id": lesson_id, "status": "active", "note": decision_note})
    return {"lesson_id": lesson_id, "status": "active", "skill_name": f"lesson-{L['family'].lower()}-{lesson_id[-8:]}"}


@mcp.tool(annotations=APPEND)
def revert_lesson(lesson_id: str, reason: str) -> dict:
    """Mark a candidate lesson as reverted (benchmark regressed or falsifier won)."""
    _append("status", {"lesson_id": lesson_id, "status": "reverted", "note": reason})
    return {"lesson_id": lesson_id, "status": "reverted"}


@mcp.tool(annotations=READ)
def get_lesson(lesson_id: str) -> dict:
    """Full lesson record with evidence and status history."""
    return _replay()["lessons"].get(lesson_id) or {"error": "unknown lesson"}


@mcp.tool(annotations=READ)
def get_active_lessons() -> dict:
    """Active (promoted) lessons — the rules a fresh agent must load."""
    st = _replay()
    active = [L for L in st["lessons"].values() if L["status"] == "active"]
    return {"count": len(active),
            "lessons": [{"lesson_id": L["id"], "family": L["family"], "invariant": L["invariant"],
                         "rule_text": L["rule_text"], "intervention_type": L["intervention_type"]}
                        for L in active]}


@mcp.tool(annotations=READ)
def ledger_summary() -> dict:
    """Counts and the latest records — for the dashboard and for the agent to orient."""
    st = _replay()
    return {"corrections": len(st["corrections"]),
            "lessons": {s: sum(1 for L in st["lessons"].values() if L["status"] == s)
                        for s in ("candidate", "active", "reverted")},
            "recent": [{"id": L["id"], "family": L["family"], "status": L["status"]}
                       for L in list(st["lessons"].values())[-5:]]}


if __name__ == "__main__":
    print(f"lesson-ledger MCP on http://127.0.0.1:{mcp.settings.port}/mcp  (ledger: {LOG})", file=sys.stderr)
    mcp.run(transport="streamable-http")
