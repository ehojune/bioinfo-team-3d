"""#90 R06 / #58: source resolver and evidence verifier. Offline only; live lookup stays off."""

import asyncio
import time

import pytest
from pydantic import ValidationError

from labhq.evidence.verify import LookupFailed, Resolution, SourceRecord, StaticResolver, verify_sources
from labhq.research.contract import ResearchResult

DOI = "10.1038/s41586-020-2649-2"


def row(eid, scheme=None, value=None, *, kind="database_annotation", group=None, version=None, artifact=None):
    source = {"artifact_id": artifact, "locator": "row IL6"} if artifact else {
        "id_scheme": scheme, "id_value": value, "accessed_at": "2026-10-01", "locator": f"{value} summary"}
    if version:
        source["version"] = version
    return {"id": eid, "kind": kind, "observation": f"{eid} observation", "status": "observed", "source": source,
            "directness": "direct", "source_level": "primary", "independence_group": group or eid,
            "assessment_reason": "answers the asked comparison"}


def claim(cid, status="supported"):
    return {"id": cid, "revision": 1, "statement": f"{cid} statement.", "scope": "cohort", "kind": "finding",
            "status": status, "importance": "major", "status_reason": "see linked evidence"}


def link(cid, eid, relation="supports"):
    return {"claim_id": cid, "claim_revision": 1, "evidence_id": eid, "relation": relation, "rationale": "same donors"}


def build(claims, evidence, links):
    return ResearchResult.model_validate({
        "schema_version": 2, "plan_sha256": "b" * 64, "step_id": "s1", "claims": claims, "evidence": evidence,
        "links": links, "artifact_refs": [{"artifact_id": "a1", "path": "out/de.tsv"}],
        "not_established": [], "failures": [], "method_changes": []})


def resolver(**extra):
    return StaticResolver({("doi", DOI): [{"id_scheme": "doi", "id_value": DOI, "title": "Array programming"}],
                           ("geo", "GSE99999999"): [],
                           ("refseq", "NM_004985"): [{"id_scheme": "refseq", "id_value": "NM_004985", "version": "4"}]},
                          schemes={"doi", "geo", "refseq", "dbsnp"}, **extra)


def by_claim(report):
    return {check.claim: check for check in report.claims}


def test_network_failure_is_requires_verification_not_absence():
    result = build([claim("c1")], [row("e1", "geo", "GSE79973")], [link("c1", "e1")])
    report = asyncio.run(verify_sources(result, resolver(failures={("geo", "GSE79973"): "network"})))
    resolution = report.evidence[0].resolution
    assert (resolution.lookup, resolution.status, resolution.error_kind) == ("failed", "requires_verification",
                                                                               "network")
    check = by_claim(report)["c1@1"]
    assert check.state == "unverified" and check.unverified_evidence == ["e1"] and not check.defects
    assert report.lookup_failures == ["e1"] and report.ok is False


def test_not_found_and_malformed_ids_are_defects_without_guessing():
    result = build([claim("c1"), claim("c2")],
                   [row("e1", "geo", "GSE99999999"), row("e2", "dbsnp", "rs-12"), row("e3", "doi", DOI)],
                   [link("c1", "e1"), link("c1", "e3"), link("c2", "e2")])
    fixed = resolver()
    report = asyncio.run(verify_sources(result, fixed))
    resolutions = {check.evidence_id: check.resolution for check in report.evidence}
    assert (resolutions["e1"].lookup, resolutions["e1"].status) == ("succeeded", "not_found")
    assert (resolutions["e2"].lookup, resolutions["e2"].status, resolutions["e2"].error_kind) == (
        "skipped", "insufficient", "malformed_id")
    assert ("dbsnp", "rs-12") not in fixed.calls  # a malformed ID is never sent to the authority
    checks = by_claim(report)
    # One fabricated ID makes the claim defective even though another source verified.
    assert checks["c1@1"].state == "defective" and checks["c1@1"].verified_evidence == ["e3"]
    assert "GSE99999999 which is not_found" in checks["c1@1"].defects[0]
    assert checks["c2@1"].state == "defective" and report.ok is False


