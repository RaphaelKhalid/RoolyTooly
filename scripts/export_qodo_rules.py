"""Export promoted lessons as Qodo review rules.

Qodo reads `best_practices.md` from the repository root and applies it as custom review guidance on
every PR. Running this after a promotion means what the agent learned is also enforced on human code.

  python scripts/export_qodo_rules.py            # writes best_practices.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import os
LEDGER = Path(os.environ.get("ROOLY_LEDGER_DIR", ROOT / "ledger")) / "ledger.jsonl"
OUT = ROOT / "best_practices.md"

HEADER = """# Best practices (auto-generated from promoted agent lessons)

These rules were compiled by the RoolyTooly immune-system agent from human corrections, each backed by
a regression test the pre-correction agent fails and a before/after benchmark that improved. They apply
to code, tests, scripts and PR descriptions in this repository.

## Standing rules

- A zero exit code or a printed success line is not evidence that the requested outcome exists; code
  that reports results must read them from the produced artifact and report null/empty explicitly.
- Evidence a reviewer will rely on (benchmark scores, test results) must come from a file produced by
  the run, referenced by path — never from a caller-supplied summary.
- Scripts must not delete raw outputs, checkpoints or logs before the results have been evaluated.

## Promoted lessons
"""


def main() -> None:
    lessons: dict[str, dict] = {}
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if not isinstance(r, dict):
                    continue
                if r.get("kind") == "lesson" and isinstance(r.get("id"), str):
                    lessons[r["id"]] = {**r, "status": "candidate"}
                elif r.get("kind") == "status" and r.get("lesson_id") in lessons:
                    lessons[r["lesson_id"]]["status"] = r.get("status")
            except (json.JSONDecodeError, TypeError, KeyError):
                continue  # quarantined by the ledger server; never fatal here
    active = [L for L in lessons.values() if L["status"] == "active"]
    body = HEADER
    if not active:
        body += "\n_No promoted lessons yet._\n"
    for L in active:
        body += (f"\n### {L['family']} — {L['invariant']}\n\n"
                 f"- **Rule:** {L['rule_text']}\n"
                 f"- **Pre-flight check:** {L.get('preflight_check') or '—'}\n"
                 f"- **Do not flag when:** " + "; ".join(L.get("counterexamples") or []) + "\n"
                 f"- **Provenance:** lesson `{L['id']}`, correction `{L.get('correction_id', '?')}`, "
                 f"intervention type `{L.get('intervention_type', '?')}`\n")
    OUT.write_text(body, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} with {len(active)} promoted lesson(s)")


if __name__ == "__main__":
    sys.exit(main())
