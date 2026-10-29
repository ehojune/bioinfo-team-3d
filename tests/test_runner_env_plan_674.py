import copy
import json

import pytest

from labhq.gateway.server import Hub
from labhq.models import Task, TaskResult
from labhq.orchestrator.cso import (ENV_LOCK_OUTPUT, ENV_MANIFEST_OUTPUT, environment_runner_pin,
                                    Orchestrator, split_runner_environments, validate_steps)
from labhq.research.contract import ResearchStep
from labhq.util import INPUT_STEP_ID
from tests.test_cso import FakeHub
from labhq.research.contract import plan_sha256
from labhq.settings import Settings
from tests import test_research_continue as cont
import time


def plan():
    return [
        {"id": "env", "agent_id": "old", "instruction": "Install approved packages", "outputs": [ENV_LOCK_OUTPUT], "depends_on": []},
        {"id": "a", "agent_id": "a", "instruction": "run", "outputs": [], "depends_on": ["env"]},
        {"id": "b", "agent_id": "b", "instruction": "run", "outputs": [], "depends_on": ["env"]},
    ]


def test_split_is_stable_and_rewires_with_platform_manifest():
    original = plan()
    before = copy.deepcopy(original)
    args = ({"a": "win", "b": "wsl"}, {"win": "windows", "wsl": "wsl"}, lambda _: True)
    result = split_runner_environments(original, *args)
    assert original == before
    assert result == split_runner_environments(original, *args)
    envs = [s for s in result if ENV_LOCK_OUTPUT in s["outputs"]]
    assert len(envs) == 2
    for env in envs:
        binding = env["environment_binding"]
        assert INPUT_STEP_ID.fullmatch(env["id"])
        assert ENV_MANIFEST_OUTPUT in env["outputs"]
        assert json.dumps(binding) in env["instruction"]
        consumer = next(s for s in result if s["id"] in {"a", "b"} and s["agent_id"] == env["agent_id"])
        assert consumer["depends_on"] == [env["id"]]
        assert consumer["environment_bindings"] == [binding]
        assert "runner-local interpreter at inputs/" not in consumer["instruction"]
        assert binding["python_path"] == (".venv/Scripts/python.exe" if binding["runtime"] == "windows" else ".venv/bin/python")


def test_single_runner_selects_first_authorized_consumer():
    result = split_runner_environments(plan(), {"a": "r", "b": "r"}, {"r": "linux"}, lambda a: a == "b")
    assert len(result) == 3
    assert result[0]["id"] == "env" and result[0]["agent_id"] == "b"
    assert result[1]["depends_on"] == result[2]["depends_on"] == ["env"]


def test_creator_can_be_original_on_consumer_runner():
    runners = {"old": "win", "a": "win", "b": "wsl", "wrong": "elsewhere"}
    result = split_runner_environments(plan(), runners, {"win": "windows", "wsl": "wsl"},
                                       lambda agent: agent in {"old", "b", "wrong"})
    clones = [step for step in result if "environment_binding" in step]
    assert [(step["agent_id"], step["environment_binding"]["runner_id"]) for step in clones] == [
        ("old", "win"), ("b", "wsl")]
    with pytest.raises(ValueError, match="no authorized creator on runner wsl"):
        split_runner_environments(plan(), runners, {"win": "windows", "wsl": "wsl"},
                                  lambda agent: agent in {"old", "wrong"})


def test_claude_bash_capability_does_not_require_preapproval():
    hub = FakeHub(None)
    hub.agents = {"old": {"engine": "claude_code", "tools": ["Bash(python *)"]},
                  "a": {"engine": "mock"}, "b": {"engine": "mock"}}
    hub.agent_runner = {"old": "r", "a": "r", "b": "r"}
    hub.runners = {"r": object()}
    hub.runner_capabilities = {"r": {"runtime_env": "linux"}}
    assert Orchestrator(hub)._prepare_runner_environments(plan())[0]["agent_id"] == "old"
    hub.agents["old"]["disallowed_tools"] = ["Bash"]
    with pytest.raises(ValueError, match="no authorized creator"):
        Orchestrator(hub)._prepare_runner_environments(plan())


def test_transitive_consumer_receives_its_runner_binding():
    source = plan()
    source[2]["depends_on"] = ["a"]
    result = split_runner_environments(source, {"a": "win", "b": "wsl"},
                                       {"win": "windows", "wsl": "wsl"}, lambda _: True)
    b = next(s for s in result if s["id"] == "b")
    binding = b["environment_bindings"][0]
    assert binding["runner_id"] == "wsl"
    assert b["depends_on"] == ["a", binding["step_id"]]


