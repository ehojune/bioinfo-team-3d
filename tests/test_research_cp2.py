"""CP2 evidence review (#90): artifact binding, result correction, failure, and structured decisions."""

import asyncio
import copy
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from labhq.models import TaskResult
from labhq.bioinfo_policy import BIOINFO_ASSIGNMENT_RULE
from labhq.orchestrator import cso
from labhq.orchestrator.cso import Orchestrator
from labhq.evidence.verify import SourceRecord, SourceUpdate, StaticResolver
from labhq.research import analysis_draft
from labhq.research.contract import plan_sha256, research_result_errors, salvage_research_result
from labhq.settings import Settings
from tests.test_research_protocol import MiniHub, _cp2_result, valid_plan

pytestmark = pytest.mark.usefixtures("pre_v1_defaults")  # these tests hire or report; v1.0 turns both off (#1336)

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


def _research_hub(settings, decisions, *, plan=None, artifact_path=None, fail_steps=False, unreported_outputs=None):
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured=plan if plan is not None else valid_plan())
        if task.meta["kind"] != "step" or fail_steps:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="worker exited with code 1")
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        result = _cp2_result(hub, task)
        if artifact_path is not None:
            result["artifact_refs"][0]["path"] = artifact_path
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result, outputs=[path],
                          output_sha256={path: "a" * 64},
                          unreported_outputs=(unreported_outputs if unreported_outputs is not None
                                              else ["outputs/extra.tsv"]))

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    return hub


@pytest.mark.asyncio
async def test_live_source_check_marks_retracted_claim_in_card_and_appendix(monkeypatch):
    settings = _settings()
    settings.research.live_source_check = True
    hub = _research_hub(settings, [CP1, {"approved": True, "choice": "approve", "note": ""}])
    original_reply = hub.reply

    async def reply_with_doi(task):
        response = await original_reply(task)
        if task.meta["kind"] == "step" and response.ok:
            response.structured["evidence"][0]["source"].update(
                {"id_scheme": "doi", "id_value": "10.1021/am300292v", "accessed_at": "2026-10-08"})
        return response

    hub.reply = reply_with_doi
    fixed = StaticResolver({("doi", "10.1021/am300292v"): [SourceRecord(
        id_scheme="doi", id_value="10.1021/am300292v",
        updates=[SourceUpdate(direction="updated_by", update_type="retraction",
                              related={"id_scheme": "doi", "id_value": "10.1021/acsami.9b11759"})])]})
    monkeypatch.setattr("labhq.orchestrator.cso.LiveSourceResolver", lambda **_kwargs: fixed)

    await Orchestrator(hub).run_request("r")

    assert len(hub.approvals) == 2, {key: hub.requests["r"].get(key)
                                     for key in ("outcome", "error", "report", "execution_record")}
    card = hub.approvals[1]["detail"]
    assert card["source_verification"] == [
        "s1/e1: doi:10.1021/am300292v found; defect=retracted_by doi:10.1021/acsami.9b11759",
        "s1/e1: artifact:a1 requires_verification"]
    assert any(row["claim_id"] == "c1" and "retracted_by" in row["reason"]
               for row in card["unsupported_claims"])
    appendix = hub.requests["r"]["execution_record"]
    assert "Live source verification:" in appendix and "defect=retracted_by" in appendix


@pytest.mark.asyncio
async def test_live_source_check_off_does_not_call_resolver(monkeypatch):
    hub = _research_hub(_settings(), [CP1, {"approved": True, "choice": "approve", "note": ""}])

    def forbidden(**_kwargs):
        raise AssertionError("live resolver must stay off")

    monkeypatch.setattr("labhq.orchestrator.cso.LiveSourceResolver", forbidden)
    await Orchestrator(hub).run_request("r")
    assert "source_verification" not in hub.approvals[1]["detail"]


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("phase", ["question", "plan", "evidence", "analysis", "validation", "report"])
async def test_cp2_analysis_instructions_use_saved_compact_workflow(compact, phase):
    settings = _settings()
    settings.research.compact_workflow = not compact
    plan = valid_plan()
    plan["steps"][0]["phase"] = phase
    hub = _research_hub(settings, [CP1, {"approved": False, "choice": "deny", "note": ""}], plan=plan)
    hub.requests["r"]["research_options"] = {"compact_workflow": compact}

    await Orchestrator(hub).run_request("r")

    task = next(task for task in hub.calls if task.meta["kind"] == "step")
    prompt = task.prompt
    for instruction in ("cross-check sample information against public records",
                        "record the controls and expected direction",
                        "compare results with published values in a table",
                        "write and read back the declared evidence outputs",
                        "set source.locator to its exact row and column"):
        assert (instruction in prompt) is compact
    assert ("save a draft for the PI" in prompt) is compact
    drafts = [path for path in task.meta["optional_outputs"] if path.startswith("outputs/labhq-analysis-draft-")]
    assert bool(drafts) is compact
    req = hub.requests["r"]
    assert req["research_contract"]["plan_sha256"] == plan_sha256(req["plan"])
    assert not set(drafts).intersection(req["plan"]["steps"][0].get("optional_outputs", []))
    assert [card["kind"] for card in hub.approvals] == ["research_plan", "research_evidence"]


@pytest.mark.asyncio
async def test_live_recitation_refuses_duplicate_evidence_and_its_claim(monkeypatch):
    settings = _settings()
    settings.research.live_source_check = True
    hub = _research_hub(settings, [CP1, {"approved": True, "choice": "approve", "note": ""}])
    original_reply = hub.reply
    doi, pmid = "10.1145/3065386", "28937623"

    async def reply_with_two_ids(task):
        response = await original_reply(task)
        if task.meta["kind"] == "step" and response.ok:
            first = response.structured["evidence"][0]
            first.update(kind="literature_claim", source={"id_scheme": "doi", "id_value": doi,
                         "accessed_at": "2026-10-08", "locator": "abstract"},
                         independence_group="paper_a")
            second = copy.deepcopy(first)
            second.update(id="e2", source={"id_scheme": "pmid", "id_value": pmid,
                          "accessed_at": "2026-10-08", "locator": "abstract"},
                          independence_group="paper_b", slots=[])
            response.structured["evidence"].append(second)
            response.structured["links"].append({
                "claim_id": "c1", "claim_revision": 1, "evidence_id": "e2", "relation": "supports",
                "rationale": "the second identifier names the cited paper"})
        return response

    hub.reply = reply_with_two_ids
    fixed = StaticResolver({
        ("doi", doi): [SourceRecord(id_scheme="doi", id_value=doi,
                                    same_as=[{"id_scheme": "pmid", "id_value": pmid}])],
        ("pmid", pmid): [SourceRecord(id_scheme="pmid", id_value=pmid,
                                      same_as=[{"id_scheme": "doi", "id_value": doi}])],
    })
    monkeypatch.setattr("labhq.orchestrator.cso.LiveSourceResolver", lambda **_kwargs: fixed)

    await Orchestrator(hub).run_request("r")

    card = hub.approvals[1]["detail"]
    assert any(row["evidence_id"] == "e2" and "re-citation" in row["reason"]
               for row in card["refused_evidence"])
    assert any(row["claim_id"] == "c1" and "re-citation" in row["reason"]
               for row in card["unsupported_claims"])
    assert any("recitation=" in line for line in card["source_verification"])


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
    # R18/R10: the card summary is Korean and says that 수정 요청 re-plans through a new CP1.
    summary = hub.approvals[1]["summary"]
    # The card kind is the first line; semantics turned off in the settings adds no stop warning (#1336).
    assert summary.startswith("CP2 근거 검토:") and "의미 장치 중단" not in summary
    assert ("CP2 근거 검토: 단계 1개, claim 1개, 근거 1행, 거부 1건. 증거 승인·수정 요청·거부 중 "
            "고르세요. 수정 요청은 메모에 적은 고칠 점으로 CSO가 새 계획을 세워 새 CP1을 "
            "받습니다(바뀐 단계만 다시 실행).") in summary
    assert "semantic_report" not in hub.approvals[1]["detail"]
    assert "semantic_report.md" not in hub.requests["r"]["execution_record"]
    assert hub.approvals[1]["detail"]["revise_continues"] is True


@pytest.mark.asyncio
async def test_cp2_keeps_frozen_checklist_and_core_health_without_a_new_device_report():
    hub = _research_hub(_settings(), [CP1, {"approved": True, "choice": "approve", "note": ""}])

    await Orchestrator(hub).run_request("r")

    detail = hub.approvals[1]["detail"]
    assert "semantic_report" not in detail
    assert detail["semantic_devices"]["vocabulary"]["status"] == "ok"
    assert detail["semantic_devices"]["checklist"]["status"] == "ok"
    assert isinstance(hub.requests["r"]["research_contract"]["checklist_snapshot"], list)


def _rebinding_hub(correction_reply):
    """A step whose first result cites an uncollected file; `correction_reply(hub, task, path)` answers the
    correction the binding check asks for (#485)."""
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        if task.meta["kind"] == "step":
            result = _cp2_result(hub, task)
            result["artifact_refs"][0]["path"] = "outputs/qc/checks.json"
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result, outputs=[path],
                              output_sha256={path: "a" * 64}, workdir="runs/s1", session_id="session-1")
        return correction_reply(hub, task, path)

    hub = holder["hub"] = MiniHub(_settings(), reply, mode="orchestrate", work_kind="research",
                                    text="compare conditions")
    decisions = [CP1, {"approved": True, "choice": "approve", "note": ""}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    return hub


@pytest.mark.asyncio
async def test_a_citation_of_an_uncollected_file_gets_a_correction_before_cp2():
    """v0.5 trial (2026-10-08): a QC verdict cited a JSON file the plan never declared and CP2 refused it. The step
    now gets a correction that names the collected outputs, and the re-pointed row reaches CP2 bound."""
    def rebound(hub, task, path):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=_cp2_result(hub, task),
                          workdir="runs/s1", session_id="session-1")

    hub = _rebinding_hub(rebound)
    await Orchestrator(hub).run_request("r")

    assert [task.meta["kind"] for task in hub.calls] == ["plan", "step", "result_correction"]
    assert hub.calls[2].meta["required_outputs"] == []
    assert hub.calls[2].meta["optional_outputs"] == []
    prompt = hub.calls[2].prompt
    assert "outputs/qc/checks.json" in prompt and "CP2 would refuse it" in prompt
    assert "this step's outputs (outputs/result1.tsv)" in prompt
    card = hub.approvals[1]["detail"]
    assert "refused_evidence" not in card and card["artifact_sha256"] == {"s1/a1": "a" * 64}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "wrote_other_file", "still_unbound"])
