"""Project spend tracker for the TrueForge harness (TrueForge has no native budget cap).

Sums token metrics of EVERY session turn on the local TrueForge (worker runs, immune-system
sessions, UI chats) and prices them with the model's published rates. Cache reads are billed at
10% of input. Used by the eval-runner budget guard and the dashboard.

  python -m harness.spend            # print totals
  python -m harness.spend --json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness import tf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BUDGET_FILE = ROOT / "budget.json"

# USD per 1M tokens (input, output). Source: OpenAI list prices as of Aug 2026 (see README).
DEFAULT_PRICES = {
    "openai/gpt-5-4-mini": (0.75, 4.50),
    "openai/gpt-5-5": (5.00, 30.00),
    "openai/gpt-5-6-luna": (0.20, 1.20),
    "openai/gpt-5-6-terra": (2.00, 12.00),
    "openai/gpt-5-6-sol": (5.00, 30.00),
}
CACHE_READ_DISCOUNT = 0.10


def load_budget() -> dict:
    b = {"cap_usd": 50.0, "reserve_usd": 5.0, "prices": DEFAULT_PRICES}
    if BUDGET_FILE.exists():
        b.update(json.loads(BUDGET_FILE.read_text(encoding="utf-8")))
    return b


def turn_cost(model: str, metrics: dict, prices: dict) -> float:
    pin, pout = prices.get(model, (5.0, 30.0))  # unknown model -> priced at the top tier, never at zero
    inp = metrics.get("total_input_tokens", 0)
    cached = metrics.get("total_cache_read_tokens", 0)
    out = metrics.get("total_output_tokens", 0)
    return ((inp - cached) * pin + cached * pin * CACHE_READ_DISCOUNT + out * pout) / 1_000_000


def _session_model(sess: dict) -> str:
    agent = sess.get("agent") or {}
    spec = agent.get("spec") or {}
    if not spec and agent.get("name"):
        try:
            named = {a["name"]: a for a in tf.list_agents()}.get(agent["name"])
            spec = (named or {}).get("manifest") or {}
        except Exception:  # noqa: BLE001
            spec = {}
    return (spec.get("model") or {}).get("name") or "unknown"


def total_spend() -> dict:
    """Scan all sessions/turns. Returns totals by model plus the grand total in USD."""
    b = load_budget()
    prices = {k: tuple(v) for k, v in b["prices"].items()}
    by_model: dict[str, dict] = {}
    sessions, token = [], None
    while True:  # page size is capped at 25
        page = tf._req("GET", "/sessions?limit=25" + (f"&page_token={token}" if token else ""))
        sessions.extend(page["data"])
        token = (page.get("pagination") or {}).get("next_page_token")
        if not token:
            break
    for s in sessions:
        model = _session_model(s)
        try:
            turns = tf._req("GET", f"/sessions/{s['id']}/turns")["data"]
        except Exception:  # noqa: BLE001
            continue
        for t in turns:
            m = (t.get("state") or {}).get("metrics") or {}
            if not m:
                continue
            row = by_model.setdefault(model, {"turns": 0, "input": 0, "cached": 0, "output": 0, "usd": 0.0})
            row["turns"] += 1
            row["input"] += m.get("total_input_tokens", 0)
            row["cached"] += m.get("total_cache_read_tokens", 0)
            row["output"] += m.get("total_output_tokens", 0)
            row["usd"] += turn_cost(model, m, prices)
    total = round(sum(r["usd"] for r in by_model.values()), 4)
    for r in by_model.values():
        r["usd"] = round(r["usd"], 4)
    return {"cap_usd": b["cap_usd"], "reserve_usd": b["reserve_usd"], "spent_usd": total,
            "remaining_usd": round(b["cap_usd"] - total, 4), "sessions": len(sessions), "by_model": by_model}


def can_spend(estimate_usd: float) -> tuple[bool, dict]:
    st = total_spend()
    ok = st["spent_usd"] + estimate_usd <= st["cap_usd"] - st["reserve_usd"]
    return ok, st


if __name__ == "__main__":
    st = total_spend()
    if "--json" in sys.argv:
        print(json.dumps(st, indent=2))
    else:
        print(f"spent ${st['spent_usd']:.2f} of ${st['cap_usd']:.0f} (reserve ${st['reserve_usd']:.0f}) "
              f"across {st['sessions']} sessions")
        for m, r in st["by_model"].items():
            print(f"  {m:<24} turns={r['turns']:<4} in={r['input']:>9} cached={r['cached']:>9} "
                  f"out={r['output']:>7}  ${r['usd']:.3f}")
