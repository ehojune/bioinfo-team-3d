"""Research lane: continue after a review "revise" through a new CP1 that reuses unchanged steps (#90, #58)."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import sys
from pathlib import Path

import pytest

from labhq.evidence.audit import ledger_binding_problems, research_ledgers
from labhq.gateway.server import Hub, SavedResults
from labhq.models import Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.research import continuation
from labhq.research import semantics as sem
from labhq.research.contract import plan_sha256, validate_research_result
from labhq.util import output_relpath
from tests.test_research_cp2 import CP1, _settings
from tests.test_research_protocol import PACK, MiniHub, valid_pack_values, valid_plan

APPROVE = {"approved": True, "choice": "approve", "note": ""}
CONTINUE = {"approved": True, "note": ""}
P1 = {"step_id": "s2", "claim_id": "c2", "priority": "P1", "category": "unsupported_by_artifact",
      "evidence_quote": "donor-level effect", "problem": "the s2 table has no effect column",
      "request": "add the effect column to s2"}
REVISE = {"verdict": "revise", "issues": [P1]}
ACCEPT = {"verdict": "accept", "issues": []}
REPORT = "The donor-level effect is present [[claim:s1/c1]] and holds in s2 [[claim:s2/c2]]."
FIXED = "work 2 with the effect column"
DAY = "2026-10-06"


def _plan(round_no: int, extra: dict | None = None) -> dict:
    plan = {**valid_plan(steps=2), **(extra or {})}
    plan["steps"][1]["depends_on"] = ["s1"]  # s2 reads s1, so the run order is fixed
    if round_no >= 2:
        plan["steps"][1]["instruction"] = FIXED
    return plan


def _result(hub, task, step) -> dict:
    return _ledger(hub.requests["r"]["research_contract"]["plan_sha256"], step)


def _ledger(plan_hash: str, step: dict) -> dict:
    claim, slot = step["claim_ids"][0], step["evidence_slots"][0]["id"]
    return {
        "schema_version": 2, "plan_sha256": plan_hash,
        "step_id": step["id"],
        "claims": [{"id": claim, "revision": 1, "statement": f"The donor-level effect is present in {step['id']}.",
                    "scope": "the frozen public cohort", "kind": "finding", "status": "supported",
                    "importance": "major", "status_reason": "the frozen comparison supports it",
                    "limitations": ["one cohort"]}],
        "evidence": [{"id": slot, "kind": "experimental", "observation": "donor-level effect",
                      "status": "observed", "source": {"artifact_id": "a1", "locator": "row 1"},
                      "directness": "direct", "source_level": "primary", "independence_group": "cohort1",
                      "assessment_reason": "the planned donor comparison", "slots": [slot]}],
        "links": [{"claim_id": claim, "claim_revision": 1, "evidence_id": slot, "relation": "supports",
                   "rationale": "the row reports the planned comparison"}],
        "artifact_refs": [{"artifact_id": "a1", "path": step["outputs"][0]}],
        "not_established": [], "failures": [], "method_changes": [],
    }


def _hub(tmp_path: Path, decisions: list, *, reviews=None, limit: int = 2, mutate=None, fail_round2_plan=False,
         plan_extra: dict | None = None):
    settings = _settings()
    settings.orchestrator.reviewer_agent = "sci_reviewer"
    settings.runner.workspace_root = str(tmp_path / "ws")
    settings.research.revise_continuations = limit
    reviews = list(reviews or [REVISE, ACCEPT])
    holder: dict = {}

    async def reply(task):
        hub = holder["hub"]
        kind = task.meta["kind"]
        assert len(hub.calls) < 30, "runaway continuation"
        if kind == "plan":
            round_no = 2 if "Continuation (research round 2)" in task.prompt else 1
            if round_no == 2 and fail_round2_plan:
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="planner crashed")
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                              structured=_plan(round_no, plan_extra))
        if kind == "step":
            step = next(s for s in hub.requests["r"]["plan"]["steps"] if s["id"] == task.meta["step_id"])
            relative = output_relpath(step["outputs"][0])
            workdir_id = f"wd_{task.id}"
            workdir = tmp_path / "ws" / DAY / workdir_id
            body = f"gene\teffect\n{step['id']}\t{step['instruction']}\n".encode()
            (workdir / relative).parent.mkdir(parents=True, exist_ok=True)
            (workdir / relative).write_bytes(body)
            structured = _result(hub, task, step)
            if mutate:
                mutate(task, step, structured)
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=structured,
                              outputs=[relative], output_sha256={relative: hashlib.sha256(body).hexdigest()},
                              workdir=str(workdir), workdir_id=workdir_id)
        if kind == "review":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=reviews.pop(0))
        if kind == "synthesis":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text=REPORT)
        raise AssertionError(f"unexpected task kind {kind}")

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                                  text="compare conditions")
    hub.agents["sci_reviewer"] = {"id": "sci_reviewer", "name": "sci_reviewer", "role": "test", "engine": "mock"}
    hub.clear_step_jobs = lambda rid, sid: None
    hub.result_map = lambda rid: SavedResults(hub, rid)  # results persist as in the real gateway
    hub.hooks = {}

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        hook = hub.hooks.get(len(hub.approvals))
        if hook:
            hook()
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    return hub


def _steps(hub, start=0):
    return [task.meta.get("step_id") or task.meta["kind"] for task in hub.calls[start:]]


def _kinds(hub):
    return [approval["kind"] for approval in hub.approvals]


@pytest.mark.asyncio
async def test_revise_continues_through_a_new_cp1_and_runs_only_the_changed_step(tmp_path):
    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1, APPROVE])
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    contract = req["research_contract"]
    assert _kinds(hub) == ["research_plan", "research_evidence", "research_continue", "research_plan",
                           "research_evidence"]
    assert _steps(hub) == ["plan", "s1", "s2", "review", "plan", "s2", "review", "synthesis"]
    assert req["outcome"] == "research_reported" and req["status"] == "done"

    first, = contract["rounds"]
    assert first["round"] == 1 and first["review"]["verdict"] == "revise"
    assert contract["round"] == 2 and contract["plan_sha256"] != first["plan_sha256"]
    assert contract["approval"]["target_sha256"] == contract["plan_sha256"]  # the new CP1 froze the new hash
    # s1 kept its first-round task and files; only the changed s2 ran again.
    assert req["results"]["s1"]["task_id"] == first["results"]["s1"]["task_id"]
    assert req["results"]["s2"]["task_id"] != first["results"]["s2"]["task_id"]
    carried = contract["continuation"]
    assert carried["reuse"] == ["s1"] and carried["rerun"] == {"s2": "spec changed"}
    assert carried["verified"] and carried["refused_reuse"] == []

    card = hub.approvals[2]
    assert card["detail"]["p1_issues"] == [P1] and card["detail"]["plan_sha256"] == first["plan_sha256"]
    cp1 = hub.approvals[3]
    assert cp1["detail"]["continuation"]["reuse"] == ["s1"] and "재사용: s1" in cp1["summary"]
    assert hub.approvals[4]["detail"]["continuation"]["reuse"] == ["s1"]  # the CP2 card names the reuse

    plan_prompt = hub.calls[4].prompt
    assert "Continuation (research round 2)" in plan_prompt and P1["problem"] in plan_prompt
    assert first["plan_sha256"] in plan_prompt
    assert P1["problem"] in hub.calls[6].prompt  # the second reviewer checks the earlier P1 issue
    assert all(task.meta.get("research_round") == 2 for task in hub.calls[4:])
    assert all("research_round" not in task.meta for task in hub.calls[:4])
    assert "reused from plan" in req["report_appendix"]


@pytest.mark.asyncio
async def test_every_cp2_result_of_a_continuation_validates_under_the_current_plan(tmp_path):
    """The reused s1 ran under round 1's plan. CP2, the stored results, the report check, `labhq verify` and the
    semantics records all read it as a result of the plan the new CP1 froze, while where it ran stays recorded
    (PR #448 review)."""
    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1, APPROVE])
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    contract = req["research_contract"]
    first, = contract["rounds"]
    assert req["outcome"] == "research_reported" and contract["continuation"]["reuse"] == ["s1"]
    cp2 = hub.approvals[4]["detail"]
    assert cp2["plan_sha256"] == contract["plan_sha256"] == plan_sha256(req["plan"]) != first["plan_sha256"]
    assert set(cp2["results"]) == {"s1", "s2"}
    for ledger in [*cp2["results"].values(), *research_ledgers(req).values(),
                   *(result["structured"] for result in req["results"].values())]:
        assert validate_research_result(ledger, plan=req["plan"]).plan_sha256 == contract["plan_sha256"]
    assert ledger_binding_problems(req) == []
    reused_from = {"round": 1, "plan_sha256": first["plan_sha256"], "task_id": first["results"]["s1"]["task_id"]}
    assert contract["continuation"]["reused_from"] == {"s1": reused_from} == cp2["continuation"]["reused_from"]
    assert first["results"]["s1"]["structured"]["plan_sha256"] == first["plan_sha256"]  # the archive keeps it

    # Task rows as the gateway stores them: round 1's s1 and s2 and round 2's s2 each read under their own plan.
    stored = {**{sid: first["results"][sid] for sid in ("s1", "s2")}, "s2'": req["results"]["s2"]}
    calls = {task.id: task for task in hub.calls}
    rows = {result["task_id"]: {"request_id": "r", "step_id": calls[result["task_id"]].meta["step_id"],
                                "kind": "step", "payload": calls[result["task_id"]].model_dump(mode="json"),
                                "result": result} for result in stored.values()}
    records, invalid = sem.records_from_rows({"r": req}, rows)
    assert invalid == 0 and set(records.results) == set(rows)


