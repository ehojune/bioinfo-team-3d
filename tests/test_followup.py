"""#36 PR A: a finished request takes follow-up questions in the same CSO session and workspace."""

import asyncio
import shutil
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from labhq.gateway.server import Hub, RequestIn, create_app
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.util import free_port

REPO = Path(__file__).resolve().parents[1]
REFS = [{"kind": "path", "value": "/srv/refs/yuan", "note": None, "source": "request"}]


class FakeHub:
    def __init__(self, reply, *, resume=True, mode="orchestrate"):
        self.s = Settings()
        self.requests = {"r": {"id": "r", "text": "Compare cohorts", "mode": mode, "agent_id": "worker",
                               "status": "done", "report": "Final report: cohort A wins", "cost_usd": 1.5,
                               "cso_session_id": "cso-1", "cso_workdir": "/w/cso", "references": REFS,
                               "results": {"s1": {"workdir": "/w/s1", "outputs": ["outputs/a.tsv"]},
                                           "s2": {"workdir": "/w/s2", "outputs": []}},
                               "followups": []}}
        self.agents = {name: {"id": name, "name": name, "role": "test", "engine": "mock"} for name in ("cso", "worker")}
        self.calls, self.events, self.saved = [], [], 0
        self.reply, self.resume = reply, resume

    async def dispatch(self, task):
        self.calls.append(task)
        return await self.reply(task)

    async def publish(self, event):
        self.events.append(event)

    def supports_resume(self, agent_id):
        return self.resume

    def save_request(self, rid):
        self.saved += 1

    def ask(self, text, fid="fu_1"):
        entry = {"id": fid, "text": text, "agent_id": "cso", "status": "running", "asked_at": 1.0}
        self.requests["r"]["followups"].append(entry)
        return entry


def answer(text="Cohort A had more donors", session="cso-2", ok=True, error=None):
    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=ok, text=text, session_id=session,
                          workdir="/w/cso", error=error)
    return reply


@pytest.mark.asyncio
async def test_followup_resumes_the_cso_session_in_its_workspace_read_only():
    hub = FakeHub(answer())
    hub.ask("Why cohort A?")
    await Orchestrator(hub).run_followup("r", "fu_1")
    task = hub.calls[0]
    assert task.agent_id == "cso" and task.resume_session_id == "cso-1"
    assert task.meta["kind"] == "followup" and task.meta["workdir"] == "/w/cso"
    assert task.meta["agent_overrides"]["sandbox"] == "read-only"
    assert task.meta["agent_overrides"]["builtin_mcp"] == []
    assert task.meta["upstream_dirs"] == ["/w/s1"] and task.meta["reference_dirs"] == ["/srv/refs/yuan"]
    assert "PI follow-up question: Why cohort A?" in task.prompt and "Final report: cohort A wins" in task.prompt
    req = hub.requests["r"]
    entry = req["followups"][0]
    assert entry["status"] == "done" and entry["answer"] == "Cohort A had more donors"
    assert entry["resumed_session"] == "cso-1"
    assert req["status"] == "done", "a follow-up never reopens the request"
    assert req["cso_session_id"] == "cso-2", "the next follow-up resumes the latest turn"
    types = [e["type"] for e in hub.events if e["type"].startswith("request.followup")]
    assert types == ["request.followup", "request.followup_done"]
    done = hub.events[-1]["data"]
    assert done == {"id": "fu_1", "ok": True, "answer": "Cohort A had more donors", "error": None,
                    "cost_usd": 1.5, "cost_known": True}


@pytest.mark.asyncio
async def test_followup_without_resume_carries_report_and_earlier_answers():
    hub = FakeHub(answer(), resume=False)
    hub.requests["r"]["followups"].append({"id": "fu_0", "text": "Which test?", "status": "done",
                                           "answer": "Wilcoxon on donors", "agent_id": "cso"})
    hub.ask("And the effect size?")
    await Orchestrator(hub).run_followup("r", "fu_1")
    task = hub.calls[0]
    assert task.resume_session_id is None
    assert "Earlier follow-up: Which test?\nYour answer: Wilcoxon on donors" in task.prompt
    assert "Original request: Compare cohorts" in task.prompt and "[path] /srv/refs/yuan" in task.prompt
    assert hub.requests["r"]["cso_session_id"] == "cso-1"


