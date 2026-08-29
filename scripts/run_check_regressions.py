"""Run regression tests for check interventions without the MCP transport.

Usage: python scripts/run_check_regressions.py M09_stale_report_train:m09_stale_derived M14_regression_train:m14_full_suite
Writes the same regress_compare artifacts the eval-runner writes.
"""
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # the eval server imports mcp; stub it on Windows exactly as tests/conftest.py does
    import mcp  # noqa: F401
except ImportError:
    fake = types.ModuleType("mcp"); fake_server = types.ModuleType("mcp.server"); fake_fm = types.ModuleType("mcp.server.fastmcp")
    fake_types = types.ModuleType("mcp.types")

    class _FastMCP:
        def __init__(self, *a, **k):
            self.settings = types.SimpleNamespace(port=0)

        def tool(self, *a, **k):
            return lambda f: f

        def run(self, *a, **k):
            pass

    fake_fm.FastMCP = _FastMCP
    fake_types.ToolAnnotations = lambda *a, **k: None
    sys.modules.update({"mcp": fake, "mcp.server": fake_server, "mcp.server.fastmcp": fake_fm, "mcp.types": fake_types})

from mcp_servers import eval_server as es  # noqa: E402

RULES = {
    "m09_stale_derived": "Before reporting a value taken from a derived artifact (report, bundle, summary), run the check; "
                         "if it says the artifact is older than its source or carries its own timestamp, regenerate it or report the source value and say the report is stale.",
    "m14_full_suite": "Before calling a test suite green, run the check; it executes every test file, not only the one named in the task. "
                      "If any file fails, report exactly which and do not claim green.",
}

for spec in sys.argv[1:]:
    case_id, script = spec.split(":")
    out = es._run_regression_sync(case_id, RULES[script], "", 2, "check", script)
    print(json.dumps({k: out[k] for k in ("case_id", "base_fails", "candidate_passes", "valid_regression_test", "artifact")}))
    for c in out["candidate"]["cases"]:
        print("   cand", c["score"], c["caps"], "|", c["final_message"][:160].replace("\n", " "))