@pytest.mark.asyncio
async def test_a_changed_pack_definition_reruns_every_step(tmp_path):
    """A custom pack edited between rounds keeps its ``id@version`` but gets a new sha256: nothing judged under the
    old rules is reused (PR #448 review)."""
    pack_dir = tmp_path / "packs"
    pack_dir.mkdir()
    source = (Path(continuation.__file__).with_name("packs") / "single_cell_de_v3.yaml").read_text(encoding="utf-8")
    source = source.replace('version: "3"', 'version: "9"', 1)
    (pack_dir / "custom.yaml").write_text(source, encoding="utf-8")
    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1, APPROVE],
               plan_extra={"pack_values": {"single_cell_de@9": valid_pack_values()[PACK]}})
    hub.s.research.pack_dirs = [str(pack_dir)]
    hub.s.research.active_packs = ["single_cell_de@9"]

    def edit():  # the continuation card is up: someone edits the custom pack under the same id@version
        (pack_dir / "custom.yaml").write_text(source.replace("title: ", "title: Edited ", 1), encoding="utf-8")

    hub.hooks[3] = edit
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    contract = req["research_contract"]
    first, = contract["rounds"]
    edited = contract["pack_snapshot"]["single_cell_de@9"]
    assert first["pack_snapshot"]["single_cell_de@9"] != edited
    assert [ref["sha256"] for ref in req["plan"]["protocol"]["packs"]] == [edited]
    assert _steps(hub) == ["plan", "s1", "s2", "review", "plan", "s1", "s2", "review", "synthesis"]
    carried = contract["continuation"]
    assert carried["reuse"] == [] and carried["rerun"] == {"s1": "domain pack snapshot changed",
                                                           "s2": "spec changed"}
    assert req["outcome"] == "research_reported"


