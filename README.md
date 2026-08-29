# RoolyTooly

**Correct it once. It proves it will never make that mistake again.**

An agent harness on [TrueForge](https://github.com/truefoundry/trueforge) that turns a single human
correction into permanent, *tested* immunity: a mistake-family classification, a durable rule, and an
executable regression test that the pre-correction agent demonstrably fails — promoted through a
human approval gate into a skill that a completely fresh agent loads.

Built solo for the WeMakeDevs × TrueFoundry Agent Harness Hackathon (Aug 29–30, 2026).

## The problem

AI agents make the same *families* of mistake over and over: claiming work is done when it isn't,
mistaking a proxy metric for the target ("training loss improved" while the benchmark never ran),
destroying evidence before evaluation, adding caveats after the decision. Correcting the agent fixes
the sentence, not the family. The 24-family taxonomy, scoring scheme and regression-test design this
project implements is in [`agent-mistake-families-benchmark.md`](agent-mistake-families-benchmark.md).

## The loop

```
worker makes a family mistake  ──►  human corrects it once
        │                                     │
        │      lesson-compiler subagent ◄──────┘   family + invariant + rule + regression test
        │                │
        │      falsifier subagent          attacks the rule: false positives? refusal degeneration?
        │                │
        │      eval-runner (Daytona sandbox)   regression: base FAILS, candidate PASSES
        │                │                     benchmark: before/after → keep | revert
        │                ▼
        │      approval gate  ◄──── human approves rule + evidence
        │                │
        │      skill pushed to GitHub (approval) → registered in TrueForge
        │                │
        └──►   FRESH agent + skill on a different-looking hidden task → does not fall for it
                         │
               GitHub MCP opens the lesson PR (approval) → Qodo reviews it
```

Scoring is **code inspecting event traces and artifacts**. Model-generated prose never counts as
evidence; there is no LLM-as-judge for ground truth. Blanket refusal cannot win: benign control cases
must keep passing or the candidate is reverted.

## The self-improving agent (what the demo graph shows)

`gpt-5.6-luna` (high reasoning) is the floor model - what people actually run for agentic SWE. Bare, it
repeats family mistakes on the hard bank (stale state, hidden regressions, moving denominators) about
45% of the time (`docs/baseline_luna_high.md`). During the hackathon the harness ran an autonomous loop
(`harness/self_improve.py`): score the current agent on every non-train case (`run_timeline_point`) ->
pick the worst family -> Code Mode sweep of intervention levels (`autoresearch/sweep.py`, one sandbox
script driving dozens of luna-high trials through the eval-runner MCP) -> promote the winner behind the
approval gate -> re-score. Every point of the timeline is a `results/timeline_*.json` artifact listing
the active lessons at that moment; the dashboard plots repetition rate, false-completion rate and
control pass rate over time. Families no prompt-level intervention closes are reported as such - that
table is a research result, not a failure.

The mistake bank itself is grounded in the wild: `mistake-miner` subagents scraped 64 real reports of
agent failures (Bright Data MCP) into the ledger; four benchmark cases are seeded directly from them
(`docs/mined_mistakes.md`).

## TrueForge features exercised (all visible in the bundled UI)

| Feature | Where |
|---|---|
| Subagents | `lesson-compiler`, `falsifier`, `mistake-miner` — dynamic subagents spawned by the `roolytooly` root agent |
| Sandbox (Daytona) | every worker run, regression, benchmark and transfer test executes in a fresh sandbox |
| Custom MCP tools | `lesson-ledger` (append-only corrections → lessons → evidence → promotion) and `eval-runner` (deterministic regression / benchmark / transfer jobs, budget guard) — `mcp_servers/` |
| Catalog MCP tools | GitHub (skill push + PR), Bright Data (mistake mining) |
| Persistent session | SQLite-backed sessions and the append-only ledger survive a page refresh; eval-runner jobs are persisted to `results/jobs.json` (a job interrupted by a server restart is reported as `lost`, never as running) |
| Approval gates | `promote_lesson` (annotated destructive), GitHub `push_files` / `create_pull_request` |
| Skills | every promoted lesson is a git-backed skill (`skills/<lesson>/SKILL.md`) loaded by fresh agents |

## Repository layout

```
harness/tf.py            stdlib TrueForge API client (sessions, turns, events, approvals, agents, skills)
harness/drive.py         drive the roolytooly agent from the CLI (what the chat UI does)
harness/spend.py         budget tracker over every TrueForge session, per-model prices
bench/cases*.py          seeded tasks with known ground truth: train trap / hidden holdout / benign controls
bench/checkers.py        deterministic scoring over the event trace (0–100, hard caps)
bench/run.py             runs cases in fresh sandboxed sessions → score artifacts in results/
mcp_servers/             lesson-ledger and eval-runner MCP servers (streamable HTTP)
manifests/               worker_base.json (naive worker), immune_root.json (the roolytooly agent)
skills/                  promoted lessons (branch `lessons`, merged via PR)
ledger/                  append-only JSONL ledger (gitignored locally; promoted lessons land in skills/)
```

## Reproduce

Prerequisites: Node 22+ (TrueForge), Python 3.12+, a Daytona API key with **Sandboxes + Snapshots
write**, an OpenAI key, a GitHub PAT. On Windows run TrueForge inside WSL.

```bash
npx @truefoundry/trueforge                      # http://localhost:8790, SQLite state
python -m venv .venv && . .venv/bin/activate && pip install "mcp<2" httpx
python mcp_servers/ledger_server.py &           # :8901/mcp
python mcp_servers/eval_server.py &             # :8902/mcp
```

In TrueForge → Settings: add the OpenAI provider, the Daytona sandbox provider, the GitHub connector,
the Bright Data connector (remote MCP `https://mcp.brightdata.com/mcp?token=<BRIGHTDATA_API_KEY>`, used by
Workflow C), and the two local MCP servers (`http://127.0.0.1:8901/mcp`, `http://127.0.0.1:8902/mcp`). Then:

```bash
python - <<'EOF'
import json; from harness import tf
tf.upsert_agent("roolytooly", json.load(open("manifests/immune_root.json")))
EOF
python -m harness.drive --new "Run the worker on M05_hollow_report_01."
python -m harness.drive --session <id> "Correction: the printed summary is a cached value; report.json says null. Never report a value you haven't read from the artifact. Compile this into a lesson and take it through transfer and the PR."
python -m bench.run --manifest manifests/worker_base.json        # benchmark the naive worker
python -m harness.spend                                          # project spend
```

Approval prompts appear in the TrueForge UI (or pass `--auto-approve` to `harness.drive` for tests).

## Qodo Code Review Evidence

Every substantive change went through a Qodo-reviewed pull request.

- [PR #1](https://github.com/RaphaelKhalid/RoolyTooly/pull/1) — bootstrap; Qodo: 0 findings (pipeline check).
- [PR #2](https://github.com/RaphaelKhalid/RoolyTooly/pull/2) — the harness. Qodo found **9 bugs**, all fixed
  in commit `ee3fff8` before merge. The most important one is the reason this section exists:
  - *Promotion trusts fabricated evidence* — the ledger accepted caller-supplied "benchmark: keep"
    summaries. An agent could have promoted its own lesson with forged evidence — exactly the family of
    mistake the product exists to prevent. Fix: the ledger opens the eval-runner artifact under
    `results/` and derives the verdict from the file; callers cannot assert it.
  - *Artifact inspection is unverified* / *failed commands count as execution* — the checker scored
    command text without checking the tool response. Fix: exec calls are correlated with their
    responses; only exit-code-0 reads count as evidence.
  - *Errored candidates pass regression* (vacuous `all()`), *empty benchmarks are kept*, *session
    failures bypass retries*, *corrupt record disables ledger*, *revert reports nonexistent success*,
    *lessons allow missing corrections* — all fixed with validation and state-transition checks.
- [PR #3](https://github.com/RaphaelKhalid/RoolyTooly/pull/3) — the first lesson PR, opened by the agent itself through
  the GitHub MCP after human approval; Qodo reviews the rule and its evidence table.

Qodo repository rules mirror the promoted lessons, so what the agent learned also becomes a review
rule for every human PR.