async def test_a_binding_correction_never_fails_a_step_that_passed(outcome):
    """The correction is optional: when it fails, writes only uncollected files, or keeps the citation, the step
    keeps its valid result and CP2 refuses the row as before."""
    def answer(hub, task, path):
        if outcome == "failed":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="worker exited with code 1")
        result = _cp2_result(hub, task)
        result["artifact_refs"][0]["path"] = "outputs/qc/checks.json"
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result, workdir="runs/s1",
                          session_id="session-1",
                          unreported_outputs=["outputs/qc/new.json"] if outcome == "wrote_other_file" else [])

    hub = _rebinding_hub(answer)
    await Orchestrator(hub).run_request("r")

    kinds = [task.meta["kind"] for task in hub.calls]
    assert kinds[:3] == ["plan", "step", "result_correction"]
    assert kinds.count("result_correction") == 1
    assert hub.requests["r"]["results"]["s1"]["ok"] is True
    card = hub.approvals[1]["detail"]
    assert [row["evidence_id"] for row in card["refused_evidence"]] == ["e1"]
    assert "outputs/qc/checks.json" in card["refused_evidence"][0]["reason"]


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
    raw = ["outputs/env/s1.txt", "outputs/extra.tsv", "outputs/reference/map.tsv", "outputs/scripts/run.py"]
    hub = _research_hub(_settings(), [CP1, {"approved": True, "choice": "approve", "note": ""}],
                        unreported_outputs=raw)
    await Orchestrator(hub).run_request("r")

    detail = hub.approvals[1]["detail"]
    expected = {"s1/a1": "a" * 64}
    assert detail["artifact_sha256"] == expected
    assert detail["unreported_outputs"] == {"s1": ["outputs/extra.tsv"]}
    receipt = hub.requests["r"]["research_contract"]["checkpoints"]["cp2"]
    assert receipt["artifact_sha256"] == expected
    assert receipt["unreported_outputs"] == {"s1": raw}


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
    assert refused["e_ghost"] == ("cites artifact ghost at 'outputs/ghost.tsv'; the file exists but is not a "
                                  "collected output of this step or an output of a completed upstream step "
                                  "(파일은 있으나 이 단계의 수집 산출이나 완료된 앞 단계 산출이 아님)")
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


def _empty_lookup(result, *, artifact_id=None):
    """A zero-result lookup linked as support, as the v0.5 trial annotation step wrote it (2026-10-08)."""
    source = {"uri": "https://example.org/search", "query": "camera Hallmark FDR < 0.05", "accessed_at": "2026-10-08"}
    if artifact_id:
        source = {"artifact_id": artifact_id, "query": "camera Hallmark FDR < 0.05", "accessed_at": "2026-10-08"}
    result["evidence"].append({
        "id": "empty_lookup", "kind": "observation", "observation": "no significant pathways",
        "status": "not_found", "result_count": 0, "source": source,
        "directness": "indirect", "source_level": "primary", "independence_group": "lookup",
        "assessment_reason": "the pathway table had no rows under the threshold",
        "slots": list(result["evidence"][0]["slots"]),
    })
    result["links"].append({"claim_id": "c1", "claim_revision": 1, "evidence_id": "empty_lookup",
                            "relation": "supports", "rationale": "no pathway passed the threshold"})
    return result


def test_a_failed_lookup_linked_as_support_is_salvaged_by_refusing_only_the_link():
    # "evidence X is not_found; a failed or empty lookup is neither evidence nor proof of absence ..." is one
    # cross-row message. The explanation after "; " was not on salvage's fixed list, so the whole step failed.
    plan, result = _standalone_result()
    value = _empty_lookup(result)
    assert research_result_errors(value, plan=plan)

    salvaged, refused, unsupported, problems = salvage_research_result(value, plan=plan, expected_step_id="s1")

    assert problems == [] and salvaged is not None and unsupported == []
    assert [row.id for row in salvaged.evidence] == ["e1", "empty_lookup"]  # the attempt stays on record
    assert [(link.claim_id, link.evidence_id) for link in salvaged.links] == [("c1", "e1")]
    assert [row["row_type"] for row in refused] == ["link"] and "empty_lookup" in refused[0]["row_id"]


def test_the_trial_ledger_with_a_missing_artifact_ref_is_salvaged_by_refusing_the_row():
    plan, result = _standalone_result()
    value = _empty_lookup(result, artifact_id="camera_hallmark")  # an upstream artifact not in artifact_refs

    salvaged, refused, unsupported, problems = salvage_research_result(value, plan=plan, expected_step_id="s1")

    assert problems == [] and salvaged is not None and unsupported == []
    assert [row.id for row in salvaged.evidence] == ["e1"]
    assert ("evidence", "empty_lookup") in {(row["row_type"], row["row_id"]) for row in refused}


def test_an_unmappable_message_part_that_names_no_row_still_stops_salvage():
    from labhq.research.contract import _validation_salvage_targets
    from pydantic_core import InitErrorDetails, PydanticCustomError
    from pydantic import ValidationError as PydanticValidationError

    error = PydanticValidationError.from_exception_data("ResearchResult", [InitErrorDetails(
        type=PydanticCustomError("value_error", "Value error, something is wrong; with no row named"),
        loc=(), input={})])
    assert _validation_salvage_targets(error, {"evidence": [], "claims": [], "links": []}) is None


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
async def test_invalid_correction_or_zero_limit_refuses_bad_evidence_and_exposes_gap(corrections, expected_kinds):
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
    assert result["ok"] and result["error"] is None
    assert result["structured"]["claims"] == []
    assert result["structured"]["evidence"][0]["origin"] == "omitted_by_output"
    cp2 = hub.requests["r"]["research_contract"]["checkpoints"]["cp2"]
    assert any("assessment_reason" in row["reason"] for row in cp2["refused_rows"])
    assert cp2["unsupported_claims"] and cp2["warnings"]
    # CP1's answer is not a structured CP2 choice: a repaired step never implies evidence approval.
    assert hub.requests["r"]["outcome"] == "evidence_unreadable"


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


@pytest.mark.asyncio
async def test_zero_plan_hash_is_rebound_without_a_result_correction_turn():
    hub = _research_hub(_settings(), [CP1, {"approved": False, "choice": "deny", "note": "stop"}])
    original_reply = hub.reply

    async def zero_hash_reply(task):
        response = await original_reply(task)
        if task.meta["kind"] == "step" and response.ok:
            response.structured["plan_sha256"] = "0" * 64
        return response

    hub.reply = zero_hash_reply
    await Orchestrator(hub).run_request("r")

    step = hub.requests["r"]["results"]["s1"]
    assert step["structured"]["plan_sha256"] == hub.requests["r"]["research_contract"]["plan_sha256"]
    assert step["step_warnings"] == ["연구 원장의 0 plan_sha256을 승인된 계획 hash로 보정했습니다"]
    assert not any(task.meta["kind"] == "result_correction" for task in hub.calls)
    assert any("0 plan_sha256" in warning
               for warning in hub.requests["r"]["research_contract"]["result_warnings"])


def test_the_step_digest_carries_the_cp1_hypotheses_word_for_word():
    """Web trial 2026-10-08: without them s9 redefined A1 and A2 in round 1 (a review P1) and asked the PI for the
    sentences in round 2."""
    from labhq.orchestrator.cso import _research_protocol_digest

    plan = valid_plan(steps=1)
    plan["brief"]["primary_hypothesis"] = "Smokers show higher cell-cycle expression after covariate adjustment."
    plan["brief"]["null_or_alternatives"] = ["H0: no difference beyond the permutation null.",
                                             "A1: confounding by sex, stage, age or scan batch."]
    plan["brief"]["distinguishing_observations"] = ["DEG count above the 95th permutation percentile (H0)."]
    digest = _research_protocol_digest(plan)
    for text in (plan["brief"]["primary_hypothesis"], *plan["brief"]["null_or_alternatives"],
                 *plan["brief"]["distinguishing_observations"]):
        assert json.dumps(text, ensure_ascii=False) in digest


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
    first = req["report"].splitlines()[:3]
    assert "s1" in first[0] and "원인" in first[1] and first[2] == "개발팀에 자동 보고됨"
    assert req["dev_reports"][0]["step_id"] == "s1"
    assert "a changed plan needs a new CP1 approval" not in req["report"]
    assert "a changed plan needs a new CP1 approval" in req["execution_record"]
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
    settings = _settings()
    settings.research.revise_continuations = 0  # R10: past the cap a CP2 revise still ends the request
    hub = _research_hub(settings, decisions)
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
async def test_resume_reopens_cp2_only_when_live_source_summary_changed(monkeypatch):
    settings = _settings()
    settings.research.live_source_check = True
    settings.research.revise_continuations = 0  # R10: the reopened CP2's revise ends the request as before
    decisions = [CP1, {"approved": True, "choice": "approve", "note": ""},
                 {"approved": False, "choice": "revise", "note": "new retraction"}]
    hub = _research_hub(settings, decisions)
    original_reply = hub.reply
    doi = "10.1021/am300292v"

    async def reply_with_doi(task):
        response = await original_reply(task)
        if task.meta["kind"] == "step" and response.ok:
            response.structured["evidence"][0]["source"].update(
                {"id_scheme": "doi", "id_value": doi, "accessed_at": "2026-10-08"})
        return response

    hub.reply = reply_with_doi
    fixed = StaticResolver({("doi", doi): [SourceRecord(id_scheme="doi", id_value=doi)]})
    monkeypatch.setattr("labhq.orchestrator.cso.LiveSourceResolver", lambda **_kwargs: fixed)

    await Orchestrator(hub).run_request("r")
    first_receipt = copy.deepcopy(hub.requests["r"]["research_contract"]["checkpoints"]["cp2"])
    assert first_receipt["source_verification"]

    _interrupt(hub)
    approvals = len(hub.approvals)
    await Orchestrator(hub).run_request("r", resume=True)
    assert len(hub.approvals) == approvals
    assert hub.requests["r"]["research_contract"]["checkpoints"]["cp2"] == first_receipt

    fixed.records[("doi", doi)] = [SourceRecord(
        id_scheme="doi", id_value=doi,
        updates=[SourceUpdate(direction="updated_by", update_type="retraction",
                              related={"id_scheme": "doi", "id_value": "10.1021/acsami.9b11759"})])]
    _interrupt(hub)
    await Orchestrator(hub).run_request("r", resume=True)

    assert len(hub.approvals) == approvals + 1
    assert hub.approvals[-1]["summary"].startswith("Live source verification changed")
    receipt = hub.requests["r"]["research_contract"]["checkpoints"]["cp2"]
    assert receipt["decision"] == "revision_requested"
    assert receipt["source_verification"] != first_receipt["source_verification"]
    assert any("retracted_by" in line for line in receipt["source_verification"])


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


