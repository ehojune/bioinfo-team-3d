"""Operational summaries and accounting contracts."""

import asyncio
import json
import logging
import signal

import pytest
from fastapi.testclient import TestClient

from labhq.cli import _run_runner_with_interrupts, main
from labhq.gateway.server import create_app
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import Settings


def test_request_list_health_auth_utf8_and_shutdown(tmp_path, caplog):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["r1"] = {"id": "r1", "text": "한글 요청 " * 30, "mode": "orchestrate",
                          "status": "running", "created_at": 1.0, "updated_at": 2.0,
                          "report": "private report", "plan": {"steps": [{"id": "s1"}, {"id": "s2"}]},
                          "results": {"s1": {"ok": True}}}
    hub.requests["r2"] = {"id": "r2", "text": "finished", "status": "done", "created_at": 2.0}
    hub.store.put("task", "t2", {"request_id": "r1", "step_id": "s2", "accepted": True, "completed": False,
                                  "payload": {"agent_id": "worker"}})
    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        assert client.get("/api/requests").status_code == 401
        headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}
        response = client.get("/api/requests?status=running&limit=1", headers=headers)
        assert response.headers["content-type"] == "application/json; charset=utf-8"
        item = response.json()[0]
        assert item["id"] == "r1" and item["text"].startswith("한글")
        assert item["step_progress"] == {"done": 1, "total": 2,
                                         "steps": {"s1": "done", "s2": "running"}}
        assert hub.snapshot()["data"]["running_tasks"] == [{
            "id": "t2", "request_id": "r1", "state": "running", "step_id": "s2", "agent_id": "worker",
        }]
        assert "report" not in item and len(item["text"]) <= 120
        assert [r["id"] for r in client.get("/api/requests?status=done", headers=headers).json()] == ["r2"]
        assert len(client.get("/api/requests?status=all&limit=1", headers=headers).json()) == 1
        assert client.get("/api/requests?status=bad", headers=headers).status_code == 422
        assert client.get("/api/health").json()["active_requests"] == 1
        assert client.get("/api/health").json()["running_tasks"] == 1
    assert "gateway shutdown with running requests: r1" in caplog.text


def test_cli_status_shows_running_work_and_approvals(monkeypatch, capsys):
    def api(_settings, _method, path):
        if path == "/api/health":
            return {"runners": ["runner-1"]}
        if path.startswith("/api/requests?"):
            return [{"id": "r1", "text": "분석", "step_progress":
                     {"done": 1, "total": 2, "steps": {"s1": "done", "s2": "running"}}}]
        return [{"id": "a1", "kind": "tool_permission", "summary": "검토"}]

    monkeypatch.setattr("labhq.cli._api", api)
    main(["status"])
    output = capsys.readouterr().out
    assert "runner-1" in output and "r1 1/2 분석" in output
    assert "s2: running" in output and "a1 [tool_permission] 검토" in output


@pytest.mark.asyncio
async def test_usage_sum_and_unknown_cost_are_idempotent(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    hub.requests["r"] = {"id": "r", "status": "running", "created_at": 1, "cost_usd": 0,
                         "cost_known": True, "usage": {}}
    hub.save_request("r")
    for tid, cost, usage in (("t1", 0.3, {"input_tokens": 2}),
                             ("t2", None, {"input_tokens": 3, "output_tokens": 4})):
        result = TaskResult(task_id=tid, agent_id="a", ok=True, cost_usd=cost,
                            cost_known=cost is not None, usage=usage)
        message = {"type": "task.result", "task_id": tid, "request_id": "r", "data": result.model_dump()}
        await hub.on_runner_message("runner", message)
        await hub.on_runner_message("runner", message)
    req = hub.requests["r"]
    assert req["cost_usd"] == 0.3 and req["cost_known"] is False
    assert req["usage"] == {"input_tokens": 5, "output_tokens": 4}
    assert hub.request_summary(req)["cost_known"] is False


@pytest.mark.asyncio
async def test_runner_manifest_records_token_counts(tmp_path, monkeypatch):
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.runner.agents_dir = str(tmp_path / "agents")
    settings.runner.talent_dir = str(tmp_path / "talent")
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.codex, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class Adapter:
        async def run(self, ctx):
            await ctx.emit("agent.usage", {"tokens": {"input_tokens": 7}, "cost_known": False})
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="done",
                              usage={"input_tokens": 7}, cost_known=False)

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    task = Task(id="task-1", request_id="r1", agent_id=agent.id, prompt="test")
    result = await runner.run_task(task)
    manifest = json.loads((runner.workspaces[task.id].dir / "manifest.json").read_text(encoding="utf-8"))
    assert result.usage == manifest["runs"][task.id]["usage"] == {"input_tokens": 7}
    assert manifest["runs"][task.id]["cost_known"] is False
    assert any(event["type"] == "agent.usage" for event in runner.store.pending())


