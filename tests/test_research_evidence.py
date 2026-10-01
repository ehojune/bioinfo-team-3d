"""#90 PR 2 (R04-R07): typed claim/evidence/link rows in the research result contract."""

import copy

import pytest
from pydantic import ValidationError

from labhq.research import contract as rc

COUNTABLE = {"observation", "database_annotation", "experimental", "literature_claim"}


def claim(cid="c1", status="supported", revision=1, **extra):
    return {"id": cid, "revision": revision, "statement": "IL6 expression is higher in cases than controls.",
            "scope": "donor-level pseudobulk of one public cohort", "kind": "finding", "status": status,
            "importance": "major", "status_reason": "donor-level effect observed in the frozen analysis",
            "limitations": ["single cohort"], **extra}


def source(status="observed"):
    """Artifact source with the R06 field each retrieval status requires."""
    if status == "not_found":
        return {"artifact_id": "a1", "query": "gene == IL6 in pseudobulk_de.tsv"}
    return {"artifact_id": "a1", "locator": "row gene=IL6"}


def evidence(eid, kind="experimental", status="observed", **extra):
    row = {"id": eid, "kind": kind, "observation": f"observation {eid}"}
    if kind in COUNTABLE:
        if status in {"failed", "unavailable"}:
            row["status_detail"] = "pseudobulk step exited 1 before writing the table"
        row.update(status=status, source=source(status), directness="direct", source_level="primary",
                   independence_group="cohort1", assessment_reason="measures the asked comparison in raw counts")
    else:
        row["derived_from"] = ["e1"]
    row.update(extra)
    return row


def link(cid, eid, relation="supports", revision=1):
    return {"claim_id": cid, "claim_revision": revision, "evidence_id": eid, "relation": relation,
            "rationale": f"{eid} {relation} {cid} on the same donors"}


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


def _slotted_plan():
    plan = minimal_plan()
    plan["steps"][0]["evidence_slots"] = [{"id": "e1", "required": True, "description": "IL6 donor-level DE row"},
                                          {"id": "e_rep", "required": False, "description": "replication cohort"}]
    return plan, rc.plan_sha256(plan)


def test_result_binds_to_the_selected_steps_claims_and_required_slots():
    # #128: step s1 declared claim c1 and the required slot e1; a result reporting only c999 is not its answer.
    plan, digest = _slotted_plan()
    declared = result(plan_sha256=digest, evidence=[evidence("e1", slots=["e1"])])
    assert rc.validate_research_result(declared, plan=plan).evidence[0].slots == ["e1"]  # optional e_rep may stay empty
    outside = result(plan_sha256=digest, claims=[claim("c999")], links=[link("c999", "e1")],
                     evidence=[evidence("e1", slots=["e1"])])
    with pytest.raises(ValueError, match=r"claim c999 is outside the claim_ids \['c1'\] that step s1 declared"):
        rc.validate_research_result(outside, plan=plan)
    with pytest.raises(ValueError, match="required evidence slot e1 of step s1 has no evidence row"):
        rc.validate_research_result(result(plan_sha256=digest), plan=plan)
    undeclared = result(plan_sha256=digest, evidence=[evidence("e1", slots=["e1", "e9"])])
    with pytest.raises(ValueError, match="evidence e1 fills slot e9 that step s1 does not declare"):
        rc.validate_research_result(undeclared, plan=plan)
    # Every binding defect comes back at once, so one correction can fix them all.
    with pytest.raises(ValueError, match="c999.*required evidence slot e1"):
        rc.validate_research_result(result(plan_sha256=digest, claims=[claim("c999")], links=[link("c999", "e1")]),
                                    plan=plan)


def test_a_failed_attempt_still_addresses_a_required_slot():
    # The gap is recorded, not hidden: CP2 shows the slot as tried and failed instead of silently absent.
    plan, digest = _slotted_plan()
    tried = result(plan_sha256=digest, claims=[claim(status="unresolved")], links=[link("c1", "e1", "context")],
                   evidence=[evidence("e1", status="failed", slots=["e1"])])
    assert rc.validate_research_result(tried, plan=plan).evidence[0].status == "failed"


def test_only_countable_rows_fill_evidence_slots():
    rejects(result(evidence=[evidence("e1"), evidence("e2", kind="inference", slots=["e1"])]),
            "inference row e2 is not evidence and cannot fill evidence slots")
    rejects(result(evidence=[evidence("e1", slots=["e1", "e1"])]), "evidence e1 lists slot e1 more than once")


