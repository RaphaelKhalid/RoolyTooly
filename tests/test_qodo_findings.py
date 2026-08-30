"""Offline tests for the Qodo-review mistake channel (harness/qodo_findings.py).

No network: every comment here is canned JSON shaped like a real
`repos/<repo>/pulls/comments` / `issues/comments` payload (the badge <img>, the numbered title
with <code> chips, the <pre> body, the "Agent Prompt" <details> block).
"""
from __future__ import annotations

import importlib
import json
import sys

import pytest

from harness import qodo_findings as qf


def _pr_comment(cid: int, title: str, body: str, *, pr: int = 7, severity: str = "High",
                category: str = "Bug", path: str = "harness/thing.py", line: int = 12,
                login: str = "qodo-code-review[bot]") -> dict:
    return {
        "id": cid,
        "user": {"login": login},
        "path": path,
        "line": line,
        "original_line": line,
        "html_url": f"https://github.com/RaphaelKhalid/RoolyTooly/pull/{pr}#discussion_r{cid}",
        "pull_request_url": f"https://api.github.com/repos/RaphaelKhalid/RoolyTooly/pulls/{pr}",
        "created_at": "2026-08-20T10:00:00Z",
        "body": (
            f'<img src="https://img.shields.io/badge/{severity}-634FD1?style=flat-square" '
            f'height="20px" alt="Action required">\n\n'
            f'1\\. {title} <code>\U0001f41e {category}</code> <code>≡ Correctness</code>\n\n'
            f'<pre>\n{body}\n</pre>\n\n'
            '<details>\n<summary><strong>Agent Prompt</strong></summary>\n\n'
            '```\n## Issue description\nignore me, I am the agent prompt\n```\n</details>'
        ),
    }


def _clean_summary_comment(cid: int = 9001, pr: int = 7) -> dict:
    """Qodo's PR-level 'no issues found' summary: real comment, but carries no finding."""
    return {
        "id": cid,
        "user": {"login": "qodo-code-review[bot]"},
        "html_url": f"https://github.com/RaphaelKhalid/RoolyTooly/pull/{pr}#issuecomment-{cid}",
        "issue_url": f"https://api.github.com/repos/RaphaelKhalid/RoolyTooly/issues/{pr}",
        "created_at": "2026-08-20T10:05:00Z",
        "body": ("\n<h3>Code Review by Qodo</h3>\n<code>\U0001f41e Bugs (0)</code>\n\n"
                 "<h3>Great, no issues found!</h3>\nQodo reviewed your code and found no material "
                 "issues that require review\n"),
    }


# ---- parsing ---------------------------------------------------------------------------------

def test_parse_extracts_title_body_location_and_severity():
    c = _pr_comment(111, "Failed commands count as execution",
                    "Program execution is inferred only from submitted command strings, without\n"
                    "checking the corresponding tool responses.")
    f = qf.parse_comment(c)
    assert f is not None
    assert f["title"] == "Failed commands count as execution"
    assert "inferred only from submitted command strings" in f["body"]
    assert f["comment_id"] == 111
    assert f["import_key"] == "qodo:111"
    assert f["pr_number"] == 7
    assert f["path"] == "harness/thing.py"
    assert f["line"] == 12
    assert f["severity"] == "High"
    assert f["category"] == "Bug"
    assert f["html_url"].startswith("https://github.com/")
    # The quote the ledger stores must be a real excerpt, capped at 300 chars.
    assert f["title"] in f["quote"] and 20 <= len(f["quote"]) <= 300


def test_parse_drops_the_agent_prompt_block_and_badge_chips():
    f = qf.parse_comment(_pr_comment(112, "Stale index hides lessons", "The index is never rebuilt."))
    assert "agent prompt" not in f["body"].lower()
    assert "ignore me" not in f["body"]
    # Qodo's <code> severity/category chips are metadata, not finding text.
    assert "Bug" not in f["title"]
    assert "Correctness" not in f["title"]


