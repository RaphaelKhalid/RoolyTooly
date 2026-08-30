"""Import the bugs Qodo's PR reviewer caught in THIS repository as ledger observations.

The Bright Data mining channel (Workflow C) turns other people's agent mistakes, found on the
web, into `observation` records. This module is the same channel pointed at ourselves: every
finding the Qodo PR reviewer raised on `RaphaelKhalid/RoolyTooly` is a real mistake this project
actually made, with a permanent URL and a verbatim quote, which is exactly what
`mcp_servers/ledger_server.py:record_observation` wants.

Usage:
    python -m harness.qodo_findings --dry-run          # print the table; touch nothing
    python -m harness.qodo_findings                    # import into the ledger (idempotent)
    python -m harness.qodo_findings --propose          # also write results/qodo_rule_proposals.json
    python -m harness.qodo_findings --dry-run --propose

Source: `gh api --paginate` (stdlib subprocess, no new dependencies) over
    repos/<repo>/pulls/comments?per_page=100     (inline review findings)
    repos/<repo>/issues/comments?per_page=100    (review summary comments)
filtered to authors matching /qodo/i.

Idempotency: each observation carries `import_key = "qodo:<comment_id>"`; a re-run skips any key
already present in the ledger, so importing twice is a no-op.

Honesty: the family classification here is a KEYWORD HEURISTIC over the finding's title and body.
These are review findings about harness code, not human corrections of a running agent, and the
family label is a guess that a human should sanity-check. Nothing is auto-promoted: `propose_rules`
emits CANDIDATE rule texts into results/qodo_rule_proposals.json, which still have to go through
the existing compile -> falsify -> regression -> benchmark -> approval path before they are live.
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import re
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPO = "RaphaelKhalid/RoolyTooly"
RESULTS = ROOT / "results"
PROPOSALS_PATH = RESULTS / "qodo_rule_proposals.json"

SECRET_RE = re.compile(r"(sk-|dtn_|ghp_|gho_|Bearer )[A-Za-z0-9_\-]+")
QODO_AUTHOR_RE = re.compile(r"qodo", re.IGNORECASE)


# ---- ledger access -----------------------------------------------------------------------------

def _install_fake_mcp() -> None:
    """Let this module import ledger_server on a machine without the `mcp` package.

    The harness is stdlib-only on Windows (see AGENTS.md) while `mcp_servers/ledger_server.py`
    imports FastMCP at module scope. Same trick tests/conftest.py uses: a decorator-shaped stub,
    installed ONLY when no real `mcp` is importable, so the ledger write path itself is the real
    `record_observation` function and not a reimplementation.
    """
    try:
        if importlib.util.find_spec("mcp") is not None:
            return
    except (ImportError, ValueError):
        pass

    class _Settings:
        def __init__(self, port=None):
            self.port = port

    class FastMCP:
        def __init__(self, *args, **kwargs):
            self.name = args[0] if args else kwargs.get("name")
            self.settings = _Settings(kwargs.get("port"))

        def tool(self, *_a, **_k):
            def _decorator(fn):
                return fn
            return _decorator

        def run(self, *_a, **_k):
            return None

    class ToolAnnotations:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    types_mod = types.ModuleType("mcp.types")
    fastmcp_mod.FastMCP = FastMCP
    types_mod.ToolAnnotations = ToolAnnotations
    server_mod.fastmcp = fastmcp_mod
    mcp_mod.server, mcp_mod.types = server_mod, types_mod
    sys.modules.setdefault("mcp", mcp_mod)
    sys.modules.setdefault("mcp.server", server_mod)
    sys.modules.setdefault("mcp.server.fastmcp", fastmcp_mod)
    sys.modules.setdefault("mcp.types", types_mod)


def ledger_module():
    """Import mcp_servers.ledger_server (honouring ROOLY_LEDGER_DIR, which it reads at import)."""
    _install_fake_mcp()
    import mcp_servers.ledger_server as ls  # noqa: PLC0415  (deliberately late: needs the shim)
    return ls


# ---- classification ----------------------------------------------------------------------------

# Keyword -> mistake family, checked in this order; first match wins. Families and their meanings
# come from FAMILIES in mcp_servers/ledger_server.py; the phrasing below is what Qodo's findings
# actually say when they hit that trap.
FAMILY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("M07", ("delete", "deletion", "destructive", "overwrit", "truncat", "purge", "rm -rf",
             "data loss", "clobber", "wipes", "removes the file", "unlink")),
    ("M20", ("placeholder", "hardcoded", "hard-coded", "dummy", "todo", "stubbed", "stub value",
             "fake value", "magic number", "sentinel")),
    ("M05", ("silently", "silent", "swallow", "suppress", "except: pass", "exception is ignored",
             "ignores the error", "ignored error", "empty result", "returns none", "returns null",
             "exit code is not checked", "without raising", "no error is raised", "fails quietly",
             "unhandled", "not checked", "unchecked", "no validation", "missing validation",
             "falls back", "fallback")),
    ("M09", ("stale", "cached", "cache", "outdated", "out of date", "not refreshed", "reload",
             "never invalidat", "old snapshot")),
    ("M10", ("cwd", "working directory", "relative path", "wrong path", "path resolution",
             "encoding", "cp1252", "utf-8", "windows", "platform", "environment variable",
             "permission", "file system", "os.path", "newline")),
    ("M14", ("regression", "breaks existing", "backward compat", "backwards compat",
             "breaking change", "missing test", "no test", "untested", "not covered by a test",
             "test coverage", "existing behaviour", "existing behavior")),
    ("M13", ("denominator", "percentage", "rate is", "average", "mean of", "double counted",
             "double-count", "divide by zero", "division by zero", "mixes", "mixed units",
             "inflates", "miscount", "off-by-one", "count is wrong")),
    # M23 also covers Qodo's docstring rule violations: a summary that overflows, omits, or
    # conflates what the unit does is a description that does not match the deliverable.
    ("M23", ("render", "display", "displayed", "shown", "ui ", "dashboard", "label", "chart",
             "the report shows", "presented", "tooltip", "legend",
             "docstring", "summary exceeds", "summary is incomplete", "summary lacks",
             "summary combines", "summary omits", "summary does not", "screenshot")),
    ("M03", ("proxy", "treated as success", "counts as", "treats any", "inferred from",
             "assumed to have", "considered passing", "marked as passing")),
    ("M06", ("claims success", "reports success", "marked complete", "reported as done",
             "declares", "asserts that it")),
    ("M12", ("out of scope", "unrelated", "scope", "side effect", "unintended change")),
    ("M21", ("not awaited", "never called", "dropped", "abandoned", "interrupt", "orphan",
             "leaked", "not cleaned up", "never closed")),
    ("M22", ("regressed again", "same mistake", "previously fixed", "reintroduc")),
    ("M17", ("unnecessary warning", "over-cautious", "hedge", "redundant guard")),
    # M11 is last and deliberately narrow: generic words like "bug" would swallow everything.
    ("M11", ("incorrect", "wrong", "mismatch", "misread", "misinterpret", "does not match",
             "logic error", "misdiagnos", "root cause")),
]

UNCLASSIFIED = "UNCLASSIFIED"


def classify(text: str) -> str:
    """Map a finding's text to a mistake family with a keyword heuristic. Never guesses blindly."""
    low = (text or "").lower()
    for fam, keywords in FAMILY_KEYWORDS:
        for kw in keywords:
            if kw in low:
                return fam
    return UNCLASSIFIED


