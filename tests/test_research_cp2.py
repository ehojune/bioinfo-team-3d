"""CP2 evidence review (#90): artifact binding, the research lane's own failure path, and structured decisions."""

import asyncio

import pytest
from pydantic import ValidationError

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.settings import Settings
from tests.test_research_protocol import MiniHub, _cp2_result, valid_plan

CP1 = {"approved": True, "note": "approved"}


def _settings(*, max_replans=0, evidence_checkpoint=True):
    settings = Settings()
    settings.research.enabled = True
    settings.research.evidence_checkpoint = evidence_checkpoint
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    settings.orchestrator.max_replans = max_replans
    return settings


def _research_hub(settings, decisions, *, artifact_path=None, fail_steps=False):
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        if task.meta["kind"] != "step" or fail_steps:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="worker exited with code 1")
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        result = _cp2_result(hub, task)
        if artifact_path is not None:
            result["artifact_refs"][0]["path"] = artifact_path
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result, outputs=[path])

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    return hub


# ---------- P1: artifact_refs bind to collected files ----------

@pytest.mark.asyncio
async def test_cp2_refuses_evidence_whose_artifact_was_not_collected():
    hub = _research_hub(_settings(), [CP1, {"approved": True, "choice": "approve", "note": ""}],
                        artifact_path="outputs/missing.tsv")
    await Orchestrator(hub).run_request("r")

    card = hub.approvals[1]["detail"]
    assert [row["evidence_id"] for row in card["refused_evidence"]] == ["e1"]
    assert "outputs/missing.tsv" in card["refused_evidence"][0]["reason"]
    assert card["unsupported_claims"][0]["claim_id"] == "c1"
    receipt = hub.requests["r"]["research_contract"]["checkpoints"]["cp2"]
    assert [row["evidence_id"] for row in receipt["refused_evidence"]] == ["e1"]
    assert [row["claim_id"] for row in receipt["unsupported_claims"]] == ["c1"]
    assert "Refused evidence (not approved at CP2):\n- s1/e1:" in hub.requests["r"]["report"]


@pytest.mark.asyncio
async def test_cp2_accepts_a_normalized_path_to_the_collected_output():
    hub = _research_hub(_settings(), [CP1, {"approved": True, "choice": "approve", "note": ""}],
                        artifact_path="./outputs/../outputs/result1.tsv")
    await Orchestrator(hub).run_request("r")

    assert "refused_evidence" not in hub.approvals[1]["detail"]
    assert hub.requests["r"]["research_contract"]["checkpoints"]["cp2"]["refused_evidence"] == []
    assert hub.requests["r"]["outcome"] == "evidence_approved"


def test_artifact_refs_bind_only_to_own_or_verified_upstream_outputs():
    from labhq.research.contract import bind_result_artifacts

    result = {
        "artifact_refs": [{"artifact_id": "own", "path": "outputs\\a.tsv"},
                          {"artifact_id": "up_id", "path": "wd_up/outputs/b.tsv"},
                          {"artifact_id": "up_abs", "path": "C:\\work\\up\\outputs\\b.tsv"},
                          {"artifact_id": "ghost", "path": "outputs/ghost.tsv"},
                          {"artifact_id": "escape", "path": "outputs/../../etc/passwd"},
                          {"artifact_id": "bare_up", "path": "outputs/b.tsv"}],
        "evidence": [{"id": "e_own", "source": {"artifact_id": "own"}},
                     {"id": "e_up", "source": {"artifact_id": "up_id"}},
                     {"id": "e_abs", "source": {"artifact_id": "up_abs"}},
                     {"id": "e_ghost", "source": {"artifact_id": "ghost"}},
                     {"id": "e_escape", "source": {"artifact_id": "escape"}},
                     {"id": "e_bare", "source": {"artifact_id": "bare_up"}},
                     {"id": "inference", "derived_from": ["e_ghost"]}],
        "claims": [{"id": "kept", "status": "supported"}, {"id": "lost", "status": "supported"}],
        "links": [{"claim_id": "kept", "evidence_id": "e_own", "relation": "supports"},
                  {"claim_id": "kept", "evidence_id": "e_ghost", "relation": "supports"},
                  {"claim_id": "lost", "evidence_id": "e_escape", "relation": "supports"}],
    }
    bound = bind_result_artifacts(result, outputs=["outputs/a.tsv"],
                                  upstream=[("wd_up", "C:\\work\\up", ["outputs/b.tsv"])])

    refused = {row["evidence_id"]: row["reason"] for row in bound["refused_evidence"]}
    # A bare relative path names this step's workspace, where the upstream file is not.
    assert set(refused) == {"e_ghost", "e_escape", "e_bare", "inference"}
    assert refused["inference"] == "derived from refused evidence e_ghost"
    assert [row["claim_id"] for row in bound["unsupported_claims"]] == ["lost"]


