"""After CP2 approval (#90, #58 ③⑤): one research review, a CSO report whose conclusions name ledger claims,
and a machine check of those claim anchors."""

import pytest

from labhq.evidence.audit import rerun_report_check
from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator
from tests.test_research_cp2 import CP1, _interrupt, _settings
from tests.test_research_protocol import MiniHub, _cp2_result, valid_plan

APPROVE = {"approved": True, "choice": "approve", "note": ""}
ACCEPT = {"verdict": "accept", "issues": [
    {"step_id": "s1", "claim_id": "c1", "priority": "P2", "category": "no_comparator",
     "evidence_quote": "donor-level effect", "problem": "no external cohort", "request": "state the single cohort"}]}
REVISE = {"verdict": "revise", "issues": [
    {"step_id": "s1", "claim_id": "", "priority": "P3", "category": "other", "evidence_quote": "row 1",
     "problem": "wording", "request": "say donor-level"},
    {"step_id": "s1", "claim_id": "c1", "priority": "P1", "category": "unsupported_by_artifact",
     "evidence_quote": "donor-level effect", "problem": "the file has no effect column", "request": "add the column"}]}
REPORT = "The donor-level effect is present [[claim:s1/c1]]."
FAILED_LOOKUP = {"id": "e2", "kind": "database_annotation", "observation": "GEO lookup for a replication cohort",
                 "status": "failed", "status_detail": "the GEO query timed out",
                 "source": {"uri": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE1",
                            "accessed_at": "2026-10-01"},
                 "directness": "indirect", "source_level": "primary", "independence_group": "geo1",
                 "assessment_reason": "a replication cohort would test the effect"}


def _hub(*, review=ACCEPT, report=REPORT, artifact_path=None, failed_lookup=False, synthesis_usd=None,
         contract_violation=False):
    settings = _settings()
    settings.orchestrator.reviewer_agent = "sci_reviewer"
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        kind = task.meta["kind"]
        if kind == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        if kind in {"step", "result_correction"}:
            path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
            result = _cp2_result(hub, task)
            if artifact_path is not None:
                result["artifact_refs"][0]["path"] = artifact_path
            if failed_lookup:
                result["evidence"].append(dict(FAILED_LOOKUP))
            if contract_violation:
                result["evidence"].append({
                    "id": "bad_date", "kind": "observation", "observation": "lookup",
                    "status": "observed", "source": {"uri": "https://example.org", "accessed_at": "2026/10/03",
                                                       "locator": "row 1"},
                    "directness": "indirect", "source_level": "primary", "independence_group": "lookup",
                    "assessment_reason": "cross-check", "slots": [],
                })
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result,
                              outputs=[path] if kind == "step" else [],
                              output_sha256={path: "a" * 64})
        if kind == "review":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=review)
        if kind == "synthesis":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text=report, cost_usd=synthesis_usd)
        raise AssertionError(f"unexpected task kind {kind}")

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")
    hub.agents["sci_reviewer"] = {"id": "sci_reviewer", "name": "sci_reviewer", "role": "test", "engine": "mock"}
    decisions = [CP1, APPROVE]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        if kwargs["kind"] == "budget":
            return {"approved": False, "note": "denied", "approval_id": "budget_no", "decided_at": 1.0}
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    return hub


def _kinds(hub, start=0):
    return [task.meta["kind"] for task in hub.calls[start:]]


# ---------- ① approve → accept → anchored report ----------

@pytest.mark.asyncio
async def test_approved_evidence_is_reviewed_and_reported_with_checked_anchors():
    hub = _hub()
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _kinds(hub) == ["plan", "step", "review", "synthesis"]
    assert req["outcome"] == "research_reported" and req["status"] == "done"
    assert req["research_contract"]["report_check"] == {"anchors": 1, "problems": []}
    assert req["report"].startswith(REPORT) and "Claim check" not in req["report"]
    review = hub.calls[2]
    assert review.agent_id == "sci_reviewer"
    assert review.output_schema["properties"]["issues"]["items"]["properties"]["priority"]["enum"] == ["P1", "P2", "P3"]
    assert "a" * 64 in review.prompt and "The donor-level effect is present." in review.prompt
    assert 'verdict "revise" only when there is at least one P1 issue' in review.prompt
    synthesis = hub.calls[3].prompt
    assert "[[claim:s1/c1]]" in synthesis and "no external cohort" in synthesis
    assert req["research_contract"]["review"]["verdict"] == "accept"