@pytest.mark.parametrize("kind", ["inference", "hypothesis"])
@pytest.mark.parametrize("cited", [{"id_scheme": "doi", "id_value": "10.1038/s41586-020-2649-2"},
                                   {"uri": "https://example.org/pathway.html"}])
def test_reasoning_rows_that_cite_an_external_source_keep_its_access_date(kind, cited):
    # #129 (R06): the access date belongs to the source a row actually used, whatever the row kind.
    rejects(result(evidence=[evidence("e1"), evidence("e2", kind=kind, source=cited)]),
            "evidence e2 cites an external source without accessed_at")
    dated = evidence("e2", kind=kind, source={**cited, "accessed_at": "2026-10-01"})
    assert rc.ResearchResult.model_validate(result(evidence=[evidence("e1"), dated])).evidence[1].kind == kind


def test_a_search_that_counted_zero_results_is_not_an_observation():
    # #118: when the row states how many records the search returned, code can tell 0 hits from a finding.
    rejects(result(evidence=[evidence("e1", result_count=0)]),
            "evidence e1 counted 0 results but is observed; record a zero-result search as not_found")
    rejects(result(evidence=[evidence("e1"), evidence("e2", status="not_found", result_count=3)],
                   links=[link("c1", "e1")]), "evidence e2 is not_found but counted 3 results")
    rejects(result(evidence=[evidence("e1"), evidence("e2", kind="inference", result_count=1)]),
            "inference row e2 is not a retrieval")
    rejects(result(evidence=[evidence("e1", result_count=-1)]), "greater than or equal to 0")
    counted = rc.ResearchResult.model_validate(result(evidence=[evidence("e1", result_count=12)]))
    assert counted.evidence[0].result_count == 12
    empty = result(evidence=[evidence("e1"), evidence("e2", status="not_found", result_count=0)],
                   links=[link("c1", "e1")])
    assert rc.ResearchResult.model_validate(empty).evidence[1].result_count == 0


# --- R05: directness, independence, source level and reasons ------------------------------------

@pytest.mark.parametrize("field", ["directness", "source_level", "independence_group", "assessment_reason"])
def test_countable_evidence_must_state_its_assessment(field):
    row = evidence("e1")
    del row[field]
    rejects(result(evidence=[row]), rf"evidence e1 \(experimental\) must state {field}")
    rejects(result(evidence=[{**evidence("e1"), field: "  "}]), field)


def test_assessment_enums_are_closed():
    rejects(result(evidence=[{**evidence("e1"), "directness": "strong"}]), "Input should be .direct.")
    rejects(result(evidence=[{**evidence("e1"), "source_level": "gold"}]), "Input should be .primary.")


def test_non_countable_rows_do_not_need_a_source_assessment():
    value = result(evidence=[evidence("e1"), evidence("e2", kind="hypothesis")],
                   links=[link("c1", "e1"), link("c1", "e2", "context")])
    assert rc.ResearchResult.model_validate(value).evidence[1].directness is None


def test_links_and_claim_status_need_a_judgement_reason():
    no_rationale = link("c1", "e1")
    del no_rationale["rationale"]
    rejects(result(links=[no_rationale]), "rationale")
    rejects(result(links=[{**link("c1", "e1"), "rationale": "   "}]), "link c1->e1 needs a rationale")
    no_reason = claim()
    del no_reason["status_reason"]
    rejects(result(claims=[no_reason]), "status_reason")
    rejects(result(claims=[claim(status_reason=" ")]), "claim c1@1 needs a status_reason")


def test_recited_source_is_not_independent_support():
    geo = {"id_scheme": "geo", "id_value": "GSE79973", "accessed_at": "2026-10-01", "locator": "series matrix"}
    first = {**evidence("e1"), "source": geo, "kind": "database_annotation"}
    recited = {**evidence("e2"), "source": {**geo, "id_value": "gse79973"}, "kind": "literature_claim",
               "independence_group": "paper2", "source_level": "secondary"}
    links = [link("c1", "e1"), link("c1", "e2")]
    rejects(result(evidence=[first, recited], links=links),
            "e1 and e2 cite the same source geo:gse79973 but declare independence groups cohort1 and paper2")
    parsed = rc.ResearchResult.model_validate(
        result(evidence=[first, {**recited, "independence_group": "cohort1"}], links=links))
    from labhq.evidence.claims import independent_groups
    assert independent_groups("c1", parsed.evidence, parsed.links) == ["cohort1"]
    other = {**recited, "source": {**geo, "id_value": "GSE118916"}}
    parsed = rc.ResearchResult.model_validate(result(evidence=[first, other], links=links))
    assert independent_groups("c1", parsed.evidence, parsed.links) == ["cohort1", "paper2"]


