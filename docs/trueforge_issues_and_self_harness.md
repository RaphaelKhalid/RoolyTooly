# TrueForge open issues + the "self-harness" paper

Research snapshot, 2026-08-29. Read-only: no code changes, nothing filed upstream.

## Part A — the self-harness paper

Found the exact-name paper: **"Self-Harness: Harnesses That Improve Themselves"**, arXiv [2606.09498](https://arxiv.org/abs/2606.09498) (June 2026). Closest related work, labelled as a substitute only where noted: **SIA: Self Improving AI with Harness & Weight Updates**, arXiv [2605.27276](https://arxiv.org/abs/2605.27276).

**Method (Self-Harness).** A three-stage loop from a *minimal* initial harness: (1) Weakness Mining runs the agent on a benchmark and mines model-specific failure patterns from execution traces; (2) Harness Proposal generates diverse, minimal, targeted edits to declared "harness surfaces" (prompt/scaffold/tool code) tied to a mined weakness; (3) Proposal Validation re-evaluates each candidate under the same benchmark protocol and accepts it into the harness lineage only under a non-regressive rule. Iterated across models/benchmarks, the harness accumulates accepted edits.

**Measuring improvement.** Evaluated on three benchmarks (Terminal-Bench-2.0, SWE-bench Verified, AppWorld) across three models, using held-in pass rate (same family as the mined edit) **and** held-out pass rate (a disjoint split) specifically to catch overfitting to the mining set. Reported relative gains up to 132%; every final harness improved both held-in and held-out rates.

**Safeguards.** Acceptance is regression-gated — a candidate merges only if it doesn't regress the benchmark protocol, with held-out rate checked so an edit can't just memorize mining traces. The initial harness is deliberately minimal, bounding blast radius to declared surfaces only. The available text does not describe a human-in-the-loop approval step — validation is automated regression testing, not human review (a gap relative to RoolyTooly's approval gates).

**SIA (substitute, for contrast).** Extends the idea to also update model *weights* via a Feedback-Agent that jointly proposes harness and weight updates — relevant only if RoolyTooly ever couples lesson-compilation to fine-tuning rather than pure skill/prompt edits; out of scope today.

**Three ideas to borrow:**
1. **Held-in vs. held-out pass rate per lesson**, not just a pass/fail transfer test — track both numbers in the ledger so "did this just memorize the training case" is visible at a glance, matching the paper's core anti-overfitting signal.
2. **Bound each harness edit's surface declaratively.** Our contribution-pipeline scope check ("diff must touch only the localized fix," `docs/trueforge_contribution_pipeline.md` stage 4) is the same idea applied to upstream PRs — worth applying identically to our own `manifests/immune_root.json` / `skills/` edits.
3. **Minimal-initial-harness discipline.** Before adding a new lever (hooks, budgets, tool scoping) to our own agent, ask whether a *skill* (prompt-level) can do it first — the paper's gains came from small, targeted edits, not scaffold expansion. Matches our "skills before code" bias, now with a citable justification.

Sources: [arXiv 2606.09498](https://arxiv.org/abs/2606.09498), [HTML](https://arxiv.org/html/2606.09498v1), [arXiv 2605.27276](https://arxiv.org/abs/2605.27276), [bdtechtalks.com summary](https://bdtechtalks.com/2026/07/13/ai-agents-self-improving-harness/).

## Part B — TrueForge open issues scan

`gh api --paginate repos/truefoundry/trueforge/issues?state=open&per_page=100` → 74 open items, 35 are PRs (excluded), **39 open issues** as of 2026-08-29.

| # | Title | Labels | Age | Category | Already hit by us? |
|---|---|---|---|---|---|
| 283 | Stream sandbox file downloads end to end | — | 12d | sandbox | no |
| 284 | Migrate offset pagination to keyset cursors | needs-maintainer-attention | 12d | other | no |
| 301 | Cannot delete configured model providers | — | 11d | UI | no |
| 318 | Code Mode destructive-tool gate fails open on unannotated tools | needs-discussion | 10d | approvals | **yes** — matches "no approval on built-in exec" |
| 341 | OpenAI SSO option with api key | needs-maintainer-attention | 9d | other | no |
| 342 | Openrouter as first class provider | — | 9d | other | no |
| 352 | show chat title on top | bug,ui | 9d | UI | no |
| 357 | Generate system instructions from chat history | — | 9d | UI | no |
| 363 | Show logged in User Profile | ui | 9d | UI | no |
| 370 | Integrate Modal as Sandbox Provider | help wanted | 8d | sandbox | no |
| 371 | Integrate OpenSandbox as a Sandbox Provider | help wanted | 8d | sandbox | no |
| 375 | Adaptive shared execution budgets (root+subagents) | needs-maintainer-attention | 8d | subagents | partial — related to our 10-min turn timeout, but $/token, not wall-clock |
| 379 | Operator-configured lifecycle hooks (pre/post tool use) | needs-maintainer-attention | 8d | tools & MCP | **yes** — exact match for "no hooks" |
| 387 | Integrate E2B as Sandbox Provider | help wanted | 7d | sandbox | no |
| 397 | Stop the sandbox when cancel API cancels a turn | enhancement,help wanted | 7d | sandbox | no |
| 404 | Subagent permission enhancements | enhancement,needs-maintainer-attention | 6d | subagents | **yes** — exact match for "no per-subagent tool scoping" |
| 405 | Background execution mode for subagents | enhancement,needs-maintainer-attention | 6d | subagents | related to our 10-min turn timeout workaround |
| 406 | Add option to rename chats | enhancement,needs-maintainer-attention | 6d | UI | no |
| 410 | Expo MCP Connector | — | 5d | tools & MCP | no |
| 413 | Declarative per-tool authorization backend (vendor pitch) | needs-maintainer-attention | 4d | approvals | related, not a fix |
| 416 | TFY sandbox uploadFile fails >~96 KiB (argv E2BIG) | needs-discussion | 4d | sandbox | no |
| 419 | Encrypt persisted secrets at rest | needs-discussion | 4d | other | no |
| 421 | docker-compose exposes Postgres/Redis on 0.0.0.0 | needs-maintainer-attention | 4d | other | no |
| 422 | OIDC login leaks raw error into redirect | needs-maintainer-attention | 4d | other | no |
| 424 | ChatFileDownload href built without URL-encoding | ui,needs-maintainer-attention | 4d | UI | no |
| 427 | `npx @truefoundry/trueforge` fails on native Windows | bug,windows | 3d | other | **yes** — matches "WSL only" fact in CLAUDE.md |
| 429 | Delete controls for saved agents | ui | 3d | UI | no |
| 433 | No cancellation during first-turn sandbox init | needs-maintainer-attention | 3d | sandbox | related — same init-flakiness area as our readiness probe |
| 441 | Sidebar minimize arrow looks funny | ui,needs-maintainer-attention | 2d | UI | no |
| 443 | Option to pin chats | enhancement,needs-maintainer-attention | 2d | UI | no |
| 444 | Add an option to search chats | enhancement,needs-maintainer-attention | 2d | UI | no |
| 445 | Add a Projects option to organize chats | enhancement,needs-maintainer-attention | 2d | UI | no |
| 447 | [local-mode] turns end at finish_reason=tool_calls | bug | 2d | other | no |
| 452 | Update agents.md re: repeated flagging | — | 2d | other | no |
| 461 | Daytona setup misreports write:snapshots as invalid key | help wanted | 1d | sandbox | related — Daytona onboarding fragility |
| 466 | Self-hostable sandbox provider (Daytona closed-source) | needs-maintainer-attention | 1d | sandbox | no |
| 482 | Standalone sandbox bootstrap fails installing pydantic (proxy) | — | 1d | sandbox | related — another bootstrap flake |
| 490 | Bright Data API key requires "Bearer" prefix | bug,needs-maintainer-attention | 0d | tools & MCP | no |
| 491 | Bring-your-own per-property connector config | — | 0d | tools & MCP | no |

Totals: sandbox 12, UI 10, other 9, tools & MCP 3, subagents 3, approvals 2.

**Scope note:** of the six gaps we name in `docs/trueforge_contribution_pipeline.md`/`CLAUDE.md` (sandbox readiness flake, sandbox pool accounting, no approval on built-in exec, no per-subagent tool scoping, ~10-min turn timeout, no hooks), 3 map to open issues: **#318** (exec approval), **#404** (subagent scoping), **#379** (hooks). No open issue tracks sandbox pool accounting or the ~10-min wall-clock turn timeout as such (#375/#405 are adjacent — cost budget and background execution, not a turn cap) — those two stay candidate new reports, not confirmed duplicates.

### Top 5 issues where a contribution would help essentially all TrueForge users

Random feature requests (UI polish, new providers, connector config) are out of scope — only issue-backed or near-universal harness-correctness items are listed.

1. **#318 — Code Mode destructive-tool gate fails open on unannotated tools.** Evidence: we hit the adjacent gap (no approval on built-in `exec` at all — pipeline doc candidate #3); no `results/*.json` reproduces #318 itself. Fix sketch: read `destructive_hint`/`read_only_hint` (mcp≥2.0 snake_case, fallback to old camelCase) and default unannotated tools to destructive instead of `False` — single-function diff in `mcp_client.py`'s `_is_destructive()`. Risk: defaulting to destructive routes more tools through approval than today, which will surprise users expecting silent Code-Mode execution — ship with a changelog note.

2. **#379 — Operator-configured lifecycle hooks.** Evidence: CLAUDE.md records "no hook system" as a verified v0.1.4 fact; we built custom approval-gated MCP tools (`mcp_servers/*`) as a workaround for the `pre_tool_use` veto this issue proposes. Fix sketch: the author reports a working implementation with tests/docs ready — this is "review and unblock," not "build": verify a deny-by-hook surfaces as a normal tool-approval denial to the model (per our M-family checks) and report on the issue. Risk: hook commands run as operator-trusted host code — needs the proposal's stated "off by default."

3. **#404 — Subagent permission enhancements (no per-subagent tool scoping).** Evidence: pipeline doc candidate #4 — our `falsifier` subagent cannot be made read-only because `create_sub_agent` shares all tools with the root, a reproducible gap in `manifests/immune_root.json`. Fix sketch: optional `tools:` allowlist on subagent creation, enforced at the toolset-decorator seam #379 already proposes (`toolSetDecorators` on `AgentCapability`) so a subagent's tools are a subset. Risk: API-shape (allowlist vs. denylist, inheritance default) needs maintainer buy-in — a comment with our concrete use case beats a speculative PR right now.

4. **#427 — `npx @truefoundry/trueforge` fails to start on native Windows.** Evidence: README.md/CLAUDE.md both instruct "run inside WSL" as a hard-won operational fact; we never captured a native-Windows repro artifact (we just route around it). Fix sketch: none yet — contribution should start at "reproduce and attach a stack trace," not a patch. Risk: low (read-only repro), but no fix sketch exists until the native-Windows error is actually captured.

5. **Sandbox init flakiness cluster (#433 first-turn cancel, #461 Daytona permission misclassification, #482 pip/proxy bootstrap failure).** Not individually top-tier, but as a cluster they corroborate our own finding. Evidence: `bench/run.py`'s heredoc warm-up probe exists because Daytona sandboxes intermittently reject multi-line execs with `fork/exec /usr/bin/bash` (CLAUDE.md), reproduced in `results/base_all_1788027200.json`, `results/base_seq_1788025765.json`, `results/bench_after_1788031590_70a7.json`, `results/bench_after_1788031992_dfb3.json`, `results/bench_before_1788031543_62e6.json`. None share a root cause with ours (cancellation propagation vs. permission-error mapping vs. proxy connectivity), but all four point at under-hardened sandbox init. Fix sketch: pipeline doc candidate #1 — a `SandboxProvider`-level "verify ready" probe at creation, surfaced once instead of per-caller heredoc probes like ours. Risk: must stay provider-generic — a Daytona-shaped probe would miss #461/#482's causes.

No speculative feature work (new providers, chat UI, connector config) is included even though such issues exist in the open list.
