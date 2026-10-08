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
            "dispatched_at": 0,
        }]
        assert "report" not in item and len(item["text"]) <= 120
        assert [r["id"] for r in client.get("/api/requests?status=done", headers=headers).json()] == ["r2"]
        assert len(client.get("/api/requests?status=all&limit=1", headers=headers).json()) == 1
        assert client.get("/api/requests?status=bad", headers=headers).status_code == 422
        assert client.get("/api/health").json()["active_requests"] == 1
        assert client.get("/api/health").json()["running_tasks"] == 1
    assert "gateway shutdown with running requests: r1" in caplog.text


def test_waiting_quota_request_stays_visible_and_active(tmp_path, caplog):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["quota"] = {"id": "quota", "text": "resume later", "mode": "orchestrate",
                              "status": "waiting_quota", "created_at": 1.0, "results": {},
                              "quota_waits": {"s1": {"engine": "codex", "resume_at": 9999999999,
                                                       "deadline_at": 9999999999, "reason": "limit"}}}
    hub.store.put("task", "held", {"request_id": "quota", "step_id": "s1", "accepted": True,
                                     "completed": False, "payload": {"agent_id": "worker"}})
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}

    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        assert [r["id"] for r in client.get("/api/requests?status=running", headers=headers).json()] == ["quota"]
        health = client.get("/api/health").json()
        assert {key: health[key] for key in ("service", "runners", "agents", "active_requests", "running_tasks")} == {
            "service": "labhq gateway", "runners": [], "agents": 0,
            "active_requests": 1, "running_tasks": 1,
        }
        assert health["runner_online"] is False  # R15: no runner, so the web shows 러너 꺼짐
    assert "gateway shutdown with running requests: quota" in caplog.text


def test_waiting_login_request_stays_visible_and_active(tmp_path, caplog):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["login"] = {"id": "login", "text": "resume after login", "mode": "orchestrate",
                              "status": "waiting_login", "created_at": 1.0, "results": {},
                              "login_waits": {"s1": {"engine": "codex", "resume_at": 9999999999,
                                                       "deadline_at": 9999999999, "reason": "expired"}}}
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}

    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        assert [r["id"] for r in client.get("/api/requests?status=running", headers=headers).json()] == ["login"]
        assert client.get("/api/health").json()["active_requests"] == 1
    assert "gateway shutdown with running requests: login" in caplog.text


def test_cli_status_shows_running_work_and_approvals(monkeypatch, capsys):
    def api(_settings, _method, path):
        if path == "/api/health":
            return {"runners": ["runner-1"]}
        if path.startswith("/api/requests?"):
            return [{"id": "r1", "text": "분석", "step_progress":
                     {"done": 1, "total": 2, "steps": {"s1": "done", "s2": "running"}},
                     "cost_summary": {"actual_usd": 0.3, "estimated_usd": 0.2, "unknown_count": 1,
                                      "by_engine": {"claude_code": {"actual_usd": 0.3,
                                                                       "estimated_usd": 0, "unknown_count": 0},
                                                    "codex": {"actual_usd": 0, "estimated_usd": 0.2,
                                                              "unknown_count": 1}}, "warnings": []}}]
        return [{"id": "a1", "kind": "tool_permission", "summary": "검토"}]

    monkeypatch.setattr("labhq.cli._api", api)
    main(["status"])
    output = capsys.readouterr().out
    assert "runner-1" in output and "r1 1/2 분석" in output
    assert "s2: running" in output and "a1 [tool_permission] 검토" in output
    assert "확인 $0.30 + 추정 $0.20 + 미집계 1건" in output
    assert "claude_code 확인 $0.30" in output and "codex 추정 $0.20 + 미집계 1건" in output