DOI = "10.1038/s41586-020-2649-2"


def cited(eid, group, source_value, kind="literature_claim"):
    return {**evidence(eid), "kind": kind, "source": source_value, "independence_group": group}


@pytest.mark.parametrize("second_source, artifacts, shared", [
    # The same file reached through two artifact ids, written with different separators.
    ({"artifact_id": "a2", "locator": "row IL6"}, [{"artifact_id": "a2", "path": ".\\out\\pseudobulk_de.tsv"}],
     "artifact:out/pseudobulk_de.tsv"),
    # The same DOI cited once as an identifier and once as its resolver URL.
    ({"uri": f"https://doi.org/{DOI.upper()}", "accessed_at": "2026-10-01", "locator": "Fig. 2b"}, [],
     f"doi:{DOI}"),
    # One row carries both an identifier and the downloaded artifact; the other only the artifact.
    ({"artifact_id": "a1", "locator": "row IL6"}, [], "artifact:out/pseudobulk_de.tsv"),
])
def test_recitation_is_caught_through_every_identifier_of_a_source(second_source, artifacts, shared):
    first = cited("e1", "cohort1", {"artifact_id": "a1", "id_scheme": "doi", "id_value": DOI,
                                    "accessed_at": "2026-10-01", "locator": "row IL6"})
    value = result(evidence=[first, cited("e2", "paper2", second_source)],
                   links=[link("c1", "e1"), link("c1", "e2")])
    value["artifact_refs"] += artifacts
    rejects(value, f"e1 and e2 cite the same source {shared}")


@pytest.mark.parametrize("uri, scheme, value", [
    ("https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE79973", "geo", "GSE79973"),
    ("https://identifiers.org/geo:GSE79973", "geo", "GSE79973"),
    ("https://identifiers.org/GEO/GSE79973", "geo", "GSE79973"),
    ("https://pubmed.ncbi.nlm.nih.gov/22140103/", "pmid", "22140103"),
    ("https://www.ncbi.nlm.nih.gov/pubmed/22140103", "pmid", "22140103"),
    ("https://identifiers.org/pubmed:22140103", "pmid", "22140103"),
    ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3084216/", "pmcid", "PMC3084216"),
    ("https://pmc.ncbi.nlm.nih.gov/articles/PMC3084216/", "pmcid", "PMC3084216"),
    ("https://identifiers.org/doi:10.1038/s41586-020-2649-2", "doi", "10.1038/s41586-020-2649-2"),
    ("https://dx.doi.org/10.1038/S41586-020-2649-2", "doi", "10.1038/s41586-020-2649-2"),
    ("https://www.ncbi.nlm.nih.gov/bioproject/PRJNA257197", "bioproject", "PRJNA257197"),
    ("https://www.ncbi.nlm.nih.gov/snp/rs7412", "dbsnp", "rs7412"),
    ("https://www.uniprot.org/uniprotkb/P05231/entry", "uniprot", "P05231"),
    ("https://www.rcsb.org/structure/1ALU", "pdb", "1ALU"),
    ("https://identifiers.org/hgnc:6018", "hgnc", "HGNC:6018"),
])
def test_registry_urls_are_the_same_source_as_their_identifier(uri, scheme, value):
    # #117: a resolver URL and the bare ID are one source, so they cannot be two independence groups.
    base = {"accessed_at": "2026-10-01", "locator": "summary"}
    rows = [cited("e1", "lab1", {"id_scheme": scheme, "id_value": value, **base}),
            cited("e2", "lab2", {"uri": uri, **base})]
    rejects(result(evidence=rows, links=[link("c1", "e1"), link("c1", "e2")]),
            f"e1 and e2 cite the same source {scheme}:{value.casefold()}")


