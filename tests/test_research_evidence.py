"""#90 PR 2 (R04-R07): typed claim/evidence/link rows in the research result contract."""

import copy

import pytest
from pydantic import ValidationError

from labhq.research import contract as rc

COUNTABLE = {"observation", "database_annotation", "experimental", "literature_claim"}


def claim(cid="c1", status="supported", revision=1, **extra):
    return {"id": cid, "revision": revision, "statement": "IL6 expression is higher in cases than controls.",
            "scope": "donor-level pseudobulk of one public cohort", "kind": "finding", "status": status,
            "importance": "major", "limitations": ["single cohort"], **extra}


def evidence(eid, kind="experimental", status="observed", **extra):
    row = {"id": eid, "kind": kind, "observation": f"observation {eid}"}
    if kind in COUNTABLE:
        row.update(status=status, source={"artifact_id": "a1"})
    else:
        row["derived_from"] = ["e1"]
    row.update(extra)
    return row


def link(cid, eid, relation="supports", revision=1):
    return {"claim_id": cid, "claim_revision": revision, "evidence_id": eid, "relation": relation}


def result(**overrides):
    value = {
        "schema_version": 2, "plan_sha256": "a" * 64, "step_id": "s1",
        "claims": [claim()],
        "evidence": [evidence("e1")],
        "links": [link("c1", "e1")],
        "artifact_refs": [{"artifact_id": "a1", "path": "out/pseudobulk_de.tsv"}],
        "not_established": [], "failures": [], "method_changes": [],
    }
    value.update(overrides)
    return value


def minimal_plan():
    return {
        "schema_version": 2,
        "intake": {"work_kind": "research", "reason": "PI specified work_kind=research", "source": "explicit"},
        "brief": {"question": "Is IL6 higher in cases?", "purpose": "choose the next assay", "subject": "cohort",
                  "scope": "donor level", "deliverables": ["claims"], "completion_conditions": ["effect reported"],
                  "study_type": "exploratory"},
        "protocol": {"revision": 1, "analysis_unit": "donor", "selection_criteria": [], "exclusion_criteria": [],
                     "comparators": [], "primary_metrics": ["log fold change"], "validation_methods": ["QC"],
                     "resource_limits": ["one call"], "stop_conditions": ["no donors"],
                     "approval_conditions": ["CP1"], "data_boundaries": ["public data only"],
                     "statistics": {"applicable": False, "reason": "descriptive only"}},
        "pack_values": {}, "clarifying_questions": [],
        "steps": [{"id": "s1", "agent_id": "worker", "instruction": "analyze", "phase": "analysis",
                   "claim_ids": ["c1"], "input_refs": ["counts"], "outputs": ["de.tsv"], "checks": ["QC"],
                   "evidence_slots": [], "depends_on": []}],
        "recruit": [], "notes": "",
    }


def rejects(value, match):
    with pytest.raises(ValidationError, match=match):
        rc.ResearchResult.model_validate(value)


# --- R04: typed claim / evidence / link ---------------------------------------------------------

def test_result_ledger_accepts_typed_claims_evidence_and_links():
    parsed = rc.ResearchResult.model_validate(result(
        claims=[claim(), claim("c2", status="proposed", kind="hypothesis")],
        evidence=[evidence("e1"), evidence("e2", kind="inference")],
        links=[link("c1", "e1"), link("c2", "e2", "context")]))
    assert [c.key for c in parsed.claims] == ["c1@1", "c2@1"]
    assert parsed.evidence[1].counts is False and parsed.evidence[0].counts is True
    schema = rc.RESEARCH_RESULT_SCHEMA
    assert {"claims", "evidence", "links"} <= set(schema["required"])
    assert set(schema["$defs"]["Evidence"]["properties"]["kind"]["enum"]) == COUNTABLE | {"inference", "hypothesis"}


@pytest.mark.parametrize("mutate, match", [
    (lambda v: v["links"].append(link("c1", "e9")), "cites unknown evidence"),
    (lambda v: v["links"].append(link("c9", "e1")), "unknown claim c9"),
    (lambda v: v["links"].append(link("c1", "e1", "context")), "linked to claim c1 more than once"),
    (lambda v: v["claims"].append(claim()), "duplicate claim id c1"),
    (lambda v: v["evidence"].append(evidence("e1")), "duplicate evidence id e1"),
    (lambda v: v["evidence"][0]["source"].update(artifact_id="a9"), "artifact a9 missing from artifact_refs"),
    (lambda v: v["evidence"].append(evidence("e2", kind="inference", derived_from=["e7"])),
     "derived_from unknown evidence e7"),
])
def test_ledger_rejects_dangling_references(mutate, match):
    value = result()
    mutate(value)
    rejects(value, match)


