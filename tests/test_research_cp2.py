"""CP2 evidence review (#90): artifact binding, result correction, failure, and structured decisions."""

import asyncio
import copy

import pytest
from pydantic import ValidationError

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.research.contract import plan_sha256, research_result_errors, salvage_research_result
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


def _invalid_result(hub, task):
    result = _cp2_result(hub, task)
    del result["evidence"][0]["assessment_reason"]
    return result


def _standalone_result():
    plan = valid_plan()
    frozen = plan_sha256(plan)
    hub = type("Hub", (), {"requests": {"r": {"plan": plan,
        "research_contract": {"plan_sha256": frozen}}}})()
    task = type("Task", (), {"meta": {"step_id": "s1"}})()
    return plan, _cp2_result(hub, task)


def _extra_observation(row_id="bad"):
    return {"id": row_id, "kind": "observation", "observation": "cross-check",
            "status": "observed",
            "source": {"uri": "https://example.org/record", "accessed_at": "2026-10-03",
                       "locator": "table 1"},
            "directness": "indirect", "source_level": "primary", "independence_group": "crosscheck",
            "assessment_reason": "an independent cross-check", "slots": []}


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
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result, outputs=[path],
                          output_sha256={path: "a" * 64}, unreported_outputs=["outputs/extra.tsv"])

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


@pytest.mark.asyncio
async def test_cp2_records_bound_artifact_hash_and_unreported_outputs():
    hub = _research_hub(_settings(), [CP1, {"approved": True, "choice": "approve", "note": ""}])
    await Orchestrator(hub).run_request("r")

    detail = hub.approvals[1]["detail"]
    expected = {"s1/a1": "a" * 64}
    assert detail["artifact_sha256"] == expected
    assert detail["unreported_outputs"] == {"s1": ["outputs/extra.tsv"]}
    receipt = hub.requests["r"]["research_contract"]["checkpoints"]["cp2"]
    assert receipt["artifact_sha256"] == expected


