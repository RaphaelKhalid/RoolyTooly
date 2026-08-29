"""Offline tests for mcp_servers/eval_server.py's pure functions: decide, candidate_manifest, and
_load_jobs. `tests/conftest.py` stubs the `mcp` package before this module is imported, since the
real `mcp` package is not installed in this Windows environment.
"""
from __future__ import annotations

import json

from mcp_servers import eval_server as E


def _summary(**overrides) -> dict:
    base = {"mean_score": 60.0, "mistake_repetition_rate": 0.5, "false_completion_rate": 0.1,
            "control_pass_rate": 0.9, "refusal_rate": 0.05, "evidence_rate": 0.8}
    base.update(overrides)
    return base


def test_decide_keeps_when_repetition_drops_and_controls_hold():
    before = _summary()
    after = _summary(mistake_repetition_rate=0.2, mean_score=70.0)
    d = E.decide(before, after)
    assert d["decision"] == "keep"
    assert d["reasons"] == []


def test_decide_reverts_when_controls_regress():
    before = _summary()
    after = _summary(control_pass_rate=0.7, mistake_repetition_rate=0.1)
    d = E.decide(before, after)
    assert d["decision"] == "revert"
    assert any("control" in r for r in d["reasons"])


def test_decide_reverts_when_repetition_unchanged_even_if_mean_score_rose():
    # The M22 rule: a higher mean score alone is not immunity if repetition of the corrected
    # mistake did not actually improve.
    before = _summary(mistake_repetition_rate=0.3, mean_score=60.0)
    after = _summary(mistake_repetition_rate=0.3, mean_score=90.0)
    d = E.decide(before, after)
    assert d["decision"] == "revert"
    assert any("repetition" in r for r in d["reasons"])


def test_decide_keeps_when_repetition_stays_zero_and_score_improves():
    before = _summary(mistake_repetition_rate=0.0, mean_score=50.0)
    after = _summary(mistake_repetition_rate=0.0, mean_score=65.0)
    d = E.decide(before, after)
    assert d["decision"] == "keep"
    assert d["reasons"] == []


def test_decide_reverts_when_refusal_rate_increases():
    before = _summary()
    after = _summary(refusal_rate=0.2, mistake_repetition_rate=0.1)
    d = E.decide(before, after)
    assert d["decision"] == "revert"
    assert any("refusal" in r for r in d["reasons"])


def test_candidate_manifest_appends_learned_lessons_section():
    m = E.candidate_manifest("always check the artifact before claiming success")
    assert "## Learned lessons" in m["instructions"]
    assert "always check the artifact before claiming success" in m["instructions"]


def test_candidate_manifest_adds_skill_entry():
    m = E.candidate_manifest("", "skill-x")
    assert {"name": "skill-x"} in m["skills"]


def test_load_jobs_marks_a_running_job_lost(tmp_path, monkeypatch):
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(
        json.dumps({"job_abc": {"status": "running", "label": "regression:x", "started": 0.0}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(E, "JOBS_FILE", jobs_file)
    monkeypatch.setattr(E, "JOBS", {})

    E._load_jobs()

    assert E.JOBS["job_abc"]["status"] == "lost"
    assert "error" in E.JOBS["job_abc"]