# ---- parsing -----------------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_DETAILS_RE = re.compile(r"<details>.*?</details>", re.DOTALL)
_TITLE_RE = re.compile(r"^\s*(?:\*\*)?(.+?)(?:\*\*)?\s*$")
_SEVERITY_RE = re.compile(r"badge/(High|Medium|Low|Critical)-", re.IGNORECASE)
_CATEGORY_RE = re.compile(r"<code>[^<]*?(Bug|Rule violation|Requirement gap|Correctness|Security|"
                          r"Performance|Maintainability)[^<]*?</code>", re.IGNORECASE)
_PR_NUM_RE = re.compile(r"/(?:pull|issues)/(\d+)")
_NUMBERED_RE = re.compile(r"^\d+\\?\.\s+\S")


_CODE_TAG_RE = re.compile(r"<code>.*?</code>", re.DOTALL)


def _strip_html(s: str) -> str:
    s = _DETAILS_RE.sub(" ", s or "")
    # Qodo renders its severity/category badges as <code>...</code> chips appended to the title
    # line. Their text ("Bug", "Correctness", ...) is metadata, not the finding, and left in place
    # it makes every finding match the classifier's generic keywords. Severity and category are
    # captured separately from the raw body before this runs.
    s = _CODE_TAG_RE.sub(" ", s)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</(?:p|pre|div|li|h\d)>", "\n", s)
    s = _TAG_RE.sub("", s)
    return html.unescape(s)