@pytest.mark.asyncio
async def test_reuse_is_refused_when_a_reused_output_changed_after_the_first_round(tmp_path):
    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1, APPROVE])

    def tamper():  # the new CP1 card is up: someone edits s1's recorded output
        saved = hub.requests["r"]["results"]["s1"]
        Path(saved["workdir"], saved["outputs"][0]).write_bytes(b"edited by hand\n")

    hub.hooks[4] = tamper
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    carried = req["research_contract"]["continuation"]
    assert _steps(hub) == ["plan", "s1", "s2", "review", "plan", "s1", "s2", "review", "synthesis"]
    assert carried["reuse"] == [] and carried["rerun"]["s1"].startswith("output hash check:")
    assert "mismatch" in carried["refused_reuse"][0]["reason"]
    assert req["results"]["s1"]["task_id"] != req["research_contract"]["rounds"][0]["results"]["s1"]["task_id"]
    assert req["outcome"] == "research_reported"


class _Restart(BaseException):
    """The gateway process dies here: nothing after it runs, and saved state is what a restart reads."""


@pytest.mark.asyncio
@pytest.mark.parametrize(("die_at", "after"), [
    (3, ["plan", "s2", "review", "synthesis"]),  # on the continuation card
    (4, ["s2", "review", "synthesis"]),  # on the new CP1 card, after the new plan was adopted
])
async def test_restart_in_the_middle_of_a_continuation(tmp_path, die_at, after):
    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1, APPROVE])

    def die():
        raise _Restart()

    hub.hooks[die_at] = die
    with pytest.raises(_Restart):
        await Orchestrator(hub).run_request("r")
    hub.hooks.clear()
    hub.approvals.pop()  # the card that was up when the process died is asked again
    calls = len(hub.calls)
    hub.requests["r"]["status"] = "interrupted"
    await Orchestrator(hub).run_request("r", resume=True)

    req = hub.requests["r"]
    assert _steps(hub, calls) == after  # s1 and the first review never run again
    assert req["outcome"] == "research_reported"
    assert len(req["research_contract"]["rounds"]) == 1
    assert req["research_contract"]["continuation"]["reuse"] == ["s1"]
    # The reused s1 came back from the restart bound to the plan that ran, as on the path without a restart.
    for result in req["results"].values():
        validate_research_result(result["structured"], plan=req["plan"])
    assert ledger_binding_problems(req) == []