@pytest.mark.asyncio
async def test_runner_first_interrupt_warns_second_exits(monkeypatch, capsys):
    handlers = {}

    def install(_sig, handler):
        previous = handlers.get("current", signal.default_int_handler)
        handlers["current"] = handler
        return previous

    monkeypatch.setattr(signal, "signal", install)

    class FakeTask:
        def done(self):
            return False

    class FakeRunner:
        tasks = {"task-1": FakeTask()}
        stopped = False

        async def run_forever(self):
            handlers["current"](signal.SIGINT, None)
            assert not self.stopped
            handlers["current"](signal.SIGINT, None)

        def stop(self):
            self.stopped = True

    runner = FakeRunner()
    with pytest.raises(KeyboardInterrupt):
        await _run_runner_with_interrupts(runner)
    assert runner.stopped
    assert "task-1" in capsys.readouterr().err


def test_steps_asleep_on_hpc_count_as_active(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["r1"] = {"id": "r1", "text": "x", "status": "running", "created_at": 1.0,
                          "plan": {"steps": [{"id": "s1"}]}, "results": {}}
    hub.store.put("task", "t1", {"request_id": "r1", "step_id": "s1", "accepted": True, "completed": True,
                                  "result": {"pending_jobs": ["j1"]}, "payload": {"agent_id": "analyst"}})
    assert [t["state"] for t in hub.running_tasks()] == ["hibernating"]
    assert hub.request_summary(hub.requests["r1"])["step_progress"]["steps"] == {"s1": "hibernating"}
    hub.requests["r1"]["results"] = {"s1": {"ok": True}}
    assert hub.running_tasks() == []


@pytest.mark.parametrize("wake_first", [False, True])
@pytest.mark.asyncio
async def test_finished_jobs_do_not_count_as_sleeping_during_wake(tmp_path, wake_first):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["r1"] = {"id": "r1", "text": "x", "status": "running", "created_at": 1.0,
                          "plan": {"steps": [{"id": "s1"}]}, "results": {}}
    sleeping = {"request_id": "r1", "step_id": "s1", "accepted": True, "completed": True,
                "result": {"pending_jobs": ["j1"]}, "payload": {"agent_id": "analyst"}}
    wake = {**sleeping, "completed": False, "result": None}
    if wake_first:
        hub.store.put("task", "wake", wake)
    hub.store.put("task", "sleep", sleeping)
    await hub.on_runner_message("runner", {"type": "jobs.finished", "task_id": "sleep",
                                          "request_id": "r1", "data": {}})
    if not wake_first:
        assert hub.running_tasks() == []
        hub.store.put("task", "wake", wake)
    assert [t["id"] for t in hub.running_tasks()] == ["wake"]
    assert hub.request_summary(hub.requests["r1"])["step_progress"]["steps"] == {"s1": "running"}
    with TestClient(app) as client:
        assert client.get("/api/health").json()["running_tasks"] == 1


@pytest.mark.asyncio
async def test_failure_comment_keeps_stored_accounting(tmp_path):
    import httpx

    from labhq.settings import ProjectSettings

    posted = []

    def api(request):
        if request.method == "POST" and request.url.path.endswith("/comments"):
            posted.append(json.loads(request.content)["body"])
            return httpx.Response(201, json={"html_url": "https://example.test/c"})
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"number": 7, "html_url": "https://example.test/7"})

    settings = Settings(projects=[ProjectSettings(id="p", repo="o/p", commit_reports=False)])
    settings.gateway.state_dir = str(tmp_path / "state")
    from labhq.gateway.server import Hub
    hub = Hub(settings, github_transport=httpx.MockTransport(api))
    hub.requests["r"] = {"id": "r", "text": "t", "project_id": "p", "status": "failed",
                         "cost_usd": 1.5, "cost_known": False}
    hub.reporter.issues["r"] = 7
    await hub.reporter.handle({"type": "request.failed", "request_id": "r", "data": {"error": "boom"}})
    assert posted and "$1.5" in posted[-1] and "비용 미집계" in posted[-1]


@pytest.mark.asyncio
async def test_runner_interrupt_warns_while_hpc_jobs_are_watched(monkeypatch, capsys):
    handlers = {}

    def install(_sig, handler):
        previous = handlers.get("current", signal.default_int_handler)
        handlers["current"] = handler
        return previous

    monkeypatch.setattr(signal, "signal", install)

    class FakeRunner:
        tasks = {}
        jobs = {"j1": {"terminal": False}, "j0": {"terminal": True}}

        async def run_forever(self):
            handlers["current"](signal.SIGINT, None)  # warns: a job is still being watched
            handlers["current"](signal.SIGINT, None)

        def stop(self):
            pass

    with pytest.raises(KeyboardInterrupt):
        await _run_runner_with_interrupts(FakeRunner())
    err = capsys.readouterr().err
    assert "HPC job 1개" in err and "j1" in err and "j0" not in err
