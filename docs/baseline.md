# Baseline: naive worker (gpt-5.4-mini, low reasoning), all cases x2

Run: `2026-08-29T12:05:41`, 32 runs, 2 environment errors excluded.

| mean score | mistake repetition | false completion | control pass | refusal | evidence rate |
|---:|---:|---:|---:|---:|---:|
| 71.0 | 0.556 | 0.167 | 0.917 | 0.033 | 0.633 |

| family | trap runs | mistakes | repetition rate | controls passed |
|---|---:|---:|---:|---:|
| HE (M03 on HumanEval+) | 8 | 3 | 0.38 | 4/4 |
| M05 | 4 | 4 | 1.00 | 3/4 |
| M06 | 3 | 0 | 0.00 | 2/2 |
| M07 | 3 | 3 | 1.00 | 2/2 |

Scores are deterministic checker output over TrueForge event traces (`bench/checkers.py`); the raw artifact is `results/full_base_*.json`.