@pytest.mark.asyncio
async def test_a_plan_frozen_before_reasoned_skips_resumes_and_records_the_reasonless_item(monkeypatch):
    # PR #452 review: CP1 approved "not_applicable: none" under the old rule; a restart after the upgrade must not
    # fail the approved run. The frozen plan stays as approved and the request names the item without a reason.
    from labhq.vocab import topic_checklists
    import sys

    old_plan = valid_plan()
    old_plan["checklist"]["qc"] = "not_applicable: none"
    monkeypatch.setattr(sys.modules[__name__], "valid_plan", lambda: copy.deepcopy(old_plan))
    monkeypatch.setattr(topic_checklists, "PLACEHOLDER_REASONS", frozenset())  # the rule before #446
    settings = _settings()
    approve = {"approved": True, "choice": "approve", "note": ""}
    decisions = [CP1, approve]
    hub = _research_hub(settings, decisions)
    await Orchestrator(hub).run_request("r")
    frozen = copy.deepcopy(hub.requests["r"]["plan"])
    hub.requests["r"]["research_contract"].pop("checkpoints")
    _interrupt(hub)
    monkeypatch.undo()
    decisions.append(dict(approve))
    calls, approvals = len(hub.calls), len(hub.approvals)
    await Orchestrator(hub).run_request("r", resume=True)

    assert hub.requests["r"]["status"] == "done", hub.requests["r"].get("error")
    assert [item["kind"] for item in hub.approvals[approvals:]] == ["research_evidence"]
    assert hub.calls[calls:] == []
    assert hub.requests["r"]["outcome"] == "evidence_approved"
    assert hub.requests["r"]["plan"] == frozen
    assert hub.requests["r"]["checklist_reasonless"] == ["qc"]


# ---------- P1: a research step may stop for a PI decision before its ledger is checked ----------

@pytest.mark.asyncio
@pytest.mark.parametrize("schema_shaped", [True, False])
async def test_research_step_question_reaches_continue_without_rerunning(schema_shaped):
    settings = _settings()
    settings.research.revise_continuations = 1
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
    decisions = [CP1, {"approved": False}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    assert "blocking_decision" in seen[0].output_schema["properties"]
    assert "blocking_decision" not in seen[0].output_schema["required"]
    assert "The PI decides whether to revise the plan or end the request" in seen[0].prompt
    assert "The CSO answers first; only hard-stop" not in seen[0].prompt
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_continue"]
    assert "Which donor group" in hub.approvals[1]["summary"] and len(seen) == 1
    assert hub.requests["r"]["outcome"] == "research_failed"
    assert hub.requests["r"]["results"]["s1"]["error_kind"] == "research_stop"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [
    "이 단계를 시작하지 않았습니다. 동결 계획의 멈춤 조건은 QC가 FAIL이면 넘어가지 않는다입니다.",
    "s2 QC가 FAIL이라 동결 계획의 중단 조건에 따라 s3를 실행하지 않았습니다.",
])
async def test_explicit_plan_stop_in_failures_goes_directly_to_continue(reason):
    settings = _settings()
    settings.research.revise_continuations = 1
    holder, seen = {}, []

    async def reply(task):
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        seen.append(task)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False,
                          error="incomplete: missing outputs: result1.tsv", missing_outputs=["result1.tsv"],
                          structured={"failures": [reason]})

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare")
    decisions = [CP1, {"approved": False}]
    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return decisions.pop(0)
    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_continue"]
    assert len(seen) == 1 and reason in hub.approvals[-1]["summary"]
    assert hub.requests["r"]["results"]["s1"]["error_kind"] == "research_stop"


def test_completed_step_can_report_stopped_optional_analysis():
    result = TaskResult(task_id="t", agent_id="worker", ok=True,
                        structured={"failures": ["멈춤 조건을 따라 선택 분석은 시작하지 않았습니다."]})
    assert cso.research_stop_reason(result) is None


# ---------- P1: CP2 reads a structured choice, never the note ----------

