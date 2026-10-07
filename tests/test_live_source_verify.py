"""#422 live citation checks. Every HTTP answer comes from tests/fixtures/live_sources."""

import asyncio
import copy
import json
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

from labhq.evidence.audit import render_verify, verify_request
from labhq.evidence.verify import (LiveSourceResolver, LookupFailed, SourceRecord, SourceUpdate,
                                   StaticResolver, verify_sources)
from labhq.settings import Settings
from tests.test_evidence_verify import build, by_claim, claim, link, row

FIXTURES = Path(__file__).parent / "fixtures" / "live_sources"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def idconv_missing(request):
    return httpx.Response(200, request=request, json={
        "status": "ok", "records": [{"requested-id": request.url.params.get("ids"),
                                        "errmsg": "Identifier not found in PMC"}]})


def resolver(handler, **kwargs):
    return LiveSourceResolver(transport=httpx.MockTransport(handler), **kwargs)


def doi_handler(fixtures):
    def handle(request):
        if request.url.host == "api.crossref.org":
            doi = unquote(request.url.path.split("/works/", 1)[1])
            return httpx.Response(200, request=request, json=fixture(fixtures[doi]))
        if request.url.host == "pmc.ncbi.nlm.nih.gov":
            return idconv_missing(request)
        raise AssertionError(f"unexpected request {request.method} {request.url}")
    return handle


def verify_doi(doi, fixed):
    result = build([claim("c1")], [row("e1", "doi", doi, kind="literature_claim")], [link("c1", "e1")])
    return asyncio.run(verify_sources(result, fixed))


def test_crossref_distinguishes_retracted_paper_notice_correction_and_normal():
    names = {
        "10.1021/am300292v": "crossref_retracted.json",
        "10.1021/acsami.9b11759": "crossref_retraction_notice.json",
        "10.1088/1361-6595/ab245f": "crossref_correction.json",
        "10.1016/j.lungcan.2026.109332": "crossref_concern.json",
        "10.1093/nar/gks1195": "crossref_normal.json",
    }
    reports = {doi: verify_doi(doi, resolver(doi_handler(names))) for doi in names}

    retracted = reports["10.1021/am300292v"]
    assert retracted.evidence[0].source_defects == ["retracted_by doi:10.1021/acsami.9b11759"]
    assert by_claim(retracted)["c1@1"].state == "defective"
    assert retracted.defective_evidence == ["e1"] and retracted.ok is False

    notice = reports["10.1021/acsami.9b11759"]
    assert notice.evidence[0].source_warnings == ["is_retraction_notice doi:10.1021/am300292v"]
    assert by_claim(notice)["c1@1"].state == "verified" and notice.ok is True

    correction = reports["10.1088/1361-6595/ab245f"]
    assert correction.evidence[0].source_warnings == ["is_correction_notice doi:10.1088/1361-6595/aaebdb"]
    assert by_claim(correction)["c1@1"].state == "verified" and correction.ok is True
    concern = reports["10.1016/j.lungcan.2026.109332"]
    assert concern.evidence[0].source_warnings == [
        "is_expression_of_concern doi:10.1016/j.lungcan.2003.10.006"]
    assert reports["10.1093/nar/gks1195"].warnings == []


