"""Facilities uses the shipped roster and the existing consult/session boundary."""
import json
from pathlib import Path

import pytest

from labhq.adapters.codex import CodexAdapter
from labhq.gateway.server import Hub
from labhq.models import AskRequest, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import DataZone, Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def staff(tmp_path, monkeypatch):
    settings = Settings()
    settings.gateway.state_dir = settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.runner.agents_dir = str(ROOT / "agents")
    settings.runner.contract_dir = str(tmp_path / "contracts")
    settings.runner.talent_dir = str(tmp_path / "talent")
    settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex")}
    settings.hpc.scheduler = "none"
    runner = Runner(settings)
    runner.registry.load()
    # No installed engine, login, live runner or port is needed by this fake runner.
    monkeypatch.setattr("labhq.runner.daemon.adapter_preflight_error", lambda *args: None)
    hub = Hub(settings)
    hub.agents = {a["id"]: a for a in runner.roster()}
    hub.agent_runner = {a: "fake" for a in hub.agents}
    hub.requests["r"] = {"id": "r", "text": "diagnose missing tool", "status": "running"}
    yield hub, runner
    hub.store.close()
    runner.store.close()


async def test_shipped_facilities_answers_and_resumes_own_session(staff, monkeypatch):
    hub, runner = staff
    calls = []

    class Adapter:
        async def run(self, ctx):
            calls.append(ctx)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True,
                              text="원인: 도구 없음. 근거: doctor. PI 할 일: 설치 여부 결정.",
                              session_id="facility-session", workdir=str(ctx.workdir))

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())

    async def dispatch(task):
        result = await runner.run_task(task)
        hub.store.put("task", task.id, {"request_id": "r", "runner_id": "fake", "completed": True,
                                       "payload": task.model_dump(mode="json"),
                                       "result": result.model_dump(mode="json")})
        return result

    hub.dispatch = dispatch
    for i in range(2):
        ask = AskRequest(task_id=f"source-{i}", agent_id="analyst", request_id="r", to="facilities",
                         question=f"도구가 없어요 {i}", why_blocked="실행 불가")
        await hub.orchestrator.answer_ask(ask, "fake")
        answer = hub.store.get("ask", ask.id)["answer"]
        assert answer["from"] == answer["routed_to"] == "facilities"
    assert calls[0].agent.name == "시설팀"
    assert calls[0].agent.model == "gpt-6.1-sol"
    assert calls[0].task.resume_session_id is None
    assert calls[1].task.resume_session_id == "facility-session"
    assert calls[0].workdir == calls[1].workdir
    assert all(c.read_only and not c.mcp_servers for c in calls)


async def test_facilities_evidence_and_command_are_diagnostic_only(staff, monkeypatch, tmp_path):
    hub, runner = staff
    manifest = Path(runner.s.runner.state_dir) / "capabilities.json"
    manifest.write_text(json.dumps({"checks": [{"group": "compute", "name": "docker", "status": "warn",
                                                "detail": "token=private-value /home/private/person"}]}), encoding="utf-8")
    source = Path(runner.s.runner.workspace_root) / "source"
    source.mkdir(parents=True)
    (source / "error.txt").write_text("ModuleNotFoundError: missing_tool", encoding="utf-8")
    captured = []

    class Adapter:
        async def run(self, ctx):
            captured.append(ctx)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="진단")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())
    result = await runner.run_task(Task(agent_id="facilities", prompt="진단", meta={
        "kind": "consult", "source_workdir": str(source), "consult_refs": ["error.txt"],
        "agent_overrides": {"sandbox": "danger-full-access", "builtin_mcp": ["hpc"]}}))
    assert result.ok
    ctx = captured[0]
    assert "docker" in ctx.prompt and "warn" in ctx.prompt and "missing_tool" in ctx.prompt
    assert "private-value" not in ctx.prompt and "/home/private/person" not in ctx.prompt
    assert ctx.agent.sandbox == "read-only" and not ctx.agent.tools and not ctx.agent.plugin_dirs
    assert not ctx.mcp_servers and not ctx.agent.allow_skills
    command = CodexAdapter(runner.s).build_command(ctx)
    assert all(command[i - 1] == "--disable" for i, v in enumerate(command) if v in {"shell_tool", "unified_exec"})
    assert {"shell_tool", "unified_exec"} <= set(command)
    assert not any('"write"' in flag for flag in command if flag.startswith("permissions."))


@pytest.mark.parametrize("engine", ["gemini", "antigravity", "cli"])
async def test_facilities_force_engine_cannot_bypass_read_only(staff, engine):
    _, runner = staff
    runner.s.runner.force_engine = engine
    result = await runner.run_task(Task(agent_id="facilities", prompt="diagnose", meta={"kind": "consult"}))
    assert not result.ok and "read-only policy" in result.error


def test_facilities_engine_preflight_still_controls_roster(staff, monkeypatch):
    _, runner = staff
    assert "facilities" in {a["id"] for a in runner.roster()}
    monkeypatch.setattr("labhq.runner.daemon.adapter_preflight_error",
                        lambda settings, agent, root: "not ready" if agent.id == "facilities" else None)
    assert "facilities" not in {a["id"] for a in runner.roster()}


@pytest.mark.parametrize("offline", [False, True])
async def test_facilities_missing_or_offline_keeps_cso_fallback(staff, offline):
    hub, _ = staff
    hub.agents.pop("facilities", None)
    ask = AskRequest(task_id="source", agent_id="analyst", request_id="r", to="facilities",
                     question="도구 없음", why_blocked="실행 불가")
    waited = []
    if offline:
        hub.store.put("ask", ask.id, {"routed_to": "facilities"})

    async def wait(agent, seconds):
        waited.append(agent)

    async def dispatch(task):
        assert task.agent_id == "cso"
        return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="시설팀 연결 필요")

    hub.wait_agent_online = wait
    hub.dispatch = dispatch
    await hub.orchestrator.answer_ask(ask, "fake")
    assert hub.store.get("ask", ask.id)["answer"]["from"] == "cso"
    assert waited == (["facilities"] if offline else [])


@pytest.mark.parametrize("ref", ["../outside.txt", "private/error.txt"])
async def test_facilities_rejects_unpermitted_refs_before_adapter(staff, tmp_path, monkeypatch, ref):
    _, runner = staff
    source = Path(runner.s.runner.workspace_root) / "source"
    private = source / "private"
    private.mkdir(parents=True)
    (private / "error.txt").write_text("private evidence", encoding="utf-8")
    runner.s.policy.data_zones = [DataZone(path=str(private), level="restricted")]
    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: pytest.fail("must refuse before adapter"))
    result = await runner.run_task(Task(agent_id="facilities", prompt="진단", meta={
        "kind": "consult", "source_workdir": str(source), "consult_refs": [ref]}))
    assert not result.ok and "consult refs" in result.error
