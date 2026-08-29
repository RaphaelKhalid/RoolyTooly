"""Cold-start the mistake ledger from the user's OWN agent chat history.

Scans local Claude Code and Codex transcripts (read-only) for moments where a human corrected
an agent, and turns each one into a `correction` record the ledger understands (same shape as
`mcp_servers/ledger_server.py`'s `record_correction`, plus `family_hint` and `source`, the way
the existing "self-observed" corrections in ledger/ledger.jsonl already do).

Sources (read-only, never modified):
  Claude Code: %USERPROFILE%\\.claude\\projects\\*\\*.jsonl
  Codex:       %USERPROFILE%\\.codex\\sessions\\**\\*.jsonl

Usage:
    python -m harness.import_history --dry-run [--project SUBSTR] [--limit N]
    python -m harness.import_history [--project SUBSTR] [--limit N]

--dry-run   prints a table (source, session, family_hint, claim, correction) and per-family
            counts; does NOT touch ledger/ledger.jsonl.
(default)   appends the new correction records to ledger/ledger.jsonl, skipping any whose
            import_key (sha1 of file+turn index) already appears in the ledger.
--limit N   caps the number of records considered, newest sessions first (default 50).
--project S only scan Claude projects whose directory path contains substring S (default: all;
            does not affect Codex sessions, which have no per-project directory).

Nothing here is sent anywhere. Anything that looks like a secret (API key / bearer token) is
redacted before it is ever written to the ledger.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "ledger" / "ledger.jsonl"

HOME = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home()))
CLAUDE_PROJECTS_DIR = HOME / ".claude" / "projects"
CODEX_SESSIONS_DIR = HOME / ".codex" / "sessions"

# ---- detection --------------------------------------------------------------------------------

CORRECTION_RE = re.compile(
    r"\b(no[,.]|wrong|incorrect|that'?s not|you (said|claimed|told me)|didn'?t actually|"
    r"but it (didn'?t|doesn'?t|isn'?t)|actually[, ]|not (done|fixed|working)|"
    r"still (broken|failing|not)|you missed|hallucinat|made up|fabricat|i told you|"
    r"why did you|stop (doing|saying))\b",
    re.IGNORECASE,
)

SKIP_PREFIXES = (
    "<local-command", "<system-reminder", "[Image", "{",
    # Additional obviously-system-injected preambles observed in real transcripts (same spirit as
    # the four patterns above: none of these were ever typed by a human).
    "<task-notification", "<command-message>", "<command-name>",
    "Base directory for this skill:",  # Skill tool's injected content, surfaces as a "user" turn
    "The following is the Codex agent history",  # Codex approval-bridge synthetic transcript digest
)

SECRET_RE = re.compile(r"(sk-|dtn_|ghp_|gho_|Bearer )[A-Za-z0-9_\-]+")

# Family keyword map, checked in this priority order; first match wins.
FAMILY_KEYWORDS = [
    ("M05", ("null", "empty", "0 rows", "didn't run", "didnt run", "silently")),
    ("M06", ("done", "finished", "complete", "committed", "installed")),
    ("M03", ("passes", "tests pass", "score", "metric")),
    ("M07", ("deleted", "removed", "lost", "overwrote")),
    ("M09", ("stale", "old", "cached", "outdated", "already fixed")),
    ("M12", ("didn't ask", "didnt ask", "out of scope", "why did you change", "don't touch", "dont touch")),
    ("M14", ("broke", "regression", "used to work")),
    ("M11", ("root cause", "actually the", "wrong cause")),
    ("M17", ("stop hedging", "just do it", "too cautious", "unnecessary warning")),
]


def family_hint(text: str) -> str:
    low = text.lower()
    for fam, keywords in FAMILY_KEYWORDS:
        for kw in keywords:
            if kw in low:
                return fam
    return "unknown"


def redact(text: str) -> str:
    return SECRET_RE.sub("[REDACTED]", text or "")


def clean_text(text) -> str | None:
    """Normalize an extracted turn's text; return None if it should be skipped."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    if not t:
        return None
    if t.startswith(SKIP_PREFIXES):
        return None
    return t


def format_ts(raw: str | None) -> str:
    if raw:
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---- transcript parsing -----------------------------------------------------------------------

def _text_from_content_list(content: list) -> str | None:
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            parts.append(item["text"])
    return "\n".join(parts) if parts else None


