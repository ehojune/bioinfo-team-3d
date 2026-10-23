"""#891: the gateway's live source check counts its outbound HTTP requests in the request's lookup counter
(annot.request_lookup_limit), and a request refused at the limit is 'could not verify', never 'not found'."""
import asyncio
import json
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

from labhq.evidence.verify import LiveSourceResolver, LookupFailed, verify_sources
from labhq.runner.lookup_admission import FILE, LookupAdmission, admission_for
from labhq.settings import Settings
from tests.test_evidence_verify import build, claim, link, row

FIXTURES = Path(__file__).parent / "fixtures" / "live_sources"
DOI = "10.1093/nar/gks1195"


def handler(request):
    if request.url.host == "api.crossref.org":
        doi = unquote(request.url.path.split("/works/", 1)[1])
        assert doi == DOI
        return httpx.Response(200, request=request, json=json.loads(
            (FIXTURES / "crossref_normal.json").read_text(encoding="utf-8")))
    if request.url.host == "pmc.ncbi.nlm.nih.gov":
        return httpx.Response(200, request=request, json={"status": "ok", "records": [{
            "requested-id": request.url.params.get("ids"), "errmsg": "Identifier not found in PMC"}]})
    raise AssertionError(f"unexpected request {request.method} {request.url}")


def resolver(admission, rid="req_a", **kwargs):
    return LiveSourceResolver(transport=httpx.MockTransport(handler), admission=admission, request_id=rid, **kwargs)


def verify(fixed):
    result = build([claim("c1")], [row("e1", "doi", DOI, kind="literature_claim")], [link("c1", "e1")])
    return asyncio.run(verify_sources(result, fixed))


def test_every_outbound_request_of_the_source_check_is_counted_with_its_host(tmp_path):
    admission = LookupAdmission(tmp_path / FILE, 50)
    report = verify(resolver(admission))
    sends = admission.sends("req_a")
    assert sends and report.evidence[0].resolution.status == "found"
    assert {row["tool"] for row in sends} == {"source_verify"} and "api.crossref.org" in {row["host"] for row in sends}
    assert all(row["http_status"] == 200 and row["outcome"] == "ok" for row in sends)  # each send was finished
    assert [row["seq"] for row in sends] == list(range(1, len(sends) + 1))


def test_the_count_is_shared_with_labhq_annot_requests_of_the_same_request(tmp_path):
    admission = LookupAdmission(tmp_path / FILE, 50)
    for _ in range(3):
        assert admission.admit("req_a", "task", "bio", "annot", "pubmed.ncbi.nlm.nih.gov")["admitted"]
    verify(resolver(admission))
    sends = admission.sends("req_a")
    assert [row["tool"] for row in sends[:3]] == ["annot"] * 3 and sends[3]["tool"] == "source_verify"
    assert admission.sends("req_other") == []  # another request keeps its own count


def test_at_the_limit_the_lookup_is_could_not_verify_not_not_found_and_nothing_is_sent(tmp_path):
    admission = LookupAdmission(tmp_path / FILE, 2)
    for _ in range(2):  # the request's allowance is already used up by labhq_annot
        admission.admit("req_a", "task", "bio", "annot", "pubmed.ncbi.nlm.nih.gov")
    sent = []

    def counting(request):
        sent.append(request)
        return handler(request)

    fixed = LiveSourceResolver(transport=httpx.MockTransport(counting), admission=admission, request_id="req_a")
    resolution = verify_sources(build([claim("c1")], [row("e1", "doi", DOI, kind="literature_claim")],
                                      [link("c1", "e1")]), fixed)
    resolution = asyncio.run(resolution).evidence[0].resolution
    assert (resolution.lookup, resolution.status, resolution.error_kind) == ("failed", "requires_verification",
                                                                             "lookup_limit")
    assert resolution.status != "not_found" and not sent
    assert len(admission.sends("req_a")) == 2  # a refused request is not counted
    with pytest.raises(LookupFailed) as caught:
        asyncio.run(fixed.lookup("doi", DOI))
    assert caught.value.kind == "lookup_limit"


def test_a_limit_reached_after_the_primary_lookup_keeps_the_found_record(tmp_path):
    admission = LookupAdmission(tmp_path / FILE, 1)  # the Crossref read passes; the enrichment request is refused
    report = verify(resolver(admission))
    resolution = report.evidence[0].resolution
    assert resolution.status == "found" and len(admission.sends("req_a")) == 1
    assert any(item.error_kind == "lookup_limit" for item in resolution.record.enrichment_failures)


def test_without_the_limit_nothing_is_counted_and_nothing_is_created(tmp_path):
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    assert admission_for(settings) is None
    report = verify(resolver(None))
    assert report.evidence[0].resolution.status == "found"
    assert not (tmp_path / "state").exists()
    assert LiveSourceResolver().admission is None
    assert LiveSourceResolver(admission=object(), request_id="").admission is None  # no request id: not counted


def test_the_factory_opens_the_runners_file_with_the_configured_limit(tmp_path):
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.annot.request_lookup_limit = 9
    admission = admission_for(settings)
    assert admission.limit == 9 and admission.path == Path(settings.path(settings.runner.state_dir)) / FILE
