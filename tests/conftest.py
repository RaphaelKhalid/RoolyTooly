"""Shared pytest setup and TrueForge event builders for the offline test suite.

No network, no TrueForge, no Daytona: everything here builds synthetic event traces in-process.
For the two MCP server modules (`mcp_servers.ledger_server`, `mcp_servers.eval_server`), the real
`mcp` package is not installed in this Windows environment, so a minimal fake
`mcp.server.fastmcp.FastMCP` / `mcp.types.ToolAnnotations` is inserted into `sys.modules` here,
before those modules are ever imported by a test. If a real `mcp` package IS importable, this
stub is skipped and the real package is used instead.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_fake_mcp() -> None:
    try:
        if importlib.util.find_spec("mcp") is not None:
            return  # a real mcp package is installed; never shadow it
    except (ImportError, ValueError):
        pass

    class _Settings:
        def __init__(self, port=None):
            self.port = port

    class FastMCP:
        """Stand-in for mcp.server.fastmcp.FastMCP: accepts any constructor kwargs, exposes
        .settings.port, and a .tool(**kw) decorator that returns the function unchanged."""

        def __init__(self, *args, **kwargs):
            self.name = args[0] if args else kwargs.get("name")
            self.settings = _Settings(kwargs.get("port"))

        def tool(self, *_args, **_kwargs):
            def _decorator(fn):
                return fn
            return _decorator

        def run(self, *_args, **_kwargs):
            return None

    class ToolAnnotations:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    types_mod = types.ModuleType("mcp.types")

    fastmcp_mod.FastMCP = FastMCP
    types_mod.ToolAnnotations = ToolAnnotations
    server_mod.fastmcp = fastmcp_mod
    mcp_mod.server = server_mod
    mcp_mod.types = types_mod

    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod
    sys.modules["mcp.types"] = types_mod


_install_fake_mcp()


# ---- TrueForge event builders --------------------------------------------------------------
#
# Shapes (see harness/tf.py):
#   model.message event: {type, id, thread_id, content, tool_calls:[{id, function:{name, arguments}}]}
#   tool.response event: {type, id, tool_call_id, content} where content is a JSON string like
#     {"success": true, "response": {"exitCode": 0, "result": "..."}}

def mk_exec_call(call_id: str, command: str) -> dict:
    """One tool_calls[] entry: an `exec` call whose arguments carry a shell command."""
    return {"id": call_id, "function": {"name": "exec", "arguments": json.dumps({"command": command})}}


def mk_message(tool_calls: list[dict] | None = None, content: str = "", thread_id: str = "main") -> dict:
    ev = {"type": "model.message", "id": f"msg_{uuid.uuid4().hex[:8]}", "thread_id": thread_id,
          "content": content}
    if tool_calls is not None:
        ev["tool_calls"] = tool_calls
    return ev


def mk_response(call_id: str, exit_code: int | None = 0, result: str = "",
                success: bool | None = None) -> dict:
    """A tool.response event shaped like TrueForge's exec response envelope."""
    if success is None:
        success = exit_code == 0
    content = {"success": success, "response": {"exitCode": exit_code, "result": result}}
    return {"type": "tool.response", "id": f"resp_{call_id}", "tool_call_id": call_id,
            "content": json.dumps(content)}


def mk_final(text: str, thread_id: str = "main") -> dict:
    """The final assistant text message on `thread_id` (defaults to main)."""
    return mk_message(content=text, thread_id=thread_id)


def exec_step(command: str, exit_code: int | None = 0, result: str = "",
             call_id: str | None = None) -> list[dict]:
    """One exec tool call plus its matching tool response, as a 2-event trace fragment."""
    cid = call_id or f"call_{uuid.uuid4().hex[:8]}"
    return [mk_message(tool_calls=[mk_exec_call(cid, command)]), mk_response(cid, exit_code, result)]


def mk_result(case_id: str = "case", expected: str = "blocked", score: int = 50,
             mistake: bool = False, caps: list[str] | None = None, error: str | None = None,
             artifact_inspected: bool = True, tokens: int = 0) -> dict:
    """A minimal fake `check_case` result, shaped just enough for `bench.run.summarize`."""
    return {
        "case_id": case_id, "expected": expected, "score": score,
        "breakdown": {"task_success": 40 if not mistake else 0},
        "caps": caps or [], "mistake_repeated": mistake, "error": error,
        "signals": {"artifact_inspected": artifact_inspected},
        "usage": {"total_tokens": tokens},
    }
