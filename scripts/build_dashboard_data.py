"""Build the static evidence snapshot consumed by the dashboard.

This module intentionally uses only the Python standard library. It reads the
repository artifacts, derives the small rollups the UI asks for, and writes one
JSON snapshot. It never calls a model or invents a value when an artifact is
missing or malformed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dashboard" / "public" / "data.json"


def read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path.relative_to(ROOT).as_posix()}: {exc}"


def latest(pattern: str) -> Path | None:
    paths = sorted(ROOT.glob(pattern), key=lambda item: item.name)
    return paths[-1] if paths else None


def artifact_name(path: Path | None) -> str | None:
    return path.relative_to(ROOT).as_posix() if path else None


def metric(value: Any, artifact: str | None) -> dict[str, Any]:
    return {"value": value, "artifact": artifact}


def parse_ledger(path: Path | None) -> tuple[list[dict[str, Any]], list[str]]:
    if not path or not path.exists():
        return [], ["ledger/ledger.jsonl"]
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
            else:
                warnings.append(f"ledger/ledger.jsonl:{line_number}: JSON value is not an object")
        except json.JSONDecodeError as exc:
            warnings.append(f"ledger/ledger.jsonl:{line_number}: {exc}")
    return records, warnings


def record_statuses(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("kind") == "status" and record.get("lesson_id"):
            statuses[record["lesson_id"]] = record
    return statuses


def lesson_for_rule(lessons: list[dict[str, Any]], rule_text: str | None) -> dict[str, Any] | None:
    if not rule_text:
        return None
    matches = [lesson for lesson in lessons if lesson.get("rule_text") == rule_text]
    return matches[-1] if matches else None


def build_board(compares: list[tuple[Path, dict[str, Any]]], lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    names = [
        "mean_score",
        "mistake_repetition_rate",
        "false_completion_rate",
        "control_pass_rate",
        "evidence_rate",
    ]
    for path, obj in compares:
        before = obj.get("before") if isinstance(obj.get("before"), dict) else {}
        after = obj.get("after") if isinstance(obj.get("after"), dict) else {}
        delta = obj.get("delta") if isinstance(obj.get("delta"), dict) else {}
        lesson = lesson_for_rule(lessons, obj.get("rule_text"))
        before_file = obj.get("before_artifact") or None
        after_file = obj.get("after_artifact") or None
        row = {
            "artifact": artifact_name(path),
            "before_artifact": before_file,
            "after_artifact": after_file,
            "family": lesson.get("family") if lesson else None,
            "lesson_id": lesson.get("id") if lesson else None,
            "intervention_type": obj.get("intervention_type") or (lesson or {}).get("intervention_type"),
            "decision": obj.get("decision"),
            "reasons": obj.get("reasons") if isinstance(obj.get("reasons"), list) else [],
            "rule_text": obj.get("rule_text"),
            "metrics": {
                name: {
                    "before": metric(before.get(name), before_file),
                    "after": metric(after.get(name), after_file),
                    "delta": metric(delta.get(name), artifact_name(path)),
                }
                for name in names
            },
        }
        rows.append(row)
    return rows


def build_baseline(path: Path | None, obj: dict[str, Any] | None) -> dict[str, Any]:
    artifact = artifact_name(path)
    if not isinstance(obj, dict):
        return {"artifact": artifact, "summary": {}, "families": [], "worst_cases": []}
    raw_results = obj.get("results") if isinstance(obj.get("results"), list) else []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in raw_results:
        if isinstance(result, dict):
            groups[str(result.get("family") or "no data")].append(result)

    families: list[dict[str, Any]] = []
    failure_cases: list[dict[str, Any]] = []
    for family, results in sorted(groups.items()):
        traps = [result for result in results if result.get("split") != "control"]
        controls = [result for result in results if result.get("split") == "control"]
        mistakes = [result for result in traps if result.get("mistake_repeated") is True]
        passed_controls = [result for result in controls if ((result.get("breakdown") or {}).get("task_success", 0) or 0) > 0]  # same definition as the checker
        scores = [result.get("score") for result in traps if isinstance(result.get("score"), (int, float))]
        bad_cases = sorted(
            [result for result in traps if isinstance(result.get("score"), (int, float))],
            key=lambda result: (result.get("score", 101), result.get("case_id", "")),
        )
        family_cases = [
            {
                "case_id": result.get("case_id"),
                "score": result.get("score"),
                "caps": result.get("caps") if isinstance(result.get("caps"), list) else [],
                "claim": result.get("final_message"),
                "artifact": artifact,
            }
            for result in bad_cases[:2]
        ]
        families.append(
            {
                "family": family,
                "trap_runs": metric(len(traps) if traps else None, artifact),
                "mistakes": metric(len(mistakes) if traps else None, artifact),
                "repetition_rate": metric((len(mistakes) / len(traps)) if traps else None, artifact),
                "controls_passed": metric(f"{len(passed_controls)}/{len(controls)}" if controls else None, artifact),
                "mean_score": metric((sum(scores) / len(scores)) if scores else None, artifact),
                "failure_cases": family_cases,
                "fabricated_completion": any("fabricated_completion" in (result.get("caps") or []) for result in traps),
            }
        )
        failure_cases.extend(family_cases)

    return {
        "artifact": artifact,
        "summary": {key: metric(value, artifact) for key, value in (obj.get("summary") or {}).items()},
        "families": families,
        "worst_cases": sorted(
            failure_cases,
            key=lambda result: (result.get("score", 101), result.get("family", ""), result.get("case_id", "")),
        )[:6],
    }


def build_interventions(
    board: list[dict[str, Any]], regressions: list[tuple[Path, dict[str, Any]]], lessons: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    def group_for(name: str) -> dict[str, Any]:
        if name not in grouped:
            grouped[name] = {"intervention_type": name if name != "" else None, "bench": [], "regressions": []}
        return grouped[name]

    for row in board:
        group_for(str(row.get("intervention_type") or "")).get("bench").append(row)
    for path, obj in regressions:
        lesson = lesson_for_rule(lessons, obj.get("rule_text"))
        intervention = obj.get("intervention_type") or (lesson or {}).get("intervention_type") or ""
        group_for(str(intervention)).get("regressions").append(
            {
                "artifact": artifact_name(path),
                "case_id": obj.get("case_id"),
                "base_fails": metric(obj.get("base_fails"), artifact_name(path)),
                "candidate_passes": metric(obj.get("candidate_passes"), artifact_name(path)),
                "valid_regression_test": metric(obj.get("valid_regression_test"), artifact_name(path)),
                "rule_text": obj.get("rule_text"),
            }
        )

    rows: list[dict[str, Any]] = []
    for name, group in sorted(grouped.items(), key=lambda pair: pair[0]):
        bench = group["bench"]
        metrics = {}
        for key in ("mistake_repetition_rate", "control_pass_rate"):
            metrics[key] = [
                {
                    "before": row["metrics"][key]["before"],
                    "after": row["metrics"][key]["after"],
                    "artifact": row["artifact"],
                }
                for row in bench
            ]
        rows.append(
            {
                "intervention_type": name or None,
                "repetition": metrics["mistake_repetition_rate"],
                "control_pass": metrics["control_pass_rate"],
                "decisions": [{"value": row.get("decision"), "artifact": row.get("artifact")} for row in bench],
                "regressions": group["regressions"],
            }
        )
    return rows


def build_ledger(records: list[dict[str, Any]], statuses: dict[str, dict[str, Any]], artifact: str | None) -> dict[str, Any]:
    corrections = [record for record in records if record.get("kind") == "correction"]
    evidence = [record for record in records if record.get("kind") == "evidence"]
    lessons = [record for record in records if record.get("kind") == "lesson"]
    observations = [record for record in records if record.get("kind") == "observation"]
    evidence_by_lesson: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in evidence:
        summary = record.get("derived") if isinstance(record.get("derived"), dict) else (record.get("summary") if isinstance(record.get("summary"), dict) else {})
        verdict = summary.get("decision")
        if verdict is None and "passed" in summary:
            verdict = "passed" if summary.get("passed") else "failed"
        evidence_by_lesson[str(record.get("lesson_id"))].append(
            {
                "id": record.get("id"),
                "evidence_kind": record.get("evidence_kind"),
                "artifact_path": record.get("artifact_path"),
                "verdict": verdict,
                "summary": summary,
                "artifact": artifact,
            }
        )
    correction_by_id = {record.get("id"): record for record in corrections}
    lesson_rows = []
    for lesson in lessons:
        status_record = statuses.get(lesson.get("id"))
        status = status_record.get("status") if status_record else "candidate"
        lesson_rows.append(
            {
                "id": lesson.get("id"),
                "ts": lesson.get("ts"),
                "family": lesson.get("family"),
                "status": status,
                "status_note": status_record.get("note") if status_record else None,
                "intervention_type": lesson.get("intervention_type"),
                "rule_text": lesson.get("rule_text"),
                "invariant": lesson.get("invariant"),
                "correction": correction_by_id.get(lesson.get("correction_id")),
                "evidence": evidence_by_lesson.get(str(lesson.get("id")), []),
                "provenance": {
                    "correction_id": lesson.get("correction_id"),
                    "lesson_id": lesson.get("id"),
                    "evidence_ids": [item.get("id") for item in evidence_by_lesson.get(str(lesson.get("id")), [])],
                    "status_id": status_record.get("id") if status_record else None,
                },
            }
        )
    obs_counts = Counter(str(item.get("family") or "no data") for item in observations)
    return {
        "artifact": artifact,
        "lessons": lesson_rows,
        "observations": observations,
        "observation_counts": [{"family": family, "count": count, "artifact": artifact} for family, count in sorted(obs_counts.items())],
    }


def build_spend() -> dict[str, Any]:
    command = "python -m harness.spend --json"
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "harness.spend", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if completed.returncode == 0:
            parsed = json.loads(completed.stdout)
            return {"artifact": command, "data": parsed, "error": None}
        return {"artifact": command, "data": None, "error": completed.stderr.strip() or "command failed"}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"artifact": command, "data": None, "error": str(exc)}


def build_humaneval(warnings: list[str]) -> dict[str, Any]:
    """Return the latest paired bare and harness HumanEval+ runs sharing one label.

    "Bare" is the worker alone; "harness" is the worker plus its active lessons. Both modes
    must come from the same `--label` run so the comparison is like for like."""
    out: dict[str, Any] = {"bare": None, "harness": None, "label": None}
    runs: dict[str, dict[str, Path]] = {}
    for path in sorted(ROOT.glob("results/humaneval_plus_*.json")):
        parts = path.stem.split("_")  # humaneval_plus_<label>_<mode>_<ts>
        if len(parts) < 5 or parts[-2] not in ("bare", "harness"):
            continue
        label = "_".join(parts[2:-2])
        runs.setdefault(label, {})[parts[-2]] = path
    complete = [(lbl, modes) for lbl, modes in runs.items() if "bare" in modes and "harness" in modes]
    chosen = max(complete, key=lambda item: item[1]["harness"].stat().st_mtime) if complete else None
    if not chosen:
        return out
    out["label"] = chosen[0]
    for mode, path in chosen[1].items():
        obj, error = read_json(path)
        if error or not isinstance(obj, dict):
            warnings.append(error or f"{path.name}: not an object")
            continue
        summary = obj.get("summary") if isinstance(obj.get("summary"), dict) else obj
        out[mode] = {"artifact": artifact_name(path), "n": summary.get("n") or summary.get("n_cases"),
                     "pass_at_1": summary.get("pass_at_1"), "false_completion_rate": summary.get("false_completion_rate"),
                     "honest_fail_rate": summary.get("honest_fail_rate"), "unknown_rate": summary.get("unknown_rate"),
                     "evidence_rate": summary.get("evidence_rate"), "mean_tokens": summary.get("mean_tokens")}
    return out


def build_timeline(warnings: list[str]) -> list[dict[str, Any]]:
    """Return one benchmark-run summary per saved timeline snapshot, for trend charts.

    Reads every results/timeline_*.json file on disk, one point per file."""
    points: list[dict[str, Any]] = []
    for path in sorted(ROOT.glob("results/timeline_*.json")):
        obj, error = read_json(path)
        if error or not isinstance(obj, dict):
            warnings.append(error or f"{path.name}: not an object")
            continue
        summary = obj.get("summary") or {}
        points.append({"artifact": artifact_name(path), "label": obj.get("label"), "ran_at": obj.get("ran_at"),
                       "n_active_lessons": obj.get("n_active_lessons"), "n_cases": summary.get("n_cases"),
                       "mean_score": summary.get("mean_score"), "mistake_repetition_rate": summary.get("mistake_repetition_rate"),
                       "false_completion_rate": summary.get("false_completion_rate"), "control_pass_rate": summary.get("control_pass_rate"),
                       "per_family": {k: v.get("repetition_rate") for k, v in (obj.get("per_family") or {}).items() if isinstance(v, dict)}})
    return points


def main() -> None:
    warnings: list[str] = []
    ledger_path = ROOT / "ledger" / "ledger.jsonl"
    ledger_records, ledger_warnings = parse_ledger(ledger_path)
    warnings.extend(ledger_warnings)
    statuses = record_statuses(ledger_records)
    lessons = [record for record in ledger_records if record.get("kind") == "lesson"]

    compare_pairs: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(ROOT.glob("results/bench_compare_*.json")):
        obj, error = read_json(path)
        if error:
            warnings.append(error)
        elif isinstance(obj, dict):
            compare_pairs.append((path, obj))
    regress_pairs: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(ROOT.glob("results/regress_compare_*.json")):
        obj, error = read_json(path)
        if error:
            warnings.append(error)
        elif isinstance(obj, dict):
            regress_pairs.append((path, obj))

    baseline_path = latest("results/full_base_*.json")
    baseline_obj, error = read_json(baseline_path) if baseline_path else (None, None)
    if error:
        warnings.append(error)
    transfer_path = latest("results/transfer_*.json")
    transfer_obj, error = read_json(transfer_path) if transfer_path else (None, None)
    if error:
        warnings.append(error)
    transfer_result = None
    if isinstance(transfer_obj, dict) and isinstance(transfer_obj.get("results"), list) and transfer_obj["results"]:
        transfer_result = transfer_obj["results"][0]

    board = build_board(compare_pairs, lessons)
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_files": {
            "baseline": artifact_name(baseline_path),
            "bench_compare": [artifact_name(path) for path, _ in compare_pairs],
            "regress_compare": [artifact_name(path) for path, _ in regress_pairs],
            "ledger": artifact_name(ledger_path),
            "transfer": artifact_name(transfer_path),
        },
        "baseline": build_baseline(baseline_path, baseline_obj if isinstance(baseline_obj, dict) else None),
        "board": board,
        "interventions": build_interventions(board, regress_pairs, lessons),
        "ledger": build_ledger(ledger_records, statuses, artifact_name(ledger_path)),
        "transfer": {
            "artifact": artifact_name(transfer_path),
            "summary": (transfer_obj or {}).get("summary") if isinstance(transfer_obj, dict) else {},
            "result": transfer_result,
            "trueforge_base_url": "http://localhost:8790/sessions/",
        },
        "spend": build_spend(),
        "humaneval": build_humaneval(warnings),
        "timeline": build_timeline(warnings),
        "warnings": warnings,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(snapshot, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    if warnings:
        print(f"warnings: {len(warnings)}", file=sys.stderr)


if __name__ == "__main__":
    main()
