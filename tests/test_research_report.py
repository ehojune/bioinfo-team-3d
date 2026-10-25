"""After CP2 approval (#90, #58 ③⑤): one research review, a CSO report whose conclusions name ledger claims,
and a machine check of those claim anchors."""

from copy import deepcopy
import hashlib

import pytest

from labhq.evidence.audit import rerun_report_check
from labhq.evidence.report_check import check_report, claim_rows, claim_evidence_rows, claim_evidence_section
from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator, normalize_precedents
from labhq.portfolio import build_portfolio_files, scan_publish_files
from labhq.report_contract import reader_markdown
from tests.test_research_cp2 import CP1, _interrupt, _settings
from tests.test_research_protocol import MiniHub, _cp2_result, valid_plan

APPROVE = {"approved": True, "choice": "approve", "note": ""}
DECLINE = {"approved": False, "note": ""}  # the continuation card after a revise (#90)
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
         contract_violation=False, reports=None):
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
            text = reports.pop(0) if reports else report  # one text per synthesis call, corrections included
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text=text, cost_usd=synthesis_usd)
        raise AssertionError(f"unexpected task kind {kind}")

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")
    hub.agents["sci_reviewer"] = {"id": "sci_reviewer", "name": "sci_reviewer", "role": "test", "engine": "mock"}
    decisions = [CP1, APPROVE, DECLINE]

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
    assert "계약에 맞지 않아 뺀 근거" in req["execution_record"] and "bad_date" in req["execution_record"]


@pytest.mark.asyncio
async def test_budget_crossed_by_the_finished_report_records_the_overrun_without_a_card():
    # The report's own cost crosses the cap after the request has no work left. It is recorded without parking the
    # completed report on a decision whose approve and deny choices cannot change any later dispatch.
    hub = _hub(synthesis_usd=2.0)
    hub.requests["r"]["budget_usd"] = 1.0
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _kinds(hub) == ["plan", "step", "review", "synthesis"]
    assert [item["kind"] for item in hub.approvals] == ["research_plan", "research_evidence"]
    assert req["report"].startswith(REPORT) and "stopped: the budget was not approved" not in req["report"]
    assert req["research_contract"]["report_check"] == {"anchors": 1, "problems": []}
    assert req["outcome"] == "research_reported" and req["status"] == "done"
    assert "failure" not in req["research_contract"]
    assert "Budget: $2.00 > $1.00; final turn; no further work." in req["execution_record"]


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
    # The office feed prints this after "요청이 실패했어요: "; it was empty (web trial readiness 2026-10-12).
    assert "claim 검사" in req["error"]
    check = req["research_contract"]["report_check"]
    assert check["anchors"] == report.count("[[claim:")
    assert any(problem in line for line in check["problems"])
    assert req["report"].startswith(report)  # the body stays; the problems move to the execution record
    assert "Claim check:" not in req["report"] and "실행 기록 참고" in req["report"]
    assert "Claim check:" in req["execution_record"] and problem in req["execution_record"].split("Claim check:", 1)[1]


@pytest.mark.asyncio
async def test_research_claim_check_and_execution_status_share_the_appendix():
    hub = _hub(report="The effect is present.")
    await Orchestrator(hub).run_request("r")

    report = hub.requests["r"]["report"]
    appendix = hub.requests["r"]["execution_record"]
    assert report.startswith("The effect is present.") and "Claim check:" not in report
    assert "실행 기록 참고" in report
    assert appendix.startswith("## 실행 기록")
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
    assert "[[claim:s1/c1]]" not in req["report"] and "[[claim:s1/c1]]" in req["execution_record"]
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
    assert "[[claim:s1/c9]]" not in req["report"] and "[[claim:s1/c9]]" in req["execution_record"]
    assert rerun and rerun["same"] is True and rerun["rerun"] == recorded


