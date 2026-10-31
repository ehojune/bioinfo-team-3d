"""#676: a public lookup that waits on its service shows 'n minutes of the limit' on the task board, and every
labhq_annot tool of the research lane goes through the 20-minute executor."""
import ast
from pathlib import Path

import pytest

from labhq import cli
from labhq.gateway.server import create_app, external_wait_view
from labhq.settings import Settings

ANNOT_MCP = Path(__file__).resolve().parents[1] / "labhq" / "tools" / "annot_mcp.py"


def _hub(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    hub = create_app(settings).state.hub
    hub.requests["req"] = {"id": "req", "status": "running", "plan": {"steps": [{"id": "s1"}]}}
    hub.store.put("task", "t", {"accepted": True, "request_id": "req", "step_id": "s1", "dispatched_at": 100,
                                "payload": {"agent_id": "analyst", "meta": {"title": "Lookup"}}})
    return hub


async def _send(hub, data, ts):
    await hub.on_runner_message("r", {"type": "agent.log", "agent_id": "analyst", "task_id": "t",
                                      "request_id": "req", "ts": ts, "data": data})


def _lookup(status, elapsed, tool="fulltext"):
    return {"level": "warn" if status == "timeout" else "info", "text": f"{tool}: {status}",
            "external_lookup": {"tool": tool, "lookup": "abc", "status": status, "elapsed_s": elapsed,
                                "timeout_s": 1200}}


@pytest.mark.asyncio
async def test_a_waiting_lookup_shows_elapsed_over_the_limit_until_its_final_status(tmp_path):
    hub = _hub(tmp_path)
    await _send(hub, _lookup("waiting", 0), 1000)
    await _send(hub, _lookup("waiting", 120), 1060)
    task = hub.snapshot()["data"]["running_tasks"][0]
    assert task["activity"]["external_wait"] == {"tool": "fulltext", "elapsed_s": 120, "timeout_s": 1200, "at": 1060}
    waits = hub.request_summary(hub.requests["req"])["step_waits"]
    assert waits["s1"]["tool"] == "fulltext" and waits["s1"]["timeout_s"] == 1200 and waits["s1"]["elapsed_s"] >= 120
    await _send(hub, _lookup("ok", 200), 1140)
    assert "external_wait" not in hub.snapshot()["data"]["running_tasks"][0]["activity"]
    assert "step_waits" not in hub.request_summary(hub.requests["req"])


@pytest.mark.asyncio
async def test_a_timed_out_lookup_clears_the_wait_and_keeps_the_warning(tmp_path):
    hub = _hub(tmp_path)
    await _send(hub, _lookup("waiting", 1140), 1000)
    await _send(hub, _lookup("timeout", 1200), 1060)
    activity = hub.snapshot()["data"]["running_tasks"][0]["activity"]
    assert "external_wait" not in activity and "timeout" in activity["warning"]


def test_the_view_and_the_cli_line_cap_at_the_limit_and_ignore_bad_records():
    assert external_wait_view(None) is None and external_wait_view({"elapsed_s": "x"}) is None
    view = external_wait_view({"tool": "fulltext", "elapsed_s": 100, "timeout_s": 1200, "at": 1000}, now=1080)
    assert view == {"tool": "fulltext", "elapsed_s": 180, "timeout_s": 1200}
    assert cli.external_wait_text({"elapsed_s": 180, "timeout_s": 1200}) == "외부 응답 대기 3분째 / 상한 20분"
    assert cli.external_wait_text({"elapsed_s": 5000, "timeout_s": 1200}) == "외부 응답 대기 20분째 / 상한 20분"


def test_every_labhq_annot_tool_goes_through_the_20_minute_executor():
    tree = ast.parse(ANNOT_MCP.read_text(encoding="utf-8"))
    runners = {"_run", "_run_literature", "_run_key_fulltext"}
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))}

    def calls(node):
        return {n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

    # A tool is registered with `@server.tool()` or, since #1200, `@offered()` (which does the same unless the agent's
    # annot_tools leaves it out).
    tools = [node for node in functions.values() if any(
        isinstance(d, ast.Call) and (getattr(d.func, "attr", "") == "tool" or getattr(d.func, "id", "") == "offered")
        for d in node.decorator_list)]
    assert len(tools) >= 15
    unbounded = [node.name for node in tools if not calls(node) & runners]
    assert unbounded == []  # a new tool must call one of the runners below, not reach the network itself
    for name in runners:
        assert "run_lookup" in calls(functions[name]) or name == "_run_key_fulltext" and "automatic" in {
            n.name for n in ast.walk(functions[name]) if isinstance(n, ast.AsyncFunctionDef)}
    assert "run_lookup" in calls(next(n for n in ast.walk(functions["_run_key_fulltext"])
                                      if isinstance(n, ast.AsyncFunctionDef) and n.name == "automatic"))