def test_found_sources_verify_and_report_location_and_independence():
    result = build([claim("c1"), claim("c2", status="proposed")],
                   [row("e1", "doi", DOI, kind="literature_claim", group="paper"),
                    row("e2", artifact="a1", kind="experimental", group="cohort")],
                   [link("c1", "e1"), link("c1", "e2"), link("c2", "e1", "context")])
    report = asyncio.run(verify_sources(result, resolver(), observed_artifacts={"out/de.tsv": "c" * 64}))
    checks = by_claim(report)
    assert checks["c1@1"].state == "verified" and checks["c1@1"].independent_groups == ["cohort", "paper"]
    assert checks["c1@1"].support_review == "reviewer_required"  # an ID that exists is not a fitting citation
    assert checks["c2@1"].state == "not_asserted" and report.ok is True
    located = {check.evidence_id: check for check in report.evidence}
    assert located["e1"].source_location == f"{DOI} summary"
    assert located["e1"].resolution.record.title == "Array programming"
    assert located["e2"].resolution.record.version == "c" * 64


def test_artifact_outside_the_observed_manifest_is_not_found():
    result = build([claim("c1")], [row("e1", artifact="a1", kind="experimental")], [link("c1", "e1")])
    report = asyncio.run(verify_sources(result, resolver(), observed_artifacts={"out/other.tsv": "d" * 64}))
    assert report.evidence[0].resolution.status == "not_found" and by_claim(report)["c1@1"].state == "defective"


def test_version_mismatch_ambiguity_and_alias_are_conflicting():
    result = build([claim("c1")], [row("e1", "refseq", "NM_004985", version="5")], [link("c1", "e1")])
    report = asyncio.run(verify_sources(result, resolver()))
    assert report.evidence[0].resolution.status == "conflicting"
    assert "cited version 5" in report.evidence[0].resolution.detail
    two = StaticResolver({("geo", "GSE1"): [{"id_scheme": "geo", "id_value": "GSE1", "version": "a"},
                                            {"id_scheme": "geo", "id_value": "GSE1", "version": "b"}],
                          ("geo", "GSE2"): [{"id_scheme": "geo", "id_value": "GSE3"}]})
    result = build([claim("c1")], [row("e1", "geo", "GSE1"), row("e2", "geo", "GSE2")],
                   [link("c1", "e1"), link("c1", "e2")])
    resolutions = {c.evidence_id: c.resolution for c in asyncio.run(verify_sources(result, two)).evidence}
    assert resolutions["e1"].status == "conflicting" and len(resolutions["e1"].candidates) == 2
    assert resolutions["e2"].status == "conflicting" and "different identifier" in resolutions["e2"].detail


def test_live_lookup_is_off_by_default_and_artifacts_need_the_manifest():
    result = build([claim("c1")], [row("e1", "doi", DOI), row("e2", artifact="a1", kind="experimental")],
                   [link("c1", "e1"), link("c1", "e2")])
    report = asyncio.run(verify_sources(result))
    kinds = {c.evidence_id: (c.resolution.lookup, c.resolution.status, c.resolution.error_kind) for c in report.evidence}
    assert kinds == {"e1": ("skipped", "requires_verification", "disabled"),
                     "e2": ("skipped", "requires_verification", "manifest_unavailable")}
    assert report.resolver == "none" and by_claim(report)["c1@1"].state == "unverified" and not report.lookup_failures


class SlowResolver:
    name = "slow"

    def supports(self, scheme):
        return True

    async def lookup(self, scheme, value):
        await asyncio.sleep(5)
        return []


class BrokenResolver:
    def __init__(self, behavior):
        self.name, self.behavior = "broken", behavior

    def supports(self, scheme):
        return True

    async def lookup(self, scheme, value):
        if self.behavior == "crash":
            raise RuntimeError("socket closed")
        if self.behavior == "rate_limited":
            raise LookupFailed("rate_limited", "429 Retry-After 60")
        return {"not": "a list"}


@pytest.mark.parametrize("make, kind", [
    (SlowResolver, "timeout"),
    (lambda: BrokenResolver("crash"), "resolver_error"),
    (lambda: BrokenResolver("rate_limited"), "rate_limited"),
    (lambda: BrokenResolver("garbage"), "invalid_response"),
])
def test_incomplete_lookups_never_become_not_found(make, kind):
    result = build([claim("c1")], [row("e1", "doi", DOI)], [link("c1", "e1")])
    report = asyncio.run(verify_sources(result, make(), timeout_s=0.05))
    resolution = report.evidence[0].resolution
    assert (resolution.lookup, resolution.status, resolution.error_kind) == ("failed", "requires_verification", kind)
    assert by_claim(report)["c1@1"].state == "unverified"