@pytest.mark.asyncio
async def test_declining_the_continuation_ends_the_request_as_before(tmp_path):
    hub = _hub(tmp_path, [CP1, APPROVE, {"approved": False, "note": "stop here"}])
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _steps(hub) == ["plan", "s1", "s2", "review"]
    assert req["outcome"] == "research_review_revise" and req["status"] == "failed"
    assert "rounds" not in req["research_contract"] and req["research_contract"].get("round") is None
    assert req["research_contract"]["continue_decision"]["decision"] == "declined"
    assert "PI가 새 CP1로 이어 가기를 거절했습니다." in req["report"] + req.get("report_appendix", "")


@pytest.mark.asyncio
async def test_no_continuation_card_past_the_limit(tmp_path):
    hub = _hub(tmp_path, [CP1, APPROVE], limit=0)
    await Orchestrator(hub).run_request("r")

    assert _kinds(hub) == ["research_plan", "research_evidence"]
    assert hub.requests["r"]["outcome"] == "research_review_revise"


def _round_one(plan: dict) -> dict:
    """A round archive whose steps all completed with ledgers bound to ``plan``."""
    return {"round": 1, "plan": plan, "plan_sha256": plan_sha256(plan), "pack_snapshot": {},
            "results": {step["id"]: {"ok": True, "task_id": f"t-{step['id']}", "agent_id": "worker",
                                     "structured": _ledger(plan_sha256(plan), step)} for step in plan["steps"]}}


def test_split_reuse_reruns_changed_new_named_and_downstream_steps():
    old = valid_plan(steps=3)
    old["steps"][2]["depends_on"] = ["s1"]
    results = _round_one(old)["results"]
    same = copy.deepcopy(old)
    assert continuation.split_reuse(old, same, results, []) == (["s1", "s2", "s3"], {})

    new = copy.deepcopy(old)
    new["steps"][0]["instruction"] = "changed"
    new["steps"].append({**copy.deepcopy(new["steps"][1]), "id": "s4"})
    assert continuation.split_reuse(old, new, results, [{"step_id": "s2"}]) == (
        [], {"s1": "spec changed", "s2": "named by a P1 review issue", "s3": "upstream re-runs: s1",
             "s4": "new step"})
    assert continuation.split_reuse(old, same, {**results, "s2": {"ok": False}}, [])[1] == {
        "s2": "no completed result in the previous round"}
    protocol = copy.deepcopy(old)
    protocol["protocol"]["primary_metrics"] = ["odds ratio"]
    assert continuation.split_reuse(old, protocol, results, [])[0] == []
    assert continuation.with_dependents(old, {"s1"}) == {"s1", "s3"}