@pytest.mark.asyncio
async def test_research_report_keeps_p3_as_a_count_and_every_issue_verbatim_in_the_record():
    """PR #438 review: the body counts P3, so the execution record must hold what the PI is pointed to."""
    p3 = {"step_id": "s1", "claim_id": "", "priority": "P3", "category": "other", "evidence_quote": "row 1",
          "problem": "unit spelled two ways", "request": "use TPM throughout"}
    hub = _hub(review={**ACCEPT, "issues": [*ACCEPT["issues"], p3]})
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["outcome"] == "research_reported"
    assert "- P3(표현) 1건: 실행 기록 참고" in req["report"] and "unit spelled two ways" not in req["report"]
    assert "P2 · s1: no external cohort" in req["report"]
    record = req["report_appendix"]
    assert "P3 · s1: unit spelled two ways → use TPM throughout" in record
    assert "P2 · s1: no external cohort → state the single cohort" in record


@pytest.mark.asyncio
async def test_contract_refusal_is_kept_in_the_final_report_metadata():
    hub = _hub(contract_violation=True)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["outcome"] == "research_reported"
    assert req["research_contract"]["report_check"] == {"anchors": 1, "problems": []}
    assert "계약에 맞지 않아 뺀 근거" in req["report_appendix"] and "bad_date" in req["report_appendix"]


@pytest.mark.asyncio
async def test_budget_denied_after_a_finished_report_keeps_the_report():
    # The cap is crossed by the report's own cost; the card after it is denied. The report already ran, so it
    # stays and is checked, and the denial only fails the request (as in the generic synthesis).
    hub = _hub(synthesis_usd=2.0)
    hub.requests["r"]["budget_usd"] = 1.0
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _kinds(hub) == ["plan", "step", "review", "synthesis"]
    assert [item["kind"] for item in hub.approvals][-1] == "budget"
    assert req["report"].startswith(REPORT) and "stopped: the budget was not approved" not in req["report"]
    assert req["research_contract"]["report_check"] == {"anchors": 1, "problems": []}
    assert req["outcome"] == "research_reported" and req["status"] == "failed"
    assert "failure" not in req["research_contract"]
    assert "Budget: $2.00 > $1.00; denied." in req["report_appendix"]


# ---------- ②③④ the anchor check marks the report incomplete ----------

@pytest.mark.asyncio
@pytest.mark.parametrize(("report", "artifact_path", "problem"), [
    ("The effect is present [[claim:s1/c9]].", None, "claim c9 is not in step s1"),
    ("The effect is present [[claim:s9/c1]].", None, "step s9"),
    ("The effect is present.", None, "anchors no claim"),
    (REPORT, "outputs/missing.tsv", "refused at CP2"),
])
async def test_report_whose_anchors_do_not_check_out_is_incomplete(report, artifact_path, problem):
    hub = _hub(report=report, artifact_path=artifact_path)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["outcome"] == "report_incomplete" and req["status"] == "failed"
    check = req["research_contract"]["report_check"]
    assert check["anchors"] == report.count("[[claim:")
    assert any(problem in line for line in check["problems"])
    assert req["report"].startswith(report)  # the body stays; the problems move to the execution record
    assert "Claim check:" not in req["report"] and "실행 기록 참고" in req["report"]
    assert "Claim check:" in req["report_appendix"] and problem in req["report_appendix"].split("Claim check:", 1)[1]