def test_unsupported_scheme_is_skipped_and_lookups_are_cached():
    result = build([claim("c1")], [row("e1", "doi", DOI), row("e2", "doi", DOI.upper(), group="e1"),
                                   row("e3", "ensembl", "ENSG00000136244")],
                   [link("c1", "e1"), link("c1", "e2"), link("c1", "e3")])
    fixed = resolver()
    report = asyncio.run(verify_sources(result, fixed))
    assert fixed.calls == [("doi", DOI.casefold())]
    e3 = next(c for c in report.evidence if c.evidence_id == "e3").resolution
    assert (e3.lookup, e3.status, e3.error_kind) == ("skipped", "requires_verification", "unsupported_scheme")


@pytest.mark.parametrize("fields, match", [
    ({"lookup": "failed", "status": "not_found", "error_kind": "network"}, "never not_found"),
    ({"lookup": "failed", "status": "requires_verification", "error_kind": "disabled"}, "failure kind"),
    ({"lookup": "skipped", "status": "found", "error_kind": "disabled"}, "skipped lookup"),
    ({"lookup": "succeeded", "status": "found"}, "matching record"),
    ({"lookup": "succeeded", "status": "requires_verification"}, "completed lookup"),
    ({"lookup": "succeeded", "status": "conflicting"}, "disagreeing candidates"),
])
def test_resolution_keeps_lookup_outcome_and_id_status_apart(fields, match):
    with pytest.raises(ValidationError, match=match):
        Resolution(id_scheme="doi", id_value=DOI, resolver="x", **fields)
    assert Resolution(id_scheme="doi", id_value=DOI, resolver="x", lookup="succeeded", status="found",
                      record=SourceRecord(id_scheme="doi", id_value=DOI)).status == "found"


def test_record_from_another_scheme_with_the_same_number_is_conflicting():
    other_db = StaticResolver({("pmid", "12345"): [{"id_scheme": "clinvar", "id_value": "12345"}]})
    result = build([claim("c1")], [row("e1", "pmid", "12345")], [link("c1", "e1")])
    report = asyncio.run(verify_sources(result, other_db))
    assert report.evidence[0].resolution.status == "conflicting" and report.ok is False
    assert "different scheme" in report.evidence[0].resolution.detail


def test_artifact_cited_version_must_match_the_observed_hash():
    cited = row("e1", artifact="a1", kind="experimental")
    cited["source"]["version"] = "a" * 64
    result = build([claim("c1")], [cited], [link("c1", "e1")])
    report = asyncio.run(verify_sources(result, observed_artifacts={"out/de.tsv": "b" * 64}))
    assert report.evidence[0].resolution.status == "conflicting" and by_claim(report)["c1@1"].state == "defective"
    report = asyncio.run(verify_sources(result, observed_artifacts={"out/de.tsv": "a" * 64}))
    assert report.evidence[0].resolution.status == "found" and report.ok is True


@pytest.mark.parametrize("accession", ["NZ_CP012345.1", "NM_004985.5", "WP_000000001.1", "NZ_AAAA01000001.1"])
def test_valid_refseq_accessions_reach_the_resolver(accession):
    fixed = StaticResolver({("refseq", accession): [{"id_scheme": "refseq", "id_value": accession}]})
    result = build([claim("c1")], [row("e1", "refseq", accession)], [link("c1", "e1")])
    report = asyncio.run(verify_sources(result, fixed))
    assert report.evidence[0].resolution.status == "found" and fixed.calls == [("refseq", accession.casefold())]


def test_claim_with_an_unverified_source_is_not_verified():
    result = build([claim("c1")], [row("e1", "doi", DOI), row("e2", "geo", "GSE79973")],
                   [link("c1", "e1"), link("c1", "e2")])
    report = asyncio.run(verify_sources(result, resolver(failures={("geo", "GSE79973"): "network"})))
    check = by_claim(report)["c1@1"]
    assert check.verified_evidence == ["e1"] and check.unverified_evidence == ["e2"]
    assert check.state == "unverified" and report.ok is False
    # An unverified source on the contradicting side also keeps the claim open.
    result = build([claim("c1", status="partially_supported")], [row("e1", "doi", DOI), row("e2", "geo", "GSE79973")],
                   [link("c1", "e1"), link("c1", "e2", "contradicts")])
    report = asyncio.run(verify_sources(result, resolver(failures={("geo", "GSE79973"): "timeout"})))
    assert by_claim(report)["c1@1"].state == "unverified" and report.ok is False


