# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

As of 2026-08-29 this repo contains only `hackathon-kickoff-prompt.md` — the full project brief. Read it before doing anything; it is the source of truth for scope, build order, and guardrails. No build/test/lint commands exist yet. **Update this file with real commands (install, run TrueForge, run the benchmark, run a single regression test) as soon as they exist.**

## What this is

A solo entry for the WeMakeDevs / TrueFoundry "Agent Harness" hackathon (deadline: plan for a complete demo by ~4 PM Sat Aug 29, 2026). Functional label: **the mistake-immune agent** — an agent that converts each human correction into a permanent, tested immunity (classified mistake family + durable rule + executable regression test), promoted through an approval gate into a skill ledger that a fresh agent loads.

Do **not** name the product. The user names things. The concept is settled — do not re-ideate.

## Architecture (planned — must run on TrueForge)

Judges must visibly see each of these, so keep them distinct and observable:

- **Subagents** with separated tools/context: `worker` (does the seeded task, makes the mistake), `lesson-compiler` (correction → family + rule + failing regression test), `falsifier` (attacks the rule for false positives / blanket-refusal degeneration).
- **Sandbox (Daytona)**: all regression tests and benchmark runs execute here. Keep/revert autoresearch loop: keep the candidate manifest only if the deterministic benchmark score improves.
- **MCP tools**: custom `lesson-ledger` MCP (append-only: mistake → family → rule → test → provenance), `eval-runner` MCP, and GitHub MCP (opens the promotion PR after approval).
- **Persistent session**: ledger and in-flight experiment must survive a page refresh (done live in the demo).
- **Approval gates**: lesson promotion AND the GitHub PR both pause for the human.
- **Skills**: promoted lessons are the transferable artifact loaded by a zero-history agent.

Scoring is **code inspecting event traces and artifacts**. Model-generated prose never counts as evidence; no LLM-as-judge for ground truth. The mistake-family benchmark lives in `agent-mistake-families-benchmark.md` (~22 families, 100-point scoring) — ask the user if it isn't in the repo.

## Build order

Vertical slice first, do not widen early: Preflight (TrueForge in WSL, `gh auth`, Qodo bootstrap PR) → Slice 1 (one seeded task end-to-end through transfer to a clean agent) → Slice 2 (3–5 more families + hidden holdout cases) → Dashboard (prefer TrueForge's bundled UI/traces) → Demo hardening (scripted, rehearsed, chaos-tested) → README/reproducibility.

The demo must have a guaranteed ending: seeded tasks with known ground truth, deterministic checkers, retry or pre-verified fallback lesson if a model call flakes.

## Environment

- Windows 11 host, Node 24, npm, git, `gh` installed. No Docker.
- **TrueForge crashes on native Windows.** Run it in WSL (Ubuntu, Node 22 via nvm) at http://localhost:8790 with SQLite state. If it doesn't run, reinstall in WSL — never natively.
- Budget ~$50 API credit: cheap/low-reasoning models for repeated eval runs, strong models only for lesson compilation and adjudication. Log usage.
- Credentials (OpenAI key, Daytona key, scoped GitHub token, Qodo) are provided by the user on request — never scrape or guess them.

## Standing guardrails

- Every substantive change goes through a **Qodo-reviewed pull request** on the public GitHub repo.
- Nothing public and no PR/message/post to any external service without asking the user first.
- Don't fabricate results; don't report a benchmark as run unless the score artifact exists; don't delete experiment evidence before the user has seen it. Show failing output when something fails.
- Batch questions when blocked; otherwise keep moving.

## TrueForge facts (verified 2026-08-29 against v0.1.4 API)

- Start: in WSL, `source ~/.nvm/nvm.sh && npx -y @truefoundry/trueforge` (launch detached from Windows with `Start-Process wsl.exe` — a plain `nohup … &` dies with the shell). OpenAPI at `http://localhost:8790/api/v1/openapi.json`; Swagger at `/api/v1/docs`. No auth in standalone mode.
- Everything is API-configurable: `PUT /api/v1/settings/{model-providers,mcp-servers,sandbox-providers,skills}`, `POST /api/v1/agents` (`{name, manifest: AgentSpec}`), `POST /api/v1/sessions`, `POST /api/v1/sessions/{id}/turns`, `GET /api/v1/sessions/{id}/events` (the trace the scorer reads).
- **There is no hook system.** Prevention levers are: `instructions`, `messages` (seed), `skills`, `mcp_servers[].enable_tools/disable_tools/require_approval_for_tools` (`@all`/`@write`/`@destructive`/tool name), `response_format` (JSON schema), `config.sandbox`, `config.iteration_limit`. Anything else (deterministic checkers, keep/revert loop) lives in our own orchestrator around the API.
- Approval gate = `tool.approval_required` event on the turn stream; resume with a `user.tool_approval` event (`{thread_id, tool_call_id, approval:{status:"allow"|"deny"}}`).
- Subagents are dynamic only (`create_sub_agent` tool): share the root's MCP tools + sandbox, one level deep, cannot ask the user questions, run concurrently.
- MCP servers must be **remote (HTTP URL)** — custom MCPs run as local HTTP servers in WSL (or on Vercel). GitHub MCP = `https://api.githubcopilot.com/mcp/` with a `Authorization: Bearer <PAT>` header.
- Skills are **git-backed** (`{url: github repo, path, ref}`) and require the sandbox — so a promoted lesson = a skill directory committed to this repo.
- Sandbox = Daytona only (key needs Sandboxes + Snapshots-write). Local SRT fallback is unavailable in this WSL until `socat` and `ripgrep` are installed.
