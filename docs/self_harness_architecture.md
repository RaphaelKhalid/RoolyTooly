# RoolyTooly, defined: a self-harness with a neurosymbolic core

**One sentence.** A TrueForge agent that mines its own and other agents' mistakes, compiles them
into *executable* rules through autoresearch, stores and retrieves those rules through Qodo's
rule index, proves each one with a regression test before it can ship, and tells the human every
morning in plain language what it learned and which rules it added, merged, or retired.

## Why the first version did not move coding benchmarks (2026-08-29 post-mortem)

We assumed "better harness decisions → better coding scores for the same model". Measured:
HumanEval+ (n=30, gpt-5.6-luna high) bare pass@1 0.96 / false completions 0 vs. harness 0.89 /
0.04, within noise, +25% tokens (`docs/humaneval_plus.md`). Five causes, all in the evidence:

1. **Wrong target metric.** Every lesson we compiled is about *claim honesty* (read the artifact
   before you say "done"). A rule about honesty cannot raise pass@1; it can only lower the
   false-completion rate — and on HumanEval+ luna-high's false-completion rate was already 0.
   We measured the harness on a benchmark where the failure it prevents never occurs.
2. **Prompt-level interventions only.** rule / seed / constraint are all text. The sweeps on
   M09, M14, M12 found *no* text that closes those families for luna-high
   (`results/sweep_*.json`). The Self-Harness paper's gains come from changing the harness's
   *tools* (verifiers, test runners, retry policies), not its prose.