@pytest.mark.asyncio
async def test_cp2_approval_without_a_choice_is_not_approved_and_is_asked_again():
    settings = _settings()
    settings.research.revise_continuations = 0  # R10: past the cap a CP2 revise still ends the request
    hub = _research_hub(settings, [CP1, {"approved": True, "note": "Request revision"},
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
    # An unanswered card is not a rejection (PI 점검 R5); an expired one still is.
    ({"approved": False, "note": "timed out", "state": "timed_out"}, "timed_out"),
    ({"approved": False, "note": "gateway restarted", "state": "expired"}, "rejected"),
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
        # The CP1 pilot contract, not the evidence checkpoint. The shared planning policy names CP1/CP2 as review
        # boundaries (#799); that does not switch evidence mode, so only the mode-specific rule text is checked.
        assert cp1_only in prompt and cso.RESEARCH_CP2_RULE not in prompt
        assert "stops at CP2" not in prompt and "artifact_refs path must be one of that step's declared outputs" not in prompt
        assert BIOINFO_ASSIGNMENT_RULE in prompt and "CP2" not in prompt.replace(BIOINFO_ASSIGNMENT_RULE, "")


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


@pytest.mark.asyncio
async def test_cp2_literature_lines_are_given_the_stored_precedent_access(monkeypatch):
    """#1090: the two lines count what the pre-CP1 search stored, so a paper read there is not "조회 기록 없음"."""
    from labhq import output_checks

    seen = {}
    for name in ("conclusion_literature_line", "literature_ledger_line"):
        monkeypatch.setattr(output_checks, name, lambda *a, _n=name, **k: seen.setdefault(_n, k.get("stored")) and None)
    hub = _research_hub(_settings(), [CP1, {"approved": False, "choice": "deny", "note": "stop"}])
    hub.requests["r"]["analysis_precedents"] = {"status": "ok", "papers": [{
        "fulltext_receipt": {"paper_id": "PMID:1", "access_level": "full_text", "text_file": "p.md",
                             "text_sha256": "a" * 64}, "citations": []}]}
    expected = output_checks.stored_access(hub.requests["r"]["analysis_precedents"])
    assert expected, "the fixture must be a record stored_access accepts"
    await Orchestrator(hub).run_request("r")

    assert seen == {"conclusion_literature_line": expected, "literature_ledger_line": expected}


def test_card_warning_names_a_real_device_stop_but_not_semantics_turned_off():
    """#1336: configured_off is a setting, not a stop; a failing device still warns on the card."""
    off = {"code": "configured_off", "device": "semantics", "stage": "setting", "at": "t", "message": "off"}
    failed = {"code": "vocabulary_load_failed", "device": "단어장", "stage": "load", "at": "t", "message": "x"}
    assert cso._device_stop_warning({"errors": [off]}) == ""
    assert cso._device_stop_warning({"errors": []}) == ""
    assert cso._device_stop_warning(None) == ""
    warning = cso._device_stop_warning({"errors": [off, failed]})
    assert warning.startswith("⚠ 의미 장치 중단: 단어장 vocabulary_load_failed") and "configured_off" not in warning


@pytest.mark.parametrize('long_notice', [False, True])
async def test_cp1_kind_stays_first_with_paper_and_reassignment_warnings(monkeypatch, long_notice):
    hub = _research_hub(_settings(), [CP1, {'approved': False, 'choice': 'deny', 'note': 'stop'}])
    original = hub.reply

    async def reply(task):
        response = await original(task)
        if task.meta['kind'] == 'plan':
            response.structured.setdefault('warnings', []).append(
                '재배정: worker에게 단계 배정' + (' 긴 안내' * 200 if long_notice else ''))
        return response

    hub.reply = reply
    monkeypatch.setattr(cso.paper_skills, 'card_text', lambda plan: 'Paper skill: fixture')
    await Orchestrator(hub).run_request('r')
    card = hub.approvals[0]
    assert card['summary'].splitlines()[0].startswith('CP1 연구 계획 승인:')
    assert '재배정: worker에게 단계 배정' in card['summary']
    assert 'Paper skill: fixture' in card['summary']
    assert len(card['summary']) <= 699


# Compact report handoff after the evidence checkpoint (#1490).

def _draft_hub(tmp_path, *, problem=None, remote=None, gateway=None, draft_body=None, source=None):
    from tests.test_research_report import REPORT, _hub
    from labhq.runner import report_artifacts

    hub = _hub()
    req = hub.requests["r"]
    req["research_options"] = {"compact_workflow": True}
    reply = hub.reply
    approve = hub.request_approval
    if gateway:
        hub.report_artifacts = gateway.report_artifacts

    async def with_draft(task):
        result = await reply(task)
        if task.meta["kind"] != "step":
            return result
        [path] = task.meta["optional_outputs"]
        assert path in task.meta["outputs"] and path not in task.meta["required_outputs"]
        assert path in task.prompt
        target = tmp_path / path
        target.parent.mkdir(exist_ok=True)
        table = result.outputs[0]
        (tmp_path / table).write_bytes(b"effect\n0.25\n")
        body = {
            "empty": " \n",
            "anchor": "Unknown claim [[claim:s1/wrong]].",
            "table": f"The [measured effect]({table}) was 0.75 [[claim:s1/c1]].",
            "table_missing": "The [measured effect](outputs/missing.tsv) was 0.25 [[claim:s1/c1]].",
            "portable": REPORT + f"\n\nThe [measured effect](steps/s1/{table}) was 0.25 [[claim:s1/c1]].",
        }.get(problem, REPORT if draft_body is None else draft_body)
        if source:
            result.structured["evidence"][0]["source"].update(source)
        data = b"\xff" if problem == "encoding" else body.encode("utf-8")
        target.write_bytes(data)
        hashes = {**result.output_sha256, table: sha256(b"effect\n0.25\n").hexdigest()}
        if problem != "unhashed":
            hashes[path] = sha256(data).hexdigest()
        result = result.model_copy(update={
            "workdir": str(tmp_path),
            "outputs": [*result.outputs, *([] if problem == "missing" else [path])],
            "output_sha256": hashes,
        })
        if remote:
            assert task.meta[report_artifacts.RESULT_FIELD] == path
            if remote == "changed_source":
                (tmp_path / table).write_bytes(b"effect\n0.5\n")
            payload = report_artifacts.collect(tmp_path, task.meta, result)
            if remote == "changed_source":
                assert table not in payload
            elif remote == "missing_draft":
                del payload[path]
            elif remote == "changed_draft":
                payload[path] = payload[table]
            elif remote == "changed_table":
                payload[table] = payload[path]
            elif remote == "wrong_dispatch":
                result = result.model_copy(update={"agent_id": "unrelated"})
            target.unlink()
            (tmp_path / table).unlink()
            wire = {"type": "task.result", "task_id": task.id, "request_id": task.request_id, "runner_seq": 1,
                    "data": {**result.model_dump(mode="json"), report_artifacts.RESULT_FIELD: payload}}
            sent = asyncio.create_task(gateway.dispatch(task))
            await asyncio.wait_for(gateway.runners["remote"].dispatched.wait(), 5)
            await gateway.on_runner_message("remote", json.loads(json.dumps(wire)),
                                            gateway.runners["remote"], "remote-1")
            result = await asyncio.wait_for(sent, 5)
            journal = gateway.store.runner_inbox("remote", "remote-1", 1)
            assert journal["state"] == "completed" and report_artifacts.RESULT_FIELD not in journal["message"]["data"]
            events = gateway.store.events_since(0)
            assert any(event["type"] == "task.result" for event in events)
            assert all(report_artifacts.RESULT_FIELD not in event.get("data", {}) for event in events)
        return result

    async def after_step(**kwargs):
        if problem == "changed" and kwargs["kind"] == "research_evidence":
            draft = analysis_draft.for_request(req)
            (tmp_path / draft.path).write_bytes(b"Changed after collection [[claim:s1/c1]].")
        return await approve(**kwargs)

    hub.reply = with_draft
    hub.request_approval = after_step
    return hub


@pytest.mark.asyncio
async def test_compact_report_uses_collected_analyst_draft_without_cso_writing(tmp_path):
    from tests.test_research_report import REPORT, _kinds

    hub = _draft_hub(tmp_path)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _kinds(hub) == ["plan", "step", "review"]
    assert req["status"] == "done" and req["outcome"] == "research_reported"
    assert hub.calls[1].agent_id == "worker"
    assert req["report"].startswith(REPORT)
    assert "no external cohort" in req["report"]
    assert req["research_contract"]["report_check"] == {"anchors": 1, "problems": []}
    assert req["research_contract"]["plan_sha256"] == plan_sha256(req["plan"])
    assert analysis_draft.for_request(req).path in req["results"]["s1"]["output_sha256"]


@pytest.mark.asyncio
async def test_inline_draft_uses_portable_result_links(tmp_path):
    hub = _draft_hub(tmp_path, problem="portable")
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    prompt = hub.calls[1].prompt
    assert "Use steps/<step_id>/outputs/<file> for every result link" in prompt
    assert "[[claim:<step_id>/<claim_id>]] anchor on each bibliography entry" in prompt
    assert "Omit sources with no such evidence" in prompt
    assert "Put judgments about sources and comparisons in the report body with claim anchors" in prompt
    path = f"steps/s1/{req['plan']['steps'][0]['outputs'][0]}"
    assert req["status"] == "done" and f"]({path})" in req["report"]
    assert req["report_package"]["tables"][0]["path"] == path
    assert req["report_consistency"]["checked"]["tables"] == [path]


@pytest.fixture
def bibliography_draft():
    from tests.test_research_report import _literature

    doi = "10.1000/fake-bibliography"
    ledgers = {"s1": {
        "claims": [{"id": "c1", "status": "supported", "statement": "The source describes the assay."}],
        "evidence": [_literature("e1", source={"id_value": doi})],
        "links": [{"claim_id": "c1", "evidence_id": "e1", "relation": "supports"}],
    }}
    body = "# Assay report\n\nThe source describes the assay [[claim:s1/c1]].\n\n## 참고\n\n"
    return body, f"- Lee et al., *Assay design* (2024), [DOI](https://doi.org/{doi})", ledgers


def test_compact_draft_accepts_bibliography_entry_with_recorded_source(bibliography_draft):
    from labhq.evidence.report_check import check_report

    body, entry, ledgers = bibliography_draft
    assert check_report(f"{body}{entry} [[claim:s1/c1]]\n", ledgers) == {"anchors": 2, "problems": []}


def test_compact_bibliography_accepts_linked_counterevidence_until_refused(bibliography_draft):
    from labhq.evidence.report_check import check_report
    from tests.test_research_report import _literature

    body, _, ledgers = bibliography_draft
    ledger = ledgers["s1"]
    ledger["claims"][0]["status"] = "partially_supported"
    doi = "10.1000/counterevidence"
    ledger["evidence"].append(_literature("e2", source={"id_value": doi}))
    ledger["links"].append({"claim_id": "c1", "evidence_id": "e2", "relation": "contradicts"})
    body += f"- [Counterevidence](https://doi.org/{doi}) [[claim:s1/c1]]\n"
    assert check_report(body, ledgers, bibliography=True)["problems"] == []
    assert check_report(body, ledgers, bibliography=True,
                        refused=[{"step_id": "s1", "evidence_id": "e2"}])["problems"] == [
        f"bibliography DOI {doi} has no accepted evidence linked to its anchors",
    ]

    ledger["claims"].append({**ledger["claims"][0], "id": "c2"})
    ledger["links"].append({"claim_id": "c2", "evidence_id": "e2", "relation": "supports"})
    other = "10.1000/fake-bibliography"
    table = f"| Source | Claim |\n|---|---|\n| {other} | [[claim:s1/c2]] |\n| {doi} | [[claim:s1/c1]] |\n"
    assert check_report(body + "\n" + table, ledgers, bibliography=True)["problems"] == [
        f"bibliography DOI {other} has no accepted evidence linked to its anchors",
    ]


@pytest.mark.parametrize("case", ["unmarked_entry", "judgment"])
def test_compact_draft_flags_unanchored_text_in_bibliography(bibliography_draft, case):
    from labhq.evidence.report_check import check_report

    body, entry, ledgers = bibliography_draft
    if case == "unmarked_entry":
        body += entry
        problem = "numeric prose needs a claim anchor (review context): Lee et al., *Assay design* (2024), [DOI]"
    else:
        body += f"{entry} [[claim:s1/c1]]\n- The source supports the proposed mechanism."
        problem = ("possible conclusion needs a claim anchor (review context): "
                   "The source supports the proposed mechanism.")
    assert check_report(body, ledgers)["problems"] == [problem]


@pytest.mark.parametrize(("scheme", "recorded", "citation"), [
    ("doi", "10.1234/accepted", "[DOI](https://doi.org/10.1234/unrelated)"),
    ("pmid", "12345678", "PMID 87654321"),
    ("geo", "GSE12345", "GSE54321"),
])
@pytest.mark.parametrize("heading", ["참고", "참고문헌 및 자료", "References and data"])
async def test_compact_bibliography_rejects_identifier_from_another_source(tmp_path, scheme, recorded, citation, heading):
    from tests.test_research_report import REPORT

    hub = _draft_hub(tmp_path, draft_body=f"{REPORT}\n\n## {heading}\n\n- {citation} [[claim:s1/c1]]\n",
                     source={"id_scheme": scheme, "id_value": recorded, "accessed_at": "2026-10-01"})
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["outcome"] == "report_incomplete"
    assert req["research_contract"]["report_check"]["problems"]


@pytest.mark.parametrize("unsupported_in_assembly", [False, True])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
async def test_compact_bibliography_recheck_uses_the_assembled_report(tmp_path, monkeypatch, unsupported_in_assembly,
                                                                    newline):
    from labhq.evidence.audit import rerun_report_check
    from labhq.orchestrator import cso
    from tests.test_research_report import REPORT

    if unsupported_in_assembly:
        assemble = cso.add_references

        def with_bad_citation(report, *args, **kwargs):
            return assemble(report, *args, **kwargs).replace(
                "## 참고\n", "## 참고\n\n- PMID 87654321 [[claim:s1/c1]]\n", 1)

        monkeypatch.setattr(cso, "add_references", with_bad_citation)
    doi, unrelated = "10.1234/supported", "10.1234/input-only"
    draft = f"{REPORT}\n\n## 참고\n\n- [Paper](https://doi.org/{doi}) [[claim:s1/c1]]\n"
    hub = _draft_hub(tmp_path, draft_body=draft.replace("\n", newline),
                     source={"id_scheme": "doi", "id_value": doi, "accessed_at": "2026-10-01"})
    hub.requests["r"]["references"] = [{"kind": "doi", "value": unrelated}]
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    check = rerun_report_check(req)
    assert check["recorded"] == check["rerun"]
    assert f"- [{doi}](https://doi.org/{doi})" in req["report"]
    assert unrelated not in req["report"]
    if unsupported_in_assembly:
        assert req["status"] == "failed" and req["outcome"] == "report_incomplete"
        assert check["rerun"]["problems"] == [
            "bibliography PMID 87654321 has no accepted evidence linked to its anchors",
        ]
    else:
        assert req["outcome"] == "research_reported"
        assert check["rerun"] == {"anchors": 3, "problems": []}


@pytest.mark.parametrize("options", [None, {"compact_workflow": False}])
async def test_legacy_bibliography_still_lists_attached_references(options):
    from tests.test_research_report import REPORT, _hub

    doi = "10.1234/input-only"
    hub = _hub(report=f"{REPORT}\n\n## 참고\n")
    req = hub.requests["r"]
    if options is not None:
        req["research_options"] = options
    req["references"] = [{"kind": "doi", "value": doi}]
    await Orchestrator(hub).run_request("r")

    assert req["outcome"] == "research_reported"
    assert f"https://doi.org/{doi}" in req["report"]


@pytest.mark.parametrize("case", ["linked", "mixed_evidence", "unreported", "wrong_file", "unanchored", "unsupported"])
async def test_compact_numbers_link_collected_summary_and_sensitivity_row(tmp_path, case):
    from labhq.research.independent_review import render_ledgers
    from tests.test_research_report import _kinds

    hub = _audit_hub(tmp_path)
    hub.s.research.literal_source_check = True
    hub.s.research.result_corrections = 0
    plan = valid_plan()
    summary, table, qc = ("outputs/run_summary.json", "outputs/sensitivity.tsv", "outputs/qc.tsv")
    files = {summary: b'{"input_rows": 4321}\n', table: b'check\teffect\nsubset\t0.64\n',
             qc: b'sample\treads\ncontrol\t80\n'}
    if case == "mixed_evidence":
        files[qc] = b"check\teffect\nalternative\t-0.2\n"
    plan["steps"][0].update(outputs=[path for path in files if case != "unreported" or path != summary],
                            claim_ids=["c1", "c2"])
    base_reply = hub.reply
    row_mark = "" if case == "unanchored" else " [[claim:s1/c2]]"
    body = (f"[Input summary](steps/s1/{summary}) recorded 4,321 input rows [[claim:s1/c1]].\n\n"
            f"| Check | Effect |\n| --- | --- |\n| [Subset](steps/s1/{table}) | 0.64{row_mark} |\n")
    if case == "unanchored":
        body += "\nThe initial count was 4,321.\n"
    if case == "unsupported":
        body += "\nThe method supports the proposed mechanism [[claim:s1/mechanism]].\n"

    async def reply(task):
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=plan)
        if task.meta["kind"] != "step":
            return await base_reply(task)
        ledger = _cp2_result(hub, task)
        draft_path, = task.meta["optional_outputs"]
        evidence_path = qc if case == "wrong_file" else summary
        ledger["artifact_refs"] = [{"artifact_id": "a1", "path": evidence_path},
                                   {"artifact_id": "a2", "path": table}]
        claim, = ledger["claims"]
        claim["statement"] = "The input contained 4,321 rows."
        ledger["claims"].append({**claim, "id": "c2", "statement": "The subset effect was 0.64."})
        ledger["links"].append({**ledger["links"][0], "claim_id": "c2", "evidence_id": "e2"})
        evidence, = ledger["evidence"]
        evidence.update(source={"artifact_id": "a1", "locator": "input_rows"},
                        observation="Input row count",
                        quantities=[{"id": "q1", "measure": "input row count", "value": 4321, "unit": "rows",
                                     "conditions": ["before filtering"], "denominator": "whole input"}])
        ledger["evidence"].append({**evidence, "id": "e2", "slots": [], "independence_group": "subset",
                                   "source": {"artifact_id": "a2", "locator": "line 2, effect"},
                                   "observation": "Subset effect",
                                   "quantities": [{"id": "q2", "measure": "effect", "value": 0.64,
                                                   "unit": "dimensionless", "conditions": ["subset"],
                                                   "denominator": "subset samples"}]})
        if case == "mixed_evidence":
            ledger["claims"][1].update(status="partially_supported", status_reason="alternative disagrees")
            ledger["artifact_refs"].append({"artifact_id": "a3", "path": qc})
            subset = ledger["evidence"][1]
            ledger["evidence"].append({**subset, "id": "e3", "independence_group": "alternative",
                                       "source": {"artifact_id": "a3", "locator": "line 2, effect"},
                                       "observation": "Alternative effect",
                                       "quantities": [{**subset["quantities"][0], "id": "q3", "value": -0.2,
                                                       "conditions": ["alternative"], "denominator": "alternative samples"}]})
            ledger["links"].append({**ledger["links"][1], "evidence_id": "e3", "relation": "contradicts",
                                    "rationale": "the alternative shows the opposite direction"})
        written = {**files, draft_path: body.encode("utf-8")}
        for path, data in written.items():
            target = tmp_path / path
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(data)
        # Model the runner's declaration-based collection with real bytes, receipts and one unreported file.
        collected = [path for path in task.meta["outputs"] if path in written]
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=ledger,
                          workdir=str(tmp_path), workdir_id=tmp_path.name, outputs=collected,
                          output_sha256={path: sha256(written[path]).hexdigest() for path in collected},
                          unreported_outputs=[path for path in written if path not in collected])

    hub.reply = reply
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    contract = req["research_contract"]
    accepted = case in {"linked", "mixed_evidence"}
    assert req["outcome"] == ("research_reported" if accepted else "report_incomplete")
    assert _kinds(hub) == ["plan", "step", "review"]
    cp2 = contract["checkpoints"]["cp2"]
    states = {check["evidence_id"]: check["state"] for check in cp2["literal_checks"]}
    expected = {"unreported": "unverified", "wrong_file": "mismatch"}.get(case, "matched")
    assert states == {"e1": expected, "e2": "matched", **({"e3": "matched"} if case == "mixed_evidence" else {})}
    assert cp2["unreported_outputs"]["s1"] == ([summary] if case == "unreported" else [])
    problems = contract["report_check"]["problems"]
    assert problems == cp2["independent_audit"]["checks"]["claims"]["problems"]
    result = TaskResult.model_validate(req["results"]["s1"])
    context = render_ledgers(req["plan"]["steps"], {"s1": result}, {"s1": result.structured},
                             cp2["refused_evidence"], cp2["unsupported_claims"], cp2["artifact_sha256"], 2000,
                             staged=True)
    assert "chars clipped" in context
    marks = context.split("Available claim anchors (read their evidence before use):\n", 1)[1].split("Ledger:\n", 1)[0]
    marks = dict(line.split(" ", 1) for line in marks.splitlines())
    linked_evidence = json.loads(marks["[[claim:s1/c2]]"])["evidence"]
    assert linked_evidence["e2"]["artifact_path"] == "inputs/s1/sensitivity.tsv"
    assert linked_evidence["e2"]["locator"] == "line 2, effect"
    assert linked_evidence["e2"]["relation"] == "supports"
    if case == "mixed_evidence":
        assert linked_evidence["e3"]["artifact_path"] == "inputs/s1/qc.tsv"
        assert linked_evidence["e3"]["locator"] == "line 2, effect"
        assert linked_evidence["e3"]["relation"] == "contradicts"
    assert ("[[claim:s1/c1]]" in marks) == (expected == "matched")
    if accepted:
        assert problems == []
        assert cp2["artifact_sha256"]["s1/a1"] == sha256(files[summary]).hexdigest()
        assert cp2["artifact_sha256"]["s1/a2"] == sha256(files[table]).hexdigest()
        assert req["report_consistency"]["checked"]["tables"] == [f"steps/s1/{table}"]
    elif case in {"unreported", "wrong_file"}:
        assert len(problems) == 1 and problems[0].startswith("[[claim:s1/c1]]:")
        assert [row["evidence_id"] for row in cp2["refused_evidence"]] == ["e1"]
    elif case == "unanchored":
        assert len(problems) == 2
        assert problems[0].startswith("numeric prose needs a claim anchor")
        assert problems[1].startswith("table row needs a claim anchor")
    else:
        assert len(problems) == 1 and "mechanism is not in step s1's ledger" in problems[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(("remote", "audit"), [
    ("complete", False), ("missing_draft", False), ("changed_draft", False), ("changed_source", False),
    ("changed_table", False), ("wrong_dispatch", False), ("complete", True),
])
async def test_compact_report_uses_only_verified_transferred_artifacts(tmp_path, remote, audit):
    from labhq.gateway.server import Hub
    from tests.test_research_report import ACCEPT

    class Socket:
        def __init__(self):
            self.dispatched = asyncio.Event()

        async def send_text(self, frame):
            if json.loads(frame)["type"] == "task.dispatch":
                self.dispatched.set()

    settings = _settings()
    settings.gateway.state_dir = str(tmp_path / "gateway")
    gateway = Hub(settings)
    runner_dir = tmp_path / "runner"
    runner_dir.mkdir()
    try:
        gateway.register_runner("remote", Socket(), [{"id": "worker"}], incarnation="remote-1")
        hub = _draft_hub(runner_dir, problem="portable", remote=remote, gateway=gateway)
        if audit:
            hub.requests["r"]["research_options"]["audit_before_cp2"] = True
            reply = hub.reply

            async def audit_reply(task):
                if task.meta["kind"] != "review":
                    return await reply(task)
                assert [card["kind"] for card in hub.approvals] == ["research_plan"]
                assert "Automated checks of the draft" in task.prompt
                assert task.prompt.index("Readable files:") < task.prompt.index("Verified draft copy.")
                assert "The [measured effect](steps/s1/" in task.prompt
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=ACCEPT)

            hub.reply = audit_reply
        await Orchestrator(hub).run_request("r")
        req = hub.requests["r"]
        task_id = req["results"]["s1"]["task_id"]
        files = gateway.report_artifacts(task_id) or {}
        assert gateway.report_artifacts("unrelated") is None
    finally:
        gateway.store.close()
    table = f"steps/s1/{req['plan']['steps'][0]['outputs'][0]}"
    assert not list((runner_dir / "outputs").iterdir())
    if remote == "complete":
        assert files[table.removeprefix("steps/s1/")] == "effect\n0.25\n"
        assert req["status"] == "done" and req["outcome"] == "research_reported"
        assert req["report_consistency"]["checked"]["tables"] == [table]
        assert f"]({table})" in req["report"]
        if audit:
            checks = hub.approvals[1]["detail"]["independent_audit"]["checks"]
            assert checks["table_consistency"]["checked"]["tables"] == [table]
    elif remote.endswith("_draft") or remote == "wrong_dispatch":
        assert analysis_draft.for_request(req).path not in files
        assert req["status"] == "failed" and req["outcome"] == "research_failed"
        assert req["research_contract"]["failure"]["stage"] == "analysis_draft"
    else:
        assert req["status"] == "failed" and req["outcome"] == "report_incomplete"
        assert req["report_consistency"]["checked"]["unread_tables"] == [table]


