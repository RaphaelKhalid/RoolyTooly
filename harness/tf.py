"""Minimal TrueForge HTTP client (standalone mode, no auth).

Only stdlib so it runs from Windows Python or the WSL venv alike.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

BASE = os.environ.get("TRUEFORGE_URL", "http://localhost:8790").rstrip("/")


class TrueForgeError(RuntimeError):
    pass


def _req(method: str, path: str, body: Any | None = None, timeout: float = 60) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}/api/v1{path}", data=data, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise TrueForgeError(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:500]}") from None
    return json.loads(raw) if raw else None


# ---- settings -------------------------------------------------------------

def capabilities() -> dict:
    return _req("GET", "/capabilities")["data"]


def list_models() -> list[str]:
    return [m.get("name") or m.get("id") for m in _req("GET", "/models")["data"]]


def put_mcp_server(manifest: dict) -> dict:
    return _req("PUT", "/settings/mcp-servers", {"manifest": manifest})["data"]


def mcp_tools(name: str) -> list[dict]:
    return _req("GET", f"/mcp-servers/{name}/tools", timeout=90)["data"]


def put_skill(manifest: dict) -> dict:
    return _req("PUT", "/settings/skills", {"manifest": manifest})["data"]


def list_skills() -> list[dict]:
    return _req("GET", "/settings/skills")["data"]


# ---- agents ---------------------------------------------------------------

def list_agents() -> list[dict]:
    return _req("GET", "/agents")["data"]


def upsert_agent(name: str, spec: dict) -> dict:
    existing = {a["name"]: a for a in list_agents()}
    if name in existing:
        return _req("PUT", f"/agents/{existing[name]['id']}", {"manifest": spec})["data"]
    return _req("POST", "/agents", {"name": name, "manifest": spec})["data"]


# ---- sessions / turns -----------------------------------------------------

def create_session(spec: dict | None = None, agent_name: str | None = None) -> str:
    agent = {"name": agent_name} if agent_name else {"spec": spec}
    return _req("POST", "/sessions", {"agent": agent})["data"]["id"]


def get_session(session_id: str) -> dict:
    return _req("GET", f"/sessions/{session_id}")["data"]


def start_turn(session_id: str, content: str, previous_turn_id: str | None = None) -> str:
    body: dict[str, Any] = {"input": [{"type": "user.message", "content": content}], "stream": False}
    if previous_turn_id:
        body["previous_turn_id"] = previous_turn_id
    return _req("POST", f"/sessions/{session_id}/turns", body)["data"]["id"]


def resume_turn(session_id: str, thread_id: str, tool_call_id: str, allow: bool,
                previous_turn_id: str | None = None) -> str:
    item = {"type": "user.tool_approval", "thread_id": thread_id, "tool_call_id": tool_call_id,
            "approval": {"status": "allow" if allow else "deny"}}
    body: dict[str, Any] = {"input": [item], "stream": False}
    if previous_turn_id:
        body["previous_turn_id"] = previous_turn_id
    return _req("POST", f"/sessions/{session_id}/turns", body)["data"]["id"]


def get_turn(session_id: str, turn_id: str) -> dict:
    return _req("GET", f"/sessions/{session_id}/turns/{turn_id}")["data"]


def wait_turn(session_id: str, turn_id: str, timeout_s: float = 600, poll_s: float = 2.0) -> dict:
    """Poll until the turn leaves the running state. Returns the turn object."""
    t0 = time.time()
    while True:
        turn = get_turn(session_id, turn_id)
        status = (turn.get("state") or {}).get("status")
        if status and status not in ("running", "queued", "pending", "in_progress"):
            return turn
        if time.time() - t0 > timeout_s:
            raise TrueForgeError(f"turn {turn_id} still {status} after {timeout_s}s")
        time.sleep(poll_s)


def run_turn(session_id: str, content: str, timeout_s: float = 600) -> dict:
    return wait_turn(session_id, start_turn(session_id, content), timeout_s)


def session_events(session_id: str) -> list[dict]:
    """Persisted events, oldest first, flattened to the event dicts.

    Each dict has turn_id attached."""
    rows = _req("GET", f"/sessions/{session_id}/events")["data"]
    out = []
    for row in reversed(rows):
        ev = dict(row["event"])
        ev["_turn_id"] = row.get("turn_id")
        out.append(ev)
    return out


def turn_events(session_id: str, turn_id: str) -> list[dict]:
    rows = _req("GET", f"/sessions/{session_id}/turns/{turn_id}/events")["data"]
    return [r["event"] if "event" in r else r for r in reversed(rows)]


# ---- trace helpers --------------------------------------------------------

def tool_calls(events: list[dict]) -> list[dict]:
    """Flatten model.message tool calls into a list of call dicts.

    Each dict is {id, name, args(dict|str), event_id, thread_id}."""
    calls = []
    for ev in events:
        if ev.get("type") != "model.message":
            continue
        for tc in ev.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                pass
            calls.append({"id": tc.get("id"), "name": fn.get("name"), "args": args,
                          "event_id": ev.get("id"), "thread_id": ev.get("thread_id")})
    return calls


def tool_responses(events: list[dict]) -> dict[str, Any]:
    out = {}
    for ev in events:
        if ev.get("type") == "tool.response":
            content = ev.get("content")
            try:
                content = json.loads(content) if isinstance(content, str) else content
            except json.JSONDecodeError:
                pass
            out[ev["tool_call_id"]] = content
    return out


def final_message(events: list[dict]) -> str:
    """Last assistant text on the main thread."""
    for ev in reversed(events):
        if ev.get("type") == "model.message" and ev.get("thread_id", "main") == "main":
            c = ev.get("content")
            if isinstance(c, list):
                c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
            if c:
                return c
    return ""


def usage_totals(turn: dict) -> dict:
    m = (turn.get("state") or {}).get("metrics") or {}
    return {k: m.get(k, 0) for k in ("total_input_tokens", "total_output_tokens", "total_tokens")}