def test_ncbi_maps_ids_and_pubmed_publication_types_are_directional():
    def normal(request):
        if request.url.host == "api.crossref.org":
            return httpx.Response(200, request=request, json=fixture("crossref_normal.json"))
        if "idconv" in request.url.path:
            return httpx.Response(200, request=request, json=fixture("ncbi_idconv_normal.json"))
        if "esummary" in request.url.path:
            return httpx.Response(200, request=request, json=fixture("pubmed_normal.json"))
        raise AssertionError(str(request.url))

    record = asyncio.run(resolver(normal).lookup("doi", "10.1093/nar/gks1195"))[0]
    assert {(alias.id_scheme, alias.id_value) for alias in record.same_as} == {
        ("pmid", "23193287"), ("pmcid", "PMC3531190")}

    def pubmed(request):
        if "idconv" in request.url.path:
            return idconv_missing(request)
        pmid = request.url.params["id"]
        name = "pubmed_retracted.json" if pmid == "9500320" else "pubmed_retraction_notice.json"
        payload = fixture(name)
        if pmid == "20137807":
            payload = copy.deepcopy(payload)
            payload["result"][pmid]["pubtype"] = ["Retraction of Publication"]
        return httpx.Response(200, request=request, json=payload)

    original = asyncio.run(resolver(pubmed).lookup("pmid", "9500320"))[0]
    notice = asyncio.run(resolver(pubmed).lookup("pmid", "20137807"))[0]
    assert original.updates[0].direction == "updated_by" and original.updates[0].update_type == "retraction"
    assert notice.updates[0].direction == "update_to" and notice.updates[0].update_type == "retraction"


def test_retry_after_429_then_success_and_timeout_is_requires_verification():
    scenario = fixture("scenarios.json")
    calls = 0
    waits = []

    def retry(request):
        nonlocal calls
        if request.url.host == "api.crossref.org":
            calls += 1
            if calls == 1:
                return httpx.Response(scenario["retry_then_success"]["first_status"], request=request,
                                      headers={"Retry-After": scenario["retry_then_success"]["retry_after"]})
            return httpx.Response(200, request=request,
                                  json=fixture(scenario["retry_then_success"]["then"]))
        return idconv_missing(request)

    async def fake_sleep(delay):
        waits.append(delay)

    report = verify_doi("10.1093/nar/gks1195", resolver(retry, sleep=fake_sleep))
    assert calls == 2 and waits == [0.0] and report.evidence[0].resolution.status == "found"

    def timeout(request):
        assert scenario["timeout"]["exception"] == "httpx.ReadTimeout"
        raise httpx.ReadTimeout("recorded timeout", request=request)

    timed_out = verify_doi("10.1093/nar/gks1195", resolver(timeout))
    resolution = timed_out.evidence[0].resolution
    assert (resolution.lookup, resolution.status, resolution.error_kind) == (
        "failed", "requires_verification", "timeout")
    assert timed_out.lookup_failures == ["e1"]


def test_doi_head_fallback_and_contact_etiquette():
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.host == "api.crossref.org":
            return httpx.Response(404, request=request)
        if request.url.host == "doi.org":
            return httpx.Response(302, request=request, headers={"Location": "https://example.org/article"})
        return idconv_missing(request)

    records = asyncio.run(resolver(handle, contact="maintainer@example.org").lookup("doi", "10.5555/example"))
    assert records[0].id_value == "10.5555/example"
    assert seen[0].url.params["mailto"] == "maintainer@example.org"
    assert "mailto:maintainer@example.org" in seen[0].headers["User-Agent"]
    assert [request.method for request in seen] == ["GET", "HEAD", "GET"]


def test_pubmed_error_record_is_not_found():
    def handle(request):
        if "idconv" in request.url.path:
            return idconv_missing(request)
        return httpx.Response(200, request=request, json=fixture("pubmed_missing.json"))

    report = asyncio.run(verify_sources(
        build([claim("c1")], [row("e1", "pmid", "999999999")], [link("c1", "e1")]), resolver(handle)))
    assert report.evidence[0].resolution.status == "not_found"
    assert by_claim(report)["c1@1"].state == "defective"


@pytest.mark.parametrize("missing", ["result", "requested_row"])
def test_incomplete_pubmed_response_is_invalid_not_not_found(missing):
    def handle(request):
        if "idconv" in request.url.path:
            return idconv_missing(request)
        payload = copy.deepcopy(fixture("pubmed_normal.json"))
        if missing == "result":
            payload.pop("result")
        else:
            payload["result"].pop("23193287")
        return httpx.Response(200, request=request, json=payload)

    with pytest.raises(LookupFailed, match="result object|requested PMID 23193287") as caught:
        asyncio.run(resolver(handle).lookup("pmid", "23193287"))
    assert caught.value.kind == "invalid_response"