def test_uri_lookups_keep_path_case_and_fold_only_scheme_and_host():
    uri = "https://example.org/files/Data.tsv"
    fixed = StaticResolver({("uri", uri): [{"id_scheme": "uri", "id_value": uri}]})

    def uri_row(eid, value, group):
        return {**row(eid, "doi", DOI, group=group), "source": {"uri": value, "accessed_at": "2026-10-01",
                                                                "locator": "row 1"}}

    result = build([claim("c1"), claim("c2")],
                   [uri_row("e1", uri, "g1"), uri_row("e2", "https://example.org/files/data.tsv", "g2"),
                    uri_row("e3", "HTTPS://EXAMPLE.ORG/files/Data.tsv", "g1")],
                   [link("c1", "e1"), link("c2", "e2"), link("c1", "e3")])
    report = asyncio.run(verify_sources(result, fixed))
    status = {check.evidence_id: check.resolution.status for check in report.evidence}
    assert status == {"e1": "found", "e2": "not_found", "e3": "found"}
    assert by_claim(report)["c2@1"].state == "defective" and by_claim(report)["c1@1"].state == "verified"
    assert len(fixed.calls) == 2


def test_a_defective_source_fails_the_report_even_as_context():
    fake = row("e1", "geo", "GSE99999999")
    malformed = row("e2", "dbsnp", "rs-12")
    inferred = {"id": "e3", "kind": "inference", "observation": "pathway guess", "derived_from": ["e4"],
                "source": {"id_scheme": "doi", "id_value": "10.9999/made-up", "accessed_at": "2026-10-01"}}
    real = row("e4", "doi", DOI)
    result = build([claim("c1"), claim("c2", status="proposed")], [fake, malformed, inferred, real],
                   [link("c1", "e4"), link("c2", "e1", "context")])
    report = asyncio.run(verify_sources(result, resolver()))
    assert by_claim(report)["c1@1"].state == "verified" and by_claim(report)["c2@1"].state == "not_asserted"
    assert report.defective_evidence == ["e1", "e2", "e3"] and report.ok is False


def test_every_identifier_a_source_carries_is_checked():
    # A downloaded record: the DOI resolves, but the file the row reads is not in the observed manifest.
    both = row("e1", "doi", DOI, kind="experimental")
    both["source"]["artifact_id"] = "a1"
    result = build([claim("c1")], [both], [link("c1", "e1")])
    report = asyncio.run(verify_sources(result, resolver(), observed_artifacts={"out/other.tsv": "b" * 64}))
    check = report.evidence[0]
    assert [(r.id_scheme, r.status) for r in check.resolutions] == [("doi", "found"), ("artifact", "not_found")]
    assert check.resolution.id_scheme == "artifact"
    assert by_claim(report)["c1@1"].state == "defective" and report.ok is False
    # With both identifiers, version belongs to the external record, so the artifact is checked by presence.
    report = asyncio.run(verify_sources(result, resolver(), observed_artifacts={"out/de.tsv": "b" * 64}))
    assert [r.status for r in report.evidence[0].resolutions] == ["found", "found"] and report.ok is True


def test_artifact_paths_match_the_manifest_across_separators():
    result = ResearchResult.model_validate({
        "schema_version": 2, "plan_sha256": "b" * 64, "step_id": "s1", "claims": [claim("c1")],
        "evidence": [row("e1", artifact="a1", kind="experimental")], "links": [link("c1", "e1")],
        "artifact_refs": [{"artifact_id": "a1", "path": ".\\out\\de.tsv"}],
        "not_established": [], "failures": [], "method_changes": []})
    report = asyncio.run(verify_sources(result, observed_artifacts={"out/de.tsv": "c" * 64}))
    assert report.evidence[0].resolution.status == "found" and report.ok is True
    clash = asyncio.run(verify_sources(result, observed_artifacts={"out/de.tsv": "c" * 64,
                                                                   "out\\de.tsv": "d" * 64}))
    assert clash.evidence[0].resolution.status == "conflicting" and clash.ok is False


