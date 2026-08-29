"""Bridge promoted lessons to Qodo rules and retrieve only the relevant ones per task.

Injecting every active lesson into every worker prompt does not scale (the workspace already
holds 383 rules); Qodo's semantic rule search returns the handful that match a task. Two entry
points:

    python -m harness.qodo_lessons sync        # active ledger lessons -> scoped Qodo rules (idempotent by name)
    python -m harness.qodo_lessons select      # for every seeded case: which lessons apply -> results/lesson_index.json

The eval-runner's harness manifest reads results/lesson_index.json when present and injects only
the lessons selected for that case; without it, every active lesson is injected (old behaviour).
The qodo CLI is Windows-side (node), so this runs from Windows Python; it never edits code.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench import cases as C  # noqa: E402

SCOPE = "/RaphaelKhalid/RoolyTooly/"
RULE_PREFIX = "RoolyTooly lesson "
INDEX = ROOT / "results" / "lesson_index.json"
LEDGER = ROOT / "ledger" / "ledger.jsonl"

# Cheap symbolic overlap check (no embeddings): Jaccard over lowercase word sets, stopwords removed.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when", "while", "for", "to", "of",
    "in", "on", "at", "by", "with", "from", "as", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "not", "no", "do", "does", "did", "before",
    "after", "only", "any", "must", "should", "can", "cannot", "never", "always", "so", "than",
    "into", "onto", "over", "under", "about", "than", "must", "will", "would", "may", "might",
    "each", "every", "both", "either", "neither", "you", "your", "we", "our", "i", "they", "their",
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", (text or "").lower()) if len(w) > 1 and w not in _STOPWORDS}


def _jaccard(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _append_ledger(kind: str, **payload) -> dict:
    """Append a record directly to ledger/ledger.jsonl in the same shape mcp_servers/ledger_server.py writes.

    Only used for records this bridge derives itself (overlap-audit status notes); it never edits
    an existing line, only appends, matching the ledger's append-only contract."""
    rec = {"id": payload.pop("id", None) or f"{kind[:3]}_{uuid.uuid4().hex[:8]}", "kind": kind,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **payload}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def _qodo(*args: str) -> dict | list | None:
    """Run the qodo CLI via node directly (the sh launcher mangles leading-slash scope args)."""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    node = os.environ.get("NODE_EXE", r"C:\Program Files\nodejs\node.exe")
    mjs = home / ".qodo" / "bin" / "qodo.mjs"
    if not mjs.exists():
        return None
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    out = subprocess.run([node, str(mjs), *args, "--json"], capture_output=True, text=True, env=env, timeout=180)
    first = (out.stdout or "").strip().split("\n")[0] if out.stdout else ""
    try:
        return json.loads(first)
    except json.JSONDecodeError:
        return None


def active_lessons() -> list[dict]:
    lessons: dict[str, dict] = {}
    log = LEDGER
    if not log.exists():
        return []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(r, dict):
            continue
        if r.get("kind") == "lesson":
            lessons[r["id"]] = {**r, "status": "candidate"}
        elif r.get("kind") == "status" and r.get("lesson_id") in lessons:
            lessons[r["lesson_id"]]["status"] = r.get("status")
    return [L for L in lessons.values() if L["status"] == "active"]


def mapped_rule_ids() -> dict[str, int]:
    """Lesson id -> Qodo ruleId from the append-only `qodo_rule` ledger records (authoritative)."""
    found: dict[str, int] = {}
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and r.get("kind") == "qodo_rule" and r.get("rule_id"):
                found[r["lesson_id"]] = r["rule_id"]
    return found


def existing_rule_ids() -> dict[str, int]:
    """Map lesson id -> Qodo ruleId: ledger mapping first, then a name-prefix search as a backstop."""
    found: dict[str, int] = dict(mapped_rule_ids())
    res = _qodo("rules", "search", "--query", f"Name: {RULE_PREFIX}\nCategory: Correctness\nContent: lesson compiled from a human correction",
                "--top-k", "50", "--scopes", SCOPE)
    for r in (res or {}).get("rules", []) if isinstance(res, dict) else []:
        name = r.get("name") or ""
        if name.startswith(RULE_PREFIX):
            lid = name[len(RULE_PREFIX):].split(" ")[0]
            found.setdefault(lid, r.get("ruleId") or r.get("id"))
    return found