def test_parse_ignores_non_qodo_authors_and_findingless_summaries():
    human = _pr_comment(113, "Looks good", "nice", login="RaphaelKhalid")
    assert qf.parse_comment(human) is None
    assert qf.parse_comment(_clean_summary_comment()) is None
    assert qf.parse_comment({"id": 1, "user": {"login": "qodo-code-review[bot]"}, "body": ""}) is None


def test_findings_from_comments_filters_and_sorts():
    comments = [
        _pr_comment(220, "B finding", "the artifact is deleted before evaluation", pr=9),
        _pr_comment(210, "A finding", "the cached value is stale", pr=8),
        _clean_summary_comment(230, pr=9),
        _pr_comment(240, "human note", "not a bot", pr=9, login="someone"),
    ]
    findings = qf.findings_from_comments(comments)
    assert [f["comment_id"] for f in findings] == [210, 220]  # sorted by PR then id, Qodo only


def test_redaction_of_secrets_in_the_quote():
    f = qf.parse_comment(_pr_comment(114, "Token leaked in the log line",
                                     "The handler logs ghp_abcdef1234567890 verbatim to stdout."))
    assert "ghp_abcdef1234567890" not in f["body"]
    assert "[REDACTED]" in f["body"]


# ---- classification --------------------------------------------------------------------------

@pytest.mark.parametrize("text,family", [
    ("The cleanup step deletes the results directory before the checker reads it", "M07"),
    ("A hardcoded placeholder token is shipped in the request path", "M20"),
    ("The exception is swallowed so the failure passes silently", "M05"),
    ("The dashboard serves a stale cached snapshot", "M09"),
    ("Path resolution assumes the working directory of the caller", "M10"),
    ("This change has no test covering the new branch and risks a regression", "M14"),
    ("The rate uses the wrong denominator and inflates the score", "M13"),
    ("The row is rendered from index zero so later results are hidden in the UI", "M23"),
])
def test_classify_maps_known_phrasing_to_the_right_family(text, family):
    assert qf.classify(text) == family


def test_classify_returns_unclassified_when_nothing_matches():
    assert qf.classify("Consider renaming this variable to something shorter") == qf.UNCLASSIFIED
    assert qf.classify("") == qf.UNCLASSIFIED


def test_every_family_the_classifier_can_emit_has_a_candidate_rule():
    emitted = {fam for fam, _ in qf.FAMILY_KEYWORDS} | {qf.UNCLASSIFIED}
    assert emitted <= set(qf.FAMILY_RULE)


# ---- ledger import ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_ledger(tmp_path, monkeypatch):
    """A ledger_server bound to a temp ROOLY_LEDGER_DIR (it reads the env at import time)."""
    monkeypatch.setenv("ROOLY_LEDGER_DIR", str(tmp_path / "ledger"))
    qf._install_fake_mcp()
    import mcp_servers.ledger_server as ls
    ls = importlib.reload(ls)
    monkeypatch.setattr(qf, "ledger_module", lambda: ls)
    yield ls
    monkeypatch.delenv("ROOLY_LEDGER_DIR", raising=False)
    sys.modules.pop("mcp_servers.ledger_server", None)


