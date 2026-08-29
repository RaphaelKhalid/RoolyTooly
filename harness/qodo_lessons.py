"""Bridge promoted lessons to Qodo rules; retrieve only the relevant ones per task.

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
LEDGER = Path(os.environ.get("ROOLY_LEDGER_DIR", ROOT / "ledger")) / "ledger.jsonl"

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
    """Append a record to ledger/ledger.jsonl in the shape ledger_server.py writes.

    Only used for records this bridge derives itself (overlap-audit status notes); it never edits
    an existing line, only appends, matching the ledger's append-only contract."""
    rec = {"id": payload.pop("id", None) or f"{kind[:3]}_{uuid.uuid4().hex[:8]}", "kind": kind,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **payload}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def _qodo(*args: str) -> dict | list | None:
    """Run the qodo CLI via node (the sh launcher mangles leading-slash scope args)."""
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


def all_lessons() -> list[dict]:
    """Every lesson with its current status (not only active ones)."""
    lessons: dict[str, dict] = {}
    if not LEDGER.exists():
        return []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
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
    return list(lessons.values())


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
    """Map lesson id to Qodo ruleId from the append-only `qodo_rule` ledger records."""
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
    """Map lesson id to Qodo ruleId: ledger mapping first, then a name-prefix search."""
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
    """Check whether `lesson` would duplicate a Qodo rule already shipped by sync().

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


def sync(lesson_ids: list[str] | None = None) -> None:
    have = existing_rule_ids()
    pool = all_lessons() if lesson_ids else active_lessons()
    for L in [x for x in pool if not lesson_ids or x['id'] in lesson_ids]:
        if L["id"] in have:
            print(f"exists  {L['id']} -> rule {have[L['id']]}")
            continue
        a = audit_overlap(L)
        deg = " [degraded]" if a["degraded"] else ""
        if a["verdict"] == "overlap":
            note = (f"overlap audit: near-duplicate of {a['with']} (jaccard {a['jaccard']}); "
                    f"not added to Qodo, superseded_by={a['with']}")
            _append_ledger("status", lesson_id=L["id"], status="superseded", superseded_by=a["with"], note=note)
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


def _rel(path: Path) -> str:
    """Render a path relative to the repo root when it lives under it."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _reconcile(local_ids: list[str], qodo_ids: list[str]) -> dict:
    """Compare the local selection with Qodo's by canonical lesson id, not title."""
    both = [i for i in local_ids if i in qodo_ids]
    return {"both": both, "local_only": [i for i in local_ids if i not in qodo_ids],
            "qodo_only": [i for i in qodo_ids if i not in local_ids],
            "same_set": set(local_ids) == set(qodo_ids)}


def select(top_k: int = 5, lesson_ids: list[str] | None = None, *, embedder=None,
           cosine_threshold: float | None = None, rrf_k: int | None = None) -> None:
    """Ask Qodo which lessons apply to each seeded case; write an index with receipts.

    Fail-safe: a search failure is recorded as `null` (unknown) for that case, so the eval-runner falls
    back to every active lesson instead of silently injecting nothing. Returned rules are candidates;
    they are accepted only if they map to a currently ACTIVE lesson (deterministic filter).

    Qodo stays the selector: `index` is exactly what Qodo accepted. The local three-channel
    retriever (harness.rule_index) is run for the same query and recorded under
    `receipts[case]["local"]` with per-channel receipts, so the two can be compared offline
    without changing what the eval-runner injects. Agreement is reported, never required."""
    from harness import rule_index as R  # local import: rule_index imports this module at top level

    embedder = R.embed_texts if embedder is None else embedder
    cosine_threshold = R.DEFAULT_COSINE_THRESHOLD if cosine_threshold is None else cosine_threshold
    rrf_k = R.DEFAULT_RRF_K if rrf_k is None else rrf_k
    lessons = {L["id"]: L for L in (all_lessons() if lesson_ids else active_lessons()) if not lesson_ids or L["id"] in lesson_ids}
    local_index = R.LessonIndex(list(lessons.values()))
    mapping = {v: k for k, v in mapped_rule_ids().items()}  # rule_id -> lesson_id
    ledger_hash = _sha(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else None
    index: dict[str, list[str] | None] = {}
    receipts: dict[str, dict] = {}
    agreement = {"compared": 0, "same_set": 0, "any_overlap": 0, "qodo_unknown": 0}
    dense_statuses: dict[str, int] = {}
    for c in C.CASES:
        ask = c["task"].split("Then: ")[-1][:300]
        query = f"Name: Pre-claim verification for this coding task\nCategory: Correctness\nContent: {ask}"
        local = local_index.retrieve(ask, limit=top_k, cosine_threshold=cosine_threshold,
                                     rrf_k=rrf_k, embedder=embedder)
        local_receipt = local.receipt()
        dense_statuses[local.dense_status] = dense_statuses.get(local.dense_status, 0) + 1
        res = _qodo("rules", "search", "--query", query, "--top-k", str(top_k), "--scopes", SCOPE)
        if not isinstance(res, dict) or "rules" not in res:
            index[c["id"]] = None  # unknown -> fallback to all active lessons at runtime
            agreement["qodo_unknown"] += 1
            local_receipt["agreement"] = None
            receipts[c["id"]] = {"query_hash": _sha(query), "status": "unknown",
                                 "reason": "qodo search failed or returned invalid JSON",
                                 "local": local_receipt}
            print(f"{c['id']:<32} UNKNOWN (fallback)  local={local.lesson_ids} dense={local.dense_status}")
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
        qodo_rank = {lid: n for n, lid in enumerate(accepted, start=1)}
        for h in local_receipt["hits"]:
            rank = qodo_rank.get(h["lesson_id"])
            h["qodo_rank"] = rank
            if rank is not None:
                h["channels_fired"] = h["channels_fired"] + ["qodo"]
        local_receipt["agreement"] = _reconcile(local.lesson_ids, accepted)
        agreement["compared"] += 1
        agreement["same_set"] += bool(local_receipt["agreement"]["same_set"])
        agreement["any_overlap"] += bool(local_receipt["agreement"]["both"])
        index[c["id"]] = accepted
        receipts[c["id"]] = {"query_hash": _sha(query), "status": "ok", "ranked": ranked,
                             "accepted": accepted, "rejected": rejected, "local": local_receipt}
        print(f"{c['id']:<32} {accepted}  local={local.lesson_ids} dense={local.dense_status}")
    INDEX.parent.mkdir(exist_ok=True)
    tmp = INDEX.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"scope": SCOPE, "top_k": top_k, "ledger_hash": ledger_hash,
                                 "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "retrieval": {"selector": "qodo", "local_channels": list(R.CHANNELS),
                                               "embedding_model": R.EMBEDDING_MODEL,
                                               "cosine_threshold": cosine_threshold, "rrf_k": rrf_k,
                                               "channel_depth": R.DEFAULT_CHANNEL_DEPTH,
                                               "dense_status_counts": dense_statuses},
                                 "qodo_agreement": agreement,
                                 "index": index, "receipts": receipts}, indent=1), encoding="utf-8")
    os.replace(tmp, INDEX)  # atomic: readers never see a half-written index
    print("wrote", _rel(INDEX))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    ids = sys.argv[2:] or None
    if cmd == "sync":
        sync(ids)
    elif cmd == "select":
        select(lesson_ids=ids)
    else:
        audit()