def audit_overlap(lesson: dict) -> dict:
    """Check whether `lesson` would duplicate an already-shipped Qodo rule before sync() creates one.

    Runs the same scoped Qodo rule search sync() uses for existence checks, keeps only sibling
    rules (name starts with RULE_PREFIX, lesson id differs from `lesson["id"]`), and scores each
    against `lesson["rule_text"]` with a cheap symbolic Jaccard (lowercase word sets, stopwords
    removed) — no embeddings, no extra network call. If the qodo CLI call itself fails (`_qodo`
    returns None — no CLI on PATH, timeout, bad JSON), this degrades to a Jaccard-only comparison
    against every other currently-active lesson's rule_text (no Qodo content available) and marks
    the result `"degraded": True` so callers can say so.

    Returns {"verdict": "overlap"|"review"|"distinct", "degraded": bool, and, unless distinct with
    no candidates, "with": <sibling lesson id>, "jaccard": <float rounded to 3dp>}.
    Thresholds: jaccard >= 0.5 -> overlap (do not create); 0.3 <= jaccard < 0.5 -> review (create,
    but flag for a human); otherwise -> distinct.
    """
    query = f"Name: {lesson['invariant']}\nCategory: Correctness\nContent: {lesson['rule_text']}"
    res = _qodo("rules", "search", "--query", query, "--top-k", "5", "--scopes", SCOPE)
    degraded = res is None
    candidates: dict[str, str] = {}
    if not degraded:
        for r in (res or {}).get("rules", []) if isinstance(res, dict) else []:
            name = r.get("name") or ""
            if not name.startswith(RULE_PREFIX):
                continue
            lid = name[len(RULE_PREFIX):].split(" ")[0]
            if lid != lesson["id"]:
                candidates[lid] = r.get("content") or ""
    else:
        # Qodo CLI unavailable: fall back to symbolic-only comparison against active siblings.
        for L in active_lessons():
            if L["id"] != lesson["id"]:
                candidates[L["id"]] = L.get("rule_text", "")

    best_id, best_j = None, 0.0
    for lid, text in candidates.items():
        j = _jaccard(lesson["rule_text"], text)
        if j > best_j:
            best_id, best_j = lid, j

    if best_id is None:
        return {"verdict": "distinct", "degraded": degraded}
    jaccard = round(best_j, 3)
    if jaccard >= 0.5:
        verdict = "overlap"
    elif jaccard >= 0.3:
        verdict = "review"
    else:
        verdict = "distinct"
    return {"verdict": verdict, "with": best_id, "jaccard": jaccard, "degraded": degraded}


def audit() -> None:
    """Print (never write) the overlap-audit result for every active lesson."""
    for L in active_lessons():
        a = audit_overlap(L)
        tag = f"~{a['with']} jaccard={a['jaccard']}" if "with" in a else "no siblings"
        deg = " [degraded: qodo CLI unavailable, symbolic-Jaccard-only]" if a["degraded"] else ""
        print(f"{a['verdict']:<8} {L['id']} ({L['family']}) {tag}{deg}")