def test_doi_primary_retraction_survives_optional_ncbi_enrichment_timeout():
    scenario = fixture("scenarios.json")

    def handle(request):
        if request.url.host == "api.crossref.org":
            return httpx.Response(200, request=request, json=fixture("crossref_retracted.json"))
        assert "idconv" in request.url.path and scenario["timeout"]["exception"] == "httpx.ReadTimeout"
        raise httpx.ReadTimeout("recorded timeout", request=request)

    report = verify_doi("10.1021/am300292v", resolver(handle))
    resolution = report.evidence[0].resolution
    assert resolution.status == "found" and resolution.record is not None
    assert report.evidence[0].source_defects == ["retracted_by doi:10.1021/acsami.9b11759"]
    assert resolution.record.enrichment_failures[0].stage == "ncbi_id_converter"
    assert resolution.record.enrichment_failures[0].error_kind == "timeout"
    assert any("enrichment_unverified=ncbi_id_converter:timeout" in warning for warning in report.warnings)


def test_pmid_primary_retraction_survives_optional_ncbi_enrichment_timeout():
    scenario = fixture("scenarios.json")
    seen = []

    def handle(request):
        seen.append((request.url.host, request.url.path, request.url.params.get("db")))
        if "esummary" in request.url.path:
            return httpx.Response(200, request=request, json=fixture("pubmed_retracted.json"))
        assert "idconv" in request.url.path and scenario["timeout"]["exception"] == "httpx.ReadTimeout"
        raise httpx.ReadTimeout("recorded timeout", request=request)

    result = build([claim("c1")], [row("e1", "pmid", "9500320", kind="literature_claim")],
                   [link("c1", "e1")])
    report = asyncio.run(verify_sources(result, resolver(handle)))
    resolution = report.evidence[0].resolution

    assert seen[0][1].endswith("esummary.fcgi") and seen[0][2] == "pubmed"
    assert resolution.status == "found" and resolution.record is not None
    assert report.evidence[0].source_defects == ["retracted_by doi:10.1016/S0140-6736(10)60175-4"]
    assert resolution.record.enrichment_failures[0].stage == "ncbi_id_converter"
    assert resolution.record.enrichment_failures[0].error_kind == "timeout"


def test_pmcid_primary_and_retraction_survive_optional_id_conversion_timeout():
    scenario = fixture("scenarios.json")
    seen = []

    def handle(request):
        seen.append((request.url.host, request.url.path, request.url.params.get("db")))
        if "esummary" in request.url.path and request.url.params.get("db") == "pmc":
            return httpx.Response(200, request=request, json=fixture("pmc_retracted.json"))
        if "idconv" in request.url.path:
            assert scenario["timeout"]["exception"] == "httpx.ReadTimeout"
            raise httpx.ReadTimeout("recorded timeout", request=request)
        if "esummary" in request.url.path and request.url.params.get("db") == "pubmed":
            return httpx.Response(200, request=request, json=fixture("pubmed_pmc_retracted.json"))
        raise AssertionError(str(request.url))

    result = build([claim("c1")], [row("e1", "pmcid", "PMC13546610", kind="literature_claim")],
                   [link("c1", "e1")])
    report = asyncio.run(verify_sources(result, resolver(handle)))
    resolution = report.evidence[0].resolution

    assert seen[0][1].endswith("esummary.fcgi") and seen[0][2] == "pmc"
    assert resolution.status == "found" and resolution.record is not None
    assert report.evidence[0].source_defects == ["retracted_by doi:10.7759/cureus.r249"]
    assert resolution.record.enrichment_failures[0].stage == "ncbi_id_converter"
    assert resolution.record.enrichment_failures[0].error_kind == "timeout"
    assert ("pmid", "42703480") in {
        (alias.id_scheme, alias.id_value) for alias in resolution.record.same_as}


