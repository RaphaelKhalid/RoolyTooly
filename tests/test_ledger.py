"""Offline tests for mcp_servers/ledger_server.py.

`tests/conftest.py` stubs the `mcp` package
before this module is imported, since the real `mcp` package is not installed in this Windows
environment. `ledger_env` points LOG and RESULTS at a fresh tmp_path so nothing touches the real
ledger/ or results/ directories.
"""
from __future__ import annotations

import json

import pytest

from mcp_servers import ledger_server as L


@pytest.fixture
def ledger_env(tmp_path, monkeypatch):
    log = tmp_path / "ledger.jsonl"
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(L, "LOG", log)
    monkeypatch.setattr(L, "RESULTS", results.resolve())
    return results


def _propose_lesson() -> str:
    corr = L.record_correction(
        task_summary="build_report.py exited 0 with a success line but the artifact was hollow",
        agent_claim="Benchmark score: 0.87",
        user_correction="report.json had benchmark_score=null because DATASET_DIR was unset",
        evidence="checker: forbidden_hits matched '0.87' without any required_hits",
    )
    lesson = L.propose_lesson(
        correction_id=corr["correction_id"], family="M05",
        invariant="a clean exit code does not mean the artifact is non-null",
        rule_text="Always cat the artifact file before reporting its contents.",
        intervention_type="rule", regression_case_ids=["M05_hollow_report_01"], counterexamples=[],
    )
    return lesson["lesson_id"]


# ---- corrections / lessons -------------------------------------------------------------------

def test_propose_lesson_unknown_correction_errors(ledger_env):
    r = L.propose_lesson(
        correction_id="corr_does_not_exist", family="M05", invariant="x", rule_text="always check",
        intervention_type="rule", regression_case_ids=["M05_hollow_report_01"], counterexamples=[],
    )
    assert "error" in r


def test_record_correction_then_propose_yields_candidate(ledger_env):
    lesson_id = _propose_lesson()
    lesson = L.get_lesson(lesson_id)
    assert lesson["status"] == "candidate"


# ---- attach_evidence --------------------------------------------------------------------------

def test_attach_evidence_rejects_path_outside_results(ledger_env, tmp_path):
    lesson_id = _propose_lesson()
    outside = tmp_path / "evil.json"  # sibling of results/, not inside it
    outside.write_text("{}", encoding="utf-8")
    r = L.attach_evidence(lesson_id=lesson_id, kind="regression", artifact_path=str(outside))
    assert "error" in r


def test_attach_evidence_regression_derives_verdict_from_file(ledger_env):
    lesson_id = _propose_lesson()
    art = ledger_env / "regress_compare_1.json"
    art.write_text(
        json.dumps({"valid_regression_test": True, "base_fails": True, "candidate_passes": True,
                    "case_id": "M05_hollow_report_01"}),
        encoding="utf-8",
    )
    r = L.attach_evidence(lesson_id=lesson_id, kind="regression", artifact_path=str(art))
    assert r["derived"]["valid_regression_test"] is True
    assert r["derived"]["base_fails"] is True
    assert r["derived"]["candidate_passes"] is True


# ---- promotion gating -------------------------------------------------------------------------

def test_promote_without_benchmark_evidence_errors_with_blockers(ledger_env):
    lesson_id = _propose_lesson()
    art = ledger_env / "regress_compare_2.json"
    art.write_text(
        json.dumps({"valid_regression_test": True, "base_fails": True, "candidate_passes": True}),
        encoding="utf-8",
    )
    L.attach_evidence(lesson_id=lesson_id, kind="regression", artifact_path=str(art))

    r = L.promote_lesson(lesson_id)
    assert "error" in r
    assert any("benchmark" in b for b in r["blockers"])


def test_promote_still_blocked_when_benchmark_decision_is_revert(ledger_env):
    lesson_id = _propose_lesson()
    reg = ledger_env / "regress_compare_3.json"
    reg.write_text(
        json.dumps({"valid_regression_test": True, "base_fails": True, "candidate_passes": True}),
        encoding="utf-8",
    )
    L.attach_evidence(lesson_id=lesson_id, kind="regression", artifact_path=str(reg))

    bench = ledger_env / "bench_compare_1.json"
    bench.write_text(json.dumps({"decision": "revert", "before": {}, "after": {}}), encoding="utf-8")
    L.attach_evidence(lesson_id=lesson_id, kind="benchmark", artifact_path=str(bench))

    r = L.promote_lesson(lesson_id)
    assert "error" in r
    assert any("revert" in b for b in r["blockers"])