@pytest.mark.asyncio
async def test_a_claim_resting_only_on_refused_evidence_is_not_offered_for_citation():
    hub = _hub(artifact_path="outputs/missing.tsv")
    await Orchestrator(hub).run_request("r")

    # The step first gets one correction for the uncollected citation (#485), which leaves it unchanged.
    synthesis = next(task.prompt for task in hub.calls if task.meta["kind"] == "synthesis")
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
    assert "리뷰가 수정을 요구" in req["error"]
    report = req["report"]
    assert report.index("the file has no effect column") < report.index("wording")  # P1 before P3
    assert "new CP1 approval" in report
    assert "report_check" not in req["research_contract"]
    # The PI was offered a continuation and declined it: the request ends as before (#90).
    card = hub.approvals[-1]
    assert card["kind"] == "research_continue" and card["detail"]["round"] == 2
    assert [issue["priority"] for issue in card["detail"]["p1_issues"]] == ["P1"]
    assert "PI가 새 CP1로 이어 가기를 거절했습니다." in report
    assert req["research_contract"]["continue_decision"]["decision"] == "declined"
    assert "rounds" not in req["research_contract"] or req["research_contract"]["rounds"] == []


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
    section = req["execution_record"].split("실패한 조회 — 증거도 부재 증명도 아님", 1)[1]
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
    assert len(found) == 4  # the review, its issue, the optional checklist row (#586) and blind comparison (#889)
    for schema in found:
        assert schema["additionalProperties"] is False
        # the two optional fields (#586 checklist, #889 blind_comparison)
        optional = {"checklist", "blind_comparison"} if schema is RESEARCH_LANE_REVIEW_SCHEMA else set()
        assert set(schema["required"]) == set(schema["properties"]) - optional
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


# Synthetic source-lineage integration (#907).

def _literature(evidence_id, *, source=None, lineage=None, status="observed"):
    return {"id": evidence_id, "kind": "literature_claim", "observation": "Synthetic literature record",
            "status": status, "status_detail": "Synthetic access failure" if status == "unavailable" else None,
            "source": {"id_scheme": "doi", "id_value": f"10.1000/fake-{evidence_id}",
                       "accessed_at": "2026-10-24", "locator": "Introduction p.1 paragraph 2",
                       **(source or {})},
            "directness": "indirect", "source_level": "secondary", "independence_group": evidence_id,
            "assessment_reason": "Synthetic cross-reference", "lineage": lineage}


def _ledger(base):
    ledger = deepcopy(base)
    ledger["claims"] += [
        {**ledger["claims"][0], "id": "c2", "statement": "A review repeats the original finding."},
        {**ledger["claims"][0], "id": "c3", "status": "unresolved",
         "statement": "The inaccessible follow-up remains unresolved.", "status_reason": "Full text unavailable"},
    ]
    ledger["evidence"][0]["lineage"] = {"original": True, "basis": "Synthetic experiment in Results p.2"}
    ledger["evidence"][0]["source"].update(
        id_scheme="doi", id_value="10.1000/fake-original", accessed_at="2026-10-24",
        locator="Results p.2 paragraph 1", access_status="full_text", methods_locator="Methods lines 2-3",
        qc_locator="QC lines 4-5", supplementary_status="missing")
    ledger["evidence"] += [
        _literature("e2", source={"access_status": "abstract_only"}, lineage={"cited_from": ["e1"]}),
        _literature("e3", status="unavailable", source={"access_status": "not_obtained", "locator": None}),
        _literature("e4", lineage={"cited_from": ["e5"]}),
        _literature("e5", lineage={"cited_from": ["e4"]}),
    ]
    ledger["links"] += [
        {"claim_id": "c2", "claim_revision": 1, "evidence_id": "e2", "relation": "supports",
         "rationale": "Same synthetic original"},
        *[{"claim_id": "c3", "claim_revision": 1, "evidence_id": evidence_id, "relation": "context",
           "rationale": "Unresolved context only"} for evidence_id in ("e3", "e4", "e5")],
    ]
    return ledger


