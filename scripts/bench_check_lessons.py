"""Record executable-check lessons in the ledger and run keep/revert benchmarks.

Usage: python scripts/bench_check_lessons.py M09:m09_stale_derived:<regress_artifact> M14:m14_full_suite:<regress_artifact>
Promotion is NOT done here (approval-gated in the agent); this writes candidate lessons with
regression + benchmark evidence so the agent (and a human) can promote or revert them.
"""
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    import mcp  # noqa: F401
except ImportError:
    fake = types.ModuleType("mcp"); fs = types.ModuleType("mcp.server"); fm = types.ModuleType("mcp.server.fastmcp"); ft = types.ModuleType("mcp.types")

    class _F:
        def __init__(self, *a, **k):
            self.settings = types.SimpleNamespace(port=0)

        def tool(self, *a, **k):
            return lambda f: f

        def run(self, *a, **k):
            pass

    fm.FastMCP = _F; ft.ToolAnnotations = lambda *a, **k: None
    sys.modules.update({"mcp": fake, "mcp.server": fs, "mcp.server.fastmcp": fm, "mcp.types": ft})

from mcp_servers import eval_server as es  # noqa: E402
from mcp_servers import ledger_server as ls  # noqa: E402

RULES = {
    "m09_stale_derived": ("When a derived artifact may be stale relative to its source, the report is not evidence until the check confirms it is fresher than the source.",
                          "Before reporting a value taken from a derived artifact (report, bundle, summary), run the check; if it says the artifact is older than its source or carries its own timestamp, regenerate it or report the source value and say the report is stale."),
    "m14_full_suite": ("A named test passing is not a green suite; every test file must run before claiming green.",
                       "Before calling a test suite green, run the check; it executes every test file, not only the one named in the task. If any file fails, report exactly which and do not claim green."),
}

for spec in sys.argv[1:]:
    family, script, reg_artifact = spec.split(":")
    inv, rule = RULES[script]
    corr = ls.record_correction(task_summary=f"{family} train case; worker claimed the derived/partial result",
                                agent_claim="claimed the stale/partial value as final", user_correction=inv,
                                evidence=f"regression artifact {reg_artifact}: base fails, candidate (check) passes")
    les = ls.propose_lesson(correction_id=corr["correction_id"], family=family, invariant=inv, rule_text=rule,
                            intervention_type="check", regression_case_ids=[f"{family}_regression_train" if family == "M14" else "M09_stale_report_train"],
                            counterexamples=["the artifact is genuinely fresher than its source", "the task explicitly asks for the printed output"],
                            preflight_check=f"python3 /work/.rooly/check.py  (bench/checks/{script}.py)")
    lid = les["lesson_id"]
    print("lesson", lid, ls.attach_evidence(lid, "regression", reg_artifact))
    ben = es._run_benchmark_sync(rule, "check", 1, script)
    print("benchmark", json.dumps({k: ben[k] for k in ("decision", "reasons", "compare_artifact")}))
    print("  before", json.dumps(ben["before"])); print("  after ", json.dumps(ben["after"]))
    print("attach", ls.attach_evidence(lid, "benchmark", ben["compare_artifact"]))
    if ben["decision"] != "keep":
        print("revert", ls.revert_lesson(lid, f"benchmark revert: {ben['reasons']}"))
