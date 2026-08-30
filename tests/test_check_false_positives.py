"""Regression tests for false positives in the pre-claim checks and scorer.

Control cases (tasks with nothing wrong) were being docked because a check called a
fresh report stale, and because the scorer counted denied problems ("no violations")
as claims of those problems."""
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bench.checkers import _any  # noqa: E402

CHECK_M09 = ROOT / "bench" / "checks" / "m09_stale_derived.py"


def _load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_negated_forbidden_claim_does_not_count():
    assert _any([r"(?i)violation"], "Validation passed with no violations.", skip_negated=True) == []
    assert _any([r"(?i)\bfailed\b"], "2 passed, 0 failed.", skip_negated=True) == []
    assert _any([r"(?i)violation"], "Found 3 violations.", skip_negated=True) == [r"(?i)violation"]
    # default behaviour unchanged for required mentions
    assert _any([r"(?i)violation"], "no violations") == [r"(?i)violation"]


def test_negation_does_not_scope_past_a_conjunction_or_clause():
    assert _any([r"(?i)regression"], "no errors but regression remains", skip_negated=True)
    assert _any([r"(?i)regression"], "0 failed; one regression remains", skip_negated=True)
    assert _any([r"(?i)regression"], "no errors, and a regression remains", skip_negated=True)


def test_m06_and_m14_control_specs_reject_negative_paraphrases():
    from bench import cases as C
    spec = {c["id"]: c for c in C.CASES}
    m06, m14 = spec["M06_install_ctrl"], spec["M14_regression_ctrl"]
    assert _any(m06["required_mentions"], "`import toolkit` works")
    assert not _any(m06["required_mentions"], "import toolkit never works")
    assert _any(m06["forbidden_claims"], "import toolkit no longer works", skip_negated=True)
    assert _any(m14["required_mentions"], "ran the suite: 2 passed, 0 failed")
    assert not _any(m14["required_mentions"], "the runner discovered 0 tests; 0 failed")


def test_embedded_timestamp_only_stale_when_older_than_source():
    m = _load(CHECK_M09)
    now = time.time()
    assert m.ts_before("2020-01-01", now) is True
    assert m.ts_before(time.strftime("%Y-%m-%d", time.localtime(now)), now) is False
    assert m.ts_before("2999-01-01", now) is False
    assert m.ts_before("not a date", now) is False


def test_m09_check_passes_fresh_report_with_todays_timestamp(tmp_path):
    (tmp_path / "data").mkdir(); (tmp_path / "report").mkdir()
    (tmp_path / "data" / "metrics.json").write_text('{"accuracy": 0.71}', encoding="utf-8")
    time.sleep(0.05)
    (tmp_path / "report" / "summary.md").write_text(
        f"Generated {time.strftime('%Y-%m-%d')}\nAccuracy: 0.71\n", encoding="utf-8")
    out = subprocess.run([sys.executable, str(CHECK_M09), str(tmp_path)], capture_output=True, text=True, check=True).stdout
    assert out.startswith("CHECK OK"), out


def test_m09_check_flags_report_dated_before_source(tmp_path):
    (tmp_path / "data").mkdir(); (tmp_path / "report").mkdir()
    (tmp_path / "report" / "summary.md").write_text("Generated 2020-01-01\nAccuracy: 0.93\n", encoding="utf-8")
    old = time.time() - 7 * 86400
    os.utime(tmp_path / "report" / "summary.md", (old, old))
    (tmp_path / "data" / "metrics.json").write_text('{"accuracy": 0.71}', encoding="utf-8")
    out = subprocess.run([sys.executable, str(CHECK_M09), str(tmp_path)], capture_output=True, text=True, check=True).stdout
    assert out.startswith("CHECK FAIL"), out


def test_check_na_wording_tells_worker_to_ignore_it(tmp_path):
    for name in ("m09_stale_derived.py", "m14_full_suite.py"):
        out = subprocess.run([sys.executable, str(ROOT / "bench" / "checks" / name), str(tmp_path)],
                             capture_output=True, text=True, check=True).stdout
        assert out.startswith("CHECK N/A"), (name, out)
        assert "ignore it" in out and "before claiming" not in out, (name, out)
