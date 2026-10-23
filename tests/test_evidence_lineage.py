"""#905: offline access metadata and conservative citation provenance."""

import asyncio

import pytest
from pydantic import ValidationError

from labhq.evidence.claims import (CitationLineage, Evidence, EvidenceLink, SourceRef, assess_lineage,
                                   independent_groups, ledger_errors)
from labhq.evidence.verify import StaticResolver, verify_sources
from labhq.research.contract import ResearchResult


def row(eid, **updates):
    value = dict(id=eid, kind="literature_claim", status="observed", observation=f"reported {eid}",
                 source=dict(uri=f"https://example.org/{eid}", accessed_at="2026-10-24", locator="Results, p. 2"),
                 directness="direct", source_level="primary", independence_group=eid,
                 assessment_reason="same question and population")
    value.update(updates)
    return Evidence.model_validate(value)


def original(eid="e1", **updates):
    return row(eid, lineage=dict(original=True, basis="Methods and Results locate the original experiment"), **updates)


def recited(eid="e2", refs=None, **updates):
    return row(eid, source_level="secondary", lineage=dict(cited_from=refs or ["e1"]), **updates)


def link(eid, relation="supports"):
    return EvidenceLink(claim_id="c1", claim_revision=1, evidence_id=eid, relation=relation,
                        rationale="the reported observation bears on the claim")


def test_legacy_payload_round_trip_has_no_required_new_metadata():
    payload = row("e1").model_dump(exclude_unset=True)
    assert "lineage" not in payload and "access_status" not in payload["source"]
    restored = Evidence.model_validate_json(row("e1").model_dump_json(exclude_unset=True))
    assert restored.model_dump(exclude_unset=True) == payload
    assert restored.source.access_status == "undetermined" and restored.lineage is None
    assert assess_lineage([restored])["e1"].status == "undetermined"
    assert independent_groups("c1", [restored], [link("e1")]) == ["e1"]


@pytest.mark.parametrize("access", ["full_text", "abstract_only", "not_obtained", "paywalled", "undetermined"])
def test_access_scope_is_separate_from_evidence_status(access):
    value = row("e1")
    payload = value.model_dump()
    payload["source"].update(access_status=access, methods_locator="Methods, p. 4", qc_locator="Table S1",
                             supplementary_status="missing")
    parsed = Evidence.model_validate(payload)
    assert parsed.status == "observed" and parsed.counts
    assert parsed.source.access_status == access
    assert parsed.source.methods_locator == "Methods, p. 4" and parsed.source.qc_locator == "Table S1"
    assert parsed.source.supplementary_status == "missing"
    payload.update(status="not_found")
    with pytest.raises(ValidationError, match="source.query"):
        Evidence.model_validate(payload)
    payload["source"]["query"] = "exact searched title, public catalog"
    assert Evidence.model_validate(payload).status == "not_found"
    payload.update(status="unavailable", status_detail="retrieval failed")
    assert not Evidence.model_validate(payload).counts


@pytest.mark.parametrize("field,value", [("access_status", "not_found"), ("supplementary_status", "unknown")])
def test_access_enums_reject_guesses(field, value):
    with pytest.raises(ValidationError):
        SourceRef(artifact_id="a1", **{field: value})


@pytest.mark.parametrize("payload", [dict(original=True), dict(original=True, basis=" "),
                                   dict(original=True, basis="checked", cited_from=["e1"]),
                                   dict(cited_from=["e1", "e1"]), dict(cited_from=[" "])])
def test_original_confirmation_needs_a_basis_and_no_parent(payload):
    with pytest.raises(ValidationError):
        CitationLineage.model_validate(payload)


def test_direct_and_multihop_recitations_use_one_confirmed_original_group():
    rows = [original(), recited(), recited("e3", ["e2"])]
    assessed = assess_lineage(rows)
    for eid in ("e1", "e2", "e3"):
        assert assessed[eid].status == "resolved"
        assert assessed[eid].original_evidence_ids == ["e1"]
        assert assessed[eid].independence_groups == ["e1"]
        assert assessed[eid].source_keys == ["uri:https://example.org/e1"]
    assert independent_groups("c1", rows, [link(r.id) for r in rows]) == ["e1"]
    assert independent_groups("c1", rows, [link("e3")]) == ["e1"]
    assert independent_groups("c1", rows, [link("e3", "context")]) == []


def test_identity_aliases_merge_originals_even_with_different_declared_groups():
    base = dict(accessed_at="2026-10-24", locator="Results")
    rows = [original(source=dict(id_scheme="doi", id_value="10.1234/original", **base)),
            original("e2", source=dict(uri="https://doi.org/10.1234/ORIGINAL", **base)),
            recited("e3", ["e1", "e2"])]
    assessed = assess_lineage(rows)
    assert assessed["e3"].original_evidence_ids == ["e1"]
    assert assessed["e3"].independence_groups == ["e1"]
    assert independent_groups("c1", rows, [link("e1"), link("e2"), link("e3")]) == ["e1"]
    assert any("same source" in e for e in ledger_errors([], rows, []))
    assert assess_lineage(list(reversed(rows))) == assessed