def test_carry_over_rebinds_each_reused_result_to_the_new_plan_and_keeps_where_it_ran():
    old = valid_plan(steps=3)
    old["steps"][2]["depends_on"] = ["s1"]
    previous = _round_one(old)
    new = copy.deepcopy(old)
    new["steps"][1]["instruction"] = "changed"
    carry = continuation.carry_over(previous, new, {}, [])
    assert carry.reuse == ["s1", "s3"] and carry.rerun == {"s2": "spec changed"}
    for sid, result in carry.results.items():
        assert result["structured"]["plan_sha256"] == plan_sha256(new) != plan_sha256(old)
        validate_research_result(result["structured"], plan=new)  # CP2 reads it under the plan that runs
        assert previous["results"][sid]["structured"]["plan_sha256"] == plan_sha256(old)  # the archive is untouched
    assert carry.reused_from["s1"] == {"round": 1, "plan_sha256": plan_sha256(old), "task_id": "t-s1"}

    # A step reused twice still names the round that ran it.
    second = {"round": 2, "plan": new, "plan_sha256": plan_sha256(new), "pack_snapshot": {},
              "results": carry.results, "continuation": {"reused_from": carry.reused_from}}
    assert continuation.carry_over(second, copy.deepcopy(new), {}, []).reused_from["s1"] == carry.reused_from["s1"]

    # A result that does not validate under the new plan is not reused, nor is anything below it.
    broken = copy.deepcopy(previous)
    broken["results"]["s1"]["structured"]["claims"] = []
    carry = continuation.carry_over(broken, new, {}, [])
    assert carry.reuse == [] and carry.rerun["s1"].startswith("result does not bind to the new plan")
    assert carry.rerun["s3"] == "upstream re-runs: s1"


def test_carry_over_reruns_every_step_when_a_pack_snapshot_changed():
    """The same ``id@version`` with another definition hash (an edited custom pack, a reinstall) re-runs all steps:
    a step does not declare which pack rule its result rests on (PR #448 review)."""
    ref = {"id": "custom_pack", "version": "1", "sha256": "a" * 64}
    old = valid_plan(packs=[ref], steps=2)
    previous = {**_round_one(old), "pack_snapshot": {"custom_pack@1": "a" * 64}}
    assert continuation.carry_over(previous, copy.deepcopy(old), {"custom_pack@1": "a" * 64}, []).reuse == ["s1", "s2"]

    edited = valid_plan(packs=[{**ref, "sha256": "b" * 64}], steps=2)
    carry = continuation.carry_over(previous, edited, {"custom_pack@1": "b" * 64}, [])
    assert carry.reuse == [] and set(carry.rerun.values()) == {"domain pack snapshot changed"}
    # The contract snapshot alone counts too, and an archive without one is not trusted.
    assert continuation.carry_over(previous, copy.deepcopy(old), {"custom_pack@1": "c" * 64}, []).reuse == []
    assert continuation.carry_over({**previous, "pack_snapshot": None}, copy.deepcopy(old),
                                   {"custom_pack@1": "a" * 64}, []).reuse == []


def test_restart_recovery_never_hands_a_continuation_task_an_earlier_rounds_result():
    entry = {"request_id": "r", "kind": "step", "step_id": "s2", "revision": 0, "parse_attempt": 0,
             "parent_task": None, "payload": {"meta": {"kind": "step", "step_id": "s2"}}}
    first = Task(agent_id="worker", request_id="r", prompt="x", meta={"kind": "step", "step_id": "s2"})
    second = Task(agent_id="worker", request_id="r", prompt="x",
                  meta={"kind": "step", "step_id": "s2", "research_round": 2})
    assert Hub._matches_recovery(first, entry)
    assert not Hub._matches_recovery(second, entry)
    assert Hub._matches_recovery(second, {**entry, "payload": {"meta": {**second.meta}}})