@pytest.mark.asyncio
async def test_cp2_binds_an_ancestors_output_but_not_an_unrelated_steps():
    """8th mock trial: the interpretation step depends only on the QC step, yet reads the analyses QC checked.
    Six of eight refusals cited a grandparent's collected, hashed file. Any ancestor binds; a step outside the
    chain still does not."""
    plan = valid_plan(steps=4)
    plan["steps"][1]["depends_on"] = ["s1"]
    plan["steps"][2]["depends_on"] = ["s2"]  # s3 -> s2 -> s1; s4 stands alone
    cites = {"s1": "outputs/result1.tsv", "s2": "outputs/result2.tsv", "s3": "w1/outputs/result1.tsv",
             "s4": "w1/outputs/result1.tsv"}
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=copy.deepcopy(plan))
        sid = task.meta["step_id"]
        n = sid[1:]
        result = _cp2_result(hub, task)
        result["claims"][0]["id"] = result["links"][0]["claim_id"] = f"c{n}"
        result["evidence"][0]["slots"] = [f"e{n}"]
        result["artifact_refs"][0]["path"] = cites[sid]
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result,
                          outputs=[f"outputs/result{n}.tsv"], output_sha256={f"outputs/result{n}.tsv": n * 64},
                          workdir=f"runs/w{n}", workdir_id=f"w{n}")

    hub = holder["hub"] = MiniHub(_settings(), reply, mode="orchestrate", work_kind="research",
                                    text="compare conditions")
    decisions = [CP1, {"approved": True, "choice": "approve", "note": ""}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    card = hub.approvals[1]["detail"]
    assert [(row["step_id"], row["evidence_id"]) for row in card["refused_evidence"]] == [("s4", "e1")]
    assert card["artifact_sha256"]["s3/a1"] == "1" * 64  # bound to s1's hash, read through s2
    assert "s4/a1" not in card["artifact_sha256"]


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
    bound = bind_result_artifacts(result, outputs=["outputs/a.tsv"], output_sha256={"outputs/a.tsv": "a" * 64},
                                  upstream=[("wd_up", "C:\\work\\up", ["outputs/b.tsv"],
                                             {"outputs/b.tsv": "b" * 64})])

    refused = {row["evidence_id"]: row["reason"] for row in bound["refused_evidence"]}
    # A bare relative path names this step's workspace, where the upstream file is not.
    assert set(refused) == {"e_ghost", "e_escape", "e_bare", "inference"}
    assert refused["inference"] == "derived from refused evidence e_ghost"
    assert [row["claim_id"] for row in bound["unsupported_claims"]] == ["lost"]
    assert bound["artifact_sha256"] == {"own": "a" * 64, "up_id": "b" * 64, "up_abs": "b" * 64}


# ---------- Result contract correction ----------

@pytest.mark.parametrize("broken", [
    "accessed_at", "assessment_reason", "status", "source", "conditions", "denominator", "unknown",
    "independence_group",
])
def test_field_level_contract_defects_refuse_only_the_bad_evidence_row(broken):
    plan, result = _standalone_result()
    row = _extra_observation()
    if broken == "accessed_at":
        row["source"]["accessed_at"] = "2026/10/03"
    elif broken in {"assessment_reason", "status", "source", "independence_group"}:
        row.pop(broken)
        if broken == "independence_group":
            row["kind"] = "literature_claim"
    else:
        quantity = {"id": "q_bad", "measure": "effect", "value": 1.2, "unit": "fold",
                    "conditions": ["case vs control"], "denominator": "6 donors", "unknown": {}}
        if broken == "unknown":
            quantity.pop("value")
        else:
            quantity.pop(broken)
        row["quantities"] = [quantity]
    result["evidence"].append(row)

    salvaged, refused, unsupported, problems = salvage_research_result(result, plan=plan, expected_step_id="s1")

    assert problems == [] and salvaged is not None
    assert [item.id for item in salvaged.evidence] == ["e1"]
    assert [(item["row_type"], item["row_id"]) for item in refused] == [("evidence", "bad")]
    assert unsupported == []


def test_refusing_evidence_also_refuses_derived_rows_and_their_links():
    plan, result = _standalone_result()
    bad = _extra_observation("bad_parent")
    bad["source"]["accessed_at"] = "not-a-date"
    result["evidence"] += [bad, {"id": "derived", "kind": "inference", "observation": "interpretation",
                                  "derived_from": ["bad_parent"], "slots": []}]
    result["links"].append({"claim_id": "c1", "claim_revision": 1, "evidence_id": "derived",
                            "relation": "context", "rationale": "interpretation only"})

    salvaged, refused, unsupported, problems = salvage_research_result(result, plan=plan, expected_step_id="s1")

    assert problems == [] and salvaged is not None and unsupported == []
    assert [item.id for item in salvaged.evidence] == ["e1"]
    refused_ids = {(row["row_type"], row["row_id"]) for row in refused}
    assert ("evidence", "bad_parent") in refused_ids and ("evidence", "derived") in refused_ids
    assert any(row_type == "link" and "->derived:" in row_id for row_type, row_id in refused_ids)


@pytest.mark.parametrize("change", ["json", "schema_version", "plan_sha256", "step_id", "required_slot"])
def test_structural_result_defects_are_not_salvaged(change):
    plan, result = _standalone_result()
    value = copy.deepcopy(result)
    if change == "json":
        value = "not json"
    elif change == "schema_version":
        value["schema_version"] = 1
    elif change == "plan_sha256":
        value["plan_sha256"] = "0" * 64
    elif change == "step_id":
        value["step_id"] = "s9"
    else:
        value["evidence"][0]["slots"] = []

    salvaged, refused, unsupported, problems = salvage_research_result(value, plan=plan, expected_step_id="s1")

    assert salvaged is None and refused == [] and unsupported == [] and problems


@pytest.mark.asyncio
async def test_two_failed_corrections_salvage_inference_support_into_cp2_and_report_metadata():
    settings = _settings()
    assert settings.research.result_corrections == 2
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        result = _cp2_result(hub, task)
        result["links"][0]["relation"] = "context"
        result["evidence"].append({
            "id": "E_pairing_crosscheck", "kind": "inference", "observation": "paired donors are comparable",
            "derived_from": ["e1"], "slots": [],
        })
        result["links"].append({
            "claim_id": "c1", "claim_revision": 1, "evidence_id": "E_pairing_crosscheck",
            "relation": "supports", "rationale": "the inferred pairing supports the claim",
        })
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result,
                          outputs=[path] if task.meta["kind"] == "step" else [], workdir="runs/s1",
                          session_id="session-1")

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                                    text="compare conditions")
    decisions = [CP1, {"approved": True, "choice": "approve", "note": ""}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    assert [task.meta["kind"] for task in hub.calls] == [
        "plan", "step", "result_correction", "result_correction"]
    assert hub.requests["r"]["outcome"] == "evidence_approved"
    saved = hub.requests["r"]["results"]["s1"]["structured"]
    assert [row["id"] for row in saved["evidence"]] == ["e1"]
    assert saved["claims"] == [] and saved["links"] == []
    card = hub.approvals[1]["detail"]
    assert any(row["row_id"] == "E_pairing_crosscheck" for row in card["refused_rows"])
    assert [row["claim_id"] for row in card["unsupported_claims"]] == ["c1"]
    receipt = hub.requests["r"]["research_contract"]["checkpoints"]["cp2"]
    assert receipt["refused_rows"] == card["refused_rows"]
    assert "계약에 맞지 않아 뺀 근거" in hub.requests["r"]["report"]


def test_a_missing_slot_is_reported_with_the_field_errors_and_names_the_fix():
    """9th mock trial: slot binding was checked only after the fields parsed, so the missing slots surfaced after
    the last correction. The rows were named after their slots with no "slots" field."""
    plan, result = _standalone_result()
    del result["evidence"][0]["assessment_reason"]
    del result["evidence"][0]["slots"]
    problems = research_result_errors(result, plan=plan)
    assert any("assessment_reason" in problem for problem in problems)
    assert any(problem.startswith("required evidence slot e1 of step s1 has no evidence row")
               and 'add "slots": ["e1"]' in problem for problem in problems)


@pytest.mark.parametrize("field, value", [("claims", 1), ("evidence", True), ("evidence", [1, "x"]),
                                          ("claims", None)])
def test_a_scalar_ledger_is_a_correction_not_a_crash(field, value):
    """PR #360 review: reading binding from raw JSON must not raise on a malformed ledger."""
    plan, result = _standalone_result()
    result[field] = value
    assert research_result_errors(result, plan=plan)


@pytest.mark.asyncio
async def test_the_first_correction_asks_for_the_slot_with_the_field_error():
    settings = _settings()
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        result = _cp2_result(hub, task)
        if task.meta["kind"] == "step":
            del result["evidence"][0]["assessment_reason"]
            del result["evidence"][0]["slots"]
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result,
                          outputs=[path] if task.meta["kind"] == "step" else [], workdir="runs/s1")

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                                    text="compare conditions")
    decisions = [CP1, {"approved": True, "choice": "approve", "note": ""}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    assert [task.meta["kind"] for task in hub.calls] == ["plan", "step", "result_correction"]
    prompt = hub.calls[2].prompt
    assert "assessment_reason" in prompt and "required evidence slot e1" in prompt
    assert hub.requests["r"]["outcome"] == "evidence_approved"


def test_normal_result_needs_no_salvage():
    plan, result = _standalone_result()
    salvaged, refused, unsupported, problems = salvage_research_result(result, plan=plan, expected_step_id="s1")
    assert salvaged is not None and salvaged.step_id == "s1"
    assert [row.id for row in salvaged.evidence] == ["e1"] and [claim.id for claim in salvaged.claims] == ["c1"]
    assert refused == [] and unsupported == [] and problems == []

@pytest.mark.asyncio
async def test_invalid_result_is_corrected_once_and_the_request_continues():
    settings = _settings()
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        result = (_invalid_result(hub, task) if task.meta["kind"] == "step" else _cp2_result(hub, task))
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result,
                          outputs=[path] if task.meta["kind"] == "step" else [], workdir="runs/s1",
                          session_id="session-1", cost_usd=1.0)

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                                    text="compare conditions")
    decisions = [CP1, {"approved": True, "choice": "approve", "note": ""}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    orchestrator = Orchestrator(hub)
    await orchestrator.run_request("r")

    assert [task.meta["kind"] for task in hub.calls] == ["plan", "step", "result_correction"]
    correction = hub.calls[2]
    assert correction.meta["step_id"] == "s1" and correction.meta["workdir"] == "runs/s1"
    assert correction.meta["outputs"] == [] and correction.meta["parse_attempt"] == 1
    assert "assessment_reason" in correction.prompt
    assert "Do not recreate or modify output files" in correction.prompt
    assert hub.requests["r"]["outcome"] == "evidence_approved"
    assert hub.requests["r"]["results"]["s1"]["structured"]["evidence"][0]["assessment_reason"]
    assert orchestrator.attempts["r"]["s1"] == 2
    assert orchestrator.cost["r"] == 2.0  # the original and correction turns both stay on the same step


@pytest.mark.asyncio
@pytest.mark.parametrize("corrections, expected_kinds", [
    (1, ["plan", "step", "result_correction"]),
    (0, ["plan", "step"]),
])
async def test_invalid_correction_or_zero_limit_keeps_the_existing_failure(corrections, expected_kinds):
    settings = _settings()
    settings.research.result_corrections = corrections
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured=_invalid_result(hub, task), outputs=[path], workdir="runs/s1")

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                                    text="compare conditions")

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**CP1, "approval_id": "a1", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    assert [task.meta["kind"] for task in hub.calls] == expected_kinds
    result = hub.requests["r"]["results"]["s1"]
    assert result["error"].startswith("invalid research result contract: ")
    assert "assessment_reason" in result["error"]
    assert hub.requests["r"]["outcome"] == "research_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_turns, kinds, outcome", [
    (1, ["plan", "step", "step"], "evidence_approved"),
    (0, ["plan", "step", "wrap_up"], "research_failed"),
])
async def test_a_research_step_past_its_turn_limit_finishes_in_its_session(finish_turns, kinds, outcome):
    """7th mock trial (2026-10-03): QC ran out of its 40 turns after reproducing every number, and the frozen plan
    failed. The step now finishes once in the same session; with the setting at 0 it fails as before."""
    settings = _settings()
    settings.research.finish_turns = finish_turns
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        if task.meta["kind"] == "wrap_up":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="saved", workdir="runs/s1",
                              outputs=["outputs/PARTIAL_STATUS.md"])
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        if not task.resume_session_id:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="error_max_turns",
                              error_kind="error_max_turns", session_id="session-1", workdir="runs/s1",
                              outputs=[path], output_sha256={path: "a" * 64},
                              unreported_outputs=["outputs/scratch.tsv"])
        # The runner rehashes every declared output after each turn: the finish turn rewrote this one.
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=_cp2_result(hub, task),
                          outputs=[path], output_sha256={path: "b" * 64}, workdir="runs/s1", session_id="session-1")

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                                    text="compare conditions")
    hub.supports_resume = lambda agent_id: True
    decisions = [CP1, {"approved": True, "choice": "approve", "note": ""}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    assert [task.meta["kind"] for task in hub.calls] == kinds
    assert hub.calls[1].meta["finish_turns"] == finish_turns
    assert hub.requests["r"]["outcome"] == outcome
    if finish_turns:
        assert hub.calls[2].resume_session_id == "session-1"
        receipt = hub.requests["r"]["research_contract"]["checkpoints"]["cp2"]
        assert receipt["refused_evidence"] == []
        assert receipt["artifact_sha256"] == {"s1/a1": "b" * 64}  # the file as it is now, not the first turn's
        assert hub.approvals[1]["detail"]["unreported_outputs"] == {"s1": ["outputs/scratch.tsv"]}


@pytest.mark.asyncio
async def test_research_step_prompt_lists_validator_only_field_rules():
    hub = _research_hub(_settings(), [CP1, {"approved": True, "choice": "approve", "note": ""}])
    await Orchestrator(hub).run_request("r")

    prompt = next(task.prompt for task in hub.calls if task.meta["kind"] == "step")
    for text in ("YYYY-MM-DD", "observation, literature_claim, database_annotation, experimental",
                 "directness, source_level, independence_group, assessment_reason",
                 "observed requires source.locator", "not_found requires source.query",
                 "failed/unavailable requires status_detail", "inference/hypothesis",
                 "value, unit, conditions, denominator", "unknown"):
        assert text in prompt


@pytest.mark.asyncio
async def test_research_step_prompt_carries_the_frozen_protocol():
    """8th mock trial: the DE step was told "after the low-expression filter" and invented a filter that dropped
    two positive controls, because the frozen exclusion criteria never reached it. The reviewer had to catch it."""
    hub = _research_hub(_settings(), [CP1, {"approved": True, "choice": "approve", "note": ""}])
    await Orchestrator(hub).run_request("r")

    prompt = next(task.prompt for task in hub.calls if task.meta["kind"] == "step")
    plan = hub.requests["r"]["plan"]
    for text in (plan["brief"]["question"], *plan["protocol"]["exclusion_criteria"],
                 plan["protocol"]["statistics"]["multiple_testing"], "method_changes",
                 "never describe the result as following the pre-specified rule"):
        assert text in prompt


def test_no_prompt_clips_the_frozen_protocol():
    """Real plans run past 20,000 characters (8th mock trial) and protocol fields have no length limit, so clipping
    a digest could drop the exclusion criteria in its middle (PR #358 review). Only the step list is clipped."""
    from labhq.orchestrator.cso import _research_plan_digest, _research_protocol_digest

    plan = valid_plan(steps=12)
    plan["protocol"]["selection_criteria"] = [f"selection rule {i}: " + "x" * 400 for i in range(20)]
    plan["protocol"]["exclusion_criteria"] = ["EXCLUDE genes below the 20th percentile in 80% of samples"]
    plan["protocol"]["primary_metrics"] = ["log fold change " + "y" * 4000]
    for step in plan["steps"]:
        step["instruction"] = "long instruction " + "z" * 2000
    for digest in (_research_protocol_digest(plan), _research_plan_digest(plan)):
        assert "EXCLUDE genes below the 20th percentile in 80% of samples" in digest
        assert plan["protocol"]["statistics"]["multiple_testing"] in digest
        assert "selection rule 19" in digest
    assert "chars clipped" in _research_plan_digest(plan)  # the step list still has a budget


@pytest.mark.asyncio
async def test_restart_recovers_the_step_and_its_correction_without_dispatching_them_twice(tmp_path):
    from labhq.gateway.server import Hub, SavedResults
    from labhq.models import Task

    settings = _settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(settings)
    hub.agents["worker"] = {"id": "worker", "name": "worker", "role": "test", "engine": "mock"}
    plan = valid_plan()
    plan["steps"][0]["outputs"] = ["outputs/result1.tsv"]
    frozen = plan_sha256(plan)
    hub.requests["r"] = {
        "id": "r", "text": "compare conditions", "mode": "orchestrate", "status": "interrupted",
        "plan": plan, "research_contract": {"execution_enabled": True, "plan_sha256": frozen}, "results": {},
    }
    hub.save_request("r")
    step = plan["steps"][0]
    initial_task = Task(id="initial-task", agent_id="worker", request_id="r", prompt="run",
                        meta={"kind": "step", "step_id": "s1", "revision": 0})
    initial = TaskResult(task_id=initial_task.id, agent_id="worker", ok=True,
                         structured=_invalid_result(hub, initial_task), outputs=["outputs/result1.tsv"],
                         workdir="runs/s1")
    correction_task = Task(id="correction-task", agent_id="worker", request_id="r", prompt="fix",
                           meta={"kind": "result_correction", "step_id": "s1", "revision": 0,
                                 "parse_attempt": 1, "parent_task": initial.task_id, "workdir": "runs/s1"})
    corrected = TaskResult(task_id=correction_task.id, agent_id="worker", ok=True,
                           structured=_cp2_result(hub, correction_task), workdir="runs/s1")
    for task, result in ((initial_task, initial), (correction_task, corrected)):
        hub.store.put("task", task.id, {
            "request_id": "r", "step_id": "s1", "kind": task.meta["kind"], "attempt": 1,
            "revision": 0, "parse_attempt": task.meta.get("parse_attempt", 0),
            "parent_task": task.meta.get("parent_task"), "payload": task.model_dump(mode="json"),
            "accepted": True, "completed": True, "dispatched_at": 1.0,
            "result": result.model_dump(mode="json"),
        })
    hub.recovery_steps.add("r")
    results = SavedResults(hub, "r")

    await hub.orchestrator.run_dag("r", "compare conditions", [step], results, only={"s1"})

    assert hub.recovered_tasks == {"initial-task", "correction-task"}
    assert results["s1"].ok and results["s1"].structured["evidence"][0]["assessment_reason"]
    assert not any(event["type"] == "task.dispatched" for event in hub.events)


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


# ---------- P1: a resume keeps the contract the request was frozen with ----------

def _interrupt(hub):
    """A gateway restart: the request is resumed from its saved results (SavedResults in the real hub)."""
    hub.requests["r"].update(status="interrupted")
    hub.result_map = lambda rid: {sid: TaskResult.model_validate(value)
                                  for sid, value in (hub.requests[rid].get("results") or {}).items()}


@pytest.mark.asyncio
async def test_resume_after_the_cp2_receipt_keeps_the_pi_decision():
    decisions = [CP1, {"approved": False, "choice": "revise", "note": "add a sensitivity check"}]
    hub = _research_hub(_settings(), decisions)
    await Orchestrator(hub).run_request("r")
    receipt = dict(hub.requests["r"]["research_contract"]["checkpoints"]["cp2"])

    _interrupt(hub)  # restarted after the receipt was saved, before the terminal commit
    calls, approvals = len(hub.calls), len(hub.approvals)
    await Orchestrator(hub).run_request("r", resume=True)

    assert hub.calls[calls:] == [] and hub.approvals[approvals:] == []
    assert hub.requests["r"]["research_contract"]["checkpoints"]["cp2"] == receipt
    assert hub.requests["r"]["outcome"] == "evidence_revision_requested"
    assert "PI note: add a sensitivity check" in hub.requests["r"]["report"]


@pytest.mark.asyncio
async def test_resume_keeps_execution_on_after_the_evidence_checkpoint_is_switched_off():
    settings = _settings()
    approve = {"approved": True, "choice": "approve", "note": ""}
    decisions = [CP1, approve]
    hub = _research_hub(settings, decisions)
    await Orchestrator(hub).run_request("r")
    hub.requests["r"]["research_contract"].pop("checkpoints")  # interrupted after the step, before CP2's answer
    _interrupt(hub)
    settings.research.evidence_checkpoint = False  # a config change must not reach a request already frozen
    decisions.append(dict(approve))
    calls, approvals = len(hub.calls), len(hub.approvals)
    await Orchestrator(hub).run_request("r", resume=True)

    assert hub.calls[calls:] == []  # the collected result is reused, not re-run or dropped
    assert [item["kind"] for item in hub.approvals[approvals:]] == ["research_evidence"]
    assert hub.requests["r"]["research_contract"]["execution_enabled"] is True
    assert hub.requests["r"]["outcome"] == "evidence_approved"
    assert set(hub.requests["r"]["results"]) == {"s1"}


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


@pytest.mark.asyncio
async def test_a_correction_that_changes_an_output_file_fails_the_step():
    """A correction may only rewrite the result JSON; a changed file would no longer match the hash CP2 binds
    evidence to (PR #352 review)."""
    settings = _settings()
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        if task.meta["kind"] == "step":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=_invalid_result(hub, task),
                              outputs=[path], output_sha256={path: "a" * 64}, workdir="runs/s1",
                              session_id="session-1")
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=_cp2_result(hub, task),
                          unreported_outputs=[path], workdir="runs/s1", session_id="session-1")

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                                    text="compare conditions")

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**CP1, "approval_id": "a1", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    result = hub.requests["r"]["results"]["s1"]
    assert [task.meta["kind"] for task in hub.calls] == ["plan", "step", "result_correction"]
    assert "the result correction changed output files" in result["error"]
    assert hub.requests["r"]["outcome"] == "research_failed"


