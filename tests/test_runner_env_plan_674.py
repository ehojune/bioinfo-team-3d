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


def test_runner_environment_clones_do_not_count_toward_max_steps():
    """#674 follow-up: a plan that fills max_steps with its own steps still gets its runner-local environments."""
    from labhq.orchestrator.cso import scheduler_clone_extra

    logical = plan()  # env + two consumers: exactly 3 steps
    expanded = split_runner_environments(logical, {"a": "win", "b": "wsl"}, {"win": "windows", "wsl": "wsl"}, lambda _: True)
    assert len(logical) == 3 and len(expanded) == 4 and scheduler_clone_extra(expanded) == 1
    assert scheduler_clone_extra(logical) == 0 and scheduler_clone_extra(None) == 0
    restored = json.loads(json.dumps(expanded))
    steps, _ = validate_steps(restored, {"a", "b", "old"}, 3, excluded=set(), reject_excess=True, stored_environment=True)
    assert len(steps) == 4  # the clone is not cut and not rejected
    # The planner's own steps are still limited: one more ordinary step is over, with or without clones.
    extra_step = {"id": "z", "agent_id": "a", "instruction": "more", "outputs": [], "depends_on": []}
    with pytest.raises(ValueError, match="maximum is 3 plus 1 runner environment steps"):
        validate_steps([*restored, extra_step], {"a", "b", "old"}, 3, excluded=set(), reject_excess=True,
                       stored_environment=True)
    with pytest.raises(ValueError, match="maximum is 3"):
        validate_steps(logical + [extra_step], {"a", "b", "old"}, 3, excluded=set(), reject_excess=True)
    # A plan without bindings is judged exactly as before.
    with pytest.raises(ValueError, match="maximum is 3"):
        validate_steps(restored, {"a", "b", "old"}, 3, excluded=set(), reject_excess=True)


def test_research_step_limits_leave_room_for_scheduler_clones_in_their_own_branch_only():
    from labhq.research.contract import research_plan_errors

    def clone(branch=None):
        return {"environment_binding": {"logical_step_id": "env"}, **({"branch": branch} if branch else {})}

    def limit_errors(raw, **kw):
        return [e for e in research_plan_errors(raw, active_packs={}, max_steps=2, **kw) if "maximum is" in e]

    assert limit_errors({"steps": [{}, {}, {}], "branches": []})  # three plain steps are over a limit of two
    # Two clones of one logical environment add one step beyond the planner's own two.
    assert not limit_errors({"steps": [clone(), clone(), {}], "branches": []})
    assert limit_errors({"steps": [clone(), clone(), {}, {}], "branches": []})
    # Branched: the allowance grows only for the branch that holds the clones.
    steps = [{"branch": "a"}, {"branch": "a"}, clone("a"), clone("a"), *[{"branch": "b"} for _ in range(4)]]
    errors = limit_errors({"steps": steps, "branches": [{"id": "a"}, {"id": "b"}]}, max_branches=5, max_branch_steps=3)
    assert any("branch b has 4 steps" in e for e in errors) and not any("branch a has" in e for e in errors)
    steps = [{"branch": "a"}, {"branch": "a"}, clone("a"), clone("a"), *[{"branch": "b"} for _ in range(3)]]
    assert not limit_errors({"steps": steps, "branches": [{"id": "a"}, {"id": "b"}]}, max_branches=5, max_branch_steps=3)


def _bound_task(runner="wsl", runtime="wsl", **meta):
    planned = split_runner_environments(plan(), {"a": "win", "b": "wsl"}, {"win": "windows", "wsl": "wsl"}, lambda _: True)
    consumer = next(s for s in planned if s["id"] == "b")
    producer = next(s for s in planned if s.get("environment_binding", {}).get("runner_id") == runner)
    binding = {**consumer["environment_bindings"][0], "runtime": runtime}
    return Task(agent_id="b", prompt="", meta={"session_runner": runner, "environment_bindings": [binding], **meta}), producer


def test_dispatch_rechecks_the_runner_runtime_against_the_stored_binding():
    """#674 follow-up: the same runner ID reconfigured native <-> WSL must not receive a stale interpreter path."""
    task, _ = _bound_task()
    assert Hub._session_pin_refusal(task, "wsl", "wsl") is None  # still the runtime the plan was bound to
    stale = Hub._session_pin_refusal(task, "wsl", "linux")
    assert stale is not None and not stale.ok and stale.cost_usd == 0.0
    assert "expects runtime wsl" in stale.error and "reports linux" in stale.error
    unknown = Hub._session_pin_refusal(task, "wsl", None)  # a runner that reports no runtime proves nothing
    assert unknown is not None and "no runtime" in unknown.error
    assert Hub._session_pin_refusal(task, "wsl") is None  # callers that pass no runtime keep the runner-ID check only
    assert Hub._session_pin_refusal(task, "win", "windows") is not None  # the runner pin itself still refuses


def test_the_producer_binding_is_rechecked_too_and_other_runners_bindings_are_ignored():
    _, producer = _bound_task()
    task = Task(agent_id="b", prompt="", meta={"session_runner": "wsl",
                                               "environment_binding": producer["environment_binding"]})
    assert Hub._session_pin_refusal(task, "wsl", "wsl") is None
    assert Hub._session_pin_refusal(task, "wsl", "windows") is not None
    other = Task(agent_id="b", prompt="", meta={"environment_bindings": [
        {**producer["environment_binding"], "runner_id": "win", "runtime": "windows"}]})
    assert Hub._session_pin_refusal(other, "wsl", "wsl") is None  # that binding belongs to another runner


@pytest.mark.asyncio
async def test_hub_dispatch_refuses_before_sending_when_the_runner_runtime_changed(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    task, _ = _bound_task(runner="wsl", runtime="wsl")
    hub.agent_runner["b"] = "wsl"
    hub.runner_capabilities["wsl"] = {"runtime_env": "windows"}  # same ID, now a Windows host
    result = await hub.dispatch(task)
    assert not result.ok and result.cost_usd == 0.0 and "expects runtime wsl" in result.error


@pytest.mark.asyncio
async def test_a_runner_that_changes_runtime_while_steers_attach_is_not_sent_the_stale_binding(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    task, _ = _bound_task(runner="wsl", runtime="wsl")
    hub.agent_runner["b"] = "wsl"
    hub.runner_capabilities["wsl"] = {"runtime_env": "wsl"}  # the first check passes
    sent = []

    async def attach(candidate):
        hub.runner_capabilities["wsl"] = {"runtime_env": "windows"}  # the same ID re-registers while awaiting
        return candidate

    async def send(rid, frame):
        sent.append(frame)

    hub._attach_steers = attach
    hub.send_runner = send
    import asyncio

    result = await asyncio.wait_for(hub.dispatch(task), 5)  # without the recheck the task is sent and awaits a reply
    assert not result.ok and result.cost_usd == 0.0 and "expects runtime wsl" in result.error
    assert sent == [] and task.id not in hub.futures and task.id not in hub.task_runner
