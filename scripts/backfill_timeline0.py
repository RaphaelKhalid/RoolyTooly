"""Backfill timeline point 0 from the baseline artifacts already on disk.

Point 0 is bare luna-high, no lessons."""
import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rows, arts = [], []
for lab in ["luna_high_base", "luna_hard", "luna_wild"]:
    f = sorted(glob.glob(str(ROOT / "results" / f"{lab}_*.json")))[-1]
    arts.append(Path(f).relative_to(ROOT).as_posix())
    rows += json.load(open(f, encoding="utf-8"))["results"]
rows = [r for r in rows if r["split"] != "train"]
scored = [r for r in rows if not r["error"]]
blocked = [r for r in scored if r["expected"] == "blocked"]
ctrl = [r for r in scored if r["expected"] == "success"]
n = len(scored) or 1
summary = {
    "n_cases": len(rows), "n_errors": len(rows) - len(scored),
    "mean_score": round(sum(r["score"] for r in scored) / n, 1),
    "mistake_repetition_rate": round(sum(r["mistake_repeated"] for r in blocked) / (len(blocked) or 1), 3),
    "false_completion_rate": round(sum("fabricated_completion" in r["caps"] for r in scored) / n, 3),
    "control_pass_rate": round(sum(r["breakdown"]["task_success"] > 0 for r in ctrl) / (len(ctrl) or 1), 3),
    "refusal_rate": round(sum("refusal_or_punt" in r["caps"] for r in scored) / n, 3),
    "evidence_rate": round(sum(r["signals"]["artifact_inspected"] for r in scored) / n, 3),
    "total_tokens": sum(r["usage"].get("total_tokens", 0) for r in scored),
}
fam = {}
for r in scored:
    d = fam.setdefault(r["family"], {"trap_runs": 0, "mistakes": 0, "ctrl_runs": 0, "ctrl_pass": 0})
    if r["expected"] == "blocked":
        d["trap_runs"] += 1
        d["mistakes"] += int(r["mistake_repeated"])
    else:
        d["ctrl_runs"] += 1
        d["ctrl_pass"] += int(r["breakdown"]["task_success"] > 0)
for d in fam.values():
    d["repetition_rate"] = round(d["mistakes"] / d["trap_runs"], 3) if d["trap_runs"] else None
ts = 1788032000.0
pt = {"label": "point 0: bare luna-high, no lessons (backfilled from baseline artifacts)", "ts": ts,
      "ran_at": "2026-08-29T12:35:00", "worker_model": {"name": "openai/gpt-5-6-luna", "params": {"reasoning_effort": "high"}},
      "n_active_lessons": 0, "active_lessons": [], "summary": summary, "per_family": fam, "source_artifacts": arts,
      "cases": [{"case_id": r["case_id"], "score": r["score"], "mistake": r["mistake_repeated"], "caps": r["caps"],
                 "error": r["error"], "session_id": r["session_id"], "final_message": r["final_message"][:400]} for r in rows]}
out = ROOT / "results" / f"timeline_{int(ts)}_0000.json"
out.write_text(json.dumps(pt, indent=2), encoding="utf-8")
print("wrote", out.name, summary)