def test_serialized_binding_pins_hub_dispatch_to_original_runner():
    planned = split_runner_environments(plan(), {"a": "win", "b": "wsl"},
                                        {"win": "windows", "wsl": "wsl"}, lambda _: True)
    restored = json.loads(json.dumps(planned))
    consumer = next(s for s in restored if s["id"] == "b")
    pin = environment_runner_pin(consumer)
    task = Task(agent_id="b", prompt="", meta={"session_runner": pin})
    assert Hub._session_pin_refusal(task, "wsl") is None
    assert Hub._session_pin_refusal(task, "win") is not None
    consumer["environment_bindings"][0]["python_path"] = "../../python"
    with pytest.raises(ValueError):
        environment_runner_pin(consumer)


def test_saved_general_plan_validation_retains_pin_without_cross_runner_inference():
    planned = split_runner_environments(plan(), {"a": "win", "b": "wsl"},
                                        {"win": "windows", "wsl": "wsl"}, lambda _: True)
    restored, _ = validate_steps(json.loads(json.dumps(planned)), {"a", "b"}, 10,
                                 excluded=set(), reject_excess=True, stored_environment=True)
    win_env, wsl_env = restored[:2]
    consumer = next(s for s in restored if s["id"] == "b")
    assert consumer["depends_on"] == [wsl_env["id"]]
    assert win_env["id"] not in consumer["depends_on"]
    assert environment_runner_pin(consumer) == "wsl"


@pytest.mark.asyncio
async def test_restored_plan_dispatch_refuses_runner_change(tmp_path):
    planned = split_runner_environments(plan(), {"a": "win", "b": "wsl"},
                                        {"win": "windows", "wsl": "wsl"}, lambda _: True)
    restored, _ = validate_steps(json.loads(json.dumps(planned)), {"a", "b"}, 10,
                                 excluded=set(), reject_excess=True, stored_environment=True)
    env = next(s for s in restored if s.get("environment_binding", {}).get("runner_id") == "wsl")
    seen = []

    async def dispatch(task):
        seen.append(task)
        return Hub._session_pin_refusal(task, "win") or TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True)

    hub = FakeHub(dispatch)
    prior = {env["id"]: TaskResult(task_id="env", agent_id="b", ok=True,
                                   workdir=str(tmp_path), outputs=[ENV_MANIFEST_OUTPUT])}
    await Orchestrator(hub).run_dag("r", "request", restored, prior, only={"b"})
    assert seen and seen[0].meta["session_runner"] == "wsl"
    assert not prior["b"].ok


@pytest.mark.asyncio
async def test_gateway_restart_preserves_binding_in_approved_plan_hash(tmp_path):
    source = cont._plan(1)
    source["steps"][0]["outputs"] = [ENV_LOCK_OUTPUT]
    source["steps"][0]["agent_id"] = "a"
    source["steps"][1]["agent_id"] = "b"
    source["steps"] = split_runner_environments(source["steps"], {"b": "wsl"},
                                                 {"wsl": "wsl"}, lambda _: True)
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    first = Hub(settings)
    first.requests["r"] = {"id": "r", "text": "research", "mode": "orchestrate", "status": "running",
                           "created_at": time.time(), "plan": source}
    first.save_request("r")
    restarted = Hub(settings)
    restored = restarted.requests["r"]["plan"]
    card = next(a["approval"] for a in restarted.approvals.values() if a["approval"]["kind"] == "resume")
    assert restored == source
    canonical = json.loads(card["detail"]["plan_canonical"])
    assert canonical["steps"][0]["environment_binding"] == source["steps"][0]["environment_binding"]
    assert canonical["steps"][1]["environment_bindings"] == source["steps"][1]["environment_bindings"]
    assert card["detail"]["cp1_target_sha256"] == plan_sha256(source)
    await restarted.resolve_approval(card["id"], True)
    assert restarted.requests["r"]["resume_cp1"]["target_sha256"] == plan_sha256(source)
    changed = copy.deepcopy(source)
    changed["steps"][1]["environment_bindings"][0]["runner_id"] = "other"
    assert plan_sha256(changed) != restarted.requests["r"]["resume_cp1"]["target_sha256"]


@pytest.mark.asyncio
async def test_new_research_plan_is_bound_before_cp1(tmp_path, monkeypatch):
    source = cont._plan(1)["steps"]
    source[0]["outputs"] = [ENV_LOCK_OUTPUT]
    source[0]["agent_id"] = source[1]["agent_id"] = "worker"
    hub = cont._hub(tmp_path, [cont.CP1], reviews=[cont.ACCEPT], plan_extra={"steps": source})
    monkeypatch.setattr(Orchestrator, "_prepare_runner_environments",
                        lambda self, steps: split_runner_environments(
                            steps, {"worker": "wsl"}, {"wsl": "wsl"}, lambda _: True))

    class StopAtCP1(BaseException):
        pass

    def stop():
        raise StopAtCP1()

    hub.hooks[1] = stop
    try:
        await Orchestrator(hub).run_request("r")
    except StopAtCP1:
        pass
    else:
        pytest.fail(str(hub.requests["r"].get("report")))
    saved = hub.requests["r"]["plan"]
    assert saved["steps"][0]["environment_binding"]["runner_id"] == "wsl"
    assert saved["steps"][1]["environment_bindings"] == [saved["steps"][0]["environment_binding"]]
    card = next(card for card in hub.approvals if card["kind"] == "research_plan")
    assert card["detail"]["target_sha256"] == plan_sha256(saved)
    assert json.loads(card["detail"]["plan_canonical"])["steps"][1]["environment_bindings"]


