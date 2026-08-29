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

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench import cases as C  # noqa: E402

SCOPE = "/RaphaelKhalid/RoolyTooly/"
RULE_PREFIX = "RoolyTooly lesson "
INDEX = ROOT / "results" / "lesson_index.json"


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
    log = ROOT / "ledger" / "ledger.jsonl"
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


def existing_rule_ids() -> dict[str, int]:
    """Map lesson id -> Qodo ruleId for rules this bridge created (by name prefix)."""
    found: dict[str, int] = {}
    res = _qodo("rules", "search", "--query", f"Name: {RULE_PREFIX}\nCategory: Correctness\nContent: lesson compiled from a human correction",
                "--top-k", "50", "--scopes", SCOPE)
    for r in (res or {}).get("rules", []) if isinstance(res, dict) else []:
        name = r.get("name") or ""
        if name.startswith(RULE_PREFIX):
            lid = name[len(RULE_PREFIX):].split(" ")[0]
            found[lid] = r.get("ruleId") or r.get("id")
    return found


def sync() -> None:
    have = existing_rule_ids()
    for L in active_lessons():
        if L["id"] in have:
            print(f"exists  {L['id']} -> rule {have[L['id']]}")
            continue
        name = f"{RULE_PREFIX}{L['id']} ({L['family']}): {L['invariant'][:80]}"
        res = _qodo("rules", "create", "--name", name[:128], "--category", "Correctness", "--severity", "warning",
                    "--content", L["rule_text"][:600], "--good-examples", L.get("preflight_check") or "",
                    "--bad-examples", "", "--scopes", SCOPE)
        print(f"created {L['id']} -> {(res or {}).get('ruleId') if isinstance(res, dict) else res} state={(res or {}).get('state') if isinstance(res, dict) else '?'}")


def select(top_k: int = 5) -> None:
    """For every seeded case, ask Qodo which lessons apply; write results/lesson_index.json."""
    lessons = {L["id"]: L for L in active_lessons()}
    index: dict[str, list[str]] = {}
    for c in C.CASES:
        ask = c["task"].split("Then: ")[-1][:300]
        res = _qodo("rules", "search", "--query", f"Name: {RULE_PREFIX}lesson for this task\nCategory: Correctness\nContent: {ask}",
                    "--top-k", str(top_k), "--scopes", SCOPE)
        hits = []
        for r in (res or {}).get("rules", []) if isinstance(res, dict) else []:
            name = r.get("name") or ""
            if name.startswith(RULE_PREFIX):
                lid = name[len(RULE_PREFIX):].split(" ")[0]
                if lid in lessons:
                    hits.append(lid)
        index[c["id"]] = hits
        print(f"{c['id']:<32} {hits}")
    INDEX.parent.mkdir(exist_ok=True)
    INDEX.write_text(json.dumps({"scope": SCOPE, "top_k": top_k, "index": index}, indent=1), encoding="utf-8")
    print("wrote", INDEX.relative_to(ROOT))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    {"sync": sync, "select": select}[cmd]()