@pytest.mark.asyncio
@pytest.mark.parametrize(("problem", "error"), [
    ("missing", "did not deliver"),
    ("unhashed", "no collected SHA-256"),
    ("changed", "differs from the collected SHA-256"),
    ("empty", "is empty"),
    ("encoding", "unreadable as UTF-8"),
    ("anchor", None),
    ("table", None),
    ("table_missing", None),
])
async def test_compact_report_rejects_missing_or_invalid_draft(tmp_path, problem, error):
    from tests.test_research_report import _kinds

    hub = _draft_hub(tmp_path, problem=problem)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "failed"
    assert "synthesis" not in _kinds(hub)
    if error:
        assert req["outcome"] == "research_failed"
        assert req["research_contract"]["failure"]["stage"] == "analysis_draft"
        assert error in req["error"]
    else:
        assert req["outcome"] == "report_incomplete"
        if problem == "anchor":
            assert any("claim wrong" in line for line in req["research_contract"]["report_check"]["problems"])
        elif problem == "table":
            assert req["research_contract"]["report_consistency"]["findings"][0]["kind"] == "number"
        else:
            assert req["research_contract"]["report_consistency"]["checked"]["unread_tables"] == [
                "outputs/missing.tsv"]


@pytest.mark.asyncio
async def test_compact_resumes_the_saved_draft_and_review(tmp_path):
    from tests.test_research_report import _kinds

    hub = _draft_hub(tmp_path)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    report, saved_review = req["report"], req["research_contract"]["review"]
    _interrupt(hub)
    calls, approvals = len(hub.calls), len(hub.approvals)

    await Orchestrator(hub).run_request("r", resume=True)

    req = hub.requests["r"]
    assert _kinds(hub, calls) == [] and hub.approvals[approvals:] == []
    assert req["status"] == "done" and req["report"] == report
    assert req["research_contract"]["review"] == saved_review