def test_labhq_verify_calls_live_checks_only_when_enabled(tmp_path, monkeypatch):
    result = build([claim("c1")], [row("e1", "doi", "10.1021/am300292v")], [link("c1", "e1")])
    request = {"id": "req_live", "text": "check", "status": "done", "outcome": "research_reported",
               "plan": {"steps": [{"id": "s1"}]},
               "research_contract": {"plan_sha256": "b" * 64},
               "results": {"s1": {"structured": result.model_dump(mode="json")}}}
    settings = Settings()
    settings.runner.workspace_root = str(tmp_path)
    fixed = StaticResolver({("doi", "10.1021/am300292v"): [SourceRecord(
        id_scheme="doi", id_value="10.1021/am300292v",
        updates=[SourceUpdate(direction="updated_by", update_type="retraction",
                              related={"id_scheme": "doi", "id_value": "10.1021/acsami.9b11759"})])]})
    monkeypatch.setattr("labhq.evidence.verify.LiveSourceResolver", lambda **_kwargs: fixed)

    off = verify_request(request, settings)
    assert fixed.calls == [] and off["source_verification"] == []

    settings.research.live_source_check = True
    checked = verify_request(request, settings)
    assert fixed.calls == [("doi", "10.1021/am300292v")]
    assert checked["exit_code"] == 1 and any("live source" in problem for problem in checked["problems"])
    rendered = render_verify(checked)
    assert "Live source verification:" in rendered and "defect=retracted_by" in rendered


def test_labhq_verify_fails_for_skipped_live_source_and_shows_incomplete_check(tmp_path, monkeypatch):
    result = build([claim("c1")], [row("e1", "geo", "GSE79973")], [link("c1", "e1")])
    request = {"id": "req_skipped", "text": "check", "status": "done", "outcome": "research_reported",
               "plan": {"steps": [{"id": "s1"}]},
               "research_contract": {"plan_sha256": "b" * 64},
               "results": {"s1": {"structured": result.model_dump(mode="json")}}}
    settings = Settings()
    settings.runner.workspace_root = str(tmp_path)
    settings.research.live_source_check = True
    fixed = StaticResolver(schemes={"doi", "pmid", "pmcid"})
    monkeypatch.setattr("labhq.evidence.verify.LiveSourceResolver", lambda **_kwargs: fixed)

    checked = verify_request(request, settings)

    assert checked["exit_code"] == 2
    assert any("검사 미완료" in reason and "unsupported_scheme" in reason for reason in checked["reasons"])
    rendered = render_verify(checked)
    assert "geo:GSE79973 requires_verification" in rendered and "확인 못함:" in rendered


def test_labhq_verify_reports_cross_scheme_recitation_as_a_problem(tmp_path, monkeypatch):
    doi = "10.1145/3065386"
    pmid = "28937623"
    result = build(
        [claim("c1")],
        [row("e1", "doi", doi, kind="literature_claim", group="paper_a"),
         row("e2", "pmid", pmid, kind="literature_claim", group="paper_b")],
        [link("c1", "e1"), link("c1", "e2")])
    request = {"id": "req_recitation", "text": "check", "status": "done", "outcome": "research_reported",
               "plan": {"steps": [{"id": "s1"}]},
               "research_contract": {"plan_sha256": "b" * 64},
               "results": {"s1": {"structured": result.model_dump(mode="json")}}}
    settings = Settings()
    settings.runner.workspace_root = str(tmp_path)
    settings.research.live_source_check = True
    fixed = StaticResolver({
        ("doi", doi): [SourceRecord(id_scheme="doi", id_value=doi,
                                    same_as=[{"id_scheme": "pmid", "id_value": pmid}])],
        ("pmid", pmid): [SourceRecord(id_scheme="pmid", id_value=pmid,
                                      same_as=[{"id_scheme": "doi", "id_value": doi}])],
    })
    monkeypatch.setattr("labhq.evidence.verify.LiveSourceResolver", lambda **_kwargs: fixed)

    checked = verify_request(request, settings)

    assert checked["exit_code"] == 1
    assert any("재인용" in problem for problem in checked["problems"])
    assert "recitation=" in render_verify(checked)
