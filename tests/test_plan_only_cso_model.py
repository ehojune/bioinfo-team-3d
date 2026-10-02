import asyncio

import pytest
from pydantic import ValidationError

from labhq.cli import main
from labhq.gateway.server import Hub, RequestIn
from labhq.adapters import read_only_profile
from labhq.models import AgentSpec, AskRequest, Engine, Task, TaskResult
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
    assert "agent_overrides" not in default.calls[0].meta and "agent_identity" not in default.calls[0].meta
    assert overridden.calls[0].meta["agent_identity"] == {"engine": "codex", "model": "gpt-6-astra"}
    assert overridden.requests["r"]["status"] == "done"


@pytest.mark.asyncio
async def test_gpt_cso_uses_a_different_science_reviewer_model():
    hub = ModelHub(mode="orchestrate", cso_model="gpt-6-astra")
    await Orchestrator(hub).run_request("r")

    calls = {task.meta["kind"]: task for task in hub.calls}
    assert calls["plan"].meta["agent_identity"] == {"engine": "codex", "model": "gpt-6-astra"}
    assert calls["review"].meta["agent_identity"] == {"engine": "claude_code", "model": "opus"}
    assert calls["synthesis"].meta["agent_identity"] == {"engine": "codex", "model": "gpt-6-astra"}
    assert calls["review"].meta["agent_identity"]["model"] != calls["plan"].meta["agent_identity"]["model"]
    assert "agent_identity" not in calls["step"].meta, "a worker step keeps its own registry model"


# #272 review 4163267332: a Codex CSO's saved session must never be resumed by the registry's Claude CSO.
CODEX_CSO = {"engine": "codex", "model": "gpt-6-astra"}


class FinishedHub:
    """A finished --cso-model request whose Codex plan session is saved, as run_request leaves it."""

    def __init__(self, cso_model="gpt-6-astra"):
        self.s = Settings()
        self.requests = {"r": {"id": "r", "text": "same request", "mode": "orchestrate", "status": "done",
                               "report": "done", "cso_model": cso_model, "cso_session_id": "codex-1",
                               "cso_workdir": "/w/cso", "results": {}, "followups": []}}
        self.agents = {"cso": {"id": "cso", "engine": "claude_code", "model": "opus"},
                       "worker": {"id": "worker", "engine": "mock"}}
        self.calls, self.events = [], []

    async def dispatch(self, task):
        self.calls.append(task)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="answer",
                          session_id="codex-2", workdir="/w/cso")

    async def publish(self, event):
        self.events.append(event)

    def supports_resume(self, _agent_id):
        return True

    def save_request(self, _rid):
        pass

    def ask(self):
        self.requests["r"]["followups"].append({"id": "fu_1", "text": "Why?", "agent_id": "cso",
                                                "status": "running"})


def fake_runner(agent):
    registry = type("Registry", (), {"get": lambda _self, _id: agent})()
    return type("FakeRunner", (), {"registry": registry, "s": Settings()})()


@pytest.mark.asyncio
async def test_followup_resumes_the_codex_cso_session_as_the_selected_model():
    hub = FinishedHub()
    hub.ask()

    await Orchestrator(hub).run_followup("r", "fu_1")

    task = hub.calls[0]
    assert task.meta["kind"] == "followup" and task.resume_session_id == "codex-1"
    assert task.meta["agent_identity"] == CODEX_CSO, "the Codex session is resumed by Codex, not the registry CSO"
    assert task.meta["agent_overrides"]["sandbox"] == "read-only", "the follow-up stays read-only"
    assert hub.requests["r"]["followups"][0]["status"] == "done"


async def test_consult_routed_to_the_cso_keeps_the_selected_model(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    try:
        hub.agents = {"worker": {"engine": "mock"}, "cso": {"engine": "claude_code", "model": "opus"}}
        hub.requests["r"] = {"id": "r", "text": "same request", "mode": "orchestrate", "status": "running",
                             "cso_model": "gpt-6-astra", "cso_session_id": "codex-1", "cso_workdir": "/w/cso"}
        calls = []

        async def dispatch(task):
            calls.append(task)
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="use cases",
                              session_id="codex-2", workdir="/w/cso")

        hub.dispatch = dispatch
        ask = AskRequest(task_id="blocked", agent_id="worker", request_id="r", to="cso",
                         question="Cases or controls?", why_blocked="cohort missing")
        await hub.orchestrator.answer_ask(ask, "origin")

        assert [task.meta["kind"] for task in calls] == ["consult"]
        assert calls[0].meta["agent_identity"] == CODEX_CSO and calls[0].resume_session_id == "codex-1"
        assert hub.store.get("ask", ask.id)["answer"]["status"] == "answered"
    finally:
        hub.store.close()


@pytest.mark.parametrize("kind", ["followup", "consult"])
def test_runner_keeps_the_request_identity_on_read_only_tasks(kind):
    registry_cso = AgentSpec(id="cso", name="CSO", role="plan", engine=Engine.claude_code, model="opus")
    sender = {"sandbox": "read-only", "engine": "claude_code", "model": "opus", "tools": ["Bash(*)"]}
    task = Task(agent_id="cso", prompt="q", meta={"kind": kind, "agent_overrides": sender,
                                                   "agent_identity": CODEX_CSO})

    selected = read_only_profile(Runner._resolve_agent(fake_runner(registry_cso), task))

    assert selected.engine is Engine.codex and selected.model == "gpt-6-astra"
    assert selected.tools == [] and selected.sandbox == "read-only", "agent_overrides still give nothing"
    assert registry_cso.engine is Engine.claude_code and registry_cso.model == "opus"


@pytest.mark.parametrize("identity", [{"engine": "codex"}, {**CODEX_CSO, "sandbox": "workspace-write"},
                                      {"engine": "codex", "model": "--dangerously-bypass"},
                                      {"engine": "nope", "model": "gpt-6-astra"}])
def test_runner_refuses_an_identity_it_cannot_apply(identity):
    registry_cso = AgentSpec(id="cso", name="CSO", role="plan", engine=Engine.claude_code, model="opus")
    task = Task(agent_id="cso", prompt="q", meta={"kind": "followup", "agent_identity": identity})

    with pytest.raises(ValueError):
        Runner._resolve_agent(fake_runner(registry_cso), task)


@pytest.mark.asyncio
async def test_a_cso_model_that_is_no_longer_allowed_fails_loudly_instead_of_switching():
    hub = FinishedHub()
    hub.s.orchestrator.cso_models = ["opus"]
    hub.ask()

    await Orchestrator(hub).run_followup("r", "fu_1")

    assert hub.calls == [], "nothing runs as the registry's CSO"
    entry = hub.requests["r"]["followups"][0]
    assert entry["status"] == "failed" and "gpt-6-astra" in entry["error"]


@pytest.mark.asyncio
async def test_default_request_followup_has_no_identity():
    hub = FinishedHub(cso_model=None)
    hub.ask()

    await Orchestrator(hub).run_followup("r", "fu_1")

    assert "agent_identity" not in hub.calls[0].meta
