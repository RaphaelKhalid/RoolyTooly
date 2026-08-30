"""Morning digest: a short, plain note for a human who is not a software engineer.

Turns the ledger and the latest eval-runner artifacts into that note.

    python -m harness.digest [--since ISO]

Writes docs/digest/<YYYY-MM-DD>.md (today's UTC date; the directory is created if missing).
--since defaults to 24 hours before now (UTC) and filters ledger records by their "ts" field.

Every number in the digest is read directly from a file under ledger/ or results/ - nothing here
is inferred, estimated, or invented. When the file a section needs does not exist, that section
says "no data" instead of guessing.
"""
from __future__ import annotations

import argparse
import calendar
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "ledger" / "ledger.jsonl"
RESULTS = ROOT / "results"
DIGEST_DIR = ROOT / "docs" / "digest"

# Plain-language names for the mistake families. Kept local (not imported from
# mcp_servers/ledger_server.py) so `python -m harness.digest` works without the `mcp` package,
# which this Windows environment does not have installed.
PLAIN_FAMILY = {
    "M03": "declaring victory on a side effect instead of the real goal",
    "M05": "trusting a printed summary instead of checking the real file",
    "M06": "calling something “done” when it is still a stub",
    "M07": "deleting the evidence before anyone could check it",
    "M09": "insisting on an answer that was already corrected",
    "M10": "not noticing that the computer/environment itself had failed",
    "M11": "misreading what actually happened",
    "M12": "doing more than asked, touching things out of scope",
    "M13": "mixing different kinds of numbers together",
    "M14": "a fix that breaks something else",
    "M17": "taking a shortcut and calling it careful work",
    "M20": "leaving a placeholder instead of the real thing",
    "M21": "dropping a promise made earlier in the conversation",
    "M22": "repeating a mistake it was already corrected on",
    "M23": "delivering something different from what was described",
}


def _plain(fam: str) -> str:
    if fam in PLAIN_FAMILY:
        return f"{fam} – {PLAIN_FAMILY[fam]}"
    return fam


def _to_epoch(ts: str) -> float:
    return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))


def _default_since() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 24 * 3600))


def _read_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and "ts" in r and "kind" in r:
            out.append(r)
    return out