@pytest.mark.asyncio
async def test_an_identical_continuation_plan_still_needs_a_new_cp1_cp2_and_review(tmp_path, monkeypatch):
    """A P1 execution mistake in an unchanged step: the CSO returns the revised plan byte for byte (PR #448 review).
    Its hash equals round 1's, yet the round-1 approval, CP2 receipt, review and continue decision are not reused."""
    monkeypatch.setattr(sys.modules[__name__], "FIXED", valid_plan(steps=2)["steps"][1]["instruction"])
    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1, APPROVE], reviews=[REVISE, REVISE], limit=1)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    contract = req["research_contract"]
    assert _kinds(hub) == ["research_plan", "research_evidence", "research_continue", "research_plan",
                           "research_evidence"]
    assert _steps(hub) == ["plan", "s1", "s2", "review", "plan", "s2", "review"]
    assert contract["plan_sha256"] == contract["rounds"][0]["plan_sha256"]
    assert contract["continuation"]["rerun"] == {"s2": "named by a P1 review issue"}
    assert contract["approval"]["approval_id"] == "a4" and contract["checkpoints"]["cp2"]["approval_id"] == "a5"
    # The second revise meets the limit, so the request ends instead of continuing on round 1's decision.
    assert req["outcome"] == "research_review_revise" and len(contract["rounds"]) == 1
    assert "상한" in req["report"] + req.get("report_appendix", "")


@pytest.mark.asyncio
async def test_a_refused_reuse_drops_the_steps_earlier_pi_decision(tmp_path):
    """s1 answered a blocking question in round 1, then its file changed: it re-runs fresh, not as the round-1
    session in the folder whose file changed (PR #448 review)."""
    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1, APPROVE])
    old: dict = {}

    def tamper():
        req = hub.requests["r"]
        saved = req["results"]["s1"]
        old["workdir"] = saved["workdir"]
        req["step_decisions"] = {"s1": {"answer": "b", "question": "which cohort?", "session_id": "sess-1",
                                        "workdir": saved["workdir"], "previous_result": saved}}
        Path(saved["workdir"], saved["outputs"][0]).write_bytes(b"edited by hand\n")

    hub.hooks[4] = tamper
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    rerun = [task for task in hub.calls[4:] if task.meta.get("step_id") == "s1"]
    assert len(rerun) == 1 and rerun[0].meta.get("workdir") != old["workdir"]
    assert rerun[0].resume_session_id is None and "which cohort?" not in rerun[0].prompt
    assert "s1" not in (req.get("step_decisions") or {})
    assert req["outcome"] == "research_reported"


@pytest.mark.asyncio
@pytest.mark.parametrize(("decisions", "fail_plan", "why"), [
    ([CP1, APPROVE, CONTINUE, {"approved": False, "note": "no"}], False, "plan_rejected"),
    ([CP1, APPROVE, CONTINUE], True, "계획 실패"),
])
async def test_a_continuation_that_ends_before_dispatch_keeps_the_revise_record(tmp_path, decisions, fail_plan,
                                                                               why):
    hub = _hub(tmp_path, decisions, fail_round2_plan=fail_plan)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    contract = req["research_contract"]
    first, = contract["rounds"]
    assert req["outcome"] == "research_review_revise" and req["status"] == "failed"
    assert {sid: req["results"][sid]["task_id"] for sid in ("s1", "s2")} == {
        sid: first["results"][sid]["task_id"] for sid in ("s1", "s2")}
    assert P1["problem"] in req["report"] and "이어 가기 2차가 단계 실행 전에 끝났습니다" in req["report"]
    carried = contract["continuation"]
    ended = carried["ended_before_dispatch"]
    assert why in ended["outcome"] + ended["reason"]
    assert _steps(hub)[-1] == "plan"  # no step of round 2 ran
    # A failed plan task is kept for diagnosis, never listed as a step result (PR #448 review).
    assert set(req["results"]) == {"s1", "s2"}
    if fail_plan:
        assert "plan" in ended["task_results"]

    # The record pairs round 1's results with round 1's frozen plan, approval, packs and CP2 receipt, not with a
    # draft that never ran or was approved (PR #448 review).
    assert req["plan"] == first["plan"] and contract["plan_sha256"] == first["plan_sha256"] == plan_sha256(req["plan"])
    assert contract["approval"] == first["approval"] and contract["approval"]["status"] == "approved"
    assert contract["checkpoints"]["cp2"] == first["cp2"] and contract["review"] == first["review"]
    assert contract["pack_snapshot"] == first["pack_snapshot"] and contract["round"] == 1
    for sid in ("s1", "s2"):
        validate_research_result(req["results"][sid]["structured"], plan=req["plan"])
    assert ledger_binding_problems(req) == []
    assert [step["instruction"] for step in req["plan"]["steps"]] == ["work 1", "work 2"]
    if why == "plan_rejected":  # the declined draft is kept apart, with the CP1 receipt that declined it
        draft = carried["declined_plan"]
        assert draft["plan"]["steps"][1]["instruction"] == FIXED
        assert draft["plan_sha256"] == plan_sha256(draft["plan"]) != first["plan_sha256"]
        assert draft["approval"]["status"] == "rejected" and draft["approval"]["target_sha256"] == draft["plan_sha256"]
    else:  # the CSO never returned a plan, so there is no draft to keep
        assert "declined_plan" not in carried


