"""#670: no skill from a paper whose full text was not reached (it stays a planning reference), and a meta-analysis
skill also goes to the literature steps that extract its included studies."""
import pytest

from labhq.recruit.paper_skills import attachments, cited_access, prepare_plan, selections
from labhq.settings import Settings
from tests.test_paper_skill_attach import EVIDENCE, SELECTED

ROSTER = [{"id": "worker"}, {"id": "lit_scout"}]
STEPS = [{"id": "s1", "agent_id": "worker"}, {"id": "s_lit", "agent_id": "lit_scout"},
         {"id": "s_lit2", "agent_id": "lit_scout"}]


async def _convert(task):  # never reached before CP1: a call here is the failure
    raise AssertionError("no paper skill is built before CP1")


def _prepare(plan, precedents):
    return prepare_plan(plan, precedents, ROSTER, "r", Settings(), _convert)


def test_cited_access_takes_the_best_stated_level():
    assert cited_access(["PMID:1; access_level=abstract"]) == "abstract"
    assert cited_access(["PMID:1; access_level=abstract", "PMID:1 access_level=full_text"]) == "full_text"
    assert cited_access(["PMID:1"]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("citations, seen", [(["PMID:40119824; access_level=abstract"], "초록만 확인했습니다"),
                                             (["PMID:40119824"], "본문 접근을 확인하지 못했습니다")])
async def test_a_paper_without_reached_full_text_makes_no_skill_and_stays_a_planning_reference(citations, seen):
    plan = {"steps": STEPS, "paper_skills": [{**SELECTED, "paper_id": "PMID:40119824", "title": "Abstract paper"}]}
    precedents = {"papers": [{"title": "Abstract paper", "citations": citations}]}
    out = await _prepare(plan, precedents)
    row = out["paper_skill_results"][0]
    assert row["status"] == "skipped" and not row.get("built")
    assert seen in row["skip_reason"] and "계획 참고로만" in row["skip_reason"]
    assert attachments(out, "s1", "worker", "r") == []
    assert any("논문 스킬 생략" in warning for warning in out["warnings"])


@pytest.mark.asyncio
async def test_a_partly_extracted_full_text_keeps_its_own_reason():
    plan = {"steps": STEPS, "paper_skills": [SELECTED]}
    partial = {**EVIDENCE, "extraction": "partial"}
    precedents = {"papers": [{"title": "Paired method", "citations": ["PMID:12345678; access_level=full_text"],
                              "fulltext_receipt": partial}]}
    row = (await _prepare(plan, precedents))["paper_skill_results"][0]
    assert row["status"] == "skipped" and "계획 참고로만" not in row["skip_reason"]


@pytest.mark.asyncio
async def test_a_full_text_meta_analysis_also_goes_to_the_literature_extraction_steps():
    plan = {"steps": STEPS, "paper_skills": [{**SELECTED, "title": "A meta-analysis of donor pairing"}]}
    precedents = {"papers": [{"title": "A meta-analysis of donor pairing", "citations": ["PMID:12345678; access_level=full_text"],
                              "fulltext_receipt": EVIDENCE}]}
    out = await _prepare(plan, precedents)
    row = out["paper_skill_results"][0]
    assert row["status"] == "deferred"
    assert row["step_ids"] == ["s1", "s_lit", "s_lit2"] and row["extraction_step_ids"] == ["s_lit", "s_lit2"]
    assert row["agent_ids"] == ["worker", "lit_scout"]
    assert [a["step_id"] for a in attachments(out, "s_lit", "lit_scout", "r")] == ["s_lit"]
    assert [a["step_id"] for a in attachments(out, "s1", "worker", "r")] == ["s1"]


def test_a_method_paper_is_not_extended_and_a_plan_without_literature_steps_gains_none():
    paper = {"title": "Paired method", "citations": ["PMID:12345678"]}
    plan = {"steps": STEPS, "paper_skills": [SELECTED]}
    chosen = selections(plan, {"papers": [paper]}, ROSTER)
    assert chosen[0]["step_ids"] == ["s1"] and "extraction_step_ids" not in chosen[0]
    meta = {"title": "Systematic review and meta-analysis", "citations": ["PMID:12345678"]}
    chosen = selections({"steps": [STEPS[0]], "paper_skills": [SELECTED]}, {"papers": [meta]}, ROSTER)
    assert chosen[0]["step_ids"] == ["s1"] and "extraction_step_ids" not in chosen[0]
