"""#90 R06 / #58: source resolver and evidence verifier. Offline only; live lookup stays off."""

import asyncio

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