def extract_claude_turns(path: Path) -> list[dict]:
    """Ordered list of {line_idx, role, text, ts} for a Claude Code transcript file."""
    turns = []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return turns
    for i, raw in enumerate(raw_lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        rtype = rec.get("type")
        if rtype not in ("user", "assistant"):
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = _text_from_content_list(content)
        else:
            text = None
        text = clean_text(text)
        if text is None:
            continue
        turns.append({"line_idx": i, "role": rtype, "text": text, "ts": rec.get("timestamp")})
    return turns


def extract_codex_turns(path: Path) -> list[dict]:
    """Ordered list of {line_idx, role, text, ts} for a Codex transcript file."""
    turns = []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return turns
    for i, raw in enumerate(raw_lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue
        ptype = payload.get("type")
        if ptype == "user_message":
            role = "user"
        elif ptype == "agent_message":
            role = "assistant"
        else:
            continue
        field = payload.get("message")
        if field is None:
            field = payload.get("content")
        if isinstance(field, list):
            parts = []
            for item in field:
                if isinstance(item, dict) and item.get("text"):
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            field = "\n".join(parts) if parts else None
        text = clean_text(field)
        if text is None:
            continue
        turns.append({"line_idx": i, "role": role, "text": text, "ts": rec.get("timestamp")})
    return turns


def find_corrections(turns: list[dict], source_kind: str, path: Path) -> list[dict]:
    """Scan a session's ordered turns for corrections and build ledger-shaped records."""
    records = []
    last_assistant_text = ""
    last_noncorrection_user_text = ""
    for t in turns:
        if t["role"] == "assistant":
            last_assistant_text = t["text"]
            continue
        text = t["text"]
        if len(text) >= 40 and CORRECTION_RE.search(text):
            agent_claim = redact(last_assistant_text[-400:])
            user_correction = redact(text[:500])
            task_summary = redact(last_noncorrection_user_text[:120])
            import_key = hashlib.sha1(f"{path}:{t['line_idx']}".encode("utf-8")).hexdigest()
            records.append({
                "id": f"cor_{uuid.uuid4().hex[:8]}",
                "kind": "correction",
                "ts": format_ts(t.get("ts")),
                "task_summary": task_summary,
                "agent_claim": agent_claim,
                "user_correction": user_correction,
                "evidence": f"imported from {source_kind} transcript {path.name}, turn index {t['line_idx']}",
                "session_id": path.name,
                "family_hint": family_hint(agent_claim + " " + user_correction),
                "source": f"imported from {source_kind} chat history",
                "import_key": import_key,
            })
        else:
            last_noncorrection_user_text = text
    return records


# ---- session discovery ------------------------------------------------------------------------

def discover_sessions(project_filter: str | None) -> list[tuple[Path, str]]:
    """Return (file_path, source_kind) pairs, newest file (by mtime) first."""
    sessions: list[tuple[Path, str]] = []

    if CLAUDE_PROJECTS_DIR.is_dir():
        for f in CLAUDE_PROJECTS_DIR.glob("*/*.jsonl"):
            if project_filter and project_filter not in str(f.parent):
                continue
            sessions.append((f, "claude"))

    if CODEX_SESSIONS_DIR.is_dir():
        for f in CODEX_SESSIONS_DIR.glob("**/*.jsonl"):
            sessions.append((f, "codex"))

    def mtime(item: tuple[Path, str]) -> float:
        try:
            return item[0].stat().st_mtime
        except OSError:
            return 0.0

    sessions.sort(key=mtime, reverse=True)
    return sessions


def collect_records(project_filter: str | None, limit: int) -> list[dict]:
    records: list[dict] = []
    for path, source_kind in discover_sessions(project_filter):
        if source_kind == "claude":
            turns = extract_claude_turns(path)
        else:
            turns = extract_codex_turns(path)
        records.extend(find_corrections(turns, source_kind, path))
        if len(records) >= limit:
            break
    return records[:limit]


# ---- ledger I/O ---------------------------------------------------------------------------------

def existing_import_keys() -> set[str]:
    keys: set[str] = set()
    if not LEDGER.exists():
        return keys
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and r.get("import_key"):
            keys.add(r["import_key"])
    return keys


def append_to_ledger(records: list[dict]) -> int:
    if not records:
        return 0
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return len(records)


# ---- CLI ------------------------------------------------------------------------------------

def _truncate(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def print_table(records: list[dict]) -> None:
    if not records:
        print("No corrections found.")
        return
    header = f"{'source':<8} {'session':<40} {'family':<7} {'claim':<45} {'correction':<45}"
    print(header)
    print("-" * len(header))
    for r in records:
        src = "claude" if "claude" in r.get("source", "") else ("codex" if "codex" in r.get("source", "") else "?")
        print(f"{src:<8} {_truncate(r['session_id'], 40):<40} {r['family_hint']:<7} "
              f"{_truncate(r['agent_claim'], 45):<45} {_truncate(r['user_correction'], 45):<45}")
    print()
    counts: dict[str, int] = {}
    for r in records:
        counts[r["family_hint"]] = counts.get(r["family_hint"], 0) + 1
    print("Counts per family:")
    for fam, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {fam}: {n}")
    print(f"\nTotal: {len(records)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the table; do not touch the ledger")
    ap.add_argument("--limit", type=int, default=50, help="max records to consider, newest sessions first (default 50)")
    ap.add_argument("--project", default=None, help="only scan Claude projects whose path contains this substring")
    args = ap.parse_args()

    records = collect_records(args.project, args.limit)

    if args.dry_run:
        print_table(records)
        return

    known = existing_import_keys()
    new_records = [r for r in records if r["import_key"] not in known]
    skipped = len(records) - len(new_records)
    written = append_to_ledger(new_records)
    print(f"Appended {written} new correction record(s) to {LEDGER}; skipped {skipped} duplicate(s).")


if __name__ == "__main__":
    main()