def redact(text: str) -> str:
    return SECRET_RE.sub("[REDACTED]", text or "")


def _pr_number(comment: dict) -> int | None:
    for key in ("html_url", "pull_request_url", "issue_url", "url"):
        m = _PR_NUM_RE.search(str(comment.get(key) or ""))
        if m:
            return int(m.group(1))
    return None


def is_qodo(comment: dict) -> bool:
    login = ((comment or {}).get("user") or {}).get("login") or ""
    return bool(QODO_AUTHOR_RE.search(login))


def parse_comment(comment: dict) -> dict | None:
    """Turn one Qodo comment into a finding dict, or None if it carries no finding.

    Qodo inline findings look like:
        <img src=".../badge/High-...">   1\\. <title> <code>Bug</code> <code>Correctness</code>
        <pre> <body> </pre>
        <details><summary>Agent Prompt</summary> ... </details>
    The summary comments ("Great, no issues found!") have no numbered title and are dropped.
    """
    if not is_qodo(comment):
        return None
    raw = comment.get("body") or ""
    if not raw.strip():
        return None

    severity_m = _SEVERITY_RE.search(raw)
    category_m = _CATEGORY_RE.search(raw)
    text = _strip_html(raw)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None

    # Title: the first numbered ("1. ...") or first bold line; the body is everything after it.
    title, body_lines = "", []
    for i, ln in enumerate(lines):
        if _NUMBERED_RE.match(ln) or (ln.startswith("**") and ln.endswith("**") and len(ln) > 4):
            m = _TITLE_RE.match(re.sub(r"^\d+\\?\.\s*", "", ln))
            title = (m.group(1) if m else ln).strip()
            body_lines = lines[i + 1:]
            break
    if not title:
        return None  # no finding here (review summary, tip of the day, "no issues found")

    body = " ".join(body_lines).strip()
    # Drop Qodo's own boilerplate tails from the body.
    for marker in ("Tip of the day", "More tips", "Customize Qodo", "Qodo docs"):
        idx = body.find(marker)
        if idx > 0:
            body = body[:idx].strip()

    cid = comment.get("id")
    if cid is None:
        return None
    finding = {
        "comment_id": int(cid),
        "import_key": f"qodo:{cid}",
        "pr_number": _pr_number(comment),
        "path": comment.get("path"),
        "line": comment.get("line") if comment.get("line") is not None else comment.get("original_line"),
        "html_url": comment.get("html_url"),
        "created_at": comment.get("created_at"),
        "severity": (severity_m.group(1).title() if severity_m else "Unknown"),
        "category": (category_m.group(1) if category_m else "Unknown"),
        "title": redact(title)[:200],
        "body": redact(body)[:2000],
    }
    finding["family"] = classify(f"{finding['title']} {finding['body']}")
    finding["quote"] = (f"{finding['title']}. {finding['body']}").strip()[:300]
    return finding


# ---- fetching ----------------------------------------------------------------------------------

def _gh_api(endpoint: str) -> list[dict]:
    """`gh api --paginate <endpoint>` -> list of JSON objects. Tolerates concatenated arrays."""
    proc = subprocess.run(["gh", "api", "--paginate", endpoint],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"gh api {endpoint} failed ({proc.returncode}): {proc.stderr.strip()[:400]}")
    out = (proc.stdout or "").strip()
    if not out:
        return []
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        # Some gh versions emit one JSON array per page, concatenated.
        merged: list[dict] = []
        dec = json.JSONDecoder()
        pos = 0
        while pos < len(out):
            while pos < len(out) and out[pos] in " \r\n\t":
                pos += 1
            if pos >= len(out):
                break
            obj, pos = dec.raw_decode(out, pos)
            merged.extend(obj if isinstance(obj, list) else [obj])
        return merged


def fetch_comments(repo: str = REPO) -> list[dict]:
    """All review + issue comments on the repo's PRs (unfiltered; caller filters to Qodo)."""
    return (_gh_api(f"repos/{repo}/pulls/comments?per_page=100")
            + _gh_api(f"repos/{repo}/issues/comments?per_page=100"))


def findings_from_comments(comments: list[dict]) -> list[dict]:
    out = []
    for c in comments:
        f = parse_comment(c)
        if f:
            out.append(f)
    out.sort(key=lambda f: (f.get("pr_number") or 0, f["comment_id"]))
    return out


# ---- ledger import -----------------------------------------------------------------------------