def test_a_duplicate_link_refuses_only_the_extra_link_not_the_evidence():
    """`evidence e1 is linked to claim c1 more than once` must not match the generic evidence pattern first and
    drop the valid row with all its links (PR #353 review)."""
    plan, result = _standalone_result()
    result["links"].append(dict(result["links"][0]))

    salvaged, refused, unsupported, problems = salvage_research_result(result, plan=plan, expected_step_id="s1")

    assert problems == [] and salvaged is not None and unsupported == []
    assert [item.id for item in salvaged.evidence] == ["e1"] and len(salvaged.links) == 1
    assert [row["row_type"] for row in refused] == ["link"]


@pytest.mark.asyncio
async def test_a_correction_that_still_asks_the_pi_is_not_salvaged():
    """A last correction that asks a blocking question is never salvaged into CP2 (PR #353 review)."""
    settings = _settings()
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        result = _cp2_result(hub, task)
        result["evidence"].append(_extra_observation("bad"))
        result["evidence"][-1]["source"]["accessed_at"] = "not-a-date"
        if task.meta["kind"] == "result_correction":
            result["blocking_decision"] = "Which cohort should I use?"
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result,
                          outputs=[path] if task.meta["kind"] == "step" else [], workdir="runs/s1",
                          session_id="session-1")

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                                    text="compare conditions")

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**CP1, "approval_id": "a1", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["outcome"] == "research_failed"
    assert "cannot ask a new blocking_decision" in hub.requests["r"]["results"]["s1"]["error"]
    assert len(hub.approvals) == 1  # CP1 only: nothing reached CP2
