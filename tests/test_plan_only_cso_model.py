import asyncio

import pytest
from pydantic import ValidationError

from labhq.cli import main
from labhq.gateway.server import Hub, RequestIn
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.daemon import Runner
from labhq.settings import Settings


def test_cli_send_forwards_plan_only_and_cso_model(monkeypatch):
    sent = {}

    def api(_settings, method, path, **kwargs):
        sent.update(method=method, path=path, **kwargs)
        return {"request_id": "req_1"}

    monkeypatch.setattr("labhq.cli._api", api)
    main(["send", "compare plans", "--plan-only", "--cso-model", "gpt-6-astra", "--no-wait"])

    assert sent["json"]["mode"] == "plan_only"
    assert sent["json"]["cso_model"] == "gpt-6-astra"

    with pytest.raises(SystemExit):
        main(["send", "direct", "--agent", "worker", "--cso-model", "gpt-6-astra"])


def test_cso_model_allowlist_rejects_unknown_and_fable(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)

    with pytest.raises(ValueError, match="allowed"):
        hub.create_request(RequestIn(text="plan", cso_model="unknown-model"))
    with pytest.raises(ValidationError, match="Fable"):
        Settings.model_validate({"orchestrator": {"cso_models": ["opus", "claude-fable-5-1"]}})


def test_runner_engine_override_is_validated_and_request_local():
    original = AgentSpec(id="cso", name="CSO", role="plan", engine=Engine.claude_code, model="opus")
    fake = type("FakeRunner", (), {"registry": type("Registry", (), {"get": lambda _self, _id: original})(),
                                    "s": Settings()})()
    task = Task(agent_id="cso", prompt="plan", meta={"kind": "plan", "agent_overrides": {
        "engine": "codex", "model": "gpt-6-astra"}})

    selected = Runner._resolve_agent(fake, task)

    assert selected.engine is Engine.codex and selected.model == "gpt-6-astra"
    assert original.engine is Engine.claude_code and original.model == "opus"


class ModelHub:
    def __init__(self, *, mode="plan_only", cso_model=None):
        self.s = Settings()
        self.s.orchestrator.chief_of_staff_agent = None
        self.s.orchestrator.max_revisions = 0
        self.requests = {"r": {"text": "same request", "mode": mode, "agent_id": None,
                                "work_kind": "auto", "scope_status": "in_scope", "budget_usd": 10,
                                "project_dirs": [], "cso_model": cso_model}}
        self.agents = {
            "cso": {"id": "cso", "name": "CSO", "role": "plan", "engine": "claude_code", "model": "opus"},
            "sci_reviewer": {"id": "sci_reviewer", "name": "Reviewer", "role": "review",
                             "engine": "codex", "model": "gpt-6-astra"},
            "worker": {"id": "worker", "name": "Worker", "role": "work", "engine": "mock", "model": None},
        }
        self.calls = []
        self.events = []

    async def dispatch(self, task):
        self.calls.append(task)
        kind = task.meta["kind"]
        if kind == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="same plan",
                              structured={"clarifying_questions": [], "steps": [{"id": "s1",
                                  "agent_id": "worker", "instruction": "work", "outputs": [],
                                  "depends_on": []}], "recruit": [], "notes": ""})
        if kind == "step":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="worked")
        if kind == "review":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured={"verdict": "accept", "scores": {"addresses_question": 5,
                                  "evidence": 5, "thoroughness": 5}, "issues": []})
        if kind == "synthesis":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="done")
        raise AssertionError(kind)

    async def publish(self, event):
        self.events.append(event)

    def supports_resume(self, _agent_id):
        return False

    def result_map(self, _rid):
        return {}

    def save_request(self, _rid):
        pass


@pytest.mark.asyncio
async def test_plan_only_override_changes_only_cso_and_stops_after_same_plan():
    default = ModelHub()
    overridden = ModelHub(cso_model="gpt-6-astra")

    await Orchestrator(default).run_request("r")
    await Orchestrator(overridden).run_request("r")

    assert [task.meta["kind"] for task in default.calls] == ["plan"]
    assert [task.meta["kind"] for task in overridden.calls] == ["plan"]
    assert default.requests["r"]["plan"] == overridden.requests["r"]["plan"]
    assert "agent_overrides" not in default.calls[0].meta
    assert overridden.calls[0].meta["agent_overrides"] == {"engine": "codex", "model": "gpt-6-astra"}
    assert overridden.requests["r"]["status"] == "done"


@pytest.mark.asyncio
async def test_gpt_cso_uses_a_different_science_reviewer_model():
    hub = ModelHub(mode="orchestrate", cso_model="gpt-6-astra")
    await Orchestrator(hub).run_request("r")

    calls = {task.meta["kind"]: task for task in hub.calls}
    assert calls["plan"].meta["agent_overrides"] == {"engine": "codex", "model": "gpt-6-astra"}
    assert calls["review"].meta["agent_overrides"] == {"engine": "claude_code", "model": "opus"}
    assert calls["synthesis"].meta["agent_overrides"] == {"engine": "codex", "model": "gpt-6-astra"}
    assert calls["review"].meta["agent_overrides"]["model"] != calls["plan"].meta["agent_overrides"]["model"]
