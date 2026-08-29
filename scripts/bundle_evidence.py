"""Bundle a curated, committed evidence set into evidence/.

`results/` and `ledger/` are gitignored (they're large, churny, and full of scratch
runs) -- but the numbers in README.md and the dashboard need SOMETHING committed to
point at, or "every number needs an artifact path" is unverifiable from a checkout
that never ran the harness. This script copies a small, named subset into evidence/
(which IS committed) and writes evidence/INDEX.md describing what's there.

Selection rule:
  - results/timeline_*.json  -> ALL of them (they're the points of one time series;
    the dashboard's repetition/false-completion/control-pass graph needs every point).
  - every other pattern below -> only the LATEST match (lexicographic on filename,
    which sorts chronologically here since names embed a unix-ish timestamp) --
    these are one-shot comparisons/baselines where only the final result matters.

Stdlib only. Safe to re-run: it recreates evidence/ from scratch each time.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
LEDGER_SRC = ROOT / "ledger" / "ledger.jsonl"
JOBS_SRC = RESULTS / "jobs.json"
EVIDENCE = ROOT / "evidence"
INDEX = EVIDENCE / "INDEX.md"

MAX_BYTES = 2 * 1024 * 1024  # skip any single file bigger than this

# (glob pattern relative to results/, mode) -- mode is "all" or "latest"
PATTERNS: list[tuple[str, str]] = [
    ("timeline_*.json", "all"),
    ("bench_compare_*.json", "latest"),
    ("regress_compare_*.json", "latest"),
    ("sweep_*.json", "latest"),
    ("transfer_*.json", "latest"),
    ("full_base_*.json", "latest"),
    ("luna_high_base_*.json", "latest"),
    ("luna_hard_*.json", "latest"),
    ("luna_wild_*.json", "latest"),
    ("humaneval_plus_*.json", "latest"),
]


def _latest(paths: list[Path]) -> list[Path]:
    if not paths:
        return []
    return [sorted(paths, key=lambda p: p.name)[-1]]


def select_files() -> list[Path]:
    chosen: list[Path] = []
    for pattern, mode in PATTERNS:
        matches = sorted(RESULTS.glob(pattern), key=lambda p: p.name)
        chosen.extend(matches if mode == "all" else _latest(matches))
    return chosen


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _summarize(name: str, obj: Any) -> str:
    """Best-effort one-line summary for INDEX.md. Never invents a value."""
    if not isinstance(obj, dict):
        return ""
    # timeline_*.json
    if name.startswith("timeline_"):
        s = obj.get("summary") or {}
        return (f"label={obj.get('label')!r} ran_at={obj.get('ran_at')} "
                f"n_cases={s.get('n_cases')} n_errors={s.get('n_errors')} "
                f"mean_score={s.get('mean_score')} "
                f"mistake_repetition_rate={s.get('mistake_repetition_rate')} "
                f"n_active_lessons={obj.get('n_active_lessons')}")
    # transfer_*.json, full_base_*.json, luna_high_base_*.json, luna_hard_*.json, luna_wild_*.json
    if "summary" in obj and "manifest" in obj:
        s = obj.get("summary") or {}
        return (f"label={obj.get('label')!r} ran_at={obj.get('ran_at')} "
                f"n_cases={s.get('n_cases')} n_errors={s.get('n_errors')} "
                f"mean_score={s.get('mean_score')} "
                f"mistake_repetition_rate={s.get('mistake_repetition_rate')} "
                f"false_completion_rate={s.get('false_completion_rate')} "
                f"control_pass_rate={s.get('control_pass_rate')}")
    # bench_compare_*.json
    if "decision" in obj and "before" in obj and "after" in obj:
        return (f"decision={obj.get('decision')} delta={obj.get('delta')} "
                f"intervention_type={obj.get('intervention_type')} "
                f"reasons={obj.get('reasons')}")
    # regress_compare_*.json
    if "base_fails" in obj and "candidate_passes" in obj:
        return (f"case_id={obj.get('case_id')} base_fails={obj.get('base_fails')} "
                f"candidate_passes={obj.get('candidate_passes')} "
                f"valid_regression_test={obj.get('valid_regression_test')} "
                f"intervention_type={obj.get('intervention_type')}")
    # sweep_*.json
    if "winner_lesson_id" in obj or "family_closed" in obj:
        return (f"case_id={obj.get('case_id')} family={obj.get('family')} "
                f"trials={len(obj.get('trials') or [])} "
                f"winner_lesson_id={obj.get('winner_lesson_id')} "
                f"family_closed={obj.get('family_closed')}")
    return ""


def bundle() -> tuple[list[tuple[Path, int]], list[tuple[str, int]]]:
    """Returns (copied [(dest_path, size)], skipped [(name, size)])."""
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True)

    copied: list[tuple[Path, int]] = []
    skipped: list[tuple[str, int]] = []

    for src in select_files():
        size = src.stat().st_size
        if size > MAX_BYTES:
            skipped.append((f"results/{src.name}", size))
            continue
        dest = EVIDENCE / src.name
        shutil.copy2(src, dest)
        copied.append((dest, size))

    if LEDGER_SRC.exists():
        size = LEDGER_SRC.stat().st_size
        if size > MAX_BYTES:
            skipped.append(("ledger/ledger.jsonl", size))
        else:
            dest = EVIDENCE / "ledger.jsonl"
            shutil.copy2(LEDGER_SRC, dest)
            copied.append((dest, size))

    if JOBS_SRC.exists():
        size = JOBS_SRC.stat().st_size
        if size > MAX_BYTES:
            skipped.append(("results/jobs.json", size))
        else:
            dest = EVIDENCE / "jobs.json"
            shutil.copy2(JOBS_SRC, dest)
            copied.append((dest, size))

    return copied, skipped


def write_index(copied: list[tuple[Path, int]], skipped: list[tuple[str, int]]) -> None:
    lines = [
        "# Evidence index",
        "",
        "Generated by `python scripts/bundle_evidence.py`. `results/` and `ledger/` are",
        "gitignored (scratch, large, and churny) -- this directory is the small,",
        "committed subset that backs every number this repo publishes about itself.",
        "Re-run the script to refresh it; it recreates `evidence/` from scratch each time.",
        "",
        "Selection: every `results/timeline_*.json` point is included (they are one time",
        "series). For `bench_compare_*`, `regress_compare_*`, `sweep_*`, `transfer_*`,",
        "`full_base_*`, `luna_high_base_*`, `luna_hard_*`, `luna_wild_*`, and",
        "`humaneval_plus_*`, only the latest artifact of each kind is included.",
        "",
        "`scripts/build_dashboard_data.py` (owned separately, not touched by this script)",
        "reads the live files under `results/`/`ledger/` to build `dashboard/public/data.json`;",
        "the `source_files` block in that JSON names the same artifacts bundled here, so any",
        "filename it cites can be found in this directory once bundled.",
        "",
        "## Files",
        "",
    ]
    for dest, size in sorted(copied, key=lambda t: t[0].name):
        obj = _read_json(dest) if dest.suffix == ".json" else None
        summary = _summarize(dest.name, obj) if obj is not None else ""
        kib = size / 1024
        line = f"- `evidence/{dest.name}` ({kib:.1f} KiB)"
        if summary:
            line += f" -- {summary}"
        lines.append(line)

    if skipped:
        lines.append("")
        lines.append("## Skipped (over the 2 MB evidence-bundle size cap)")
        lines.append("")
        for name, size in skipped:
            mib = size / (1024 * 1024)
            lines.append(f"- `{name}` ({mib:.2f} MiB) -- not copied, exceeds the 2 MB cap")

    missing_patterns = [p for p, _ in PATTERNS
                         if not list(RESULTS.glob(p))]
    if missing_patterns:
        lines.append("")
        lines.append("## Patterns with no matches on disk at bundle time")
        lines.append("")
        for p in missing_patterns:
            lines.append(f"- `results/{p}`")

    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    copied, skipped = bundle()
    write_index(copied, skipped)
    total = sum(size for _, size in copied)
    print(f"wrote {len(copied)} files to evidence/ ({total / 1024:.1f} KiB total)")
    if skipped:
        print(f"skipped {len(skipped)} oversized file(s), see evidence/INDEX.md", file=sys.stderr)
    print(f"wrote {INDEX.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