@pytest.mark.parametrize("uri", ["https://pubmed.ncbi.nlm.nih.gov/?term=IL6", "https://www.ncbi.nlm.nih.gov/geo/",
                                 "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=",
                                 "https://example.org/geo/query/acc.cgi?acc=GSE79973",
                                 # Endpoints and help pages under a record path are not accessions.
                                 "https://rest.uniprot.org/uniprotkb/search?query=gene:IL6",
                                 "https://rest.uniprot.org/uniprotkb/stream?query=gene:IL6&format=tsv",
                                 "https://www.ncbi.nlm.nih.gov/sra/docs/",
                                 "https://www.ncbi.nlm.nih.gov/bioproject/browse",
                                 "https://doi.org/help"])
def test_search_pages_and_other_hosts_are_not_read_as_identifiers(uri):
    from labhq.evidence.claims import registry_id
    assert registry_id(uri) is None


def test_uri_paths_are_case_sensitive_when_judging_recitation():
    base = {"accessed_at": "2026-10-01", "locator": "row 1"}
    rows = [cited("e1", "lab1", {"uri": "https://Example.org/Data.tsv", **base}),
            cited("e2", "lab2", {"uri": "https://example.ORG/data.tsv", **base})]
    parsed = rc.ResearchResult.model_validate(result(evidence=rows, links=[link("c1", "e1"), link("c1", "e2")]))
    assert [row.independence_group for row in parsed.evidence] == ["lab1", "lab2"]
    same_host_case = [rows[0], cited("e2", "lab2", {"uri": "HTTPS://EXAMPLE.ORG/Data.tsv", **base})]
    rejects(result(evidence=same_host_case, links=[link("c1", "e1"), link("c1", "e2")]),
            "e1 and e2 cite the same source uri:https://example.org/Data.tsv")


def test_inference_rows_must_trace_back_to_a_recorded_retrieval():
    loop = [evidence("e1"), evidence("e2", kind="inference", derived_from=["e3"]),
            evidence("e3", kind="hypothesis", derived_from=["e2"])]
    links = [link("c1", "e1"), link("c1", "e2", "context")]
    rejects(result(evidence=loop, links=links), r"e2 \(inference\) does not trace back to any observation")
    grounded = [loop[0], loop[1], evidence("e3", kind="hypothesis", derived_from=["e1"])]
    assert rc.ResearchResult.model_validate(result(evidence=grounded, links=links))


# --- R06: where the source is, when it was read, what was searched -------------------------------

def external(**extra):
    return {"id_scheme": "doi", "id_value": "10.1038/s41586-020-2649-2", "accessed_at": "2026-10-01",
            "locator": "Fig. 2b", **extra}


@pytest.mark.parametrize("row, match", [
    ({**evidence("e1"), "source": {k: v for k, v in external().items() if k != "accessed_at"}},
     "external source without accessed_at"),
    ({**evidence("e1"), "source": external(accessed_at="2026/10/01")}, "YYYY-MM-DD"),
    ({**evidence("e1"), "source": external(accessed_at="20261001")}, "YYYY-MM-DD"),
    # Python 3.11+ fromisoformat also reads ISO week dates of the same length; 3.10 does not.
    ({**evidence("e1"), "source": external(accessed_at="2026-W40-4")}, "YYYY-MM-DD"),
    ({**evidence("e1"), "source": {"artifact_id": "a1"}}, "observed but its source has no locator"),
    ({**evidence("e1", status="not_found"), "source": {"artifact_id": "a1"}}, "record the searched scope"),
    ({k: v for k, v in evidence("e1", status="failed").items() if k != "status_detail"},
     "say what failed in status_detail"),
    ({k: v for k, v in evidence("e1", status="unavailable").items() if k != "status_detail"},
     "say what failed in status_detail"),
])
def test_source_records_access_date_location_and_search_scope(row, match):
    rejects(result(claims=[claim(status="unresolved")], evidence=[row], links=[]), match)


def test_complete_external_source_is_accepted():
    parsed = rc.ResearchResult.model_validate(result(evidence=[{**evidence("e1"), "source": external(version="v1")}]))
    assert parsed.evidence[0].source.locator == "Fig. 2b" and parsed.evidence[0].source.external


# --- R07: value, unit, conditions and denominator ----------------------------------------------

def qty(qid="q1", **extra):
    value = {"id": qid, "measure": "IL6 log2 fold change, case vs control", "value": 1.8, "unit": "log2 ratio",
             "conditions": ["pseudobulk", "batch-adjusted"], "denominator": "6 case vs 6 control donors",
             "method": "negative binomial GLM", "uncertainty": "95% CI 1.1-2.5"}
    value.update(extra)
    return value


