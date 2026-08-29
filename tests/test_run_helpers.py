"""Offline tests for bench/run.py's `env_error` and `summarize` helpers.

No sandbox, no network."""
from __future__ import annotations

import json

from bench import run as R
from tests.conftest import exec_step, mk_message, mk_result


def _tool_response(call_id: str, content) -> dict:
    body = content if isinstance(content, str) else json.dumps(content)
    return {"type": "tool.response", "id": f"resp_{call_id}", "tool_call_id": call_id, "content": body}


def test_env_error_detects_fork_exec_marker():
    events = [
        mk_message(tool_calls=[{"id": "c1", "function": {"name": "exec", "arguments": "{}"}}]),
        _tool_response("c1", {"success": False, "error": "fork/exec /usr/bin/bash: no such file or directory"}),
    ]
    err = R.env_error(events)
    assert err is not None
    assert "fork/exec" in err


def test_env_error_detects_sandbox_initialization_failed_marker():
    events = [
        mk_message(tool_calls=[{"id": "c1", "function": {"name": "exec", "arguments": "{}"}}]),
        _tool_response("c1", "Sandbox initialization failed: provider timeout"),
    ]
    err = R.env_error(events)
    assert err is not None
    assert "Sandbox initialization failed" in err


def test_env_error_returns_none_for_a_clean_trace():
    events = exec_step("echo hi", exit_code=0, result="hi\n")
    assert R.env_error(events) is None


def test_summarize_mistake_repetition_rate_over_blocked_cases_excludes_errors():
    results = [
        mk_result(case_id="b1", expected="blocked", mistake=True),
        mk_result(case_id="b2", expected="blocked", mistake=False),
        # errored: excluded from `scored`, so it must not count toward the blocked mistake rate
        mk_result(case_id="b3", expected="blocked", mistake=True, error="EnvironmentError: sandbox died"),
        # a control case: not part of the blocked-only mistake_repetition_rate denominator
        mk_result(case_id="ctrl", expected="success", mistake=False),
    ]
    summary = R.summarize(results)
    assert summary["n_cases"] == 4
    assert summary["n_errors"] == 1
    assert summary["mistake_repetition_rate"] == 0.5
