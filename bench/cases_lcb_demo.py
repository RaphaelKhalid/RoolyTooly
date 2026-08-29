"""Three LiveCodeBench-hard problems committed as demo cases, loaded from JSON.

Generated once by bench/livecodebench.make_cases(3, seed=0, difficulty="hard") so the eval server
does not need the 130 MB dataset shard. Ids LCB_DEMO_1..3; expected "benchmark" (the verdict is the
hidden-test result in out/results.json). An absent JSON file yields no cases rather than an error."""
import json
from pathlib import Path

_JSON = Path(__file__).with_name("lcb_demo_cases.json")
CASES = json.loads(_JSON.read_text(encoding="utf-8")) if _JSON.exists() else []