# ---------- P1: the research lane never enters generic re-planning ----------

@pytest.mark.asyncio
async def test_research_step_failure_takes_its_own_path_even_with_replans_on():
    hub = _research_hub(_settings(max_replans=2), [CP1], fail_steps=True)
    await Orchestrator(hub).run_request("r")

    assert "replan" not in [task.meta["kind"] for task in hub.calls]
    assert {task.meta["kind"] for task in hub.calls} == {"plan", "step"}
    assert [item["kind"] for item in hub.approvals] == ["research_plan"]
    req = hub.requests["r"]
    assert req["outcome"] == "research_failed" and req["status"] == "failed"
    assert req["research_contract"]["failure"]["steps"] == ["s1"]
    assert "a changed plan needs a new CP1 approval" in req["report"]
    assert "replan_progress" not in req and "replan_history" not in req


@pytest.mark.asyncio
async def test_research_request_resumed_with_the_pilot_off_dispatches_nothing():
    settings = _settings(evidence_checkpoint=False)
    hub = _research_hub(settings, [CP1], fail_steps=True)
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["outcome"] == "plan_approved"

    # The PI turned the pilot off before an interrupted research request resumed, with re-planning on.
    hub.requests["r"].update(status="interrupted", outcome=None)
    hub.requests["r"]["research_contract"]["execution_enabled"] = True
    settings.research.enabled = False
    settings.research.evidence_checkpoint = True
    settings.orchestrator.max_replans = 2
    before = len(hub.calls)
    await Orchestrator(hub).run_request("r", resume=True)

    assert hub.calls[before:] == []
    assert hub.requests["r"]["outcome"] == "research_disabled"
    assert hub.requests["r"]["status"] == "failed"


# ---------- P1: a research step may stop for a PI decision before its ledger is checked ----------

@pytest.mark.asyncio
@pytest.mark.parametrize("schema_shaped", [True, False])
async def test_research_step_question_reaches_the_pi_and_the_step_reruns(schema_shaped):
    settings = _settings()
    holder, seen = {}, []

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        seen.append(task)
        plan_hash = hub.requests["r"]["research_contract"]["plan_sha256"]
        question = "Which donor group: (a) cases or (b) controls?"
        if len(seen) == 1:  # stops for a PI decision: no outputs, no ledger yet
            asked = ({"schema_version": 2, "plan_sha256": plan_hash, "step_id": "s1", "claims": [], "evidence": [],
                      "links": [], "artifact_refs": [], "not_established": [], "failures": [], "method_changes": [],
                      "blocking_decision": question} if schema_shaped else {"blocking_decision": question})
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=asked)
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        result = {**_cp2_result(hub, task), "blocking_decision": ""}
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result, outputs=[path])

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")
    decisions = [CP1, {"approved": True, "note": "a"}, {"approved": True, "choice": "approve", "note": ""}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    assert "blocking_decision" in seen[0].output_schema["properties"]
    assert "blocking_decision" not in seen[0].output_schema["required"]
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "clarify", "research_evidence"]
    assert "Which donor group" in hub.approvals[1]["summary"] and len(seen) == 2
    assert hub.requests["r"]["outcome"] == "evidence_approved"
    ledger = hub.approvals[2]["detail"]["results"]["s1"]
    assert ledger["claims"][0]["id"] == "c1" and "blocking_decision" not in ledger


# ---------- P1: CP2 reads a structured choice, never the note ----------

@pytest.mark.asyncio
async def test_cp2_approval_without_a_choice_is_not_approved_and_is_asked_again():
    hub = _research_hub(_settings(), [CP1, {"approved": True, "note": "Request revision"},
                                      {"approved": False, "choice": "revise", "note": "add a sensitivity check"}])
    await Orchestrator(hub).run_request("r")

    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_evidence", "research_evidence"]
    assert hub.approvals[2]["summary"].startswith("The previous CP2 answer had no readable")
    receipt = hub.requests["r"]["research_contract"]["checkpoints"]["cp2"]
    assert receipt["decision"] == "revision_requested" and receipt["asks"] == 2 and receipt["choice"] == "revise"
    assert hub.requests["r"]["outcome"] == "evidence_revision_requested"
    assert hub.requests["r"]["status"] == "failed"


