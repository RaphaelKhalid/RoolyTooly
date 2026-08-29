# Neuro-symbolic control plane for RoolyTooly

Status: architecture proposal; this document does not claim that the runtime changes below are implemented.

## Decision

RoolyTooly should not add a generic knowledge graph, a large ontology service, or an LLM judge. It should
turn the symbolic half of its existing loop into a small, typed control plane:

- neural components handle ambiguous work: classify a correction, draft a causal lesson, retrieve related
  rules, propose counterexamples, and generate candidate interventions;
- symbolic components hold authority: normalize trace facts, validate lesson contracts, plan comparable
  experiments, derive verdicts, produce proof certificates, and control promotion or revocation;
- every neural-to-symbolic handoff is typed, persisted, and independently replayable.

This is an evolution of the current architecture, not a rewrite. RoolyTooly already resembles an
LLM-Modulo system: models generate candidates and deterministic checkers verify executions. The largest gain
comes from making that interaction bidirectional and proof-carrying instead of passing prose and summary JSON
between stages.

The design follows three useful research patterns:

- [LLM-Modulo](https://arxiv.org/abs/2402.01817) treats the model as a flexible candidate generator and an
  external verifier as the authority, with verifier feedback returned to the model.
- [AlphaGeometry](https://research.google/pubs/solving-olympiad-geometry-without-human-demonstrations/)
  alternates neural proposals with symbolic deduction rather than asking the neural component to certify
  itself.
- [DSPy](https://arxiv.org/abs/2310.03714) separates declarative module contracts from optimization against
  explicit metrics. RoolyTooly's equivalent should optimize typed intervention specs against deterministic
  benchmark policy, not optimize free-form prompts directly.

Qodo Agentic Toolbox rule search on 2026-08-29 also shaped the proposal. The applicable workspace rules were: follow the
canonical `AGENTS.md` (`2986926`), verify artifact-backed values rather than summaries (`2988272`), never use
stub outputs as evidence of model behavior (`2987104`), validate generated scenario counts (`2987579`), keep
new interfaces fully typed (`2986940`), and document verification sources for external identifiers (`2987265`).
Workspace-global iOS, image, Cekura-template, and LLM-judge rules were retrieved but rejected as out of scope or
in conflict with this repository's deterministic-evidence guardrail.

## What exists now

The current system has strong symbolic foundations:

- [`bench/checkers.py`](../bench/checkers.py) derives scores from TrueForge events and successful tool
  responses instead of trusting model narration.
- [`mcp_servers/eval_server.py`](../mcp_servers/eval_server.py) applies deterministic regression and
  keep/revert policy, including coverage and control-regression gates.
- [`mcp_servers/ledger_server.py`](../mcp_servers/ledger_server.py) is append-only, reconstructs state by
  replay, and exposes promotion and revocation transitions. Promotion is approval-gated in the current root
  manifest; revocation is marked destructive but is not yet listed in that manifest's approval policy.
- [`autoresearch/sweep.py`](../autoresearch/sweep.py) searches bounded intervention types and keeps only a
  benchmark winner.
- [`manifests/immune_root.json`](../manifests/immune_root.json) separates the neural compiler, falsifier, and
  researcher roles from the worker.

Those pieces make RoolyTooly "neuro-symbolic already, but loosely coupled." The important gaps are:

1. **The handoff is mostly prose.** Corrections, invariants, rules, counterexamples, and evidence summaries
   are strings. The symbolic layer cannot test whether a rule's trigger, evidence obligation, exception, and
   claimed scope agree.
2. **There is no canonical fact model.** The checker repeatedly infers facts from regexes over commands and
   final text. Other components cannot query or explain the same facts without reimplementing that logic.
3. **A benchmark file carries a verdict, not a proof.** The ledger copies file-derived fields, but it does not
   bind evidence to artifact hashes, a checker version, a policy version, exact paired case IDs, manifest
   hashes, or model configuration. Old decisions therefore cannot be safely replayed under a newer policy.
4. **Vocabulary has drifted.** The ledger accepts `rule`, `seed`, `constraint`, `check`, `gate`, and
   `structural`; the evaluator can directly test only `rule`, `seed`, and `constraint`. A neural compiler can
   emit a valid ledger lesson that the experiment runner cannot execute as typed.
5. **Search is not causal planning.** The self-improvement loop selects the current worst family, and sweeps
   prompt-level variants in a fixed order. It does not use rule conflicts, coverage gaps, prior failed
   interventions, or the violated evidence obligation to choose the next experiment.
6. **Rule retrieval can become a new single point of failure.** Semantic search is useful for recall, but a
   missing, stale, or irrelevant result must never silently remove active protections. Retrieval needs a
   symbolic filter, a receipt, and a safe fallback.
7. **Lifecycle propagation is incomplete.** The ledger can revoke a lesson, but skill registration, runtime
   selection, Qodo rules, and dashboard state need one versioned lifecycle so a revoked or superseded rule
   cannot remain active elsewhere.

## Target architecture

```text
human correction + TrueForge trace
              |
              v
     neural lesson compiler  <---- Qodo rule search (candidate recall)
              |
         typed LessonSpec
              v
 symbolic validator + conflict/subsumption check
              |
              v
 neural falsifier / counterexample generator
              |
       typed CounterexampleSpecs
              v
     symbolic experiment planner
              |
       exact paired ExperimentSpec
              v
 Daytona runs -> trace-to-fact compiler -> ProofCertificate
              |                            |
              +----- verifier feedback ----+
              |
 deterministic promotion policy -> human approval -> active skill + Qodo rule
              ^                                      |
              +--------- monitoring/revocation ------+
```

### 1. A typed intermediate representation

Add a stdlib-only package such as `harness/symbolic/` with frozen dataclasses and JSON codecs:

- `ArtifactRef`: relative path, SHA-256, byte length, producing job, generated time;
- `Fact`: predicate, typed arguments, truth state (`true`, `false`, `unknown`), and source `ArtifactRef`;
- `LessonSpec`: schema version, family, causal invariant, trigger predicates, required evidence predicates,
  allowed claims, exceptions, intervention type, rule text, and regression case IDs;
- `CounterexampleSpec`: facts under which the trigger looks similar but the rule must not fire;
- `ExperimentSpec`: exact base/candidate manifests, model, case IDs, repeat seeds, budget, checker version, and
  policy version;
- `ProofCertificate`: experiment hash, artifact hashes, paired coverage, derived facts, and policy result. The
  later human-approved status event points back to this certificate by hash.

The model may propose these objects, but code validates them. Invalid family/case combinations, unsupported
interventions, empty obligations, impossible exceptions, or missing provenance are rejected before an
expensive model run.

### 2. One fact compiler for every verifier

Normalize TrueForge events into a small vocabulary shared by checkers, promotion policy, dashboards, and
falsifier feedback. Example predicates:

```text
requested(target)
executed(command, exit_code)
produced(path, sha256)
read(path, sha256)
observed(field, value, source_path)
claimed(predicate, value)
approved(action)
regenerated(path)
```

Then express mistake-family rules as deterministic predicate functions (Datalog-like semantics without a new
runtime dependency):

```text
supported(claim) :- claimed(claim), requires(claim, evidence), observed(evidence).
fabricated_completion :- claimed(done), requires(done, evidence), not supported(done).
destructive_without_gate :- executed(action), destructive(action), not approved(action).
```

Use three-valued logic. `unknown` blocks promotion or a completion certificate, but it does not automatically
prove real-world misconduct. A closed-world assumption is safe only inside a controlled benchmark trace whose
event coverage is itself proven complete.

### 3. Proof-carrying promotion

Move `decide()` into the symbolic policy module and make both the evaluator and ledger call the same function.
The compare artifact should contain inputs and a certificate, not an authoritative `decision` string. At
promotion time the ledger must:

1. resolve and hash every referenced artifact;
2. verify the artifact and case schemas;
3. verify identical paired case IDs and required run counts;
4. reject environmental errors or unequal scored coverage according to the versioned policy;
5. derive base failure, candidate success, fabrication, refusal, evidence, and control facts;
6. recompute the verdict with the current policy, or require an explicit migration/revalidation for an older
   policy;
7. append the proof hash and policy version to the status event.

This makes promotion independently replayable and detects artifact mutation, stale policy decisions, survivor
bias, and mismatched before/after populations.

### 4. A symbolic experiment planner

Replace fixed prompt sweeping with a planner over a small intervention lattice:

```text
instruction rule -> seed/demonstration -> response constraint -> executable check -> approval gate -> structural change
```

`check`, `gate`, and `structural` are proposed future planner levels. The current evaluator and sweep implement
only `rule`, `seed`, and `constraint`; the additional levels must not be passed to them until the surrounding
orchestrator/MCP layer has concrete semantics and tests for each one.

For each lesson, the planner should choose the least invasive intervention whose preconditions are executable.
It should use:

- the violated obligation from the fact graph;
- prior interventions and counterexamples for the family;
- uncovered train/holdout/control/adversarial cells;
- conflicts or subsumption with active lessons;
- estimated run cost from the existing spend guard.

The neural researcher still proposes wording and new counterexamples. The planner decides what must be tested,
which case pairs are comparable, and when evidence is insufficient. Failed experiments become symbolic facts
that prevent repeating the same intervention under the same conditions.

### 5. Hybrid Qodo retrieval, not Qodo-as-oracle

Fable's Qodo Agentic Toolbox work should own the adapter. The symbolic control plane should consume it through
a narrow interface and enforce these rules:

- Query Qodo with both task text and a structured signature: family hypothesis, action type, claim type,
  evidence obligation, tool surface, and intervention level.
- Treat returned rules as candidates. Deterministically filter them by active ledger status, trigger
  predicates, exceptions, and scope before injection.
- Always inject a tiny set of universal safety invariants. If Qodo is unavailable or its response is invalid,
  fall back to deterministic family matching or the current active set; never write an empty selector index
  that silently disables all lessons.
- Persist a retrieval receipt under `results/`: query hash, ledger hash, Qodo scope, returned rule IDs in
  ranked order (and relevance scores if the API exposes them), accepted/rejected IDs with reasons, and
  generation time. That receipt is part of evaluation provenance.
- Store the Qodo rule ID in an append-only ledger mapping. Sync, update, revoke, and supersede by ID; do not
  infer idempotency by semantically searching a truncated rule name.
- Respect Qodo permissions and returned lifecycle state. If the caller can only create pending suggestions or
  cannot deactivate a stale rule, record that state and exclude the rule deterministically at runtime instead
  of claiming external deactivation succeeded.
- Surface authentication, parse, timeout, and create/update errors. A search failure is `unknown`, never an
  empty successful result.

This lets Qodo provide scalable neural recall across hundreds of rules while the harness retains deterministic
authority over applicability and promotion.

### 6. Conflict, subsumption, and lifecycle reasoning

Before proposing a new lesson, compare its predicates with active lessons:

- same trigger + same obligation -> merge evidence or supersede wording;
- broader trigger + same action -> require extra benign controls;
- contradictory required actions -> block and ask the falsifier for a discriminating predicate;
- active lesson invalidated by newer proof -> approval-gated revoke, followed by skill/Qodo/runtime
  deactivation and a timeline point.

The ledger remains append-only. New records such as `proof`, `external_rule`, and `supersession` extend replay;
existing records receive a schema-v1 compatibility decoder rather than an in-place migration.

## Implementation sequence

### PR A — symbolic kernel and evidence integrity

- Add `harness/symbolic/models.py`, `facts.py`, `policy.py`, and `proofs.py` using only the standard library.
- Centralize the intervention enum and schema validation.
- Refactor `bench/checkers.py` to emit facts while preserving current result JSON.
- Refactor evaluator and ledger to derive verdicts through one versioned policy.
- Add artifact hashing and `ProofCertificate`; require revalidation for legacy evidence.
- Test tampered artifacts, stale policy versions, mismatched case IDs, unequal coverage, environmental errors,
  and unsupported interventions.

Acceptance: no lesson can become active unless `verify_certificate()` independently reproduces the promotion
decision from immutable referenced artifacts.

### PR B — typed compiler/falsifier contracts

- Make the lesson-compiler return `LessonSpec` JSON and the falsifier return typed counterexamples and a
  discriminating predicate.
- Validate every object before `propose_lesson` or an eval job starts.
- Add backward-compatible ledger replay and expose validation errors as normal MCP results.
- Add conflict/subsumption checks against active lessons.

Acceptance: malformed family, scope, intervention, evidence obligation, or exception combinations fail before
spend; duplicate lessons link to existing provenance instead of creating parallel rules.

### PR C — Qodo retrieval receipts and safe selection

- Integrate Fable's adapter behind a `RuleRetriever` protocol; do not duplicate its CLI work.
- Add deterministic filtering, fail-safe fallback, append-only Qodo ID mappings, and retrieval receipts.
- Make `harness_manifest(case=...)` inject only accepted lessons plus universal invariants.
- Add fake-CLI tests for auth failure, invalid JSON, timeout, duplicate sync, stale/revoked rules, and empty
  search results.

Acceptance: a Qodo outage cannot reduce the protections below the deterministic fallback, and every injected
rule has a replayable selection reason.

### PR D — planner and counterexample coverage

- Add the intervention lattice and a coverage matrix by family, split, intervention, model, and checker policy.
- Feed verifier counterexamples back to the neural researcher for bounded revision.
- Generate metamorphic variants whose surface changes while the causal predicates and ground truth remain
  fixed; deterministic validators reject invalid generated cases.
- Record failed interventions so the self-improvement loop does not repeat equivalent experiments.

Acceptance: the planner can explain why it selected the next case/intervention and stops with `unknown` when
coverage is insufficient instead of declaring a family closed.

### PR E — lifecycle and demo surface

- Add `revoke_lesson` to the root manifest's explicit approval policy and test that TrueForge pauses before
  the status transition is appended.
- Propagate promote/revoke/supersede transitions to skills, Qodo mappings, runtime selection, and timeline
  evaluation.
- Show the fact chain and proof certificate in the dashboard: claim -> required evidence -> observed fact ->
  verdict -> artifact hash -> status.
- Add an end-to-end chaos test covering process restart, stale selector cache, artifact mutation, Qodo outage,
  candidate environment error, approval denial, and revocation.

Acceptance: a judge can click from any displayed decision to the exact facts, immutable artifacts, policy
version, approval, and resulting active/revoked rule.

## What not to do before the demo

- Do not add Neo4j, a vector database, Prolog, or Z3 merely to use a neuro-symbolic label. Predicate functions,
  typed dataclasses, JSON, and SHA-256 are enough for the present rule volume.
- Do not let an LLM, Qodo result, dashboard snapshot, or stored `decision` field become the promotion oracle.
- Do not treat neural confidence as truth. Low-confidence classification means broaden the experiment or ask
  for approval; it never relaxes a hard invariant.
- Do not optimize mean score before evidence completeness, fabricated-completion caps, controls, and mistake
  repetition. The objective remains lexicographic and versioned.

## Hackathon proof story

The resulting demo remains simple: a human correction is compiled by a neural agent into a typed hypothesis;
a symbolic engine exposes the violated obligation, selects a bounded experiment, and produces a machine-checkable
certificate; a human approves promotion; a fresh TrueForge agent receives the verified lesson; and a later
counterexample can revoke it through the same proof path. The novelty is not "we added rules." It is that the
harness learns with neural flexibility while every durable change carries a replayable symbolic proof.