def test_artifact_path_aliases_and_identity_bridges_merge_roots():
    rows = [original(source=dict(artifact_id="a1", locator="row 1")),
            original("e2", source=dict(artifact_id="a2", locator="row 2")),
            recited("e3", ["e1", "e2"])]
    assessed = assess_lineage(rows, artifact_paths={"a1": "./out/results.tsv", "a2": "out\\results.tsv"})
    assert assessed["e3"].original_evidence_ids == ["e1"]
    assert assessed["e3"].source_keys == ["artifact:out/results.tsv"]
    assert independent_groups("c1", rows, [link("e3")],
                              artifact_paths={"a1": "out/results.tsv", "a2": "out/results.tsv"}) == ["e1"]
    # A row with two spellings bridges origins; it does not itself become confirmed.
    base = dict(accessed_at="2026-10-24", locator="Results")
    rows = [original(source=dict(id_scheme="doi", id_value="10.1234/original", **base)),
            original("e2", source=dict(uri="https://example.org/original", **base)),
            row("bridge", source=dict(id_scheme="doi", id_value="10.1234/original",
                                      uri="https://example.org/original", **base))]
    assert assess_lineage(rows)["e2"].original_evidence_ids == ["e1"]
    assert assess_lineage(rows)["bridge"].status == "undetermined"


@pytest.mark.parametrize("rows,status", [
    ([recited("e1", ["e1"])], "cycle"),
    ([recited("e1", ["e2"]), recited("e2", ["e1"])], "cycle"),
    ([recited("e1", ["missing"])], "dangling"),
    ([row("e1", lineage={})], "undetermined"),
    ([recited("e1", ["e2"]), row("e2")], "undetermined"),
    ([recited("e1", ["e2"]), original("e2", status="unavailable", status_detail="not read")], "undetermined"),
    ([row("e1", source_level="secondary", lineage=dict(original=True, basis="asserted"))], "undetermined"),
])
def test_incomplete_or_circular_lineage_never_becomes_independent_support(rows, status):
    assessed = assess_lineage(rows)["e1"]
    assert assessed.status == status
    assert not assessed.original_evidence_ids and not assessed.source_keys and not assessed.independence_groups
    assert independent_groups("c1", rows, [link("e1")]) == []
    assert any("citation lineage" in e for e in ledger_errors([], rows, [link("e1")]))
    assert not any("citation lineage" in e for e in ledger_errors([], rows, [link("e1", "context")]))


def test_a_good_branch_does_not_hide_a_cycle_dangling_or_unconfirmed_branch():
    for bad, expected in [(recited("e3", ["e2"]), "cycle"),
                          (recited("e3", ["missing"]), "dangling"), (row("e3"), "undetermined")]:
        rows = [original(), recited("e2", ["e1", "e3"]), bad]
        assert assess_lineage(rows)["e2"].status == expected
        assert independent_groups("c1", rows, [link("e2")]) == []


def test_shared_branches_are_not_cycles_and_long_chains_do_not_recurse():
    rows = [original(), recited(), recited("e3"), recited("e4", ["e2", "e3"])]
    assert assess_lineage(rows)["e4"].status == "resolved"
    rows = [original()] + [recited(f"e{i}", [f"e{i-1}"]) for i in range(2, 1050)]
    assert assess_lineage(rows)["e1049"].original_evidence_ids == ["e1"]


def test_distinct_originals_keep_their_groups_and_inferences_cannot_confirm_origins():
    rows = [original(), original("e2"), recited("e3", ["e1", "e2"])]
    assert independent_groups("c1", rows, [link("e3")]) == ["e1", "e2"]
    inferred = row("e4", kind="inference", status=None, derived_from=["e1"],
                   lineage=dict(original=True, basis="reasoning is not an experiment"))
    assert assess_lineage([*rows, inferred])["e4"].status == "undetermined"


@pytest.mark.parametrize("origin_found", [True, False])
def test_source_verifier_uses_original_groups_and_requires_original_resolution(origin_found):
    rows = [original(), recited(), recited("e3", ["e2"])]
    result = ResearchResult.model_validate(dict(
        schema_version=2, plan_sha256="a" * 64, step_id="s1",
        claims=[dict(id="c1", revision=1, statement="One empirical finding.", scope="public toy example",
                     kind="finding", status="supported", importance="major", status_reason="see evidence")],
        evidence=[r.model_dump() for r in rows], links=[link("e3").model_dump()], artifact_refs=[],
        not_established=[], failures=[], method_changes=[]))
    records = {("uri", r.source.uri): [dict(id_scheme="uri", id_value=r.source.uri)] for r in rows}
    resolver = StaticResolver(records, schemes={"uri"},
                              failures={} if origin_found else {("uri", rows[0].source.uri): "network"})
    report = asyncio.run(verify_sources(result, resolver))
    check = report.claims[0]
    assert check.state == ("verified" if origin_found else "unverified")
    assert check.independent_groups == (["e1"] if origin_found else [])