@pytest.mark.asyncio
async def test_failed_followup_is_recorded_and_request_stays_done():
    hub = FakeHub(answer(text="", ok=False, error="runner offline"))
    hub.s.orchestrator.step_max_attempts = 1
    hub.ask("Why?")
    await Orchestrator(hub).run_followup("r", "fu_1")
    entry = hub.requests["r"]["followups"][0]
    assert entry["status"] == "failed" and entry["error"] == "runner offline" and entry["answer"] == ""
    assert hub.events[-1]["data"]["ok"] is False and hub.requests["r"]["status"] == "done"


@pytest.mark.asyncio
async def test_followup_hitting_the_turn_limit_gets_no_writable_wrap_up():
    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="max turns",
                          error_kind="error_max_turns", session_id="cso-2", workdir="/w/cso")

    hub = FakeHub(reply)
    hub.ask("Why?")
    await Orchestrator(hub).run_followup("r", "fu_1")
    # The wrap-up asks to save PARTIAL_STATUS.md; on a read-only question it would replace the read-only
    # overrides with {"max_turns": 2} and hand back write tools and MCP servers.
    assert [t.meta["kind"] for t in hub.calls] == ["followup"]
    assert all(t.meta["agent_overrides"]["sandbox"] == "read-only" for t in hub.calls)
    assert hub.requests["r"]["followups"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_writable_step_wrap_up_keeps_its_other_overrides():
    async def reply(task):
        if task.meta["kind"] == "wrap_up":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="saved",
                              outputs=["outputs/PARTIAL_STATUS.md"])
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="max turns",
                          error_kind="error_max_turns", session_id="w-1", workdir="/w/s1")

    hub = FakeHub(reply)
    task = Task(agent_id="worker", request_id="r", prompt="work",
                meta={"kind": "step", "step_id": "s1", "agent_overrides": {"tools": ["Read"]}})
    await Orchestrator(hub).run_step(task)
    wrap = hub.calls[-1]
    assert wrap.meta["kind"] == "wrap_up"
    assert wrap.meta["agent_overrides"] == {"tools": ["Read"], "max_turns": 2}