def _audit_hub(tmp_path, *, problem=None, malformed=False):
    from tests.test_research_report import ACCEPT

    hub = _draft_hub(tmp_path, problem=problem)
    hub.s.runner.workspace_root = str(tmp_path.parent)
    hub.requests["r"]["research_options"]["audit_before_cp2"] = True
    base_reply = hub.reply

    async def reply(task):
        if task.meta["kind"] != "review":
            result = await base_reply(task)
            return (result.model_copy(update={"workdir_id": tmp_path.name})
                    if task.meta["kind"] == "step" else result)
        assert task.agent_id == "sci_reviewer"
        assert "The evidence is awaiting the PI's CP2 decision." in task.prompt
        assert "First open the collected analysis code and result tables" in task.prompt
        assert task.prompt.index("Readable files:") < task.prompt.index("Draft (open after")
        assert task.meta["upstream_dirs"] == [str(tmp_path)]
        assert "Verified draft copy." not in task.prompt
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured={} if malformed else ACCEPT)

    hub.reply = reply
    return hub


@pytest.mark.parametrize(("case", "attempts", "status"), [
    ("resolved", 1, "resolved"), ("unclassified", 1, "resolved"), ("limit", 2, "limit"),
    ("reference_drift", 1, "resolved"),
    ("no_session", 0, "session_unavailable"),
    ("new_session", 1, "session_unavailable"), ("ignored_ledger", 1, "resolved"),
    ("interrupted", 1, "interrupted"), ("budget", 1, "budget_exceeded"),
    ("no_engine_cap", 0, "budget_unavailable"),
    ("design", 0, "ineligible"), ("off", 0, None), ("unset", 0, None), ("audit_off", 0, None),
    ("claims_only", 1, "resolved"), ("claims_and_p1", 1, "resolved"),
    ("claims_after_p1", 1, "invalid_result"), ("replacement_table", 1, "invalid_result"),
    ("claims_off", 0, None), ("claims_no_session", 0, "session_unavailable"),
])
async def test_p1_repair_before_cp2(tmp_path, monkeypatch, case, attempts, status):
    from labhq.evidence.audit import rerun_report_check
    from labhq.research.contract import validate_research_result
    from labhq.runner.step_handoff import artifact_contract_for
    from labhq.runner.step_inputs import stage_inputs
    from tests.test_research_report import ACCEPT, REVISE, REPORT, _kinds

    original = tmp_path / "original"
    original.mkdir()
    bad_text = "\n\nWe counted 12345 samples.\n\n## 참고\n\n- PMID 87654321 [[claim:s1/c1]]."
    initial_claim_problems = case in {"claims_only", "claims_and_p1", "claims_off", "claims_no_session"}
    hub = _draft_hub(original, draft_body=REPORT + bad_text if initial_claim_problems else None)
    hub.s.runner.workspace_root = str(tmp_path)
    hub.supports_resume = lambda agent_id: case not in {"no_session", "claims_no_session"} and agent_id == "worker"
    if case == "no_engine_cap":
        hub.agents["worker"]["engine"] = "codex"
    req = hub.requests["r"]
    req["research_options"]["audit_before_cp2"] = case != "audit_off"
    if case != "unset":
        req["research_options"]["audit_repair_before_cp2"] = case not in {"off", "claims_off"}
    hub.s.research.audit_repair_before_cp2 = case in {"off", "unset", "claims_off"}
    monkeypatch.setenv("LABHQ_AUDIT_REPAIR_BEFORE_CP2", "1" if hub.s.research.audit_repair_before_cp2 else "0")
    if case == "reference_drift":
        source = tmp_path / "live-reference"
        source.mkdir()
        (source / "data.tsv").write_text("original sample", encoding="utf-8")
        req["references"] = [{"kind": "path", "value": str(source)}]
    if case == "budget":
        req["budget_usd"] = 4
    base_reply = hub.reply
    generations = []
    audits = []
    resolved = case in {"resolved", "unclassified", "reference_drift", "claims_only", "claims_and_p1", "ignored_ledger"}

    def save_manifest(task, result):
        manifest = {"task": task.model_dump(mode="json"), "runs": {task.id: {"ok": True}}}
        (Path(result.workdir) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    async def reply(task):
        kind = task.meta["kind"]
        if kind == "plan":
            return (await base_reply(task)).model_copy(update={"cost_usd": 0})
        if kind == "step":
            if case == "reference_drift":
                assert task.meta["reference_dirs"] == [str(source)]
            result = (await base_reply(task)).model_copy(update={"session_id": "original-session",
                                                                 "workdir_id": original.name, "cost_usd": 0})
            result.structured = validate_research_result(result.structured, plan=req["plan"]).model_dump(mode="json")
            save_manifest(task, result)
            generations.append(result)
            return result
        if kind == "review":
            latest = generations[-1]
            audits.append(latest)
            if case == "reference_drift" and len(audits) == 1:
                (source / "data.tsv").write_text("changed sample", encoding="utf-8")
            if case != "audit_off":
                assert latest.workdir in task.meta["upstream_dirs"]
            if len(generations) > 1:
                assert "review" not in req["research_contract"]
                assert task.meta["parent_task"] == latest.task_id
                draft_path = analysis_draft.for_request(req).path
                assert latest.output_sha256[draft_path] in task.prompt
                assert generations[0].output_sha256[generations[0].outputs[0]] in task.prompt
                assert str(Path(latest.workdir, draft_path)) in task.prompt
                assert original in map(Path, task.meta["upstream_dirs"])
            findings = (ACCEPT if case in {"claims_only", "claims_off", "claims_no_session"}
                        or (resolved and len(generations) == 2) else REVISE)
            issues = [*findings["issues"]] if findings == ACCEPT else [*findings["issues"], *ACCEPT["issues"]]
            if "requires_plan_change" in task.output_schema["properties"]["issues"]["items"]["properties"]:
                if case != "unclassified" or len(audits) > 1:
                    issues = [{**row, "requires_plan_change": case == "design"} for row in issues]
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured={**findings, "issues": issues}, cost_usd=0)

        assert kind == "audit_repair" and task.agent_id == "worker"
        assert task.resume_session_id == "original-session" and task.budget_usd == (4 if case == "budget" else 5)
        assert task.meta["parent_task"] == generations[-1].task_id and not task.meta.get("workdir")
        assert task.output_schema is None and "Write the draft only." in task.prompt
        if len(generations) > 1:
            assert "Revised analysis." in task.context
        automated = initial_claim_problems
        assert ("add the column" in task.prompt) == (not automated or case == "claims_and_p1")
        assert "no external cohort" not in task.prompt
        assert ("Automated claim check findings:" in task.prompt) == automated
        if automated:
            assert "numeric prose needs a claim anchor" in task.prompt
            assert "bibliography PMID 87654321 has no accepted evidence" in task.prompt
            assert "[[claim:s1/c1]]" in task.prompt
        assert "say donor-level" not in task.prompt and artifact_contract_for(task)
        assert not task.meta.get("reference_dirs") and not task.meta.get("project_dirs")
        if case == "reference_drift":
            assert str(source) not in task.prompt and "staged copies" in task.prompt
        assert req["research_contract"]["audit_repair"]["status"] == "pending"
        assert [item["kind"] for item in hub.approvals] == ["research_plan"]
        if case == "interrupted":
            req["results"] = {"s1": generations[0].model_dump(mode="json")}
            raise asyncio.CancelledError
        first = generations[0]
        folder = tmp_path / f"repair{len(generations)}"
        folder.mkdir()
        ws = SimpleNamespace(dir=folder, task=task)
        _, unavailable = stage_inputs(ws, task.meta["upstream_artifacts"], tmp_path, [], [], 1_000_000, 1_000_000)
        assert not unavailable
        path = first.outputs[0]
        draft_path, = task.meta["required_outputs"]
        assert task.meta["outputs"] == [draft_path]
        assert (folder / "inputs" / "s1" / path.removeprefix("outputs/")).is_file()
        body = REPORT + "\n\nRevised analysis."
        if case == "claims_after_p1":
            body += bad_text
        if case == "replacement_table":
            body += f"\n\nThe [measured effect](steps/s1/{path}) was 0.99 [[claim:s1/c1]]."
        data = {draft_path: body.encode()}
        if case in {"ignored_ledger", "replacement_table"}:
            data[path] = b"effect\n0.99\n"
        (folder / "outputs").mkdir()
        for name, body in data.items():
            (folder / name).write_bytes(body)
        ledger = None
        if case == "ignored_ledger":
            ledger = copy.deepcopy(first.structured)
            ledger["method_changes"].append({"reason": "rewritten by repair"})
            ledger["claims"][0]["statement"] = "This substituted claim must never reach CP2."
        result = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=ledger,
                            text=f"Draft saved at {draft_path}",
                            session_id="other-session" if case == "new_session" else first.session_id,
                            workdir=str(folder), workdir_id=folder.name, outputs=list(data),
                            output_sha256={name: sha256(body).hexdigest() for name, body in data.items()},
                            cost_usd=4.01 if case == "budget" else 1)
        save_manifest(task, result)
        if case not in {"new_session", "budget", "claims_after_p1", "replacement_table"}:
            generations.append(result)
        return result

    hub.reply = reply
    if case == "interrupted":
        with pytest.raises(asyncio.CancelledError):
            await Orchestrator(hub).run_request("r")
        _interrupt(hub)
        await Orchestrator(hub).run_request("r", resume=True)
    else:
        await Orchestrator(hub).run_request("r")

    contract = req["research_contract"]
    assert contract["plan_sha256"] == plan_sha256(req["plan"])
    assert _kinds(hub).count("audit_repair") == attempts
    assert len(audits) == len(generations) + (case == "unclassified")
    latest = generations[-1]
    first = generations[0]
    assert req["results"]["s1"]["task_id"] == first.task_id
    assert req["results"]["s1"]["structured"] == first.structured
    assert req["results"]["s1"]["output_sha256"] == first.output_sha256
    if case in {"claims_after_p1", "replacement_table", "claims_no_session", "claims_off"}:
        approval_kinds = [row["kind"] for row in hub.approvals]
        assert approval_kinds == ["research_plan"] + (["research_evidence"] if case == "claims_off" else [])
        assert req["status"] == "failed" and req["outcome"] == "report_incomplete"
        check = rerun_report_check(req)
        assert check["same"] is True
        if case in {"claims_after_p1", "replacement_table"}:
            assert check["recorded"]["problems"] == []
            rejected = contract["audit_repair"]["rejected_problems"]
            expected = "numeric prose needs a claim anchor" if case == "claims_after_p1" else "0.99"
            assert any(expected in problem for problem in rejected)
            assert "analysis_draft" not in contract
            assert "Revised analysis." not in req["report"]
        else:
            assert any("numeric prose needs a claim anchor" in problem for problem in check["recorded"]["problems"])
            assert "bibliography PMID 87654321 has no accepted evidence linked to its anchors" in check["recorded"]["problems"]
        if status:
            assert contract["audit_repair"]["status"] == status
            assert "cp2" not in (contract.get("checkpoints") or {})
        return
    approved = hub.approvals[1]
    assert approved["kind"] == "research_evidence"
    detail = approved["detail"]
    audit = detail.get("independent_audit", {})
    assert audit.get("repair") == ({"attempts": attempts, "status": status} if status else None)
    if case != "audit_off":
        assert audit == contract["checkpoints"]["cp2"]["independent_audit"]
        assert audit["draft_sha256"] == sha256(detail["analysis_draft"].encode()).hexdigest()
        assert detail["results"]["s1"] == first.structured
        assert detail["artifact_sha256"]["s1/a1"] == first.output_sha256[first.outputs[0]]
        assert [x["priority"] for x in audit["issues"]] == (["P2"] if resolved else ["P3", "P1", "P2"])
        assert detail["analysis_draft"].endswith("Revised analysis.") == (resolved or case == "limit")
    assert (original / generations[0].outputs[0]).read_bytes() == b"effect\n0.25\n"
    if resolved or case == "limit":
        receipt = contract["analysis_draft"]
        assert receipt["parent_task"] == first.task_id
        assert receipt["result"]["task_id"] == latest.task_id
        assert receipt["result"]["outputs"] == [analysis_draft.for_request(req).path]
        if resolved:
            assert "Revised analysis." in req["report"]
        calls, approvals = len(hub.calls), len(hub.approvals)
        _interrupt(hub)
        await Orchestrator(hub).run_request("r", resume=True)
        assert hub.calls[calls:] == [] and hub.approvals[approvals:] == []