@pytest.mark.asyncio
async def test_research_claim_check_and_execution_status_share_the_appendix():
    hub = _hub(report="The effect is present.")
    await Orchestrator(hub).run_request("r")

    report = hub.requests["r"]["report"]
    appendix = hub.requests["r"]["report_appendix"]
    assert report.startswith("The effect is present.") and "Claim check:" not in report
    assert "실행 기록 참고" in report
    assert appendix.startswith("## 부록: 실행 기록")
    assert "Claim check:" in appendix and "Step status and output paths" in appendix


@pytest.mark.asyncio
async def test_research_claim_check_ignores_an_anchor_found_only_in_the_model_appendix():
    model_report = ("## 결론과 권고\n본문에는 claim anchor가 없습니다.\n\n"
                    "## 부록: 실행 기록\n모델 기록 [[claim:s1/c1]].")
    hub = _hub(report=model_report)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    recorded = req["research_contract"]["report_check"]
    rerun = rerun_report_check(req)
    assert req["outcome"] == "report_incomplete" and req["status"] == "failed"
    assert recorded["anchors"] == 0 and any("anchors no claim" in line for line in recorded["problems"])
    assert "[[claim:s1/c1]]" not in req["report"] and "[[claim:s1/c1]]" in req["report_appendix"]
    assert rerun and rerun["same"] is True and rerun["rerun"] == recorded


@pytest.mark.asyncio
async def test_research_claim_check_ignores_a_bad_anchor_in_the_model_appendix():
    model_report = (REPORT + "\n\n## 부록: 실행 기록\n잘못된 기록 [[claim:s1/c9]].")
    hub = _hub(report=model_report)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    recorded = req["research_contract"]["report_check"]
    rerun = rerun_report_check(req)
    assert req["outcome"] == "research_reported" and req["status"] == "done"
    assert recorded == {"anchors": 1, "problems": []}
    assert "[[claim:s1/c9]]" not in req["report"] and "[[claim:s1/c9]]" in req["report_appendix"]
    assert rerun and rerun["same"] is True and rerun["rerun"] == recorded


@pytest.mark.asyncio
async def test_a_claim_resting_only_on_refused_evidence_is_not_offered_for_citation():
    hub = _hub(artifact_path="outputs/missing.tsv")
    await Orchestrator(hub).run_request("r")

    synthesis = hub.calls[3].prompt
    citable = synthesis.split("Citable claims:", 1)[1].split("Claims that cannot be cited:", 1)[0]
    assert "[[claim:s1/c1]]" not in citable


# ---------- ⑤ revise ends the request without synthesis ----------

@pytest.mark.asyncio
async def test_review_revise_ends_the_request_without_a_report():
    hub = _hub(review=REVISE)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _kinds(hub) == ["plan", "step", "review"]
    assert req["outcome"] == "research_review_revise" and req["status"] == "failed"
    report = req["report"]
    assert report.index("the file has no effect column") < report.index("wording")  # P1 before P3
    assert "new CP1 approval" in report
    assert "report_check" not in req["research_contract"]


@pytest.mark.asyncio
async def test_unreadable_research_review_is_asked_twice_then_ends():
    hub = _hub(review={"verdict": "accept", "scores": {}})
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _kinds(hub) == ["plan", "step", "review", "review"]
    assert req["outcome"] == "research_review_unparsed" and req["status"] == "failed"
    assert "review" not in req["research_contract"]


# ---------- ⑥ failed lookups stay in the report even when synthesis leaves them out ----------

@pytest.mark.asyncio
async def test_failed_lookup_warning_is_attached_to_the_report():
    hub = _hub(failed_lookup=True)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["outcome"] == "research_reported"
    assert "e2" not in REPORT
    section = req["report_appendix"].split("실패한 조회 — 증거도 부재 증명도 아님", 1)[1]
    assert "s1/e2 (failed)" in section and "the GEO query timed out" in section
    assert "s1/e2 (failed)" in hub.calls[3].prompt  # synthesis was told to list it as not established


# ---------- ⑦ a restart keeps the saved review ----------

