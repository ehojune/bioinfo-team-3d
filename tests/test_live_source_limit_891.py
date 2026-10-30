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


# ---------- #1088: the request's usage as a fact, or "unknown" ----------

def _spend(admission, rid, agent, tool, count):
    for _ in range(count):
        assert admission.admit(rid, f"task-{agent}", agent, tool, "www.ebi.ac.uk")["admitted"]


def test_usage_reports_used_remaining_and_who_spent_it(tmp_path):
    admission = LookupAdmission(tmp_path / FILE, 60)
    assert admission.usage("req_a") == {"used": 0, "limit": 60, "remaining": 60, "by_agent": {}, "by_tool": {}}
    _spend(admission, "req_a", "chief_of_staff", "fulltext", 26)
    _spend(admission, "req_a", "lit_scout", "fulltext", 20)
    _spend(admission, "req_a", "lit_scout", "crossref_doi", 5)
    _spend(admission, "req_other", "analyst", "annot", 4)
    assert admission.usage("req_a") == {
        "used": 51, "limit": 60, "remaining": 9, "by_agent": {"chief_of_staff": 26, "lit_scout": 25},
        "by_tool": {"crossref_doi": 5, "fulltext": 46}}
    assert admission.usage("req_other")["used"] == 4  # another request keeps its own count


def test_usage_never_reads_a_missing_identity_as_zero(tmp_path):
    admission = LookupAdmission(tmp_path / FILE, 5)
    assert admission.usage("") is None and admission.usage(None) is None and admission.usage(7) is None


def test_the_remaining_count_stops_at_zero(tmp_path):
    admission = LookupAdmission(tmp_path / FILE, 2)
    _spend(admission, "req_a", "bio", "annot", 2)
    assert admission.admit("req_a", "t", "bio", "annot", "x.org")["admitted"] is False
    assert admission.usage("req_a")["remaining"] == 0 and admission.usage("req_a")["used"] == 2


def test_the_note_states_the_count_and_what_it_is_not(tmp_path):
    from labhq.runner.lookup_admission import usage_note

    admission = LookupAdmission(tmp_path / FILE, 60)
    _spend(admission, "req_a", "chief_of_staff", "fulltext", 26)
    _spend(admission, "req_a", "lit_scout", "fulltext", 25)
    note = usage_note(admission, "req_a", now=0)
    assert "51 of 60 used, 9 left" in note and "chief_of_staff 26, lit_scout 25" in note
    assert "this PC's runner" in note and "does not enforce" in note and "retry and redirect" in note
    assert "UNKNOWN" not in note


@pytest.mark.parametrize("admission_on, request_id, why", [
    (False, "req_a", "not set"), (True, "", "no identity"), (True, None, "no identity")])
def test_the_note_says_unknown_instead_of_zero(tmp_path, admission_on, request_id, why):
    from labhq.runner.lookup_admission import usage_note

    note = usage_note(LookupAdmission(tmp_path / FILE, 5) if admission_on else None, request_id)
    assert note.startswith("Public lookup usage of this request: UNKNOWN") and why in note
    assert "Do not assume it is zero" in note and " 0 of " not in note


def test_an_unreadable_counter_is_unknown_not_zero(tmp_path, monkeypatch):
    import sqlite3

    from labhq.runner.lookup_admission import usage_note

    admission = LookupAdmission(tmp_path / FILE, 5)
    monkeypatch.setattr(admission, "usage", lambda request_id: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    assert "UNKNOWN (the lookup counter could not be read)" in usage_note(admission, "req_a")


def test_the_factory_gives_no_counter_so_the_note_is_unknown_when_the_limit_is_off(tmp_path):
    from labhq.runner.lookup_admission import usage_note

    settings = Settings()
    settings.runner.state_dir = str(tmp_path)
    assert admission_for(settings) is None
    assert "UNKNOWN" in usage_note(admission_for(settings), "req_a")
    settings.annot.request_lookup_limit = 60
    assert "0 of 60 used, 60 left" in usage_note(admission_for(settings), "req_a")


# ---------- #1088: plan, step and re-plan prompts carry the request's usage ----------

def _usage_hub(tmp_path, limit):
    from tests.test_research_cp2 import CP1, _research_hub, _settings

    settings = _settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.annot.request_lookup_limit = limit
    hub = _research_hub(settings, [CP1, {"approved": True, "choice": "approve", "note": ""}])
    return hub, settings


async def _run_prompts(hub):
    from labhq.orchestrator.cso import Orchestrator

    await Orchestrator(hub).run_request("r")
    return {task.meta["kind"]: task.prompt for task in hub.calls if task.meta["kind"] in ("plan", "step")}


@pytest.mark.asyncio
async def test_the_plan_and_step_prompts_carry_this_requests_usage(tmp_path):
    hub, settings = _usage_hub(tmp_path, 60)
    admission = admission_for(settings)
    for _ in range(51):
        assert admission.admit("r", "task", "lit_scout", "fulltext", "www.ebi.ac.uk")["admitted"]
    prompts = await _run_prompts(hub)
    for kind in ("plan", "step"):
        assert "51 of 60 used, 9 left" in prompts[kind], kind
        assert "this PC's runner" in prompts[kind] and "does not enforce" in prompts[kind]


@pytest.mark.asyncio
async def test_a_request_that_has_not_looked_anything_up_says_zero_of_the_limit(tmp_path):
    hub, _settings = _usage_hub(tmp_path, 60)
    prompts = await _run_prompts(hub)
    assert "0 of 60 used, 60 left" in prompts["plan"] and "0 of 60 used, 60 left" in prompts["step"]


@pytest.mark.asyncio
async def test_without_a_configured_cap_the_prompts_say_nothing_about_usage(tmp_path):
    hub, _settings = _usage_hub(tmp_path, None)
    prompts = await _run_prompts(hub)
    assert all("Public lookup usage" not in prompt for prompt in prompts.values())


@pytest.mark.asyncio
async def test_an_unreadable_counter_is_unknown_in_the_prompts_never_zero(tmp_path, monkeypatch):
    import sqlite3

    hub, _settings = _usage_hub(tmp_path, 60)
    monkeypatch.setattr("labhq.runner.lookup_admission.admission_for",
                        lambda settings: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    prompts = await _run_prompts(hub)
    assert "UNKNOWN (the lookup counter could not be opened)" in prompts["plan"]
    assert "UNKNOWN" in prompts["step"] and " 0 of " not in prompts["step"]


def test_the_note_helper_is_empty_without_a_cap_and_a_fact_with_one(tmp_path):
    from labhq.runner.lookup_admission import usage_note_for

    settings = Settings()
    settings.runner.state_dir = str(tmp_path)
    assert usage_note_for(settings, "req_a") == ""
    settings.annot.request_lookup_limit = 3
    assert "0 of 3 used, 3 left" in usage_note_for(settings, "req_a", now=0)
    assert "UNKNOWN (the request has no identity)" in usage_note_for(settings, "")