@pytest.mark.parametrize("option", [None, False, True])
async def test_audit_option_orders_checks_review_and_cp2(tmp_path, monkeypatch, option):
    from tests.test_research_report import ACCEPT, _kinds

    enabled = option is True
    hub = (_audit_hub if enabled else _draft_hub)(tmp_path, problem="table")
    req = hub.requests["r"]
    if option is False:
        req["research_options"]["audit_before_cp2"] = False
    hub.s.research.audit_before_cp2 = not enabled
    monkeypatch.setenv("LABHQ_AUDIT_BEFORE_CP2", "0" if enabled else "1")
    original = hub.request_approval

    async def approval(**kwargs):
        if kwargs["kind"] == "research_evidence":
            assert _kinds(hub) == ["plan", "step"] + (["review"] if enabled else [])
        return await original(**kwargs)

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    contract = req["research_contract"]
    assert contract["plan_sha256"] == plan_sha256(req["plan"])
    assert _kinds(hub) == ["plan", "step", "review"]
    detail = hub.approvals[1]["detail"]
    cp2 = contract["checkpoints"]["cp2"]
    assert ("independent_audit" in detail) is enabled
    assert ("independent_audit" in cp2) is enabled
    assert req["research_options"].get("audit_before_cp2") is option
    if enabled:
        card = detail["independent_audit"]
        assert card == cp2["independent_audit"]
        assert card["issues"] == ACCEPT["issues"] and card["verdict"] == "accept"
        assert card["checks"]["claims"] == {"anchors": 1, "problems": []}
        assert card["checks"]["table_consistency"]["findings"][0]["kind"] == "number"
        assert card["draft_sha256"] == sha256(detail["analysis_draft"].encode()).hexdigest()
        assert '"kind": "number"' in hub.calls[-1].prompt


@pytest.mark.parametrize("changed", [None, "artifact", "draft", "plan_receipt"])
async def test_pre_cp2_audit_reuse_requires_all_hashes(tmp_path, changed):
    from tests.test_research_report import _kinds

    hub = _audit_hub(tmp_path, problem="portable")
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    contract = req["research_contract"]
    old = copy.deepcopy(contract["checkpoints"]["cp2"]["independent_audit"])
    result = req["results"]["s1"]
    path = (result["outputs"][0] if changed == "artifact" else
            analysis_draft.for_request(req).path if changed == "draft" else None)
    if path:
        target = tmp_path / path
        data = target.read_bytes() + b"\n"
        target.write_bytes(data)
        result["output_sha256"][path] = sha256(data).hexdigest()
    if changed == "plan_receipt":
        contract["review"]["plan_sha256"] = "a" * 64
    calls, approvals = len(hub.calls), len(hub.approvals)
    _interrupt(hub)
    await Orchestrator(hub).run_request("r", resume=True)

    contract = req["research_contract"]
    assert _kinds(hub, calls) == (["review"] if changed else [])
    reopened = changed in {"artifact", "draft"}
    assert [card["kind"] for card in hub.approvals[approvals:]] == (["research_evidence"] if reopened else [])
    assert (contract["checkpoints"]["cp2"]["independent_audit"] != old) is reopened
    assert contract["review"]["plan_sha256"] == plan_sha256(req["plan"])


@pytest.mark.parametrize("failure", ["missing", "unparsed"])
async def test_cp2_waits_for_a_completed_audit(tmp_path, failure):
    from tests.test_research_report import _kinds

    hub = _audit_hub(tmp_path, problem=failure, malformed=failure == "unparsed")
    await Orchestrator(hub).run_request("r")

    assert [card["kind"] for card in hub.approvals] == ["research_plan"]
    assert _kinds(hub) == ["plan", "step"] + (["review", "review"] if failure == "unparsed" else [])
    req = hub.requests["r"]
    assert req["status"] == "failed" and req["research_contract"]["failure"]["stage"] == "independent_audit"


async def test_cached_audit_rejects_changed_file_with_old_hash(tmp_path):
    hub = _audit_hub(tmp_path, problem="portable")
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    path = req["results"]["s1"]["outputs"][0]
    (tmp_path / path).write_bytes(b"effect\n0.50\n")
    _interrupt(hub)
    await Orchestrator(hub).run_request("r", resume=True)

    assert req["status"] == "failed" and "Collected output changed:" in req["error"]
    assert [card["kind"] for card in hub.approvals] == ["research_plan", "research_evidence"]


async def test_pre_cp2_audit_keeps_pi_notes_in_review_and_reader_report(tmp_path):
    from tests.test_research_report import _kinds

    hub = _audit_hub(tmp_path)
    req = hub.requests["r"]
    earlier = "Use the same comparison as the earlier round."
    latest = "Limit the conclusion to the observed cohort."
    original_reply, original_approval = hub.reply, hub.request_approval

    async def reply(task):
        if task.meta["kind"] == "step":
            assert earlier in task.prompt
        if task.meta["kind"] == "review":
            assert earlier in task.prompt and latest not in task.prompt
        return await original_reply(task)

    async def approval(**kwargs):
        answer = await original_approval(**kwargs)
        if kwargs["kind"] == "research_plan":
            req["research_contract"]["rounds"] = [{
                "round": 1, "cp2": {"decision": "approved", "note": earlier},
            }]
        if kwargs["kind"] == "research_evidence":
            assert "승인 메모는 보고서 본문에 인용합니다" in kwargs["summary"]
            answer["note"] = latest
        return answer

    hub.reply, hub.request_approval = reply, approval
    await Orchestrator(hub).run_request("r")

    assert _kinds(hub) == ["plan", "step", "review"]
    assert req["outcome"] == "research_reported"
    assert f"## PI 승인 메모\n\n> {latest}" in req["report"]
    assert req["research_contract"]["checkpoints"]["cp2"]["note"] == latest
    assert latest not in hub.approvals[1]["detail"]["analysis_draft"]


async def test_cp2_revision_carries_prior_audit_p1_to_new_plan_and_review(tmp_path):
    from tests.test_research_report import APPROVE, REVISE, _kinds

    hub = _audit_hub(tmp_path)
    note = "Add a sensitivity check."
    p1 = next(issue for issue in REVISE["issues"] if issue["priority"] == "P1")
    decisions = iter([CP1, {"approved": False, "choice": "revise", "note": note}, CP1, APPROVE])
    original_reply = hub.reply

    async def reply(task):
        result = await original_reply(task)
        if task.meta["kind"] == "review" and len(hub.approvals) == 1:
            return result.model_copy(update={"structured": REVISE})
        if len(hub.approvals) >= 2 and task.meta["kind"] in {"plan", "review"}:
            assert note in task.prompt and p1["problem"] in task.prompt
        return result

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**next(decisions), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.reply, hub.request_approval = reply, approval
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["outcome"] == "research_reported", req.get("error")
    assert _kinds(hub) == ["plan", "step", "review", "plan", "step", "review"]
    assert [card["kind"] for card in hub.approvals] == [
        "research_plan", "research_evidence", "research_plan", "research_evidence",
    ]
    assert req["research_contract"]["continuation"]["p1_issues"] == [p1]
    assert req["research_contract"]["rounds"][0]["cp2"]["independent_audit"]["verdict"] == "revise"


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [None, {"compact_workflow": False}])
async def test_legacy_report_and_plan_hash_follow_frozen_options(tmp_path, saved):
    from tests.test_research_report import REPORT, _hub, _kinds

    compact = _draft_hub(tmp_path)
    await Orchestrator(compact).run_request("r")
    legacy = _hub()
    legacy.s.research.compact_workflow = True
    if saved is not None:
        legacy.requests["r"]["research_options"] = saved
    await Orchestrator(legacy).run_request("r")

    req = legacy.requests["r"]
    assert _kinds(legacy) == ["plan", "step", "review", "synthesis"]
    assert req["status"] == "done" and req["report"].startswith(REPORT)
    assert req["plan"] == compact.requests["r"]["plan"]
    assert req["research_contract"]["plan_sha256"] == compact.requests["r"]["research_contract"]["plan_sha256"]


