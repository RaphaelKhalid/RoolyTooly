"""Learning curve: score the bank as lessons accumulate, injecting checks only where retrieval selects them.

Usage: python scripts/learning_curve.py les_a les_b les_c [--parallel 5]
Phase 0 = bare; phase k = first k lessons. Each phase writes results/timeline_curve_<k>_*.json
(same shape as timeline points) plus results/learning_curve_<ts>.json with the series.
"""
import json
import sys
import time
import types
import uuid
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

from bench import cases as C  # noqa: E402
from bench.run import run_suite, summarize  # noqa: E402
from mcp_servers import eval_server as es  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
parallel = int(sys.argv[sys.argv.index("--parallel") + 1]) if "--parallel" in sys.argv else 5
all_l = {L["id"]: L for L in es._all_lessons()}
order = [all_l[i] for i in args]
cases = [c for c in C.CASES if c["split"] != "train" and not c["id"].startswith("LCB_DEMO")]
series = []
for k in range(len(order) + 1):
    active = order[:k]
    label = f"curve_{k}"
    print(f"=== phase {k}: lessons={[L['id'] for L in active]} cases={len(cases)} parallel={parallel}", flush=True)
    reports = [run_suite(es.harness_manifest(active, c["id"]), [c], 1, 1, label) for c in cases] if k else [run_suite(es.candidate_manifest(""), cases, 1, parallel, label)]
    results = [r for rp in reports for r in rp["results"]]
    summ = summarize(results)
    point = {"label": f"learning curve phase {k}", "ts": time.time(), "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "worker_model": es.worker_model(), "n_active_lessons": k,
             "active_lessons": [{"id": L["id"], "family": L["family"], "type": L.get("intervention_type")} for L in active],
             "summary": summ, "per_family": es._per_family(results), "cases": es._slim({"summary": summ, "results": results})["cases"]}
    p = ROOT / "results" / f"timeline_curve{k}_{int(time.time())}_{uuid.uuid4().hex[:4]}.json"
    p.write_text(json.dumps(point, indent=2), encoding="utf-8")
    series.append({"phase": k, "artifact": p.relative_to(ROOT).as_posix(), **{m: summ[m] for m in ("n_cases", "n_errors", "mean_score", "mistake_repetition_rate", "false_completion_rate", "control_pass_rate", "refusal_rate")}})
    print(json.dumps(series[-1]), flush=True)
out = ROOT / "results" / f"learning_curve_{int(time.time())}.json"
out.write_text(json.dumps({"lessons": args, "series": series}, indent=2), encoding="utf-8")
print("wrote", out.relative_to(ROOT))