@pytest.mark.asyncio
async def test_direct_request_followup_resumes_that_agents_last_session(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.agents = {"worker": {"id": "worker", "engine": "claude_code"}}
    hub.requests["r"] = {"id": "r", "text": "Plot it", "mode": "direct", "agent_id": "worker", "status": "done",
                         "report": "plotted", "followups": []}
    hub.store.put("task", "t1", {"request_id": "r", "kind": "direct", "dispatched_at": 1, "completed": True,
                                 "payload": {"agent_id": "worker"},
                                 "result": {"session_id": "w-1", "workdir": str(tmp_path / "w")}})
    calls = []

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="Axis is log10",
                          session_id="w-2", workdir=str(tmp_path / "w"))

    hub.dispatch = dispatch
    entry = hub.start_followup("r", "  Which axis scale?  ")
    assert entry["agent_id"] == "worker" and entry["text"] == "Which axis scale?"
    await asyncio.sleep(0)
    for _ in range(50):
        if hub.requests["r"]["followups"][0]["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert calls[0].resume_session_id == "w-1" and calls[0].meta["workdir"] == str(tmp_path / "w")
    assert hub.requests["r"]["followups"][0]["answer"] == "Axis is log10"
    assert "cso_session_id" not in hub.requests["r"]


def test_followup_endpoint_validates_state(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    hub.requests["run"] = {"id": "run", "text": "t", "mode": "orchestrate", "status": "running"}
    hub.requests["done"] = {"id": "done", "text": "t", "mode": "orchestrate", "status": "done",
                            "followups": [{"id": "fu_x", "text": "q", "status": "running"}]}
    hub.requests["idle"] = {"id": "idle", "text": "t", "mode": "orchestrate", "status": "failed"}
    post = lambda rid, text: client.post(f"/api/requests/{rid}/followup", json={"text": text}, headers=auth)
    assert client.post("/api/requests/done/followup", json={"text": "q"}).status_code == 401
    assert post("missing", "q").status_code == 404
    assert post("run", "q").status_code == 409
    assert post("done", "q").status_code == 409  # one follow-up at a time
    assert post("idle", "q").status_code == 409  # the CSO is not on any runner
    assert post("idle", "   ").status_code == 422 and post("idle", "x" * 4001).status_code == 422


def test_gateway_restart_marks_a_running_followup_interrupted(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.requests["r"] = {"id": "r", "status": "done", "text": "t",
                         "followups": [{"id": "fu_1", "text": "q", "status": "running"},
                                       {"id": "fu_0", "text": "p", "status": "done", "answer": "a"}]}
    hub.save_request("r")
    restarted = Hub(settings)
    followups = restarted.requests["r"]["followups"]
    assert followups[0]["status"] == "interrupted" and "restart" in followups[0]["error"]
    assert followups[1]["status"] == "done"
    assert restarted.snapshot()["data"]["requests"][0]["followups"] == followups
    restarted.requests["r"]["followups"] = [{"id": f"fu_{i}", "text": "q", "status": "done", "answer": "a"}
                                            for i in range(25)]
    shown = restarted.snapshot()["data"]["requests"][0]["followups"]
    assert [f["id"] for f in shown] == [f"fu_{i}" for i in range(5, 25)], "snapshots stay bounded"


@pytest.mark.asyncio
async def test_runner_gives_followups_no_ask_tool(tmp_path, monkeypatch):
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    agent = AgentSpec(id="cso", name="CSO", role="test", engine=Engine.claude_code, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)
    seen = {}

    class Adapter:
        async def run(self, ctx):
            seen.setdefault("servers", []).append([s.name for s in ctx.mcp_servers])
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    await runner.run_task(Task(agent_id="cso", prompt="q", meta={"kind": "followup"}))
    await runner.run_task(Task(agent_id="cso", prompt="q", meta={"kind": "plan"}))
    assert seen["servers"] == [[], ["labhq_ask"]]


async def _until(predicate, timeout=30.0):
    started = time.time()
    while not predicate():
        assert time.time() - started < timeout, "timed out"
        await asyncio.sleep(0.05)


async def test_followup_end_to_end_with_mock_runner(tmp_path):
    shutil.copytree(REPO / "agents", tmp_path / "agents")
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    port = free_port()
    s.gateway.port, s.gateway.url = port, f"ws://127.0.0.1:{port}"
    s.runner.broker_port, s.runner.force_engine = free_port(), "mock"
    s.runner.workspace_root, s.runner.talent_dir = str(tmp_path / "runs"), str(tmp_path / "talent")
    s.runner.agents_dir = str(tmp_path / "agents")
    s.hpc.scheduler = "none"
    app = create_app(s)
    hub = app.state.hub
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    tasks = [asyncio.create_task(server.serve())]
    await _until(lambda: server.started, 10)
    runner = Runner(s)
    tasks.append(asyncio.create_task(runner.run_forever()))
    try:
        await _until(lambda: "cso" in hub.agents, 15)
        rid = hub.create_request(RequestIn(text="공개 데이터 요약"))
        await _until(lambda: hub.requests[rid]["status"] != "running", 30)
        req = hub.requests[rid]
        assert req["status"] == "done", req.get("error")
        session, workdir = req["cso_session_id"], req["cso_workdir"]
        entry = hub.start_followup(rid, "왜 그 데이터를 골랐나요?")
        await _until(lambda: entry["status"] != "running", 20)
        assert entry["status"] == "done", entry
        task = next(v for v in hub.store.all("task").values()
                    if (v.get("payload") or {}).get("meta", {}).get("followup_id") == entry["id"])
        assert task["payload"]["resume_session_id"] == session
        assert task["payload"]["meta"]["workdir"] == workdir
        assert task["result"]["workdir"] == workdir, "the follow-up ran in the request's CSO workspace"
        assert "(깨어나서)" in entry["answer"]
        assert hub.requests[rid]["status"] == "done"
    finally:
        runner.stop()
        server.should_exit = True
        await asyncio.sleep(0.2)
        for t in tasks:
            t.cancel()