def test_cli_resume_uses_the_existing_hold_endpoint(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("labhq.cli._api", lambda _s, method, path, **kwargs:
                        calls.append((method, path, kwargs)) or {"ok": True})

    main(["resume", "request-a", "review-1"])

    assert calls == [("POST", "/api/requests/request-a/steps/review-1/resume-quota", {"json": {}})]
    assert "'ok': True" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_usage_sum_and_unknown_cost_are_idempotent(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    hub.requests["r"] = {"id": "r", "status": "running", "created_at": 1, "cost_usd": 0,
                         "cost_known": True, "usage": {}}
    hub.save_request("r")
    hub.agents = {
        "actual": {"engine": "claude_code", "model": "claude-opus-5-5"},
        "unknown": {"engine": "gemini", "model": "unpriced-model"},
        "estimated": {"engine": "codex", "model": "gpt-6.1-sol"},
    }
    rows = (("t1", "actual", 0.3, {"input_tokens": 2}),
            ("t2", "unknown", None, {"input_tokens": 3, "output_tokens": 4}),
            ("t3", "estimated", None, {"input_tokens": 1_000_000, "cached_input_tokens": 0,
                                       "cache_write_input_tokens": 0, "output_tokens": 100_000}))
    for tid, agent, cost, usage in rows:
        result = TaskResult(task_id=tid, agent_id=agent, ok=True, cost_usd=cost,
                            cost_known=cost is not None, usage=usage,
                            provenance={"engine": hub.agents[agent]["engine"],
                                        "model": hub.agents[agent]["model"], "runs": {}})
        message = {"type": "task.result", "task_id": tid, "request_id": "r", "data": result.model_dump()}
        await hub.on_runner_message("runner", message)
        await hub.on_runner_message("runner", message)
    req = hub.requests["r"]
    assert req["cost_usd"] == pytest.approx(3.3) and req["cost_known"] is False
    assert req["usage"] == {"input_tokens": 1_000_005, "output_tokens": 100_004, "cached_input_tokens": 0,
                            "cache_write_input_tokens": 0}
    assert req["cost_summary"]["actual_usd"] == 0.3
    assert req["cost_summary"]["estimated_usd"] == pytest.approx(3.0)
    assert req["cost_summary"]["unknown_count"] == 1
    assert hub.request_summary(req)["cost_summary"] == req["cost_summary"]
    assert req["cost_items"]["t3"]["price"]["model"] == "gpt-6.1-sol"


@pytest.mark.asyncio
async def test_abandoned_task_is_unaccounted_and_an_unrouted_task_is_a_real_zero(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    hub.requests["r"] = {"id": "r", "status": "running", "created_at": 1, "cost_usd": 0,
                         "cost_known": True, "usage": {}}
    hub.agents = {"worker": {"engine": "claude_code", "model": "opus"}}
    hub.store.put("task", "t1", {"request_id": "r", "step_id": "s1", "accepted": True, "completed": False,
                                  "payload": {"agent_id": "worker"}})

    abandoned = hub._abandon_previous_generation("t1", hub.store.get("task", "t1"))
    unrouted = await hub.dispatch(Task(agent_id="nobody", request_id="r", prompt="p"))
    req = hub.requests["r"]

    # The runner may have spent anything before the gateway gave up on it: never $0 (#270).
    assert abandoned.ok is False and req["cost_items"]["t1"]["reason"] == "outcome_unknown"
    assert req["cost_known"] is False and req["usage_known"] is False
    assert req["cost_summary"]["unknown_count"] == 1 and req["cost_summary"]["unknown_tasks"] == ["t1"]
    assert (unrouted.cost_usd, unrouted.cost_known) == (0.0, True)  # nothing was sent


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


def test_running_tasks_choose_state_priority_then_latest_dispatch(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    hub.requests["r1"] = {"id": "r1", "status": "running", "results": {}}
    sleeping = {"request_id": "r1", "accepted": True, "completed": True,
                "result": {"pending_jobs": ["j1"]}, "payload": {"agent_id": "analyst"}}
    running = {**sleeping, "completed": False, "result": None}
    hub.store.put("task", "sleep-newer", {**sleeping, "step_id": "s1", "dispatched_at": 20})
    hub.store.put("task", "run-older", {**running, "step_id": "s1", "dispatched_at": 10})
    hub.store.put("task", "run-oldest", {**running, "step_id": "s2", "dispatched_at": 1})
    hub.store.put("task", "run-latest", {**running, "step_id": "s2", "dispatched_at": 2})

    assert [(task["step_id"], task["id"], task["state"]) for task in hub.running_tasks()] == [
        ("s1", "run-older", "running"),
        ("s2", "run-latest", "running"),
    ]


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


def test_completion_line_and_project_report_show_unaccounted_cost_apart(tmp_path, capsys):
    from labhq.cli import render
    from labhq.gateway.server import Hub

    summary = {"actual_usd": 1.0, "estimated_usd": 0.5, "subtotal_usd": 1.5, "unknown_count": 1,
               "by_engine": {"claude_code": {"actual_usd": 1.0, "estimated_usd": 0, "unknown_count": 0},
                             "codex": {"actual_usd": 0, "estimated_usd": 0.5, "unknown_count": 1}},
               "prices": [], "warnings": []}
    render({"type": "request.completed", "ts": 1, "data": {"ok": True, "cost_usd": 1.5, "cost_known": False,
                                                            "cost_summary": summary}})
    line = capsys.readouterr().out
    assert "cost=확인 $1.00 + 추정 $0.50 + 미집계 1건" in line and "codex 추정 $0.50 + 미집계 1건" in line
    render({"type": "request.completed", "ts": 1, "data": {"ok": True, "cost_usd": 0, "cost_known": False}})
    assert "cost=비용 미집계" in capsys.readouterr().out  # never "$0" for an unreported cost

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    report = Hub(settings).reporter._report_md("r", {"text": "t", "cost_usd": 1.5, "cost_known": False,
                                                     "cost_summary": summary}, "body")
    assert "- 비용: 확인 $1.00 + 추정 $0.50 + 미집계 1건 (claude_code 확인 $1.00 · codex" in report
    assert "청구액이 아닙니다" in report


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
