"""Run benchmark cases against an agent manifest and write a score artifact.

  python -m bench.run --manifest manifests/worker_base.json --split train --out results/x.json
  python -m bench.run --manifest manifests/worker_base.json --ids M05_hollow_report_01 --repeat 3

Each case runs in a fresh TrueForge session (fresh Daytona sandbox, zero history). The score
artifact is the only thing that counts as evidence of a benchmark run.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import tf  # noqa: E402
from bench import cases as C  # noqa: E402
from bench.checkers import check_case  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


ENV_ERROR_MARKERS = ("fork/exec", "no such file or directory: /usr/bin", "sandbox unavailable",
                     "failed to create sandbox", "Sandbox initialization failed", "disk limit exceeded",
                     "ECONNREFUSED")


def env_error(events: list[dict]) -> str | None:
    """A sandbox/provider failure is not an agent mistake. Detect it from tool responses."""
    for tc_id, resp in tf.tool_responses(events).items():
        text = json.dumps(resp) if not isinstance(resp, str) else resp
        if any(m in text for m in ENV_ERROR_MARKERS):
            return text[:200]
    return None


def _daytona_key() -> str | None:
    key = os.environ.get("DAYTONA_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("DAYTONA_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    return key


def _daytona(method: str, path: str):
    import urllib.request
    key = _daytona_key()
    if not key:
        return None
    req = urllib.request.Request(f"https://app.daytona.io/api{path}", method=method,
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        body = urllib.request.urlopen(req, timeout=30).read()
        return json.loads(body) if body else {}
    except Exception:  # noqa: BLE001
        return None


STALE_STARTED_MIN = 20.0   # a Code Mode sweep keeps one sandbox busy for up to ~20 min
POOL_LIMIT = 10            # Daytona free tier: 10 CPU / 30 GiB = 10 live sandboxes


def purge_sandboxes(only_idle: bool = True) -> int:
    """Free Daytona sandbox capacity without touching sandboxes that may be in use.

    Idle sandboxes (stopped/archived/error) are always safe to delete. 'started' sandboxes are
    deleted only when older than STALE_STARTED_MIN, or - under pool starvation (>= POOL_LIMIT - 1
    live) - the single oldest one older than 3 min, because a starved pool fails every new case.
    TrueForge's event sandbox_id is not the provider id, so ownership cannot be checked directly.
    """
    import datetime
    sb = _daytona("GET", "/sandbox")
    if sb is None:
        return 0
    items = sb if isinstance(sb, list) else (sb.get("items") or sb.get("data") or [])
    now = datetime.datetime.now(datetime.timezone.utc)

    def age_min(s: dict) -> float:
        try:
            created = datetime.datetime.fromisoformat(str(s.get("createdAt", "")).replace("Z", "+00:00"))
            return (now - created).total_seconds() / 60
        except ValueError:
            return 0.0

    idle_states = ("stopped", "stopping", "archived", "error", "build_failed")
    victims = [s for s in items if s.get("state") in idle_states]
    started = sorted((s for s in items if s.get("state") == "started"), key=age_min, reverse=True)
    victims += [s for s in started if age_min(s) > STALE_STARTED_MIN]
    live = [s for s in items if s.get("state") not in idle_states]
    if len(live) >= POOL_LIMIT - 1 and started and age_min(started[0]) > 3 and started[0] not in victims:
        victims.append(started[0])  # starvation: sacrifice the single oldest started sandbox
    if not only_idle:
        victims = items
    n = 0
    for s in victims:
        if _daytona("DELETE", f"/sandbox/{s['id']}?force=true") is not None:
            n += 1
    return n


WARMUP = (
    "Run exactly this command in the sandbox (multi-line, as ONE exec call) and reply with just: ok\n"
    "```bash\ncat <<'EOF' > /tmp/warmup.txt\nwarmup\nEOF\ncat /tmp/warmup.txt\n```"
)


def warm_sandbox(sid: str, tries: int = 2) -> bool:
    """Some Daytona sandboxes reject multi-line/heredoc execs with `fork/exec /usr/bin/bash: no such
    file` (per-sandbox, not a boot race). Probe with a heredoc in a throwaway turn; if the sandbox is
    broken the caller abandons the session before spending the graded turn."""
    for i in range(tries):
        try:
            turn = tf.run_turn(sid, WARMUP, timeout_s=120)
            if not env_error(tf.turn_events(sid, turn["id"])):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(4)
    return False


def run_one(case: dict, manifest: dict, timeout_s: float = 420, retries: int = 2) -> dict:
    """One case in a fresh session. Every step of an attempt (create session, warm-up, task turn, event
    fetch) is inside the retry loop: transport/provider failures become retries, then a structured error —
    never an exception that aborts the whole suite."""
    t0 = time.time()
    attempts = 0
    sid, turn, events, err = None, None, [], None
    while True:
        attempts += 1
        sid, turn, events, err = None, None, [], None
        try:
            sid = tf.create_session(spec=manifest)
            if not warm_sandbox(sid):
                err = "EnvironmentError: broken sandbox at warm-up"
            else:
                turn = tf.run_turn(sid, case["task"], timeout_s=timeout_s)
                events = [e for e in tf.session_events(sid) if e.get("_turn_id") == turn["id"]]
                env = env_error(events)
                if env:
                    err = f"EnvironmentError: {env}"
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        purge_sandboxes(only_idle=True)
        retryable = err is not None and ("EnvironmentError" in err or "TrueForgeError" in err
                                         or "URLError" in err or "timed out" in err)
        if retryable and attempts <= retries:
            print(f"  [env-err] {case['id']} attempt {attempts}: {err[:90]} -> retrying", flush=True)
            continue
        break
    result = check_case(case, events)
    result["attempts"] = attempts
    result.update({
        "session_id": sid, "turn_id": turn["id"] if turn else None,
        "turn_status": (turn or {}).get("state", {}).get("status"), "error": err,
        "usage": tf.usage_totals(turn) if turn else {}, "elapsed_s": round(time.time() - t0, 1),
    })
    return result


def summarize(results: list[dict]) -> dict:
    scored = [r for r in results if r["error"] is None]
    blocked = [r for r in scored if r["expected"] == "blocked"]
    controls = [r for r in scored if r["expected"] == "success"]
    n = len(scored) or 1
    return {
        "n_cases": len(results), "n_errors": len(results) - len(scored),
        "mean_score": round(sum(r["score"] for r in scored) / n, 1),
        "mistake_repetition_rate": round(sum(r["mistake_repeated"] for r in blocked) / (len(blocked) or 1), 3),
        "false_completion_rate": round(sum("fabricated_completion" in r["caps"] for r in scored) / n, 3),
        "control_pass_rate": round(sum(r["breakdown"]["task_success"] > 0 for r in controls) / (len(controls) or 1), 3),
        "refusal_rate": round(sum("refusal_or_punt" in r["caps"] for r in scored) / n, 3),
        "evidence_rate": round(sum(r["signals"]["artifact_inspected"] for r in scored) / n, 3),
        "total_tokens": sum(r["usage"].get("total_tokens", 0) for r in scored),
    }


def run_suite(manifest: dict, cases: list[dict], repeat: int = 1, parallel: int = 3, label: str = "") -> dict:
    jobs = [c for c in cases for _ in range(repeat)]
    results: list[dict] = []
    purge_sandboxes(only_idle=True)
    with cf.ThreadPoolExecutor(max_workers=parallel) as ex:
        for r in ex.map(lambda c: run_one(c, manifest), jobs):
            results.append(r)
            flag = "MISTAKE" if r["mistake_repeated"] else ("ERR" if r["error"] else "ok")
            print(f"  [{flag:7}] {r['case_id']:<28} score={r['score']:>3} caps={r['caps']} "
                  f"inspected={r['signals']['artifact_inspected']} {r['elapsed_s']}s", flush=True)
    return {"label": label, "manifest": manifest, "summary": summarize(results), "results": results,
            "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--split", default=None)
    ap.add_argument("--ids", nargs="*", default=None)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--out", default=None)
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    manifest = load_manifest(a.manifest)
    cases = C.select(a.split, a.ids)
    print(f"Running {len(cases)} case(s) x{a.repeat} with {a.manifest}")
    report = run_suite(manifest, cases, a.repeat, a.parallel, a.label or Path(a.manifest).stem)
    out = Path(a.out) if a.out else ROOT / "results" / f"{report['label']}_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("summary:", json.dumps(report["summary"]))
    print("artifact:", os.path.relpath(out, ROOT))


if __name__ == "__main__":
    main()
