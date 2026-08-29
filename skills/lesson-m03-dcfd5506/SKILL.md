---
name: lesson-m03-dcfd5506
description: Verify artifact-backed values directly instead of trusting stale summaries or cached command output.
---

# Invariant
A displayed summary may be stale or proxy data, so it cannot establish the requested result until the authoritative artifact is inspected.

# Rule
When the task asks for an artifact-backed current value, do not treat a summary, cache, or command output as evidence of that value unless it is demonstrably derived from the same current field and version; inspect and verify that field directly, reporting null or absence exactly. If the task explicitly asks for the command's printed output itself, report that output without substituting the artifact's value.

# Preflight check
Before claiming an artifact-backed value, confirm the artifact and exact requested field were opened and verified against the same current version; otherwise report the field as null/absent or do not substitute a summary.

# Counterexamples
- A task explicitly asking for the command's printed output itself should be answered with that output, not the artifact field.
- A summary demonstrably derived from the same current field and version after direct inspection need not be rejected.

# Evidence
- Regression: results/regress_compare_1788028281_f072.json
- Benchmark: results/bench_compare_1788028309_cbbb.json