@pytest.mark.asyncio
async def test_single_fake_request_retains_sources_access_and_lineage_through_publication(tmp_path):
    from labhq.tools.annot_literature import fulltext_receipt, source_access_metadata

    report = """# Synthetic evidence report

## 요약(Abstract)

원 실험 결과를 확인했다 [[claim:s1/c1]]. 재인용은 같은 근거다 [[claim:s1/c2]].

## 서론

원문 연결을 검사하는 가상 자료다.

## 방법

Synthetic fixture only.

## 결과

원 실험 결과 [[claim:s1/c1]].

## 논의

후속 문헌 전문을 구하지 못해 결론을 보류했다.

## 결론

원 실험과 재인용을 구분했다 [[claim:s1/c2]].
"""
    hub = _hub(report=report, review={"verdict": "accept", "issues": []})
    hub.s.runner.workspace_root = str(tmp_path / "runs")
    paper = tmp_path / "paper.md"
    paper.write_text("# Synthetic\nMethods\nExperiment\nQC\nChecks\nResults\n", encoding="utf-8")
    receipt = fulltext_receipt("doi:10.1000/fake-original", {
        "text_file": "paper.md", "text_sha256": hashlib.sha256(paper.read_bytes()).hexdigest(),
        "access_level": "full_text", "extraction": "complete", "supplements": "missing",
    }, tmp_path)
    # The tool records access only. A fake scout supplies locations after its synthetic read.
    receipt.update(evidence=[{"kind": "methods", "start_line": 2, "end_line": 3},
                             {"kind": "qc", "start_line": 4, "end_line": 5}], supplements_required=True)
    original_reply = hub.reply

    async def reply(task):
        result = await original_reply(task)
        if task.meta["kind"] == "plan":
            result.structured["steps"][0]["claim_ids"] = ["c1", "c2", "c3"]
        if task.meta["kind"] in {"step", "result_correction"}:
            result.structured = _ledger(_cp2_result(hub, task))
            result.structured["evidence"][0]["source"].update(source_access_metadata(receipt))
        return result

    hub.reply = reply
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["outcome"] == "research_reported"
    assert req["research_contract"]["report_check"]["problems"] == []
    assert not req["research_contract"]["checkpoints"]["cp2"].get("refused_evidence")
    prompt = next(task.prompt for task in hub.calls if task.meta["kind"] == "synthesis")
    assert "Recorded claim/source metadata" in prompt and "source.locator" in prompt
    assert "### 주장별 원문 근거" in req["report"]
    req["id"] = "r"
    files = build_portfolio_files(req, hub.s, {}, {})
    assert not scan_publish_files(files)
    for output in (reader_markdown(req["report"]), files["report/report.md"],
                   files["docs/reference/claim_evidence.md"]):
        assert output.count("주장별 원문 근거") == 1
        for expected in ("s1/c1", "s1/c2", "s1/c3", "10.1000/fake-original", "Results p.2 paragraph 1",
                         "초록만", "못 구함", "unresolved", "재인용", "최초 실증 출처",
                         "순환 인용; 독립 group 0", "미판정", "보충자료: 누락", "Methods:", "QC:"):
            assert expected in output
        assert "not_found" not in output  # inaccessible full text is not an empty search result
    assert "[^claim-s1-c1]" in files["report/report.md"]
    assert ":L2-L3" in files["report/report.md"] and ":L4-L5" in files["report/report.md"]


def test_receipt_schema_preserves_unknown_dependency_and_oa_pdf_provenance():
    from labhq.recruit.paper_skills import evidence_problem

    receipt = {"coverage_version": 2, "paper_id": "doi:10.1000/fake", "source": "Synthetic",
               "access_level": "full_text", "extraction": "complete", "text_file": "paper.md",
               "text_sha256": "a" * 64,
               "evidence": [{"kind": "methods", "start_line": 1, "end_line": 1},
                            {"kind": "qc", "start_line": 2, "end_line": 2}],
               "supplements_required": None, "supplements": "missing",
               "access_status": "full_text", "methods_locator": None, "qc_locator": None,
               "supplementary_status": "missing", "pdf_file": "paper.pdf", "pdf_sha256": "b" * 64,
               "oa_copy": {"provider": "Synthetic", "url": "https://example.org/paper.pdf", "license": "CC0",
                           "version": "publishedVersion", "format": "PDF", "content_sha256": "b" * 64}}
    raw = {"papers": [{"title": "Synthetic", "citations": ["10.1000/fake"], "fulltext_receipt": receipt}],
           "required": [], "recommended": [], "limitations": []}
    parsed = normalize_precedents(raw)
    assert parsed is not None and parsed["papers"][0]["fulltext_receipt"] == receipt
    assert evidence_problem(receipt) is not None  # preserving access does not certify reading
    assert evidence_problem({**receipt, "supplements_required": False}) is None
    for invalid in ("false", 0, []):
        raw["papers"][0]["fulltext_receipt"]["supplements_required"] = invalid
        assert normalize_precedents(raw) is None


