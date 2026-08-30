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

## LiveCodeBench-hard result (2026-08-29, 15:45) - the number the harness is for

30 hard contest problems with hidden tests, gpt-5.6-luna high (`docs/livecodebench.md`,
`results/livecodebench_luna_high_{bare,harness}_*.json`):

| mode | pass@1 | false completions | honest failures | tokens/problem |
|---|---:|---:|---:|---:|
| bare | 0.900 | 3.3% | 0% | 74k |
| with harness | 0.931 | **0%** | 0% | 77k |

The harness did not change what the model can solve; it removed the case where the model claimed
"ready" while the hidden tests failed, at +3% tokens. That is the intended effect: pass@1 is the
model's number, false-completion rate is the harness's.

## Review findings as a mistake source (2026-08-30)

The Bright Data channel (Workflow C) mines *other people's* agent mistakes off the web. This channel
mines *ours*: every finding the Qodo PR reviewer raised on `RaphaelKhalid/RoolyTooly` is a real bug
this project shipped into a pull request, and it arrives with the two things `record_observation`
already demands - a permanent `html_url` and a verbatim quote.

`harness/qodo_findings.py` fetches the review comments with `gh api --paginate`
(`repos/<repo>/pulls/comments` plus `repos/<repo>/issues/comments`, authors matching `/qodo/i`),
parses each finding's title/body/path/line/PR, classifies it into a mistake family, and writes it
through `mcp_servers/ledger_server.py:record_observation` with `import_key="qodo:<comment_id>"`.
Re-running imports nothing (verified: a second run reported
`Imported 0 observation(s); skipped 114 duplicate(s)`). The MCP tools `import_qodo_findings` and
`qodo_rule_proposals` expose it to the TrueForge agent as Workflow H.

### Real import, 2026-08-30

`python harness/qodo_findings.py --propose` over PRs #1-#14: **114 findings imported**.

| family | n | meaning |
| --- | --- | --- |
| M23 | 60 | Representation and deliverable mismatch |
| `NEW:qodo-unclassified` | 23 | no keyword matched |
| M14 | 8 | Fix introduces a regression |
| M05 | 7 | Silent-null success |
| M07 | 4 | Evidence destroyed before evaluation |
| M09 | 4 | Stale-state insistence |
| M11 | 4 | Semantic misdiagnosis |
| M13 | 2 | Mixed metrics and moving denominators |
| M03 | 1 | Proxy victory mistaken for target success |
| M21 | 1 | Promise dropped after interruption |

Eight candidate rules (families with >=2 findings) were written to
`results/qodo_rule_proposals.json`, each carrying the URLs of the review comments that support it.

### What this is not

Read the numbers narrowly:

- **These are review findings about harness code, not corrections of a running worker agent.** A
  Qodo finding says a bug existed in a diff; it does not say an agent claimed success falsely. The
  two are related but they are not the same evidence.
- **The family labels are keyword heuristics**, not judgements. The classifier is an ordered
  keyword-to-family table in `qodo_findings.py`; nothing read these findings and understood them.
- **M23's 60 is inflated by one review category.** Most of that bucket is Qodo's docstring rule
  violations ("summary exceeds limit", "summary lacks clarity"). Filing a docstring that misstates
  what a unit does under "representation and deliverable mismatch" is defensible, but it is a
  judgement call baked into a keyword list, and it makes M23 look far more dominant than the
  correctness bugs do.
- **23 findings matched nothing** and are stored as `NEW:qodo-unclassified` rather than being forced
  into a family the classifier does not believe.
- **Nothing here is promoted.** The proposals are candidates. A review finding is evidence that a
  bug existed, not evidence that a rule prevents it - only a regression artifact and a benchmark
  comparison are that. Every candidate still goes through compile -> falsify -> regression ->
  benchmark -> human approval, the same gate as every other lesson.
