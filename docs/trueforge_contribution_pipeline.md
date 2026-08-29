# A self-improving harness that improves its own harness (design)

Today the agent improves *itself* (lessons → skills). The next layer is an agent that improves
**TrueForge** — the runtime it lives in — through a pipeline whose end state is a pull request to
`truefoundry/trueforge` that a human has approved twice and that is backed by the same kind of
evidence we demand of every lesson. Nothing in this pipeline can reach the upstream repo without a
human clicking Approve; "100% perfect, no room for mistakes" is enforced structurally, not by prompt.

## Daily loop (Workflow G, gated)

| stage | who | gate |
|---|---|---|
| 1. Sense | `mistake-miner` subagents (Bright Data): new agent-failure reports, TrueForge issues/PRs/releases, harness papers | none — read-only |
| 2. Reproduce | eval-runner: does the reported failure reproduce against **our** harness on TrueForge (seeded case)? If not, record observation only | none |
| 3. Localize | `harness-researcher` subagent: is the fix a lesson (our layer) or a runtime change (TrueForge)? Runtime candidates: sandbox readiness probe, tool-approval on built-in `exec` patterns (rm/cleanup), structured completion gate, subagent tool scoping, budget caps | none |
| 4. Fork & patch | Code Mode script in the sandbox on a local clone of the TrueForge fork (`git worktree` per candidate); change must be minimal and covered by a TrueForge unit test | **approval #1** (write to the fork) |
| 5. Prove | run the full mistake benchmark on the patched runtime vs. current: keep only if repetition and false-completion do not rise, controls hold, TrueForge's own test suite is green, and Qodo reports 0 bugs on the fork PR | automatic revert on any failure |
| 6. Propose | write the upstream PR body: reproduction case, before/after artifacts, test added, risk notes | **approval #2** (human reads the diff + evidence) |
| 7. Open upstream PR | GitHub MCP `create_pull_request` to `truefoundry/trueforge` | **approval #3** (the MCP tool itself is approval-gated) |

Rate limit: at most one upstream proposal per day; a proposal that is rejected upstream is recorded as
a correction (family M12 scope inflation / M23 deliverable mismatch) and feeds the next cycle.

## Why this is safe by construction

- Three independent human gates, the last one enforced by TrueForge's own tool-approval policy.
- The evidence bar is the same file-derived bar as lessons: artifacts under `results/`, verdicts
  read from files, never from prose (Qodo review of PR #2 forced this discipline on us first).
- The fork PR must pass Qodo with 0 bugs and TrueForge's tests before a human even sees the proposal.
- Scope is checked against the M12 case family: the diff must touch only the localized fix.

## Candidate first contributions (from what we hit today)

1. **Sandbox readiness probe** — some Daytona sandboxes reject multi-line execs with
   `fork/exec /usr/bin/bash: no such file` for their whole life; the harness could probe once at
   creation and recreate, instead of every agent re-discovering it.
2. **Sandbox pool accounting** — surface the Daytona sandbox id in `sandbox.created` so a runner
   can release it; today the event id is not the provider id.
3. **Approval on built-in exec patterns** — `require_approval_for_tools` covers MCP tools only;
   a regex policy for the built-in `exec` (e.g. `rm -rf`, cleanup steps) would give L4 gates to
   evidence-destroying commands.
4. **Per-subagent tool scoping** — `create_sub_agent` shares all tools; a `tools:` allowlist would let
   the falsifier be read-only.

Status: design only (2026-08-29). The agent has a Workflow G stub that stops at stage 3 and asks.