def existing_import_keys(ls) -> set[str]:
    keys: set[str] = set()
    log = ls.LOG
    if not log.exists():
        return keys
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and r.get("import_key"):
            keys.add(str(r["import_key"]))
    return keys


def _surface(f: dict) -> str:
    loc = f.get("path") or "repo"
    if f.get("line"):
        loc = f"{loc}:{f['line']}"
    return f"Qodo {f.get('severity', '?')}/{f.get('category', '?')} on PR #{f.get('pr_number')} at {loc}"


def import_findings(findings: list[dict], dry_run: bool = False) -> dict:
    """Write each finding to the ledger via ledger_server.record_observation. Idempotent."""
    ls = ledger_module()
    known = existing_import_keys(ls)
    imported, skipped, errors = [], [], []
    for f in findings:
        if f["import_key"] in known:
            skipped.append(f["import_key"])
            continue
        # record_observation only accepts Mxx or NEW:<name>; keep unclassified findings in their
        # own visible bucket rather than forcing them into a family we do not believe.
        family = f["family"] if f["family"] != UNCLASSIFIED else "NEW:qodo-unclassified"
        url = (f.get("html_url") or "").strip()
        quote = f.get("quote") or ""
        if not re.match(r"^https?://\S+$", url) or len(quote.strip()) < 20:
            errors.append({"import_key": f["import_key"], "error": "missing url or quote too short"})
            continue
        if dry_run:
            imported.append(f["import_key"])
            known.add(f["import_key"])
            continue
        res = ls.record_observation(
            source_url=url, quote=quote, family=family, surface=_surface(f),
            causal_trap=(f.get("body") or f.get("title", ""))[:400],
            proposed_case=f"Seed a case that reproduces: {f.get('title', '')}"[:400],
            confidence=0.6, import_key=f["import_key"],
        )
        if isinstance(res, dict) and res.get("error"):
            errors.append({"import_key": f["import_key"], "error": res["error"]})
        else:
            imported.append(f["import_key"])
            known.add(f["import_key"])
    return {"total_findings": len(findings), "imported": len(imported),
            "skipped_duplicates": len(skipped), "errors": errors,
            "per_family": per_family_counts(findings), "dry_run": bool(dry_run)}