3. **Rules generalized badly in both directions.** Compiler output was either generic ("inspect
   the artifact" — no behavior change) or train-case-specific ("compare data/metrics.json with
   report/summary.md" — passes the train regression, fails the holdout). We had no held-in vs.
   held-out split per lesson, so keep/revert could not see the difference.
4. **All rules injected into every prompt.** Bloat grows with every lesson; relevance was never
   computed. (Fixed: Qodo rule search now selects lessons per task — `harness/qodo_lessons.py`.)
5. **Infrastructure noise polluted the science.** The 10-sandbox pool starved under concurrent
   runs; up to 10/22 cases errored, and early keep/revert decisions ignored that. (Fixed: the
   coverage gate in `decide()`; never more than one evaluation at a time.)

## The architecture that fixes it

```
            NEURAL (propose)                          SYMBOLIC (verify, store, decide)
  ┌──────────────────────────────┐          ┌──────────────────────────────────────────┐
  │ worker (luna-high)           │  trace   │ deterministic checkers over tool traces  │
  │ lesson-compiler subagent     │ ───────► │ regression test: base fails, cand passes │
  │ falsifier subagent           │          │ keep/revert: lexicographic + coverage    │
  │ mistake-miner (Bright Data)  │          │ append-only ledger (facts, provenance)   │
  │ autoresearcher (Code Mode)   │ ◄─────── │ Qodo rule index: scoped, thresholded     │
  └──────────────────────────────┘  rules   │   retrieval, severity, overlap audit     │
                                            │ executable checks shipped inside skills  │
                                            └──────────────────────────────────────────┘
```

- **Executable checks, not prose (intervention level L3+).** A lesson may carry a `check.py`
  (a symbolic verifier: compare a derived artifact with its source and timestamps; run *every*
  test file, not the named one; refuse to report values that were regenerated rather than
  retained). The worker must run the check before its final reply; the trace checker verifies
  that the check ran, exited 0, and printed `CHECK OK`. The neural part writes the check; the
  symbolic part decides whether it counts.
- **Autoresearch chooses the level.** For each family: rule → seed → constraint → check, lowest
  level that closes the family with controls intact; every trial recorded in the ledger.
- **Qodo is the rule memory.** Every promoted lesson becomes a repo-scoped Qodo rule
  (`qodo rules create`), retrieved per task with Qodo's relevance-thresholded search, so a
  workspace with hundreds of rules injects only the few that apply. Before a rule is added, the
  same search runs an **overlap audit**: a near-duplicate is merged or superseded, never added
  twice. Qodo reviews the lesson PR and enforces the rule on human PRs too.
- **Self-harness loop (from the paper, with our gates).** weakness mining (checkers + Bright
  Data) → harness proposal (rule or check) → regression-gated validation on held-in *and*
  held-out cases → human approval → promotion → transfer proof.
- **Morning digest.** A scheduled run mines new reports, re-scores, and writes
  `docs/digest/YYYY-MM-DD.md`: *what we learned, which rules were added / merged / retired, and
  what the numbers did*, written for a human, with every number linked to its artifact.

## What "better" means from now on

Report two numbers per benchmark, never one: **pass@1** (the model's capability — the harness
is not expected to move it) and **false-completion rate** (the harness's job — it must go to 0
without lowering control pass rate). LiveCodeBench-hard with hidden tests is the benchmark where
both are visible; HumanEval+ is saturated for luna-high and is kept as an honest control.

## First results of executable checks (2026-08-29, 15:15)

- Regression (train case, luna-high, 2 runs each): **M09 check: base fails, candidate passes**;
  **M14 check: base fails, candidate passes** (`results/regress_compare_1788040172_46ef.json`,
  `results/regress_compare_1788040262_9f86.json`). Prose interventions had failed both.
- Benchmark of the M09 check applied to *every* holdout/control case (31 cases, 0 errors):
  repetition 0.333 → 0.267 (it also fixed an M14 and an M12 holdout), but false completions
  0 → 0.032 and controls 1.0 → 0.75 → **reverted** (`results/bench_compare_1788041632_799f.json`).
  Cause: on tasks with nothing to compare the check printed `CHECK OK`, and one worker treated that
  as reassurance ("READY to publish"). Fix: a check that does not apply prints `CHECK N/A`, and checks
  are injected per task through Qodo retrieval, not globally. The keep/revert gate catching this is the
  system working as designed.

## Local retrieval: three channels, RRF fusion, a dense channel that may fail

`harness/rule_index.py` is the fast local path the harness consults per task. Qodo rule search
stays the *selector* (`results/lesson_index.json` → `index`); the local retriever runs on the same
query and is recorded next to it (`receipts[case]["local"]`) so the two can be compared offline.

**Channels.** Three rankings are produced independently, each truncated to the first 50 results:

| channel | signal | absolute gate |
| --- | --- | --- |
| `bm25` | Okapi BM25 over family + invariant + rule + preflight text | `>= 2.0` |
| `char_ngram` | character 3-gram cosine (wording/morphology robust) | `>= 0.18` |
| `dense` | OpenAI `text-embedding-3-small` cosine | `>= 0.42` |

**Fusion.** Reciprocal-rank fusion at a constant `k = 60`, one-based ranks:

    RRF(d) = Σ_{c ∈ {bm25, char_ngram, dense}} 1[d ∈ c] / (60 + rank_c(d))

Raw BM25 / n-gram / cosine values never enter the sum — they are kept for receipts only. A
lesson is then reinforced once by its family (`+0.25 ×` the best fused score in its family), and
the hop never resurrects a lesson that scored zero on its own.

**The gate is a disjunction.** A lesson is eligible if *any* channel shows real evidence
(BM25 ≥ 2.0 **or** 3-gram ≥ 0.18 **or**, when dense is available, cosine ≥ 0.42). This deliberately
differs from the original plan's conjunctive final gate: a merely mediocre cosine must not be able
to delete a lesson the lexical channels already justified, and an uncalibrated 0.42 applied as an
`AND` would silently shrink the lesson set. The receipt's `gate` field records `passed` when dense
was evaluated and `not_evaluated` when it was not, so the two regimes are never confused.

**Cache and credentials.** `OPENAI_API_KEY` is read from the environment first, then from the
gitignored `.env` at the repo root. Vectors are cached at `results/embeddings/<sha256(text)>.json`
holding only `{model, sha256, dimensions, vector}` — never the source text, never the key — and are
written through a temp file plus `os.replace`, so a reader never sees a half-written entry. Entries
whose model or digest does not match are rejected and re-fetched. An in-process vector cache makes
a warm same-process retrieval do no disk or network work.

**Degradation.** The single HTTP call is `POST https://api.openai.com/v1/embeddings` through
`urllib.request` with a 5 s timeout — stdlib only, no SDK. If the key is missing, `dense_status`
becomes `missing_key`; if the request, the JSON, or the response shape fails, it becomes `error`.
In both cases the dense channel is dropped from the fusion and retrieval continues on BM25 +
char-n-gram. An embedding failure must never present as "no lessons apply": only an actually empty
lexical index may produce an empty selection.

**Receipt schema** (per selected lesson, in `results/lesson_index.json`):

```
{"lesson_id", "channels_fired": ⊆ {bm25, char_ngram, dense, qodo},
 "channels": {<channel>: {"rank", "score"}}, "rrf_score", "score",
 "cosine", "gate": "passed"|"not_evaluated", "qodo_rank": int|null}
```

with `dense_status`, `dense_error`, `cosine_threshold`, `rrf_k` and `channel_depth` recorded per
case, and `retrieval` / `qodo_agreement` blocks at the top level. Qodo is reconciled by canonical
lesson id (never by display title) and reported as provenance and diagnostics — it is not a fourth
RRF channel, and disagreement is measured, never required. `mcp_servers/eval_server.py` exposes the
same configuration through `retrieval_provenance()`; it is kept out of `harness_manifest()`'s return
value on purpose, because that dict is posted verbatim to TrueForge as a session spec.

**Calibration.** `0.42 / k=60 / depth=50` are the starting constants, not measured optima. The
inputs for calibration are the `results/retrieval_bench_*.json` artifacts written by
`python -m harness.rule_index bench`, which now record the full per-hit receipt and the dense
status for every query. Change them only with a benchmark that shows Recall@K held, MRR held or
improved, and false positives down.