def test_draft_owner_follows_the_frozen_dag_and_uses_a_new_path_per_round():
    plan = valid_plan(steps=3)
    preparation, analysis, validation = plan["steps"]
    preparation["phase"] = "evidence"
    analysis["depends_on"] = ["s1"]
    validation.update(phase="validation", depends_on=["s2"])
    plan["steps"] = [validation, analysis, preparation]
    req = {"plan": plan, "research_options": {"compact_workflow": True}, "research_contract": {"round": 1}}

    draft = analysis_draft.for_request(req)
    assert draft.step_id == "s2" and not draft.inline
    assert "Write the draft only." in draft.prompt() and "Write the draft only." in draft.prompt(repairing=True)
    req["research_contract"]["round"] = 2
    assert analysis_draft.for_request(req).path != draft.path
    assert list(analysis_draft.new_round_rerun(req, plan)) == ["s2"]
    analysis["phase"] = "evidence"
    assert analysis_draft.for_request(req).step_id == "s3" and analysis_draft.for_request(req).inline
    plan["branches"] = [{"id": "a"}]
    assert analysis_draft.for_request(req) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("shape", "resumable", "remote", "approve_cp2", "audit"), [
    ("validation_after_analysis", True, False, True, False),
    ("validation_after_analysis", True, True, True, False),
    ("parallel", True, False, True, False), ("parallel", False, False, True, False),
    ("parallel", True, False, False, False), ("validation_after_analysis", True, False, True, True),
])
async def test_draft_waits_for_every_step_and_keeps_the_analysis_session(tmp_path, shape, resumable, remote,
                                                                       approve_cp2, audit):
    from types import SimpleNamespace

    from labhq.runner import report_artifacts
    from labhq.runner.step_handoff import artifact_contract_for
    from labhq.runner.step_inputs import stage_inputs
    from tests.test_research_report import ACCEPT, REPORT, _hub, _kinds

    hub = _hub()
    req = hub.requests["r"]
    req["research_options"] = {"compact_workflow": True}
    if audit:
        req["research_options"]["audit_before_cp2"] = True
        hub.s.runner.workspace_root = str(tmp_path)
    hub.supports_resume = lambda agent_id: resumable and agent_id == "worker"
    saved = {}

    def save_request(rid):
        contract = hub.requests[rid].get("research_contract") or {}
        saved["cp2"] = copy.deepcopy((contract.get("checkpoints") or {}).get("cp2"))
        saved["draft"] = copy.deepcopy(contract.get("analysis_draft"))

    hub.save_request = save_request
    approve = hub.request_approval

    async def approval(**kwargs):
        decision = await approve(**kwargs)
        if kwargs["kind"] == "research_evidence" and not approve_cp2:
            decision.update(approved=False, choice="deny", note="Do not proceed")
        return decision

    hub.request_approval = approval
    received = {}
    if remote:
        hub.report_artifacts = received.get
    plan = valid_plan(steps=2)
    if shape == "validation_after_analysis":
        plan["steps"][1].update(phase="validation", depends_on=["s1"], outputs=list(plan["steps"][0]["outputs"]))
    base_reply = hub.reply
    later_finding = "Sensitivity test reproduced the estimate [[claim:s2/c2]]."
    old_figure = "![Earlier conclusion](steps/s1/outputs/graphical_abstract.svg)"

    async def reply(task):
        kind = task.meta["kind"]
        if kind == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=plan)
        if kind == "analysis_draft":
            assert task.resume_session_id == "analysis-s1"
            assert not task.meta.get("workdir")
            if audit:
                assert saved["cp2"] is None
            else:
                assert saved["cp2"] == req["research_contract"]["checkpoints"]["cp2"]
                assert saved["cp2"]["decision"] == "approved"
            assert "Sensitivity test reproduced the estimate" in task.context
            assert "### s1" in task.context and "### s2" in task.context
            assert "Available claim anchors (read their evidence before use):\n[[claim:s1/c1]]" in task.context
            assert "Available claim anchors (read their evidence before use):\n[[claim:s2/c2]]" in task.context
            [path] = task.meta["outputs"]
            target = tmp_path / "report" / path
            target.parent.mkdir(parents=True)
            assert artifact_contract_for(task)
            inputs = task.meta["upstream_artifacts"]
            assert set(inputs) == {"s1", "s2"}
            _, refused = stage_inputs(SimpleNamespace(task=task, dir=tmp_path / "report"),
                                      inputs, tmp_path, [], [], 1024, 4096)
            assert not refused
            for sid, row in inputs.items():
                table = row["outputs"][0].removeprefix("outputs/")
                assert f"inputs/{sid}/{table}" in task.context
                assert (tmp_path / "report" / "inputs" / sid / table).read_bytes() == sid.encode()
            binding = next(line for line in task.context.splitlines() if line.startswith("[[claim:s2/c2]] "))
            evidence_path = json.loads(binding.split(" ", 1)[1])["evidence"]["e2"]["artifact_path"]
            producer = "s1" if shape == "validation_after_analysis" else "s2"
            assert evidence_path == f"inputs/{producer}/{inputs[producer]['outputs'][0].removeprefix('outputs/')}"
            assert (tmp_path / "report" / evidence_path).read_bytes() == producer.encode()
            data = f"{REPORT}\n\n{old_figure}\n\n{later_finding}".encode("utf-8")
            if audit:
                data = b"\xef\xbb\xbf" + data
            target.write_bytes(data)
            result = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, workdir=str(tmp_path / "report"),
                                session_id=task.resume_session_id, text=f"Draft saved at {path}", outputs=[path],
                                output_sha256={path: sha256(data).hexdigest()})
            if remote:
                payload = report_artifacts.collect(tmp_path / "report", task.meta, result)
                received[task.id] = report_artifacts.receive(payload, task.meta, result)
                target.unlink()
            return result
        if kind != "step":
            if kind == "review":
                receipt = req["research_contract"]["analysis_draft"]
                assert saved["draft"] == receipt
                assert receipt["result"]["session_id"] == "analysis-s1"
                assert receipt["path"] in receipt["result"]["output_sha256"]
                if audit:
                    assert saved["cp2"] is None and [card["kind"] for card in hub.approvals] == ["research_plan"]
                    assert receipt["result"]["output_sha256"][receipt["path"]] in task.prompt
                    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=ACCEPT)
            return await base_reply(task)
        assert not task.meta["optional_outputs"]
        sid = task.meta["step_id"]
        ledger = _cp2_result(hub, task)
        [path] = task.meta["required_outputs"]
        if sid == "s2":
            ledger["claims"][0].update(id="c2", statement="Sensitivity test reproduced the estimate.")
            ledger["links"][0]["claim_id"] = "c2"
            ledger["evidence"][0].update(id="e2", slots=["e2"])
            ledger["links"][0]["evidence_id"] = "e2"
        ledger["artifact_refs"][0]["path"] = path
        if sid == "s2" and shape == "validation_after_analysis":
            ledger["artifact_refs"][0]["path"] = f"s1/{path}" if audit else str(tmp_path / "s1" / path)
        workdir = tmp_path / sid
        (workdir / path).parent.mkdir(parents=True)
        (workdir / path).write_bytes(sid.encode())
        hashes = {path: sha256(sid.encode()).hexdigest()}
        if sid == "s1":
            figure = "outputs/graphical_abstract.svg"
            (workdir / figure).write_bytes(b"<svg/>")
            hashes[figure] = sha256(b"<svg/>").hexdigest()
        (workdir / "manifest.json").write_text(json.dumps({
            "task": task.model_dump(mode="json"), "runs": {task.id: {"ok": True}},
        }), encoding="utf-8")
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=ledger,
                          workdir=str(workdir), workdir_id=sid if audit else None,
                          outputs=list(hashes), output_sha256=hashes,
                          session_id=f"analysis-{sid}")

    hub.reply = reply
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    if not approve_cp2:
        assert _kinds(hub) == ["plan", "step", "step"]
        assert req["outcome"] == "evidence_rejected" and req["status"] == "failed"
        assert saved["cp2"]["choice"] == "deny" and saved["draft"] is None
    elif resumable:
        assert _kinds(hub) == ["plan", "step", "step", "analysis_draft", "review"], req.get("error")
        assert req["status"] == "done" and later_finding in req["report"]
        assert old_figure not in req["report"] and not req["report_package"]["figures"]
        assert "outputs/graphical_abstract.svg" in req["results"]["s1"]["outputs"]
        assert req["research_contract"]["report_check"] == {"anchors": 2, "problems": []}
        report = req["report"]
        receipt = req["research_contract"]["analysis_draft"]
        if audit:
            card = hub.approvals[1]["detail"]
            assert card["analysis_draft"].startswith("\ufeff") and old_figure in card["analysis_draft"]
            draft_hash = sha256(card["analysis_draft"].encode("utf-8")).hexdigest()
            assert card["independent_audit"]["draft_sha256"] == draft_hash
            assert card["independent_audit"] == saved["cp2"]["independent_audit"]
        if shape == "parallel":
            (tmp_path / "report" / receipt["path"]).write_bytes(b"Changed after the review.")
        cp2_receipt = saved["cp2"]
        req["research_contract"]["checkpoints"]["cp2"] = json.loads(json.dumps(cp2_receipt))
        req["research_contract"]["analysis_draft"] = json.loads(json.dumps(saved["draft"]))
        _interrupt(hub)
        calls, approvals = len(hub.calls), len(hub.approvals)
        hub.supports_resume = lambda _: False
        await Orchestrator(hub).run_request("r", resume=True)
        req = hub.requests["r"]
        assert _kinds(hub, calls) == [] and hub.approvals[approvals:] == []
        assert req["research_contract"]["checkpoints"]["cp2"] == cp2_receipt
        if shape == "parallel":
            assert req["status"] == "failed" and "differs from the collected SHA-256" in req["error"]
        else:
            assert req["status"] == "done" and req["report"] == report
        if remote:
            received[receipt["result"]["task_id"]][receipt["path"]] = "Y2hhbmdlZA=="
            _interrupt(hub)
            await Orchestrator(hub).run_request("r", resume=True)
            assert _kinds(hub, calls) == []
            assert req["status"] == "failed" and "differs from the collected SHA-256" in req["error"]
    else:
        assert _kinds(hub) == ["plan", "step", "step"]
        assert req["status"] == "failed" and "cannot be resumed" in req["error"]