def with_quantities(*quantities, comparisons=(), status="supported"):
    rows = [{**evidence(f"e{i + 1}"), "quantities": [q]} for i, q in enumerate(quantities)]
    return result(claims=[claim(status=status, comparisons=list(comparisons))], evidence=rows,
                  links=[link("c1", row["id"]) for row in rows])


@pytest.mark.parametrize("field", ["value", "unit", "conditions", "denominator"])
def test_quantity_requires_value_unit_conditions_and_denominator(field):
    missing = qty()
    del missing[field]
    rejects(with_quantities(missing), f"quantity q1 needs {field}, or an unknown entry with its impact")
    rejects(with_quantities(qty(**{field: [] if field == "conditions" else " "})), f"quantity q1 needs {field}")
    # Saying it was not reported, and what that costs, is allowed; a blank impact is not.
    parsed = rc.ResearchResult.model_validate(
        with_quantities({**missing, "unknown": {field: "cannot judge effect size against the sample"}}))
    assert field in parsed.evidence[0].quantities[0].unknown
    rejects(with_quantities({**missing, "unknown": {field: " "}}), f"unknown {field} needs its impact")


def test_quantity_unknown_entries_are_checked():
    rejects(with_quantities(qty(unknown={"unit": "not reported"})), "gives unit and also marks it unknown")
    rejects(with_quantities(qty(unknown={"p_value": "not reported"})), "unknown may only name")
    rejects(with_quantities(qty(value=True)), "quantity value must be a number or the reported string")
    assert rc.ResearchResult.model_validate(with_quantities(qty(value="<0.001")))


def comparison(kind="comparable", **extra):
    return {"quantity_ids": ["q1", "q2"], "comparability": kind, "reason": "same assay and cohort", **extra}


def test_comparable_quantities_must_share_unit_and_conditions():
    same = rc.ResearchResult.model_validate(with_quantities(qty(), qty("q2", value=0.4), comparisons=[comparison()]))
    assert same.claims[0].comparisons[0].comparability == "comparable"
    rejects(with_quantities(qty(), qty("q2", unit="log10 ratio"), comparisons=[comparison()]),
            r"calls \['q1', 'q2'\] comparable with different unit; mark not_comparable")
    rejects(with_quantities(qty(), qty("q2", conditions=["cell-level"]), comparisons=[comparison()]),
            "comparable with different conditions")
    unknown_unit = {k: v for k, v in qty("q2").items() if k != "unit"}
    unknown_unit["unknown"] = {"unit": "supplement does not say"}
    rejects(with_quantities(qty(), unknown_unit, comparisons=[comparison()]), "comparable with unknown unit")
    # Different assays can still be set side by side when the claim says it cannot rank them,
    # or states what has to hold for the ranking.
    differ = (qty(), qty("q2", conditions=["cell-level"]))
    assert rc.ResearchResult.model_validate(with_quantities(*differ, comparisons=[comparison("not_comparable")]))
    rejects(with_quantities(*differ, comparisons=[comparison("comparable_with_assumptions")]), "must list the assumptions")
    assert rc.ResearchResult.model_validate(with_quantities(*differ, comparisons=[
        comparison("comparable_with_assumptions", assumptions=["cell-level and pseudobulk effects share a scale"])]))


def test_comparison_references_quantities_the_claim_links():
    rejects(with_quantities(qty(), comparisons=[comparison()]), r"compares unknown quantities \['q2'\]")
    value = with_quantities(qty(), qty("q2"), comparisons=[comparison()])
    value["links"] = [link("c1", "e1")]
    rejects(value, r"compares \['q2'\] from evidence it does not link")
    rejects(with_quantities(qty(), qty(), comparisons=[]), "duplicate quantity id q1")
    rejects(with_quantities(qty(), qty("q2"), comparisons=[{**comparison(), "quantity_ids": ["q1", "q1"]}]),
            "must be distinct")


def test_comparable_quantities_must_share_a_stated_method():
    other_assay = qty("q2", method="Wilcoxon rank-sum on cell-level counts")
    rejects(with_quantities(qty(), other_assay, comparisons=[comparison()]), "comparable with different method")
    assert rc.ResearchResult.model_validate(with_quantities(qty(), qty("q2", method=" Negative binomial GLM "),
                                                            comparisons=[comparison()]))
    assert rc.ResearchResult.model_validate(with_quantities(qty(), other_assay,
                                                            comparisons=[comparison("not_comparable")]))