@pytest.mark.parametrize("level", ["internal", "restricted"])
@pytest.mark.parametrize("discussion", [False, True])
@pytest.mark.parametrize("attributed", [False, True])
def test_publication_withholds_internal_claim_metadata(tmp_path, level, discussion, attributed):
    from labhq.orchestrator.cso import _before_bench_block
    from labhq.portfolio import portfolio_report
    from labhq.settings import DataZone, Settings

    settings = Settings()
    settings.runner.workspace_root = str(tmp_path / "runs")
    zone = tmp_path / "protected"
    settings.policy.data_zones = [DataZone(path=str(zone), level=level)]
    ledgers = {}
    for step_id in ("public", "private", "derived"):
        tag = f"SYNTHETIC_{step_id.upper()}_METADATA"
        evidence = _literature("e1", source={"uri": f"https://example.org/{tag}",
                                           "locator": tag + "_LOCATION"})
        evidence.update(status="unavailable", status_detail=tag + "_DETAIL")
        ledgers[step_id] = {
            "claims": [{"id": "c1", "statement": tag + "_STATEMENT", "status": "unresolved"}],
            "evidence": [evidence],
            "links": [{"claim_id": "c1", "evidence_id": "e1", "relation": "context"}],
        }
    refused = [{"step_id": "private", "evidence_id": "e1", "reason": "SYNTHETIC_PRIVATE_REASON"}]
    narrative = "# Synthetic report\n\n" + " ".join(
        f"Aggregate result [[claim:{step_id}/c1]]." for step_id in ledgers)
    if discussion:
        narrative += "\n\n## 논의\n\nAggregate limitations.\n\n## 결론\n\nAggregate conclusion."
    report = _before_bench_block(
        narrative, claim_evidence_section(claim_evidence_rows(ledgers, refused=refused)))
    req = {
        "id": "synthetic-private", "text": "Synthetic comparison" if attributed else f"Read {zone / 'other.tsv'}",
        "status": "done", "report": report,
        "plan": {"steps": [
            {"id": "public", "instruction": "Read public literature"},
            {"id": "private", "instruction": f"Read {zone / 'synthetic.tsv'}"},
            {"id": "derived", "instruction": "Summarize", "depends_on": ["private"]},
        ]},
        "results": {step_id: {"structured": ledger} for step_id, ledger in ledgers.items()},
        "research_contract": {"checkpoints": {"cp2": {"refused_evidence": refused}}},
    }
    before = deepcopy(req)
    files = build_portfolio_files(req, settings, {}, {})
    for output in (portfolio_report(req, settings, {}), files["report/report.md"],
                   files["docs/reference/claim_evidence.md"]):
        assert "SYNTHETIC_PRIVATE" not in output
        assert "SYNTHETIC_DERIVED" not in output
        assert ("SYNTHETIC_PUBLIC_METADATA" in output) is attributed
        assert output.count("주장별 원문 근거") == int(attributed)
    assert "Aggregate result" in files["report/report.md"]
    if discussion:
        assert "Aggregate conclusion." in files["report/report.md"]
    assert req == before  # the protected local request keeps its complete evidence ledger


def test_legacy_source_url_and_unanchored_unresolved_claim_survive_publication(tmp_path):
    from labhq.settings import Settings

    settings = Settings()
    settings.runner.workspace_root = str(tmp_path / "runs")
    req = {"id": "legacy", "text": "Synthetic question", "status": "done", "report": "# Report\n\nNo conclusion.",
           "plan": {"steps": [{"id": "s1"}]},
           "results": {"s1": {"structured": {
               "claims": [{"id": "c1", "statement": "Unresolved legacy claim", "status": "unresolved"}],
               "evidence": [{"id": "e1", "status": "unavailable", "source": {
                   "uri": "https://example.org/legacy", "locator": "Abstract paragraph 1"}}],
               "links": [{"claim_id": "c1", "evidence_id": "e1", "relation": "context"}]}}}}
    req["results"]["retired"] = {"structured": {"claims": [{"id": "old", "statement": "Retired claim"}]}}
    files = build_portfolio_files(req, settings, {}, {})
    for text in (files["report/report.md"], files["docs/reference/claim_evidence.md"]):
        assert "Unresolved legacy claim" in text and "unresolved" in text
        assert "https://example.org/legacy" in text and "Abstract paragraph 1" in text
        assert "접근: 미판정" in text and "계보: 미판정" in text
        assert "Retired claim" not in text