@pytest.mark.asyncio
async def test_consumer_dispatch_carries_binding_and_matching_producer_artifact(tmp_path):
    planned = split_runner_environments(plan(), {"a": "win", "b": "wsl"},
                                        {"win": "windows", "wsl": "wsl"}, lambda _: True)
    env = next(s for s in planned if s.get("environment_binding", {}).get("runner_id") == "wsl")
    seen = []

    async def dispatch(task):
        seen.append(task)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True)

    result = TaskResult(task_id="producer", agent_id="b", ok=True, workdir=str(tmp_path),
                        outputs=[ENV_MANIFEST_OUTPUT], output_sha256={ENV_MANIFEST_OUTPUT: "a" * 64})
    prior = {env["id"]: result}
    await Orchestrator(FakeHub(dispatch)).run_dag("r", "request", planned, prior, only={"b"})
    meta = seen[0].meta
    assert meta["session_runner"] == "wsl"
    assert meta["environment_bindings"] == [env["environment_binding"]]
    assert meta["upstream_steps"][env["id"]] == str(tmp_path)
    artifact = meta["upstream_artifacts"][env["id"]]
    assert artifact["task_id"] == "producer" and artifact["workdir"] == str(tmp_path)
    assert artifact["output_sha256"] == {ENV_MANIFEST_OUTPUT: "a" * 64}


def test_research_step_roundtrip_keeps_binding_and_legacy_omits_new_fields():
    raw = {"id": "a", "agent_id": "a", "instruction": "run", "phase": "analysis", "claim_ids": [],
           "input_refs": [], "outputs": [], "checks": ["check"], "evidence_slots": [], "depends_on": []}
    legacy = ResearchStep.model_validate(raw).model_dump(mode="json")
    assert "environment_binding" not in legacy and "environment_bindings" not in legacy
    planned = split_runner_environments(plan(), {"a": "win", "b": "wsl"},
                                        {"win": "windows", "wsl": "wsl"}, lambda _: True)
    binding = next(s for s in planned if s["id"] == "a")["environment_bindings"]
    restored = ResearchStep.model_validate({**raw, "environment_bindings": binding}).model_dump(mode="json")
    assert restored["environment_bindings"] == binding


@pytest.mark.parametrize("runners,runtimes,allowed", [
    ({"a": "r"}, {"r": "linux"}, True),
    ({"a": "r", "b": "r"}, {}, True),
    ({"a": "r", "b": "r"}, {"r": "linux"}, False),
])
def test_unknown_runner_runtime_or_creator_fails_closed(runners, runtimes, allowed):
    with pytest.raises(ValueError):
        split_runner_environments(plan(), runners, runtimes, lambda _: allowed)


def test_runner_id_is_not_restricted_to_the_step_id_grammar():
    runners = {"a": "lab pc 1", "b": "lab pc 1"}
    result = split_runner_environments(plan(), runners, {"lab pc 1": "linux"}, lambda _: True)
    binding = next(s for s in result if s["id"] == "a")["environment_bindings"][0]
    assert binding["runner_id"] == "lab pc 1"
    from labhq.research.contract import EnvironmentBinding
    assert EnvironmentBinding.model_validate(binding).runner_id == "lab pc 1"
    from labhq.orchestrator.cso import environment_runner_pin
    assert environment_runner_pin({"environment_bindings": [binding]}) == "lab pc 1"


@pytest.mark.asyncio
async def test_plan_only_request_does_not_bind_runners(tmp_path, monkeypatch):
    source = cont._plan(1)["steps"]
    source[0]["outputs"] = [ENV_LOCK_OUTPUT]
    source[0]["agent_id"] = source[1]["agent_id"] = "worker"
    hub = cont._hub(tmp_path, [cont.CP1], reviews=[cont.ACCEPT], plan_extra={"steps": source})
    hub.requests["r"]["mode"] = "plan_only"

    def refuse(self, steps):
        raise AssertionError("plan_only must not require a connected consumer runner")

    monkeypatch.setattr(Orchestrator, "_prepare_runner_environments", refuse)
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] != "failed", hub.requests["r"].get("report")
    assert "environment_binding" not in json.dumps(hub.requests["r"]["plan"])