def _records(ls) -> list[dict]:
    return [json.loads(x) for x in ls.LOG.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_import_writes_observations_through_record_observation(tmp_ledger):
    findings = qf.findings_from_comments([
        _pr_comment(301, "Evidence removed too early", "The cleanup deletes the artifact before the checker reads it"),
        _pr_comment(302, "Stale value served", "The dashboard serves a stale cached snapshot of the ledger"),
    ])
    res = qf.import_findings(findings)
    assert res["imported"] == 2 and res["skipped_duplicates"] == 0 and res["errors"] == []
    recs = _records(tmp_ledger)
    assert len(recs) == 2
    assert {r["kind"] for r in recs} == {"observation"}
    assert {r["import_key"] for r in recs} == {"qodo:301", "qodo:302"}
    assert {r["family"] for r in recs} == {"M07", "M09"}
    for r in recs:
        assert r["source_url"].startswith("https://github.com/")
        assert len(r["quote"]) >= 20
        assert "PR #7" in r["surface"]


def test_import_is_idempotent_across_runs(tmp_ledger):
    findings = qf.findings_from_comments([
        _pr_comment(401, "Evidence removed too early", "The cleanup deletes the artifact first"),
    ])
    first = qf.import_findings(findings)
    second = qf.import_findings(findings)
    assert first["imported"] == 1
    assert second["imported"] == 0 and second["skipped_duplicates"] == 1
    assert len(_records(tmp_ledger)) == 1


def test_dry_run_writes_nothing(tmp_ledger):
    findings = qf.findings_from_comments([
        _pr_comment(501, "Stale value served", "The dashboard serves a stale cached snapshot"),
    ])
    res = qf.import_findings(findings, dry_run=True)
    assert res["dry_run"] is True and res["imported"] == 1
    assert not tmp_ledger.LOG.exists() or _records(tmp_ledger) == []


def test_unclassified_findings_land_in_their_own_bucket_not_a_guessed_family(tmp_ledger):
    findings = qf.findings_from_comments([
        _pr_comment(601, "Rename this helper", "Consider a shorter name for this local variable"),
    ])
    assert findings[0]["family"] == qf.UNCLASSIFIED
    qf.import_findings(findings)
    assert _records(tmp_ledger)[0]["family"] == "NEW:qodo-unclassified"


# ---- proposals -------------------------------------------------------------------------------

def test_propose_rules_groups_families_with_at_least_two_findings():
    comments = [
        _pr_comment(701, "Evidence removed", "The cleanup deletes the artifact before evaluation", pr=3),
        _pr_comment(702, "Results wiped", "The script overwrites the results file on every run", pr=4),
        _pr_comment(703, "One-off stale read", "A stale cached value is returned", pr=4),
    ]
    doc = qf.propose_rules(qf.findings_from_comments(comments))
    families = {p["family"]: p for p in doc["proposals"]}
    assert set(families) == {"M07"}          # M09 has only one finding, so no rule is proposed
    m07 = families["M07"]
    assert m07["n_findings"] == 2
    assert m07["status"] == "candidate"      # never auto-promoted
    assert len(m07["supporting_comment_urls"]) == 2
    assert all(u.startswith("https://github.com/") for u in m07["supporting_comment_urls"])
    assert m07["prs"] == [3, 4]
    # The rule must be generalized: one imperative sentence, no project nouns.
    assert m07["rule_text"] == qf.FAMILY_RULE["M07"]
    for noun in ("RoolyTooly", "qodo", ".py", "PR #"):
        assert noun.lower() not in m07["rule_text"].lower()
    assert doc["per_family"] == {"M07": 2, "M09": 1}


def test_propose_rules_honours_a_higher_threshold():
    comments = [_pr_comment(801 + i, "Evidence removed", "deletes the artifact") for i in range(2)]
    findings = qf.findings_from_comments(comments)
    assert qf.propose_rules(findings, min_findings=3)["proposals"] == []
    assert len(qf.propose_rules(findings, min_findings=2)["proposals"]) == 1


def test_write_and_load_proposals_round_trip(tmp_path):
    doc = qf.propose_rules(qf.findings_from_comments([
        _pr_comment(901, "Evidence removed", "deletes the artifact before evaluation"),
        _pr_comment(902, "Results wiped", "overwrites the results file"),
    ]))
    p = tmp_path / "qodo_rule_proposals.json"
    qf.write_proposals(doc, p)
    assert qf.load_proposals(p) == doc
    assert qf.load_proposals(tmp_path / "missing.json")["proposals"] == []