def test_refused_original_is_visible_but_cannot_reenter_through_a_recitation():
    from labhq.output_checks import conclusion_literature_line, literature_ledger_line

    ledger = _ledger({"claims": [{"id": "c1", "statement": "Original", "status": "supported"}],
                      "evidence": [_literature("e1")], "links": []})
    ledger["evidence"][0].update(source_level="primary", directness="direct")
    rows = claim_evidence_rows({"s1": ledger}, refused=[{"step_id": "s1", "evidence_id": "e1"}],
                               unsupported=[{"step_id": "s1", "claim_id": "c2"}])
    original = next(row for row in rows if row["evidence_id"] == "e1")
    recitation = next(row for row in rows if row["evidence_id"] == "e2")
    assert original["evidence_status"] == "CP2 거부"
    assert recitation["lineage"] == "출처 ID 누락; 독립 group 0"
    assert "CP2 지지 근거 거부" in recitation["claim_status"]
    assert "최초 실증 출처 →" not in claim_evidence_section(rows)
    refused = [{"step_id": "s1", "evidence_id": "e1"}]
    assert conclusion_literature_line([], {}, {"s1": ledger}) is not None
    citable, other = claim_rows({"s1": ledger}, refused=refused)
    assert "c2" not in {row["claim"]["id"] for row in citable}
    assert any(row["claim"]["id"] == "c2" and "no CP2-accepted support" in row["reason"] for row in other)
    assert "no CP2-accepted support" in check_report("Conclusion [[claim:s1/c2]]", {"s1": ledger},
                                                    refused=refused)["problems"][0]
    assert conclusion_literature_line([], {}, {"s1": ledger}, refused=refused) is None
    assert conclusion_literature_line([], {}, {"s1": ledger}, refused=iter(refused)) is None
    results = {"s1": {"literature_ledger": {"candidate_ids": ["DOI:10.1000/fake-e2"],
                                           "selected_ids": ["DOI:10.1000/fake-e2"]}}}
    assert "결론 근거 0편." in literature_ledger_line([{"id": "s1"}], results, {"s1": ledger},
                                                   refused=iter(refused))
    # An independent accepted support row still permits citation of this claim.
    ledger["evidence"].append(_literature("independent"))
    ledger["links"].append({"claim_id": "c2", "evidence_id": "independent", "relation": "supports"})
    assert "c2" in {row["claim"]["id"] for row in claim_rows({"s1": ledger}, refused=refused)[0]}
    assert conclusion_literature_line([], {}, {"s1": ledger}, refused=refused).startswith("결론 근거 논문 1편:")
    assert "결론 근거 1편." in literature_ledger_line([{"id": "s1"}], results, {"s1": ledger},
                                                   refused=refused)
    ledger["evidence"][1]["source"]["artifact_id"] = "rejected-attachment"
    ledger["evidence"][-1]["source"]["artifact_id"] = "accepted-attachment"
    assert check_report("Conclusion [[claim:s1/c2]]", {"s1": ledger}, refused=refused,
                        artifact_sha256={"s1/accepted-attachment": "a" * 64}) == {"anchors": 1, "problems": []}
    problems = check_report("Conclusion [[claim:s1/c2]]", {"s1": ledger}, refused=refused)["problems"]
    assert len(problems) == 1 and "accepted-attachment" in problems[0]


# ---------- #956: the claim check sends the report back instead of ending the request ----------

UNANCHORED = "The donor-level effect is present."


@pytest.mark.asyncio
async def test_a_report_without_anchors_is_corrected_once_and_then_reported():
    hub = _hub(reports=[UNANCHORED, REPORT])
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _kinds(hub) == ["plan", "step", "review", "synthesis", "synthesis"]
    assert req["outcome"] == "research_reported" and req["status"] == "done"
    assert req["research_contract"]["report_check"] == {"anchors": 1, "problems": []}
    assert req["research_contract"]["report_corrections"] == 1
    fix = hub.calls[4]
    assert fix.meta["report_correction"] == 1 and fix.meta["title"] == "연구 보고서 교정"
    assert "anchors no claim" in fix.prompt  # the problem verbatim
    assert "[[claim:s1/c1]]" in fix.prompt and UNANCHORED in fix.prompt  # the citable anchors and the report to fix
    assert "Do not redo the analysis" in fix.prompt
    assert req["report"].startswith(REPORT) and "Claim check:" not in req["execution_record"]