@pytest.mark.asyncio
async def test_cp2_that_never_gets_a_readable_choice_ends_not_approved():
    unreadable = {"approved": True, "note": "Q1. a) Approve evidence"}
    hub = _research_hub(_settings(), [CP1, unreadable, dict(unreadable), dict(unreadable)])
    await Orchestrator(hub).run_request("r")

    assert [item["kind"] for item in hub.approvals].count("research_evidence") == 3
    assert hub.requests["r"]["outcome"] == "evidence_unreadable"
    assert hub.requests["r"]["status"] == "failed"
    assert hub.requests["r"]["research_contract"]["checkpoints"]["cp2"]["decision"] == "unreadable"


@pytest.mark.parametrize(("decision", "read"), [
    ({"approved": True, "choice": "approve"}, "approved"),
    ({"approved": False, "choice": "revise"}, "revision_requested"),
    ({"approved": False, "choice": "deny"}, "rejected"),
    ({"approved": False, "note": "stop"}, "rejected"),
    ({"approved": False, "note": "timed out", "state": "timed_out"}, "rejected"),
    ({"approved": True, "note": "Request revision"}, None),
    ({"approved": True, "choice": "revise"}, None),
    ({"approved": False, "choice": "approve"}, None),
    ({"approved": True, "choice": "maybe"}, None),
    ({"approved": True, "choice": ["approve"]}, None),
])
def test_cp2_decision_is_read_from_the_choice_alone(decision, read):
    from labhq.research.contract import read_evidence_decision

    assert read_evidence_decision(decision) == read


def test_cli_sends_the_cp2_choice_and_refuses_a_bare_approve(monkeypatch):
    from labhq import cli

    pending = [{"id": "a_cp2", "kind": "research_evidence", "summary": "CP2"},
               {"id": "a_tool", "kind": "tool_permission", "summary": "Bash"}]
    posts = []

    def api(_settings, method, path, **kwargs):
        if method == "GET":
            return pending
        posts.append((path, kwargs["json"]))
        return {"ok": True}

    monkeypatch.setattr("labhq.cli._api", api)
    cli.main(["approve", "a_cp2", "--choice", "revise", "--note", "add a sensitivity check"])
    cli.main(["approve", "a_cp2", "--choice", "approve"])
    cli.main(["approve", "a_tool"])
    assert posts == [("/api/approvals/a_cp2", {"approved": False, "note": "add a sensitivity check",
                                               "choice": "revise"}),
                     ("/api/approvals/a_cp2", {"approved": True, "note": "", "choice": "approve"}),
                     ("/api/approvals/a_tool", {"approved": True, "note": ""})]
    for argv in (["approve", "a_cp2", "--note", "Request revision"],  # the bot's case: refused, nothing sent
                 ["approve", "a_tool", "--choice", "revise"],
                 ["approve", "a_cp2", "--deny", "--choice", "approve"]):
        with pytest.raises(SystemExit):
            cli.main(argv)
    assert len(posts) == 3


@pytest.mark.asyncio
async def test_gateway_hands_the_structured_choice_to_the_waiting_gate(tmp_path):
    from labhq.gateway.server import DecisionIn, Hub

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    waiting = asyncio.create_task(hub.request_approval("research_evidence", "CP2", "r1", timeout_s=5))
    while not hub.approvals:
        await asyncio.sleep(0)
    aid = next(iter(hub.approvals))
    await hub.resolve_approval(aid, False, "memo", "revise")
    decision = await waiting

    assert decision["choice"] == "revise" and decision["approved"] is False
    assert hub.store.get("approval_decision", aid)["choice"] == "revise"
    assert DecisionIn(approved=True).choice is None
    with pytest.raises(ValidationError):
        DecisionIn(approved=True, choice="maybe")


# ---------- P2: the plan prompt states the contract the steps run under ----------

@pytest.mark.asyncio
@pytest.mark.parametrize("on", [True, False])
async def test_research_plan_prompt_matches_the_evidence_checkpoint(on):
    hub = _research_hub(_settings(evidence_checkpoint=on), [{"approved": False, "note": "not now"}])
    await Orchestrator(hub).run_request("r")

    prompt = hub.calls[0].prompt
    cp1_only = "PR 1 pilot stops after CP1 approval. Research steps will not run in this PR."
    if on:
        assert cp1_only not in prompt and "Research steps will not run" not in prompt
        assert "stops at CP2" in prompt and "artifact_refs path must be one of that step's declared outputs" in prompt
    else:
        assert cp1_only in prompt and "CP2" not in prompt
