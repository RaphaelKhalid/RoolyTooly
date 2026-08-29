"""Drive the `roolytooly` agent over the TrueForge API.

Same thing the chat UI does.

  python -m harness.drive --new "Run the worker on M05_hollow_report_01"
  python -m harness.drive --session <id> "Correction: ..." [--auto-approve]

Prints the agent's messages, tool calls, subagent threads, and approval pauses. Without
--auto-approve, an approval pause is printed and the script exits so a human can approve in the UI.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness import tf  # noqa: E402

AGENT = "roolytooly"


def show(events: list[dict], seen: set[str]) -> None:
    for ev in events:
        if ev["id"] in seen:
            continue
        seen.add(ev["id"])
        t, th = ev["type"], ev.get("thread_id") or "-"
        if t == "model.message":
            for tc in ev.get("tool_calls") or []:
                fn = tc["function"]
                print(f"  [{th}] -> {fn['name']}({fn['arguments'][:160]})")
            c = ev.get("content")
            if isinstance(c, list):
                c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
            if c:
                print(f"  [{th}] {c[:700]}")
        elif t == "tool.response":
            print(f"  [{th}] <- {str(ev.get('content'))[:220]}")
        elif t in ("thread.created", "thread.done", "sandbox.created", "tool.approval_required"):
            print(f"  ## {t} {json.dumps({k: v for k, v in ev.items() if k in ('thread_id', 'name', 'sandbox_id', 'tool_calls')})[:200]}")


def pending_approvals(turn: dict) -> list[dict]:
    st = turn.get("state") or {}
    out = []
    for ra in st.get("required_actions") or []:
        if ra.get("type") == "tool.approval_required":
            for tc in ra.get("tool_calls") or []:
                out.append({"thread_id": ra.get("thread_id"), "tool_call_id": tc["id"]})
    return out


def run(session_id: str, message: str | None, auto_approve: bool) -> None:
    seen: set[str] = set()
    for e in tf.session_events(session_id):
        seen.add(e["id"])
    turn_id = tf.start_turn(session_id, message) if message else None
    while True:
        turn = tf.get_turn(session_id, turn_id)
        show(tf.turn_events(session_id, turn_id), seen)
        status = (turn.get("state") or {}).get("status")
        if status in ("running", "queued", "pending", "in_progress"):
            time.sleep(3)
            continue
        appr = pending_approvals(turn)
        if appr:
            print(f"\n=== APPROVAL REQUIRED: {appr}")
            if not auto_approve:
                print("Approve in the TrueForge UI, or rerun with --auto-approve.")
                return
            for a in appr:
                turn_id = tf.resume_turn(session_id, a["thread_id"], a["tool_call_id"], True, previous_turn_id=turn_id)
                print("  approved ->", turn_id)
            continue
        print(f"\n=== turn {status}; metrics={json.dumps((turn.get('state') or {}).get('metrics'))}")
        return


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="?")
    ap.add_argument("--new", action="store_true")
    ap.add_argument("--session")
    ap.add_argument("--auto-approve", action="store_true")
    a = ap.parse_args()
    sid = tf.create_session(agent_name=AGENT) if a.new else a.session
    if not sid:
        sys.exit("--new or --session required")
    print("session:", sid)
    run(sid, a.message, a.auto_approve)


if __name__ == "__main__":
    main()