@pytest.mark.asyncio
async def test_a_report_that_stays_unanchored_after_the_corrections_is_incomplete():
    hub = _hub(reports=[UNANCHORED] * 3)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _kinds(hub) == ["plan", "step", "review", "synthesis", "synthesis", "synthesis"]  # first + 2 corrections
    assert req["outcome"] == "report_incomplete" and req["status"] == "failed"
    assert req["research_contract"]["report_corrections"] == 2
    assert any("anchors no claim" in line for line in req["research_contract"]["report_check"]["problems"])
    assert "Claim check:" in req["execution_record"]


@pytest.mark.asyncio
async def test_a_second_correction_can_pass():
    hub = _hub(reports=[UNANCHORED, "The effect is present [[claim:s1/c9]].", REPORT])
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert _kinds(hub) == ["plan", "step", "review", "synthesis", "synthesis", "synthesis"]
    assert req["outcome"] == "research_reported" and req["research_contract"]["report_corrections"] == 2
    assert "claim c9 is not in step s1" in hub.calls[5].prompt  # the second round quotes the new problem


@pytest.mark.asyncio
async def test_a_report_that_passes_the_first_check_is_not_sent_back():
    hub = _hub(reports=[REPORT])
    await Orchestrator(hub).run_request("r")
    assert _kinds(hub) == ["plan", "step", "review", "synthesis"]
    assert "report_corrections" not in hub.requests["r"]["research_contract"]
    assert hub.requests["r"]["outcome"] == "research_reported"


@pytest.mark.asyncio
async def test_a_long_report_goes_back_whole_and_is_not_elided():
    long_report = UNANCHORED + "\n\n" + ("Methods paragraph with real content. " * 700) + "\n\nEND-OF-LONG-REPORT"
    hub = _hub(reports=[long_report, long_report + " [[claim:s1/c1]]"])
    await Orchestrator(hub).run_request("r")
    fix = hub.calls[4].prompt
    assert len(long_report) > 12000 and "END-OF-LONG-REPORT" in fix and long_report in fix
    assert hub.requests["r"]["outcome"] == "research_reported"


@pytest.mark.asyncio
async def test_a_refused_budget_card_during_a_correction_keeps_the_finished_report(monkeypatch):
    from labhq.orchestrator.cso import BudgetExceeded

    original = Orchestrator.run_step

    async def refuse_corrections(self, task, *args, **kwargs):
        if task.meta.get("report_correction"):
            raise BudgetExceeded("the PI refused the budget card")
        return await original(self, task, *args, **kwargs)

    monkeypatch.setattr(Orchestrator, "run_step", refuse_corrections)
    hub = _hub(reports=[UNANCHORED])
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["outcome"] == "report_incomplete" and req["status"] == "failed"
    assert req["report"].startswith(UNANCHORED)  # not replaced by an internal error report
    assert any("anchors no claim" in line for line in req["research_contract"]["report_check"]["problems"])


def test_a_correction_call_is_not_recovered_as_the_first_report():
    from labhq.gateway.server import Hub as GatewayHub
    from labhq.models import Task

    def entry(correction):
        meta = {"kind": "synthesis", **({"report_correction": correction} if correction else {})}
        return {"request_id": "r", "kind": "synthesis", "step_id": None, "revision": 0, "parse_attempt": 0,
                "parent_task": None, "payload": {"meta": meta}}

    first = Task(agent_id="cso", request_id="r", prompt="p", meta={"kind": "synthesis"})
    fix1 = Task(agent_id="cso", request_id="r", prompt="p", meta={"kind": "synthesis", "report_correction": 1})
    assert GatewayHub._matches_recovery(first, entry(None))
    assert not GatewayHub._matches_recovery(first, entry(1))
    assert GatewayHub._matches_recovery(fix1, entry(1))
    assert not GatewayHub._matches_recovery(fix1, entry(None)) and not GatewayHub._matches_recovery(fix1, entry(2))