def per_family_counts(findings: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["family"]] = counts.get(f["family"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# ---- rule proposals ----------------------------------------------------------------------------

# One generalized, imperative candidate rule per family. Deliberately free of project nouns: a rule
# that names a file in this repo teaches nothing transferable. These are CANDIDATES only.
FAMILY_RULE = {
    "M03": "Before reporting success, verify the target outcome itself, not a side effect or proxy signal that merely correlates with it.",
    "M05": "After any command or write, open the resulting artifact and confirm it is non-empty and well-formed before treating the step as successful.",
    "M06": "Do not call work done until a tool result shows the requested outcome; the existence of a file is not evidence that it contains the deliverable.",
    "M07": "Preserve every artifact a check or a human may still need to read; never delete, overwrite, or regenerate evidence before it has been evaluated.",
    "M09": "Re-read state from its source before asserting anything about it, and never answer from a cached or previously computed value that may have changed.",
    "M10": "Resolve paths, encodings, and platform-specific behaviour explicitly rather than assuming the ambient environment, and check the environment before blaming the code.",
    "M11": "State the causal mechanism you verified for a failure and confirm it against the actual data flow before implementing a fix based on it.",
    "M12": "Change only what the request covers; when an adjacent problem appears, report it instead of fixing it unasked.",
    "M13": "Fix the denominator and the units before reporting any rate, and never combine counts drawn from different populations into one number.",
    "M14": "Run the whole existing test suite, not only the new test, and add a regression test that fails before the fix and passes after it.",
    "M17": "Perform the check rather than warning about it; a caveat is not a substitute for the verification it describes.",
    "M20": "Never ship a placeholder, hardcoded value, or stub in a code path that runs for real; make the missing input an explicit, loud failure instead.",
    "M21": "Track every commitment made earlier in the task and complete or explicitly withdraw it before finishing, especially after an interruption.",
    "M22": "Check the record of previous corrections before acting in a familiar situation, and do not repeat a mistake that already has a recorded rule.",
    "M23": "Ensure what is displayed or reported matches the underlying data exactly, including rows or cases the presentation layer would otherwise drop.",
    UNCLASSIFIED: "Classify this finding into a mistake family by hand before drafting a rule; an unclassified finding cannot produce a transferable invariant.",
}

MIN_FINDINGS_PER_FAMILY = 2


def propose_rules(findings: list[dict], min_findings: int = MIN_FINDINGS_PER_FAMILY) -> dict:
    """Group findings by family and emit candidate rule texts. Never promotes anything."""
    by_family: dict[str, list[dict]] = {}
    for f in findings:
        by_family.setdefault(f["family"], []).append(f)

    proposals = []
    for fam, group in sorted(by_family.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(group) < min_findings:
            continue
        proposals.append({
            "family": fam,
            "n_findings": len(group),
            "rule_text": FAMILY_RULE.get(fam, FAMILY_RULE[UNCLASSIFIED]),
            "intervention_type": "check",
            "status": "candidate",
            "supporting_comment_urls": [f["html_url"] for f in group if f.get("html_url")],
            "supporting_titles": [f["title"] for f in group][:10],
            "paths": sorted({f["path"] for f in group if f.get("path")})[:10],
            "prs": sorted({f["pr_number"] for f in group if f.get("pr_number")}),
        })
    return {
        "source": "qodo_pr_review",
        "repo": REPO,
        "min_findings_per_family": min_findings,
        "total_findings": len(findings),
        "per_family": per_family_counts(findings),
        "note": ("Candidate rules only. Family labels are keyword heuristics over Qodo review "
                 "findings about this repository's own code; nothing here is promoted. Each "
                 "proposal must still pass compile -> falsify -> regression -> benchmark -> "
                 "human approval before it becomes an active lesson."),
        "proposals": proposals,
    }


def write_proposals(doc: dict, path: Path = PROPOSALS_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return path


def load_proposals(path: Path = PROPOSALS_PATH) -> dict:
    """Read back the last written proposals document, or an empty shape if there is none."""
    if not path.exists():
        return {"proposals": [], "per_family": {}, "total_findings": 0,
                "note": "no proposals file yet; run harness/qodo_findings.py --propose"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"could not read {path.name}: {exc}", "proposals": []}


# ---- CLI ---------------------------------------------------------------------------------------

def _truncate(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "~"


def print_table(findings: list[dict]) -> None:
    if not findings:
        print("No Qodo findings found.")
        return
    header = f"{'PR':<5} {'family':<22} {'sev':<8} {'path':<32} {'title':<58}"
    print(header)
    print("-" * len(header))
    for f in findings:
        print(f"{str(f.get('pr_number') or '?'):<5} {f['family']:<22} {f.get('severity', '?'):<8} "
              f"{_truncate(f.get('path') or '-', 32):<32} {_truncate(f['title'], 58):<58}")
    print("\nCounts per family:")
    for fam, n in per_family_counts(findings).items():
        print(f"  {fam}: {n}")
    print(f"\nTotal: {len(findings)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the table; do not touch the ledger")
    ap.add_argument("--propose", action="store_true",
                    help="also write candidate rules to results/qodo_rule_proposals.json")
    ap.add_argument("--repo", default=REPO, help=f"owner/name (default {REPO})")
    ap.add_argument("--min-findings", type=int, default=MIN_FINDINGS_PER_FAMILY,
                    help="minimum findings in a family before a rule is proposed (default 2)")
    ap.add_argument("--comments-json", default=None,
                    help="read comments from this JSON file instead of calling gh (offline use)")
    args = ap.parse_args(argv)

    if args.comments_json:
        comments = json.loads(Path(args.comments_json).read_text(encoding="utf-8"))
    else:
        comments = fetch_comments(args.repo)
    findings = findings_from_comments(comments)

    if args.dry_run:
        print_table(findings)
    res = import_findings(findings, dry_run=args.dry_run)
    if not args.dry_run:
        print(f"Imported {res['imported']} observation(s); skipped {res['skipped_duplicates']} "
              f"duplicate(s); {len(res['errors'])} error(s).")
        print("Counts per family:")
        for fam, n in res["per_family"].items():
            print(f"  {fam}: {n}")
        for e in res["errors"][:10]:
            print(f"  ERROR {e['import_key']}: {e['error']}")

    if args.propose:
        doc = propose_rules(findings, args.min_findings)
        p = write_proposals(doc)
        print(f"\nWrote {len(doc['proposals'])} candidate rule proposal(s) to results/{p.name}")
        for pr in doc["proposals"]:
            print(f"  {pr['family']} (n={pr['n_findings']}): {_truncate(pr['rule_text'], 100)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