@pytest.mark.asyncio
async def test_an_error_before_dispatch_puts_the_previous_round_back(tmp_path):
    """An error while the new CP1 card is up ends the request as the revise it continued, with round 1's plan."""
    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1])

    def broken():
        raise RuntimeError("approval store unavailable")

    hub.hooks[4] = broken
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    contract = req["research_contract"]
    first, = contract["rounds"]
    assert req["outcome"] == "research_review_revise" and req["status"] == "failed"
    assert "approval store unavailable" in req["error"] and P1["problem"] in req["report"]
    assert req["plan"] == first["plan"] and contract["plan_sha256"] == first["plan_sha256"]
    assert contract["approval"] == first["approval"] and contract["checkpoints"]["cp2"] == first["cp2"]
    assert {sid: result["task_id"] for sid, result in req["results"].items()} == {
        sid: result["task_id"] for sid, result in first["results"].items()}
    assert ledger_binding_problems(req) == []
    assert contract["continuation"]["declined_plan"]["plan"]["steps"][1]["instruction"] == FIXED


@pytest.mark.asyncio
async def test_a_reused_step_keeps_its_salvaged_rows_at_the_next_cp2(tmp_path):
    def broken_row(task, step, structured):  # round 1's s1 carries one evidence row that salvage refuses
        if step["id"] == "s1" and "research_round" not in task.meta:
            structured["evidence"].append({
                "id": "bad", "kind": "observation", "observation": "cross-check", "status": "observed",
                "source": {"uri": "https://example.org/record", "accessed_at": "not-a-date", "locator": "table 1"},
                "directness": "indirect", "source_level": "primary", "independence_group": "crosscheck",
                "assessment_reason": "an independent cross-check", "slots": []})

    hub = _hub(tmp_path, [CP1, APPROVE, CONTINUE, CP1, APPROVE], mutate=broken_row)
    hub.s.research.result_corrections = 0
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["research_contract"]["continuation"]["reuse"] == ["s1"]
    first_card, second_card = hub.approvals[1], hub.approvals[4]
    refused = [(row["step_id"], row["row_id"]) for row in first_card["detail"]["refused_rows"]]
    assert ("s1", "bad") in refused
    assert [(row["step_id"], row["row_id"]) for row in second_card["detail"]["refused_rows"]] == refused
    assert req["research_contract"]["checkpoints"]["cp2"]["refused_rows"] == second_card["detail"]["refused_rows"]
    assert req["outcome"] == "research_reported"


def test_a_changed_hypothesis_re_runs_every_step_since_steps_are_dispatched_with_it():
    """The step prompt carries the CP1 hypotheses (web trial 2026-10-08), so a result made under other wording is not
    a result of the new plan."""
    old = valid_plan(steps=2)
    previous = _round_one(old)
    new = copy.deepcopy(old)
    new["brief"]["null_or_alternatives"] = [*new["brief"]["null_or_alternatives"], "A3: a new alternative"]
    carry = continuation.carry_over(previous, new, {}, [])
    assert carry.reuse == [] and set(carry.rerun.values()) == {"question, scope, hypotheses, protocol or pack values changed"}
    assert continuation.carry_over(previous, copy.deepcopy(old), {}, []).reuse == ["s1", "s2"]
