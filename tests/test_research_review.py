"""#118: claim-level research review (REVIEW v2). The reviewer judges what code cannot read."""

import copy

import pytest
from pydantic import ValidationError

from labhq.research.contract import ResearchResult
from labhq.research.review import RESEARCH_REVIEW_SCHEMA, validate_research_review

CHECKS = ("citation_support", "absence_as_evidence", "directness", "independence", "comparability")


def zero_hit_contradiction():
    """c1 'contradicted' by a search that found nothing, written as an observation: the bypass in #118."""
    return ResearchResult.model_validate({
        "schema_version": 2, "plan_sha256": "b" * 64, "step_id": "s1",
        "claims": [{"id": "c1", "revision": 1, "statement": "IL6 is not differentially expressed in GSE79973.",
                    "scope": "GSE79973 donor-level DE table", "kind": "finding", "status": "contradicted",
                    "importance": "major", "status_reason": "the DE table search contradicts the claim"},
                   {"id": "c2", "revision": 1, "statement": "IL6 is a candidate marker.", "scope": "cohort",
                    "kind": "hypothesis", "status": "proposed", "importance": "minor",
                    "status_reason": "not yet tested"}],
        "evidence": [{"id": "e1", "kind": "database_annotation", "status": "observed",
                      "observation": "Searched the GSE79973 DE table for IL6: 0 hits.",
                      "source": {"id_scheme": "geo", "id_value": "GSE79973", "accessed_at": "2026-10-01",
                                 "locator": "DE table, gene column"},
                      "directness": "direct", "source_level": "primary", "independence_group": "gse79973",
                      "assessment_reason": "the table the claim is about"}],
        "links": [{"claim_id": "c1", "claim_revision": 1, "evidence_id": "e1", "relation": "contradicts",
                   "rationale": "no IL6 row in the DE table"}],
        "artifact_refs": [], "not_established": [], "failures": [], "method_changes": []})


def passed(reason="checked"):
    return {"outcome": "pass", "reason": reason}


def not_applicable(reason="the claim links no countable evidence"):
    return {"outcome": "not_applicable", "reason": reason}


def defect(severity, evidence_ids, reason):
    return {"outcome": "defect", "severity": severity, "evidence_ids": evidence_ids, "reason": reason}


def review(result, c1=None, verdict="revise", **extra):
    c1_checks = {name: passed() for name in CHECKS}
    c1_checks["comparability"] = not_applicable("the claim compares no quantities")
    c1_checks.update(c1 or {})
    return {"schema_version": 2, "plan_sha256": result.plan_sha256, "step_id": result.step_id,
            "reviewer": "critic", "verdict": verdict, "notes": "",
            "claims": [{"claim": "c1@1", "checks": c1_checks},
                       {"claim": "c2@1", "checks": {name: not_applicable() for name in CHECKS}}], **extra}


ABSENCE = defect("major", ["e1"], "0 hits is absence of a record, not an observation that contradicts")


def test_the_ledger_cannot_read_a_zero_hit_observation():
    # Why the reviewer has to judge it: code accepts the row because it cannot read the sentence.
    assert zero_hit_contradiction().claims[0].status == "contradicted"


def test_the_reviewer_schema_takes_absence_as_evidence_as_a_major_defect():
    result = zero_hit_contradiction()
    parsed = validate_research_review(review(result, {"absence_as_evidence": ABSENCE}), result=result)
    assert parsed.major_defects == [f"c1@1 absence_as_evidence (e1): {ABSENCE['reason']}"]
    with pytest.raises(ValidationError, match="cannot accept while major defects remain"):
        validate_research_review(review(result, {"absence_as_evidence": ABSENCE}, verdict="accept"), result=result)
    with pytest.raises(ValidationError, match="absence_as_evidence is always a major defect"):
        validate_research_review(review(result, {"absence_as_evidence": defect("minor", ["e1"], "0 hits")}),
                                 result=result)
    assert validate_research_review(review(result, verdict="accept"), result=result).major_defects == []


def test_every_claim_gets_every_check():
    result = zero_hit_contradiction()
    value = review(result)
    del value["claims"][1]
    with pytest.raises(ValueError, match=r"does not review claims \['c2@1'\]"):
        validate_research_review(value, result=result)
    value = review(result)
    del value["claims"][0]["checks"]["absence_as_evidence"]
    with pytest.raises(ValidationError, match="absence_as_evidence"):
        validate_research_review(value, result=result)
    assert set(RESEARCH_REVIEW_SCHEMA["$defs"]["ClaimChecks"]["required"]) == set(CHECKS)


@pytest.mark.parametrize("name", ["citation_support", "absence_as_evidence", "directness", "independence"])
def test_a_claim_with_countable_links_cannot_skip_the_evidence_checks(name):
    result = zero_hit_contradiction()
    with pytest.raises(ValueError, match=f"c1@1 links countable evidence; {name} must be judged"):
        validate_research_review(review(result, {name: not_applicable()}), result=result)


def test_a_claim_that_compares_quantities_cannot_skip_comparability():
    raw = zero_hit_contradiction().model_dump(mode="json")
    raw["evidence"].append(copy.deepcopy(raw["evidence"][0]) | {"id": "e2", "independence_group": "gse2"})
    raw["evidence"][1]["source"]["id_value"] = "GSE2"
    for eid, qid in (("e1", "q1"), ("e2", "q2")):
        row = next(r for r in raw["evidence"] if r["id"] == eid)
        row["quantities"] = [{"id": qid, "measure": "IL6 log fold change", "value": 1.0, "unit": "log2",
                              "conditions": ["case vs control"], "denominator": "6 vs 6 donors"}]
    raw["links"].append({"claim_id": "c1", "claim_revision": 1, "evidence_id": "e2", "relation": "contradicts",
                         "rationale": "second cohort"})
    raw["claims"][0]["comparisons"] = [{"quantity_ids": ["q1", "q2"], "comparability": "comparable",
                                        "reason": "same assay"}]
    result = ResearchResult.model_validate(raw)
    with pytest.raises(ValueError, match="c1@1 compares quantities; comparability must be judged"):
        validate_research_review(review(result), result=result)
    validate_research_review(review(result, {"comparability": passed("same unit, conditions and method")}),
                             result=result)


def test_the_review_binds_to_its_result_and_an_independent_reviewer():
    result = zero_hit_contradiction()
    with pytest.raises(ValueError, match="reviews a different result"):
        validate_research_review(review(result) | {"step_id": "s9"}, result=result)
    with pytest.raises(ValueError, match="cites unknown evidence e9"):
        validate_research_review(review(result, {"absence_as_evidence": defect("major", ["e9"], "x")}),
                                 result=result)
    with pytest.raises(ValueError, match="reviewer critic wrote the result"):
        validate_research_review(review(result), result=result, author="critic")
    with pytest.raises(ValidationError, match="defect needs a severity"):
        validate_research_review(review(result, {"directness": {"outcome": "defect", "reason": "indirect"}}),
                                 result=result)
    with pytest.raises(ValidationError, match="needs a reason"):
        validate_research_review(review(result, {"directness": {"outcome": "not_applicable"}}), result=result)