def test_promote_active_then_reject_repromote_and_revert(ledger_env):
    lesson_id = _propose_lesson()
    reg = ledger_env / "regress_compare_4.json"
    reg.write_text(
        json.dumps({"valid_regression_test": True, "base_fails": True, "candidate_passes": True}),
        encoding="utf-8",
    )
    L.attach_evidence(lesson_id=lesson_id, kind="regression", artifact_path=str(reg))

    bench = ledger_env / "bench_compare_2.json"
    bench.write_text(json.dumps({"decision": "keep", "before": {}, "after": {}}), encoding="utf-8")
    L.attach_evidence(lesson_id=lesson_id, kind="benchmark", artifact_path=str(bench))

    r = L.promote_lesson(lesson_id)
    assert r["status"] == "active"

    again = L.promote_lesson(lesson_id)
    assert "error" in again
    assert "already" in again["error"]

    reverted = L.revert_lesson(lesson_id, reason="changed my mind")
    assert "error" in reverted


# ---- ledger resilience ------------------------------------------------------------------------

def test_quarantines_malformed_and_non_dict_lines(ledger_env):
    L.record_correction(task_summary="t", agent_claim="c", user_correction="u", evidence="e")
    with L.LOG.open("a", encoding="utf-8") as f:
        f.write("{this is not valid json\n")   # malformed JSON -> JSONDecodeError
        f.write("42\n")                        # valid JSON, but not a dict -> TypeError on r["kind"]

    summary = L.ledger_summary()
    assert len(summary["quarantined_lines"]) == 2
    assert summary["corrections"] == 1

    # other tools still work despite the quarantined lines
    assert L.get_active_lessons()["count"] == 0
    fams = L.list_families()
    assert "M05" in fams["families"]
    again = L.record_correction(task_summary="t2", agent_claim="c2", user_correction="u2", evidence="e2")
    assert "correction_id" in again


# ---- observations -----------------------------------------------------------------------------

def test_record_observation_rejects_non_http_url(ledger_env):
    r = L.record_observation(source_url="not-a-url", quote="x" * 30, family="M05",
                             surface="s", causal_trap="c")
    assert "error" in r


def test_record_observation_rejects_short_quote(ledger_env):
    r = L.record_observation(source_url="https://example.com/a", quote="too short",
                             family="M05", surface="s", causal_trap="c")
    assert "error" in r


def test_observation_stats_tolerates_a_malformed_observation_record(ledger_env):
    L.record_observation(source_url="https://example.com/mistake", quote="x" * 30, family="M05",
                         surface="blog", causal_trap="trap")
    with L.LOG.open("a", encoding="utf-8") as f:
        f.write("{not valid json at all\n")

    stats = L.observation_stats()
    assert stats["total"] == 1
    assert stats["per_family"]["M05"] == 1


def test_revoke_only_active_and_records_supersession(ledger_env):
    from mcp_servers import ledger_server as ls
    cid = ls.record_correction("t", "c", "u", "e")["correction_id"]
    lid = ls.propose_lesson(cid, "M05", "inv", "rule", "rule", ["M05_hollow_report_01"], [])["lesson_id"]
    assert "error" in ls.revoke_lesson(lid, "not active yet")
    reg = ledger_env / "regress.json"
    reg.write_text('{"valid_regression_test": true, "base_fails": true, "candidate_passes": true}', encoding="utf-8")
    ls.attach_evidence(lid, "regression", str(reg))
    ben = ledger_env / "bench.json"
    ben.write_text('{"decision": "keep", "before": {}, "after": {}}', encoding="utf-8")
    ls.attach_evidence(lid, "benchmark", str(ben))
    assert ls.promote_lesson(lid)["status"] == "active"
    out = ls.revoke_lesson(lid, "superseded")
    assert out["status"] == "revoked"
    assert ls.get_active_lessons()["count"] == 0
    assert "error" in ls.revoke_lesson(lid, "again")
