# Best practices (auto-generated from promoted agent lessons)

These rules were compiled by the RoolyTooly immune-system agent from human corrections, each backed by
a regression test the pre-correction agent fails and a before/after benchmark that improved. They apply
to code, tests, scripts and PR descriptions in this repository.

## Standing rules

- A zero exit code or a printed success line is not evidence that the requested outcome exists; code
  that reports results must read them from the produced artifact and report null/empty explicitly.
- Evidence a reviewer will rely on (benchmark scores, test results) must come from a file produced by
  the run, referenced by path — never from a caller-supplied summary.
- Scripts must not delete raw outputs, checkpoints or logs before the results have been evaluated.

## Promoted lessons

### M03 — A displayed summary may be stale or proxy data, so it cannot establish the requested result until the authoritative artifact is inspected.

- **Rule:** When the task asks for an artifact-backed current value, do not treat a summary, cache, or command output as evidence of that value unless it is demonstrably derived from the same current field and version; inspect and verify that field directly, reporting null or absence exactly. If the task explicitly asks for the command's printed output itself, report that output without substituting the artifact's value.
- **Pre-flight check:** Before claiming an artifact-backed value, confirm the artifact and exact requested field were opened and verified against the same current version; otherwise report the field as null/absent or do not substitute a summary.
- **Do not flag when:** A task explicitly asking for the command's printed output itself should be answered with that output, not the artifact field.; A summary demonstrably derived from the same current field and version after direct inspection need not be rejected.
- **Provenance:** lesson `les_dcfd5506`, correction `cor_09472dbf`, intervention type `check`
