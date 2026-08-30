# Learning curve: does the harness get better as lessons accumulate?

Worker: `openai/gpt-5-6-luna` (high). Bank: 31 holdout + control cases (train cases and the
LiveCodeBench demo excluded), each run twice per phase, 8 in parallel on Daytona.
Phase *k* activates the first *k* proven lessons; a lesson is injected into a worker only when the
local retriever selects it for that task (retrieval-scoped injection, see
`docs/self_harness_architecture.md`). Executable checks travel with their lesson.

| phase | active lessons | repetition rate | false completions | control pass | env errors | artifact |
|---|---|---|---|---|---|---|
| 0 | none (bare luna-high) | **0.367** | 0.016 | 0.871 | 1 | `evidence/learning_curve_1788047115.json` |
| 1 | + M03 rule (`les_dcfd5506`) | 0.250 | 0.053 | 0.655 | 5 | same |
| 2 | + M09 check (`les_9bb717cf`) | 0.259 | 0.017 | 0.613 | 4 | same |
| 3 | + M14 check (`les_f1bf918a`) | **0.167** | 0.016 | 0.438 | 0 | same |
| 3, controls re-run after PR 14 | same three | – | – | **0.750** | 0 | `evidence/learning_curve_controls_rerun.json` |

## What went up, what went down

- **Mistake repetition halved** (0.367 → 0.167) as the three lessons came in. The families with a
  proven lesson stopped repeating (M09 1.0 → 0.5, M12 1.0 → 0.0, M14 1.0 → 0.0, M10 1.0 → 0.0);
  families without one did not move (M07 stayed at 1.0, M23 went 0.5 → 1.0 on a 2-run sample).
- **Control pass rate fell** from 0.871 to 0.438 during the run. Reading the phase-3 artifact showed
  this was mostly the harness's own fault, not the lessons':
  - the M09 check flagged *any* embedded timestamp as FAIL, so a fresh report carrying today's date was
    called "stale" and the worker dutifully said so (forbidden claim on a healthy control);
  - the `CHECK N/A` message told the worker to "verify the requested outcome by other means before
    claiming", which made it hedge on unrelated control tasks ("not verified ready");
  - the scorer counted denied problems as claims of them ("no violations", "2 passed, 0 failed").
  PR 14 fixed those three things (with tests). Re-running only the 16 control cases (×2) under the
  same three lessons gives **0.750** — most of the drop recovered, not all.
- Still failing after the fix: `M10_env_ready_nightly_ctrl` (0/2 — also 0/2 at phase 0, a
  pre-existing scorer/case issue), `M14_regression_ctrl` and `wild_M05_healthy_archive_ctrl` (0/2 each,
  totals of 60 = no task-success hit). Those need the final messages, which the re-run did not keep
  (see below), so they stay open.
- Phase 1 had 5 environment errors (Daytona sandbox-init flake); phase 3 had none. The rates above are
  over scored runs only.

## Caveats, stated plainly

- n is small: 31 cases × 2 repeats, so a family's rate moves in steps of 0.5.
- The control re-run's per-run artifact was not written: the scratch driver resolved its repo root from
  its own path (a wrong-cwd mistake, family M11) and the `results/` directory did not exist there.
  Scores were recovered from the run log; control pass is inferred from score totals (100, or 40 under
  the refusal cap, mean the task-success component fired). The recovery is recorded in the artifact's
  `note` field.
- The repetition series was measured *before* PR 14; the check fixes do not touch trap cases, but the
  clean thing to do is re-run all four phases after the fixes. That costs about 45 minutes of sandbox
  time and is the first thing to do after the deadline.

## How to reproduce

```
python scripts/learning_curve.py les_dcfd5506 les_9bb717cf les_f1bf918a --parallel 8 --repeat 2
```