def _lesson_maps(records: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """Map lesson_id to its lesson record, and correction_id to first lesson's family."""
    lessons: dict[str, dict] = {}
    correction_family: dict[str, str] = {}
    for r in records:
        if r.get("kind") == "lesson":
            lessons[r["id"]] = r
            cid = r.get("correction_id")
            if cid and cid not in correction_family:
                correction_family[cid] = r.get("family", "?")
    return lessons, correction_family


def _quote(text: str, limit: int = 130) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def _what_we_learned(records: list[dict], since_epoch: float, correction_family: dict[str, str]) -> list[str]:
    if not records:
        return ['No data (ledger/ledger.jsonl is missing).']
    groups: dict[str, list[tuple[str, str, str]]] = {}
    for r in records:
        if _to_epoch(r["ts"]) < since_epoch:
            continue
        if r.get("kind") == "observation":
            fam = r.get("family", "?")
            groups.setdefault(fam, []).append(("observation", r.get("quote", ""), r.get("source_url", "")))
        elif r.get("kind") == "correction":
            fam = correction_family.get(r["id"], "uncategorized")
            src = f"internal session {r.get('session_id', '?')} (no public URL)"
            groups.setdefault(fam, []).append(("correction", r.get("user_correction", ""), src))
    if not groups:
        return [f"Nothing new since {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(since_epoch))}."]

    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    lines = []
    top_n = 5
    for fam, items in ranked[:top_n]:
        # Prefer an example that has a real URL (an observation) over an internal correction.
        items.sort(key=lambda it: 0 if it[0] == "observation" else 1)
        kind, text, src = items[0]
        lines.append(f"- **{_plain(fam)}** ({len(items)} new item(s)): “{_quote(text)}” — {src}")
    if len(ranked) > top_n:
        rest_items = sum(len(v) for _, v in ranked[top_n:])
        rest_fams = len(ranked) - top_n
        lines.append(f"- ...plus {rest_items} more item(s) across {rest_fams} other families (see ledger/ledger.jsonl).")
    return lines


def _status_changes(records: list[dict], since_epoch: float) -> list[dict]:
    return [r for r in records if r.get("kind") == "status" and _to_epoch(r["ts"]) >= since_epoch]


def _rules_added_merged_retired(records: list[dict], since_epoch: float, lessons: dict[str, dict]) -> list[str]:
    if not records:
        return ["No data (ledger/ledger.jsonl is missing)."]
    changes = _status_changes(records, since_epoch)
    if not changes:
        return [f"No rule changes since {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(since_epoch))}."]

    # A lesson can flip status more than once in the window (e.g. re-validated); keep only the
    # latest status event per lesson id so each lesson is reported once.
    latest_by_lesson: dict[str, dict] = {}
    for r in sorted(changes, key=lambda r: r["ts"]):
        latest_by_lesson[r.get("lesson_id")] = r

    added, merged, reverted, revoked = [], [], [], []
    for r in latest_by_lesson.values():
        lid = r.get("lesson_id")
        L = lessons.get(lid, {})
        fam = L.get("family", "?")
        status = r.get("status")
        note = r.get("note") or ""
        if status == "active" and note.startswith("overlap audit"):
            merged.append((lid, fam, note))
        elif status == "active":
            added.append((lid, fam, L.get("rule_text", "")))
        elif status == "reverted":
            reverted.append((lid, fam))
        elif status == "revoked":
            revoked.append((lid, fam, note))

    lines = []
    for lid, fam, rule_text in added:
        lines.append(f"- **Added** ({_plain(fam)}): the agent must now {_quote(rule_text[0].lower() + rule_text[1:] if rule_text else 'follow a new rule', 150)}")
    for lid, fam, note in merged:
        lines.append(f"- **Merged** ({_plain(fam)}, {lid}): {_quote(note, 150)}")
    for lid, fam, note in revoked:
        lines.append(f"- **Retired** ({_plain(fam)}, {lid}): {_quote(note, 150) or 'revoked, no reason recorded'}")
    if reverted:
        by_fam: dict[str, int] = {}
        for _, fam in reverted:
            by_fam[fam] = by_fam.get(fam, 0) + 1
        parts = ", ".join(f"{_plain(f)}: {n}" for f, n in sorted(by_fam.items(), key=lambda kv: -kv[1]))
        lines.append(f"- **Tried and rejected during testing** (did not become live rules): {len(reverted)} attempt(s) — {parts}.")
    return lines or ["No rule changes since the window started."]


def _timeline_files() -> list[Path]:
    return sorted(RESULTS.glob("timeline_*.json"))


def _fmt_point(p: Path, d: dict) -> str:
    s = d.get("summary", {})
    n_cases, n_errors = s.get("n_cases"), s.get("n_errors")
    ratio = (n_errors / n_cases) if isinstance(n_cases, (int, float)) and n_cases else None
    flag = ""
    if ratio is not None and ratio > 0.2:
        flag = f" — inconclusive: {n_errors}/{n_cases} runs errored ({ratio:.0%})"
    return (f"repetition rate {s.get('mistake_repetition_rate', 'no data')}, "
            f"false-completion rate {s.get('false_completion_rate', 'no data')} "
            f"(`{p.name}`){flag}")


def _did_it_help() -> list[str]:
    files = _timeline_files()
    if len(files) < 2:
        return ["No data (need at least two results/timeline_*.json files; found "
                 f"{len(files)})."]
    before_p, after_p = files[-2], files[-1]
    try:
        before = json.loads(before_p.read_text(encoding="utf-8"))
        after = json.loads(after_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["No data (a timeline artifact could not be read)."]
    return [
        f"- Before: {_fmt_point(before_p, before)}",
        f"- After: {_fmt_point(after_p, after)}",
    ]


def _open_problems() -> list[str]:
    files = _timeline_files()
    if not files:
        return ["No data (no results/timeline_*.json files yet)."]
    latest = files[-1]
    try:
        d = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [f"No data (`{latest.name}` could not be read)."]
    per_family = d.get("per_family", {})
    bad = sorted(((f, s.get("repetition_rate")) for f, s in per_family.items()
                  if isinstance(s.get("repetition_rate"), (int, float)) and s["repetition_rate"] > 0),
                 key=lambda kv: -kv[1])
    if not bad:
        return [f"None — every family's latest repetition rate is 0 (`{latest.name}`)."]
    parts = "; ".join(f"{_plain(f)} (still repeats {rate:.0%} of the time)" for f, rate in bad)
    return [f"- {parts} (from `{latest.name}`)."]


def _bugs_caught_in_review() -> list[str]:
    """Findings the Qodo PR reviewer caught in our own code, imported by harness/qodo_findings.py.

    Reads only files on disk: the observations already in the ledger (import_key 'qodo:*') and
    results/qodo_rule_proposals.json. Says "no data" rather than guessing when either is absent.
    """
    records = _read_ledger()
    qodo_obs = [r for r in records
                if r.get("kind") == "observation" and str(r.get("import_key", "")).startswith("qodo:")]
    if not qodo_obs:
        return ["No data (no Qodo review findings imported yet; run "
                "`python -m harness.qodo_findings --propose`)."]

    per_family: dict[str, int] = {}
    for r in qodo_obs:
        fam = str(r.get("family", "?"))
        per_family[fam] = per_family.get(fam, 0) + 1
    ranked = sorted(per_family.items(), key=lambda kv: (-kv[1], kv[0]))
    families = "; ".join(f"{_plain(f)}: {n}" for f, n in ranked)

    lines = [f"- {len(qodo_obs)} finding(s) the code reviewer caught in our own pull requests, "
             f"imported as observations (from `ledger/ledger.jsonl`).",
             f"- Grouped by kind of mistake — {families}."]

    prop_path = RESULTS / "qodo_rule_proposals.json"
    if not prop_path.exists():
        lines.append("- No candidate rules proposed yet (no `results/qodo_rule_proposals.json`).")
        return lines
    try:
        doc = json.loads(prop_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        lines.append(f"- No data (`{prop_path.name}` could not be read).")
        return lines
    proposals = doc.get("proposals") or []
    if not proposals:
        lines.append(f"- No candidate rules yet: no kind of mistake reached the minimum of "
                     f"{doc.get('min_findings_per_family', '?')} findings (`{prop_path.name}`).")
        return lines
    lines.append(f"- {len(proposals)} candidate rule(s) suggested, none of them live yet — each "
                 f"still has to pass testing and your approval (`{prop_path.name}`):")
    for p in proposals[:5]:
        lines.append(f"    - {_plain(str(p.get('family', '?')))} "
                     f"({p.get('n_findings', '?')} findings): {_quote(str(p.get('rule_text', '')), 150)}")
    return lines


def build(since: str) -> str:
    since_epoch = _to_epoch(since)
    records = _read_ledger()
    lessons, correction_family = _lesson_maps(records)
    today = time.strftime("%Y-%m-%d", time.gmtime())

    lines = [f"# Morning digest — {today}", ""]
    lines.append(f"_Covering everything since {since} (UTC)._")
    lines.append("")
    lines.append("## What we learned")
    lines += _what_we_learned(records, since_epoch, correction_family)
    lines.append("")
    lines.append("## Rules added / merged / retired")
    lines += _rules_added_merged_retired(records, since_epoch, lessons)
    lines.append("")
    lines.append("## Bugs caught in review")
    lines += _bugs_caught_in_review()
    lines.append("")
    lines.append("## Did it help?")
    lines += _did_it_help()
    lines.append("")
    lines.append("## Open problems")
    lines += _open_problems()
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Write today's plain-language morning digest.")
    ap.add_argument("--since", default=None, help="ISO 8601 UTC timestamp, e.g. 2026-08-28T21:00:00Z. "
                                                    "Defaults to 24 hours before now.")
    args = ap.parse_args(argv)
    since = args.since or _default_since()

    text = build(since)
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DIGEST_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime())}.md"
    out_path.write_text(text, encoding="utf-8")
    print(f"wrote {out_path.relative_to(ROOT)} ({len(text.splitlines())} lines)")


if __name__ == "__main__":
    main()
