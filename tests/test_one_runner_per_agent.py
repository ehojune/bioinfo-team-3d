"""Runner ownership uses mocked sockets and isolated state; no WSL or native processes."""
import ast
import asyncio
import inspect
import json
import textwrap

import pytest

from labhq import cli, instance_guard, wsl_setup
from labhq.gateway.server import Hub
from labhq.models import Task, TaskResult
from test_wsl_setup import READY, settings


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", ["bioinfo-agent", "analyst"])
async def test_busy_owner_survives_alternative_connect_and_reconnect(settings, agent_id):
    hub = Hub(settings)
    staff = [{"id": agent_id}]
    sent = []
    dispatched = asyncio.Event()

    class Socket:
        def __init__(self, name):
            self.name = name

        async def send_text(self, payload):
            msg = json.loads(payload)
            if msg["type"] == "task.dispatch":
                sent.append((self.name, msg["task"]["id"]))
                dispatched.set()

    async def start():
        task = Task(agent_id=agent_id, prompt="analysis")
        dispatched.clear()
        pending = asyncio.create_task(hub.dispatch(task))
        await asyncio.wait_for(dispatched.wait(), 2)
        await hub.on_runner_message("native" if len(sent) < 3 else "wsl", {
            "type": "task.accepted", "task_id": task.id})
        return task, pending

    async def finish(task, pending, runner):
        await hub.on_runner_message(runner, {"type": "task.result", "task_id": task.id,
            "data": TaskResult(task_id=task.id, agent_id=agent_id, ok=True).model_dump(mode="json")})
        assert (await asyncio.wait_for(pending, 2)).ok

    hub.register_runner("native", Socket("native"), staff, incarnation="native-1")
    first, first_pending = await start()
    caps = {"runtime_env": "wsl", "wsl_readiness": READY}
    try:
        for generation in ("wsl-1", "wsl-2"):
            ws = Socket("wsl")
            hub.register_runner("wsl", ws, staff, incarnation=generation, capabilities=caps)
            assert not hub.store.get("task", first.id).get("completed")
            assert hub.agent_runner[agent_id] == "native"
            assert sent == [("native", first.id)]  # no replay on another runner
            hub.unregister_runner("wsl", ws)
        hub.register_runner("wsl", Socket("wsl"), staff, incarnation="wsl-2", capabilities=caps)
        second, second_pending = await start()
        assert sent[-1] == ("native", second.id)
        await finish(first, first_pending, "native")
        assert hub.agent_runner[agent_id] == "native"  # another task still holds ownership
        await finish(second, second_pending, "native")
        third, third_pending = await start()
        assert sent == [("native", first.id), ("native", second.id), ("wsl", third.id)]
        await finish(third, third_pending, "wsl")
    finally:
        for future in list(hub.futures.values()):
            future.cancel()
        await asyncio.gather(first_pending, return_exceptions=True)
        hub.store.close()


def test_raw_rosters_are_read_only_by_effective_roster():
    tree = ast.parse(textwrap.dedent(inspect.getsource(Hub)))
    violations = []
    for method in tree.body[0].body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(method):
            if not isinstance(node, ast.Attribute) or node.attr not in {"runner_agents", "last_runner_rosters"}:
                continue
            if method.name in {"__init__", "effective_roster"}:
                continue
            # set_roster only writes the advertised input; every consumer resolves ownership.
            if method.name == "set_roster" and any(
                isinstance(parent, ast.Subscript) and parent.value is node and isinstance(parent.ctx, ast.Store)
                for parent in ast.walk(method)
            ):
                continue
            violations.append((method.name, node.attr))
    assert violations == []
    from labhq.recruit import planning
    assert "last_runner_rosters" not in inspect.getsource(planning)
    assert "runner_agents" not in inspect.getsource(planning)


def test_up_exits_zero_with_one_warning_when_wsl_start_fails(settings, monkeypatch, capsys):
    settings.runner.wsl.enabled = True
    monkeypatch.setattr(cli.Settings, "load", lambda *a, **kw: settings)
    monkeypatch.setattr(cli, "_process_record", lambda *a: ("missing", None, None))
    monkeypatch.setattr(cli, "_supervised", lambda *a: False)
    monkeypatch.setattr(cli, "_health", lambda s: None)
    monkeypatch.setattr(cli, "_wait_for_health", lambda s, **kw: {
        "service": "labhq gateway", "identity": instance_guard.public_identity(s),
        "runners": [s.runner.id] if kw["runner"] else []})
    calls = []
    monkeypatch.setattr(cli, "_start_background", lambda s, role, **kw: calls.append(role))
    monkeypatch.setattr(instance_guard, "record_success", lambda *a, **kw: calls.append("success"))

    def fail(s, action):
        assert calls == ["gateway", "runner", "success"] and action == "start"
        raise wsl_setup.WslError("WSL Python unavailable\ntry again")

    monkeypatch.setattr(wsl_setup, "control", fail)
    assert cli.main(["-c", settings.config_path, "up"]) in (None, 0)
    captured = capsys.readouterr()
    assert "웹 사무실:" in captured.out
    assert len(captured.err.strip().splitlines()) == 1
    assert "WSL Python unavailable" in captured.err and "경고" in captured.err
