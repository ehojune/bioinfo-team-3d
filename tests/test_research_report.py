"""After CP2 approval (#90, #58 ③⑤): one research review, a CSO report whose conclusions name ledger claims,
and a machine check of those claim anchors."""

import pytest

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


def _hub(*, review=ACCEPT, report=REPORT, artifact_path=None, failed_lookup=False):
    settings = _settings()
    settings.orchestrator.reviewer_agent = "sci_reviewer"
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        kind = task.meta["kind"]
        if kind == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        if kind == "step":
            path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
            result = _cp2_result(hub, task)
            if artifact_path is not None:
                result["artifact_refs"][0]["path"] = artifact_path
            if failed_lookup:
                result["evidence"].append(dict(FAILED_LOOKUP))
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result, outputs=[path],
                              output_sha256={path: "a" * 64})
        if kind == "review":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=review)
        if kind == "synthesis":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text=report)
        raise AssertionError(f"unexpected task kind {kind}")

    hub = holder["hub"] = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")
    hub.agents["sci_reviewer"] = {"id": "sci_reviewer", "name": "sci_reviewer", "role": "test", "engine": "mock"}
    decisions = [CP1, APPROVE]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
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
    assert req["report"].startswith(report)  # the body stays; the problems follow it
    assert "Claim check:" in req["report"] and problem in req["report"].split("Claim check:", 1)[1]


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
    section = req["report"].split("실패한 조회 — 증거도 부재 증명도 아님", 1)[1]
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
    assert "scores" in REVIEW_SCHEMA["properties"]  # the generic review is untouched