def searched(eid, scheme, value):
    """A completed search with no hits: the source is where it searched, the query what it searched for."""
    found_nothing = row(eid, scheme, value)
    found_nothing["status"] = "not_found"
    found_nothing["source"]["query"] = "gene == IL6, all samples"
    return found_nothing


def test_a_zero_result_search_still_resolves_where_it_searched():
    # #129: unlike a failed lookup, a finished search names a real place; a made-up or malformed one is a defect.
    result = build([claim("c1")], [row("e1", "doi", DOI), searched("e2", "geo", "GSE99999999"),
                                   searched("e3", "dbsnp", "rs-12")], [link("c1", "e1")])
    report = asyncio.run(verify_sources(result, resolver()))
    assert {c.evidence_id: c.resolution.status for c in report.evidence} == {
        "e1": "found", "e2": "not_found", "e3": "insufficient"}
    assert report.defective_evidence == ["e2", "e3"] and report.ok is False
    real_scope = build([claim("c1")], [row("e1", "doi", DOI), searched("e2", "refseq", "NM_004985")],
                       [link("c1", "e1")])
    assert asyncio.run(verify_sources(real_scope, resolver())).ok is True


def test_failed_and_unavailable_retrievals_are_still_not_resolved():
    rows = [row("e1", "doi", DOI)]
    for eid, status in (("e2", "failed"), ("e3", "unavailable")):
        attempt = row(eid, "geo", "GSE99999999", group="mirror")
        attempt.update(status=status, status_detail="the GEO mirror returned 503")
        rows.append(attempt)
    report = asyncio.run(verify_sources(build([claim("c1")], rows, [link("c1", "e1")]), resolver()))
    assert [c.evidence_id for c in report.evidence] == ["e1"] and report.ok is True


class PacedResolver:
    """Answers after ``delay`` seconds, or never for ``hang``; records how many lookups ran at once."""

    name = "paced"

    def __init__(self, delay, hang=()):
        self.delay, self.hang = delay, set(hang)
        self.active = self.peak = 0
        self.calls = []

    def supports(self, scheme):
        return True

    async def lookup(self, scheme, value):
        self.calls.append(value)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(3600 if value in self.hang else self.delay)
        finally:
            self.active -= 1
        return [SourceRecord(id_scheme=scheme, id_value=value)]


def many_sources(n):
    return build([claim("c1")], [row(f"e{i}", "geo", f"GSE{1000 + i}") for i in range(n)],
                 [link("c1", f"e{i}") for i in range(n)])


def test_lookups_run_concurrently_under_a_cap_and_finish_within_the_deadline():
    # #116: eight 0.2 s lookups one by one would take 1.6 s, past the deadline.
    paced = PacedResolver(0.2)
    start = time.monotonic()
    report = asyncio.run(verify_sources(many_sources(8), paced, concurrency=4, deadline_s=1.5))
    assert time.monotonic() - start < 1.5
    assert {c.resolution.status for c in report.evidence} == {"found"} and report.ok is True
    assert paced.peak == 4


def test_lookups_past_the_report_deadline_are_unverified_not_absent():
    # Two lookups hang and hold both slots; the per-lookup timeout (20 s) is not what ends the report.
    paced = PacedResolver(0.05, hang={"GSE1001", "GSE1002"})
    start = time.monotonic()
    report = asyncio.run(verify_sources(many_sources(6), paced, concurrency=2, deadline_s=0.5, timeout_s=20))
    assert time.monotonic() - start < 2
    resolutions = {c.evidence_id: c.resolution for c in report.evidence}
    assert resolutions["e0"].status == "found"
    for eid in ("e1", "e2", "e3", "e4", "e5"):
        assert (resolutions[eid].lookup, resolutions[eid].status, resolutions[eid].error_kind) == (
            "failed", "requires_verification", "timeout")
        assert "deadline" in resolutions[eid].detail
    assert paced.calls == ["GSE1000", "GSE1001", "GSE1002"]  # nothing is sent after the deadline
    check = by_claim(report)["c1@1"]
    assert check.state == "unverified" and not check.defects and not report.defective_evidence
    assert report.lookup_failures == ["e1", "e2", "e3", "e4", "e5"] and report.ok is False


def test_concurrency_must_allow_at_least_one_lookup():
    with pytest.raises(ValueError, match="concurrency"):
        asyncio.run(verify_sources(many_sources(1), PacedResolver(0), concurrency=0))