@pytest.mark.asyncio
async def test_restart_after_the_review_was_saved_does_not_review_again():
    hub = _hub()
    await Orchestrator(hub).run_request("r")
    saved = dict(hub.requests["r"]["research_contract"]["review"])

    _interrupt(hub)  # restarted after the review was saved, before the report was committed
    calls, approvals = len(hub.calls), len(hub.approvals)
    await Orchestrator(hub).run_request("r", resume=True)

    assert _kinds(hub, calls) == ["synthesis"] and hub.approvals[approvals:] == []
    assert hub.requests["r"]["research_contract"]["review"] == saved
    assert hub.requests["r"]["outcome"] == "research_reported"


# ---------- ⑧ the research review schema is strict ----------

def test_research_review_schema_is_strict():
    from labhq.orchestrator.cso import REVIEW_SCHEMA, RESEARCH_LANE_REVIEW_SCHEMA, valid_review

    def objects(schema):
        if schema.get("type") == "object":
            yield schema
            for value in schema["properties"].values():
                yield from objects(value)
        if schema.get("type") == "array":
            yield from objects(schema["items"])

    found = list(objects(RESEARCH_LANE_REVIEW_SCHEMA))
    assert len(found) == 2
    for schema in found:
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
    issue = RESEARCH_LANE_REVIEW_SCHEMA["properties"]["issues"]["items"]["properties"]
    assert set(issue) == {"step_id", "claim_id", "priority", "category", "evidence_quote", "problem", "request"}
    assert valid_review(ACCEPT, RESEARCH_LANE_REVIEW_SCHEMA) and not valid_review(ACCEPT)
    assert not valid_review({**ACCEPT, "issues": [{**ACCEPT["issues"][0], "priority": "P4"}]},
                            RESEARCH_LANE_REVIEW_SCHEMA)
    assert "scores" in REVIEW_SCHEMA["properties"]  # the generic review keeps its separate scoring fields


@pytest.mark.asyncio
@pytest.mark.parametrize(("review", "kinds", "outcome"), [
    # accept with a P1 issue: the conclusion would change, so the request ends as revise
    ({**ACCEPT, "issues": [{**REVISE["issues"][1]}]}, ["plan", "step", "review"], "research_review_revise"),
    # revise with only a P3 issue: no fix changes the conclusion, so the report is written
    ({**REVISE, "issues": [REVISE["issues"][0]]}, ["plan", "step", "review", "synthesis"], "research_reported"),
])
async def test_review_verdict_follows_p1_issues(review, kinds, outcome):
    """The verdict is revise exactly when an issue is P1; the reviewer's own verdict is kept (PR #336 review)."""
    hub = _hub(review=review)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _kinds(hub) == kinds and req["outcome"] == outcome
    assert req["research_contract"]["review"]["reviewer_verdict"] == review["verdict"]


# ---------- a short lead-in before the report's first heading is dropped (10th mock trial) ----------

LEAD_IN = "최종 보고서를 작성 중입니다. 필요한 내용은 과제 파일에서 모두 확인했습니다.\n\n---\n\n"


@pytest.mark.asyncio
async def test_a_lead_in_before_the_first_heading_is_not_part_of_the_report():
    body = "# 보고서\n\n" + REPORT
    hub = _hub(report=LEAD_IN + body)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["outcome"] == "research_reported"
    assert req["report"].startswith(body) and "작성 중입니다" not in req["report"]
    assert "no preamble" in hub.calls[3].prompt


@pytest.mark.parametrize("text", [
    REPORT,                                            # no heading at all
    "# 보고서\n\n" + REPORT,                           # starts with its heading
    "결론 [[claim:s1/c1]].\n\n# 근거\n\nmore",         # the lead carries an anchor: it is report text
    "예시:\n```\n# 예시 제목\n```\n\n" + REPORT,       # the "heading" sits inside a code block (PR #361 review)
    ("긴 서문 " * 120) + "\n\n# 보고서\n\n" + REPORT,   # too long to be a lead-in
])
def test_report_text_that_is_not_a_short_lead_in_is_kept(text):
    from labhq.orchestrator.cso import report_body
    assert report_body(text) == text
