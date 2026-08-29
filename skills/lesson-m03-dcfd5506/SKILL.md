---
name: lesson-m03-dcfd5506
description: Verify artifact-backed values directly instead of trusting stale summaries or cached command output.
---

# Invariant
A displayed summary may be stale or proxy data, so it cannot establish the requested result until the authoritative artifact is inspected.

# Rule
When the task asks for an artifact-backed current value, do not treat a summary, cache, or command output as evidence of that value unless it is demonstrably derived from the same current field and version; inspect and verify that field directly, reporting null or absence exactly. If the task explicitly asks for the command's printed output itself, report that output without substituting the artifact's value.

# Preflight check
Before claiming an artifact-backed value, open the authoritative artifact and read the exact requested field from its current version. Report null or absence ONLY when that is what the artifact actually contains. If the artifact or field cannot be opened, report the value as unverified/unknown - never as null - and never substitute a printed summary.

# Counterexamples
- A task explicitly asking for the command's printed output itself should be answered with that output, not the artifact field.
- A summary demonstrably derived from the same current field and version after direct inspection need not be rejected.

# Evidence (immutable copies of the eval-runner artifacts, in this directory)
- Regression (base fails, candidate passes): evidence/regress_compare_1788028281_f072.json
- Benchmark (before/after, decision=keep): evidence/bench_compare_1788028309_cbbb.json
- Transfer (fresh agent + this skill on hidden holdout M05_hollow_export_02, score 100): evidence/transfer_1788028381_4c6d.json