def test_link_to_a_superseded_claim_revision_is_rejected():
    value = result(claims=[claim(revision=2, supersedes="c1@1")])
    rejects(value, r"targets a stale revision; current is c1@2")
    value["links"] = [link("c1", "e1", revision=2)]
    assert rc.ResearchResult.model_validate(value).claims[0].key == "c1@2"


@pytest.mark.parametrize("bad_claim, match", [
    (claim(kind="fact"), "Input should be .finding."),
    (claim(status="likely"), "Input should be .proposed."),
    (claim(revision=2), "must name the revision it supersedes"),
    (claim(revision=2, supersedes="c9@1"), "supersedes must be c1@"),
    (claim(revision=2, supersedes="c1@2"), "supersedes must be c1@"),
    (claim(statement="IL6 is higher.\nTNF is lower."), "one non-empty line"),
])
def test_claim_enums_and_revision_chain_are_checked(bad_claim, match):
    rejects(result(claims=[bad_claim]), match)


def test_evidence_kind_enum_and_shape():
    rejects(result(evidence=[evidence("e1", kind="opinion")]), "Input should be .observation.")
    no_source = evidence("e1")
    del no_source["source"]
    rejects(result(evidence=[no_source]), "needs status and source")
    rejects(result(evidence=[evidence("e1"), evidence("e2", kind="hypothesis", derived_from=[])]),
            "must list the evidence it is derived_from")
    with_status = {**evidence("e2", kind="inference"), "status": "observed"}
    rejects(result(evidence=[evidence("e1"), with_status]), "leave status empty")


@pytest.mark.parametrize("kind", ["inference", "hypothesis"])
def test_inference_and_hypothesis_rows_never_count_as_evidence(kind):
    value = result(evidence=[evidence("e1"), evidence("e2", kind=kind)],
                   links=[link("c1", "e1"), link("c1", "e2")])
    rejects(value, f"{kind} row e2 is not evidence and cannot support")
    # Recording reasoning as context is fine, but it cannot carry the claim's status on its own.
    value["links"] = [link("c1", "e2", "context")]
    rejects(value, "is supported but no observed countable evidence supports it")
    value["links"].append(link("c1", "e1"))
    assert rc.ResearchResult.model_validate(value).claims[0].status == "supported"


@pytest.mark.parametrize("status", ["failed", "not_found", "unavailable"])
def test_failed_or_empty_lookup_cannot_support_or_contradict(status):
    value = result(claims=[claim(status="contradicted")], evidence=[evidence("e1", status=status)],
                   links=[link("c1", "e1", "contradicts")])
    rejects(value, f"evidence e1 is {status}; a failed or empty lookup is neither evidence nor proof of absence")
    value["claims"] = [claim(status="unresolved")]
    value["links"] = [link("c1", "e1", "context")]
    assert rc.ResearchResult.model_validate(value).claims[0].status == "unresolved"


def test_claim_status_must_match_countable_links():
    rejects(result(claims=[claim(status="contradicted")]), "is contradicted but no observed countable evidence contradicts")
    both = dict(evidence=[evidence("e1"), evidence("e2")], links=[link("c1", "e1"), link("c1", "e2", "contradicts")])
    rejects(result(**both), "has contradicting evidence; use partially_supported or contradicted")
    parsed = rc.ResearchResult.model_validate(result(claims=[claim(status="partially_supported")], **both))
    assert parsed.claims[0].status == "partially_supported"


def test_result_binds_to_the_frozen_plan_revision_and_step():
    plan = minimal_plan()
    digest = rc.plan_sha256(plan)
    assert rc.validate_research_result(result(plan_sha256=digest), plan=plan).step_id == "s1"
    with pytest.raises(ValueError, match="does not match the frozen plan"):
        rc.validate_research_result(result(), plan=plan)
    with pytest.raises(ValueError, match="step_id s9 is not in the frozen plan"):
        rc.validate_research_result(result(plan_sha256=digest, step_id="s9"), plan=plan)
    changed = copy.deepcopy(plan)
    changed["protocol"]["primary_metrics"] = ["odds ratio"]
    with pytest.raises(ValueError, match="does not match the frozen plan"):
        rc.validate_research_result(result(plan_sha256=digest), plan=changed)