def sync() -> None:
    have = existing_rule_ids()
    for L in active_lessons():
        if L["id"] in have:
            print(f"exists  {L['id']} -> rule {have[L['id']]}")
            continue
        a = audit_overlap(L)
        deg = " [degraded]" if a["degraded"] else ""
        if a["verdict"] == "overlap":
            note = (f"overlap audit: near-duplicate of {a['with']} (jaccard {a['jaccard']}); "
                    f"not added to Qodo, superseded_by={a['with']}")
            _append_ledger("status", lesson_id=L["id"], status="active", note=note)
            print(f"overlap {L['id']} ~ {a['with']} (jaccard={a['jaccard']}){deg} -> not created; ledger note appended")
            continue
        if a["verdict"] == "review":
            print(f"REVIEW  {L['id']} ~ {a.get('with')} (jaccard={a.get('jaccard')}){deg} -> creating rule, needs human review")
        name = f"{RULE_PREFIX}{L['id']} ({L['family']}): {L['invariant'][:80]}"
        res = _qodo("rules", "create", "--name", name[:128], "--category", "Correctness", "--severity", "warning",
                    "--content", L["rule_text"][:600], "--good-examples", L.get("preflight_check") or "",
                    "--bad-examples", "", "--scopes", SCOPE)
        rid = (res or {}).get("ruleId") if isinstance(res, dict) else None
        state = (res or {}).get("state") if isinstance(res, dict) else None
        if rid:
            _append_ledger("qodo_rule", lesson_id=L["id"], rule_id=rid, state=state, scopes=(res or {}).get("scopes"),
                           note="created by harness.qodo_lessons.sync" + ("" if state == "active" else f"; Qodo state={state} (not active until approved)"))
        else:
            _append_ledger("qodo_rule", lesson_id=L["id"], rule_id=None, state="error",
                           note="Qodo create failed or returned no ruleId; lesson still applies via deterministic fallback")
        print(f"created {L['id']} -> {rid} state={state}")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def select(top_k: int = 5) -> None:
    """Ask Qodo which lessons apply to each seeded case; write an index WITH retrieval receipts.

    Fail-safe: a search failure is recorded as `null` (unknown) for that case, so the eval-runner falls
    back to every active lesson instead of silently injecting nothing. Returned rules are candidates;
    they are accepted only if they map to a currently ACTIVE lesson (deterministic filter)."""
    lessons = {L["id"]: L for L in active_lessons()}
    mapping = {v: k for k, v in mapped_rule_ids().items()}  # rule_id -> lesson_id
    ledger_hash = _sha(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else None
    index: dict[str, list[str] | None] = {}
    receipts: dict[str, dict] = {}
    for c in C.CASES:
        ask = c["task"].split("Then: ")[-1][:300]
        query = f"Name: Pre-claim verification for this coding task\nCategory: Correctness\nContent: {ask}"
        res = _qodo("rules", "search", "--query", query, "--top-k", str(top_k), "--scopes", SCOPE)
        if not isinstance(res, dict) or "rules" not in res:
            index[c["id"]] = None  # unknown -> fallback to all active lessons at runtime
            receipts[c["id"]] = {"query_hash": _sha(query), "status": "unknown", "reason": "qodo search failed or returned invalid JSON"}
            print(f"{c['id']:<32} UNKNOWN (fallback)")
            continue
        ranked, accepted, rejected = [], [], []
        for r in res["rules"]:
            rid = r.get("ruleId") or r.get("id")
            name = r.get("name") or ""
            ranked.append({"rule_id": rid, "name": name[:80], "score": r.get("score")})
            lid = mapping.get(rid)
            if lid is None and name.startswith(RULE_PREFIX):
                lid = name[len(RULE_PREFIX):].split(" ")[0]
            if lid is None:
                rejected.append({"rule_id": rid, "reason": "not a RoolyTooly lesson rule"})
            elif lid not in lessons:
                rejected.append({"rule_id": rid, "lesson_id": lid, "reason": "lesson not active in ledger"})
            else:
                accepted.append(lid)
        index[c["id"]] = accepted
        receipts[c["id"]] = {"query_hash": _sha(query), "status": "ok", "ranked": ranked, "accepted": accepted, "rejected": rejected}
        print(f"{c['id']:<32} {accepted}")
    INDEX.parent.mkdir(exist_ok=True)
    INDEX.write_text(json.dumps({"scope": SCOPE, "top_k": top_k, "ledger_hash": ledger_hash,
                                 "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "index": index, "receipts": receipts}, indent=1), encoding="utf-8")
    print("wrote", INDEX.relative_to(ROOT))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    {"sync": sync, "select": select, "audit": audit}[cmd]()
