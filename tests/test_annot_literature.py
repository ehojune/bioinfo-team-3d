"""labhq_annot fulltext (#697): Europe PMC open-access body as Markdown, else the abstract, with every HTTP call faked.

In the 10-16 trial no step read a paper's body: PubMed metadata only, and PMC pages opened on the web returned a
reCAPTCHA screen. These tests pin the route that replaces it and the staff rules that point to it."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest
import yaml

from labhq.models import AgentSpec
from labhq.tools import annot, annot_literature as lit
from tests.test_annot import FakeTime
from tests.test_direct_outputs import _workspace as direct_workspace

ROOT = Path(__file__).resolve().parents[1]

JATS = """<?xml version="1.0" encoding="UTF-8"?>
<article article-type="research-article">
 <front><article-meta>
  <title-group><article-title>Sex ratio and paternal work</article-title></title-group>
  <permissions><license><license-p>Creative Commons Attribution 4.0 International License</license-p></license></permissions>
  <abstract><p>We studied births.</p></abstract>
 </article-meta></front>
 <body>
  <sec><title>Introduction</title><p>Fathers' jobs may matter <xref>1</xref>.</p></sec>
  <sec><title>Results</title>
   <sec><title>Main model</title>
    <p>The odds ratio was 1.02.<table-wrap><label>Table 1</label><caption><p>Boys by group</p></caption>
     <table><tr><th>Group</th><th>Boys</th><th>aOR</th><th colspan="2">95% CI</th></tr>
      <tr><td>Office</td><td>510</td><td>1.02</td><td>0.99</td><td>1|05</td></tr></table>
    </table-wrap>After the table, <italic>no</italic> trend.<fig><label>Figure 2</label><caption><p>Trend</p></caption></fig>Last.</p>
    <table-wrap><label>Table 2</label><caption><p>Spans</p></caption><table>
     <tr><th rowspan="2">Group</th><th colspan="2">Boys</th><th rowspan="2">p</th></tr>
     <tr><th>n</th><th>%</th></tr>
     <tr><td>Office</td><td>510</td><td>51.2</td><td>0.04</td></tr>
     <tr><td rowspan="2">Manual</td><td>480</td><td>50.9</td><td>0.30</td></tr>
     <tr><td>12</td><td>49.0</td><td>0.88</td></tr>
    </table></table-wrap>
    <fig><label>Figure 1</label><caption><p>Forest plot</p></caption></fig>
   </sec>
  </sec>
 </body>
 <back><ref-list>
  <ref><mixed-citation>Irgens A. Sex ratio. 1997. <pub-id pub-id-type="pmid">111</pub-id></mixed-citation></ref>
  <ref><element-citation>Magnuson A. Office work. 2007.</element-citation><pub-id pub-id-type="doi">10.1080/x</pub-id></ref>
 </ref-list></back>
</article>"""


def record(pmid="123", pmcid="PMC999", oa="Y", license="cc by", abstract="Boys were 51.2% of births.", links=None):
    return {"pmid": pmid, "pmcid": pmcid, "doi": "10.1000/test", "title": "Sex ratio and paternal work.",
            "journalInfo": {"journal": {"title": "Test Journal"}}, "pubYear": "2024", "authorString": "Kim A.",
            "license": license, "isOpenAccess": oa, "inEPMC": oa, "abstractText": abstract,
            "fullTextUrlList": {"fullTextUrl": links or []}}


def make(handler, tmp_path, workdir="work", unpaywall_email=""):
    calls = []

    def recorder(request):
        calls.append(request)
        return handler(request)

    clock = FakeTime()
    (tmp_path / workdir).mkdir(exist_ok=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder), follow_redirects=True)
    annotator = lit.LiteratureAnnotator(client, tmp_path / workdir, tmp_path / "cache", sleep=clock.sleep,
                                        clock=clock.clock, unpaywall_email=unpaywall_email)
    return annotator, calls


def search_answer(records):
    return httpx.Response(200, json={"version": "6.9", "hitCount": len(records), "resultList": {"result": records}})


def complete_fallback_pdf(label="Repository full text"):
    body = f"Introduction {label}. Methods " + ("complete study body " * 80) + " Results complete findings."
    return b"%PDF-1.4\nBT (" + body.encode("ascii") + b") Tj ET\n%%EOF"


def by_query(table, xml=JATS):
    """Search answers keyed by the query string; fullTextXML answers `xml` for PMC999 and 404 for anything else."""
    def handler(request):
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"meta": {"count": 0}, "results": []})
        if request.url.path.endswith("/fullTextXML"):
            return httpx.Response(200, text=xml) if "PMC999" in request.url.path else httpx.Response(404)
        return search_answer(table.get(request.url.params["query"], []))
    return handler


def chase_handler(request):
    if request.url.path.endswith("/references"):
        return httpx.Response(200, json={"version": "6.9", "hitCount": 2, "referenceList": {"reference": [
            {"source": "MED", "id": "111", "pmid": "111", "title": "Earlier study",
             "authorString": "Lee A.", "journalAbbreviation": "Old J", "pubYear": 1998},
            {"source": "PMC", "id": "PMC222", "pmcid": "PMC222", "doi": "10.1000/back",
             "title": "Open review", "authorString": "Park B.", "journalAbbreviation": "Open J",
             "pubYear": 2001, "isOpenAccess": "Y"},
        ]}})
    if request.url.path.endswith("/citations"):
        return httpx.Response(200, json={"version": "6.9", "hitCount": 1, "citationList": {"citation": [
            {"source": "MED", "id": "333", "pmid": "333", "doi": "10.1000/forward",
             "title": "Later cohort", "authorString": "Kim C.", "journalAbbreviation": "New J",
             "pubYear": 2022, "abstractText": "A later test."},
        ]}})
    return search_answer([{**record(), "source": "MED", "id": "123"}])


def test_paper_ids_are_read_in_the_forms_staff_write_them():
    assert lit.parse_paper_id("31868601") == lit.PaperId("pmid", "31868601")
    assert lit.parse_paper_id("PMID: 31868601") == lit.PaperId("pmid", "31868601")
    assert lit.parse_paper_id("pmc13248341") == lit.PaperId("pmcid", "PMC13248341")
    assert lit.parse_paper_id("https://doi.org/10.1016/S2542-5196(19)30239-6") == lit.PaperId(
        "doi", "10.1016/s2542-5196(19)30239-6")
    assert lit.parse_paper_id("doi:10.1080/X").query() == 'DOI:"10.1080/x"'
    assert lit.PaperId("pmid", "31868601").file_name() == "pmid-31868601.md"
    name = lit.PaperId("doi", "10.1016/s2542-5196(19)30239-6").file_name()
    assert name.startswith("doi-10.1016_s2542-5196_19_30239-6-") and name.endswith(".md")
    # Different DOIs never share a file (PR #698 review): the readable part alone would.
    assert lit.PaperId("doi", "10.1000/a:b").file_name() != lit.PaperId("doi", "10.1000/a/b").file_name()
    with pytest.raises(ValueError, match="PMID"):
        lit.parse_paper_id("Irgens 1997")


async def test_an_open_access_paper_is_written_as_markdown_and_the_record_keeps_only_its_path(tmp_path):
    annotator, calls = make(by_query({"EXT_ID:123 AND SRC:MED": [record()]}), tmp_path)
    out = await annotator.fulltext(["123"])
    row = out["results"][0]
    assert row["ok"] and row["access_level"] == "full_text" and row["pmcid"] == "PMC999"
    assert row["sections"] == ["Introduction", "Results"]
    assert (row["n_tables"], row["n_figures"], row["n_references"]) == (2, 2, 2)
    assert row["text_file"] == ".tmp/literature/pmid-123.md"
    receipt = row["fulltext_receipt"]
    assert receipt["paper_id"] == "pmid:123" and receipt["extraction"] == "complete"
    assert receipt["evidence"] == []  # artifact access does not establish methods/QC evidence
    assert receipt["text_sha256"] == hashlib.sha256(Path(receipt["text_file"]).read_bytes()).hexdigest()
    text = (tmp_path / "work" / row["text_file"]).read_text(encoding="utf-8")
    assert "## Results" in text and "### Main model" in text and "The odds ratio was 1.02." in text
    assert "| Group | Boys | aOR | 95% CI |  |" in text  # the spanning header keeps the columns aligned
    assert "| Office | 510 | 1.02 | 0.99 | 1\\|05 |" in text
    # Text after an inline table or figure stays, in order (PR #698 review).
    order = ["The odds ratio was 1.02.", "**Table 1**", "After the table, no trend.", "Figure 2 Trend", "Last."]
    assert [text.index(piece) for piece in order] == sorted(text.index(piece) for piece in order)
    # rowspan keeps every value under its own header.
    assert ("| Group | Boys |  | p |\n|---|---|---|---|\n|  | n | % |  |\n| Office | 510 | 51.2 | 0.04 |\n"
            "| Manual | 480 | 50.9 | 0.30 |\n|  | 12 | 49.0 | 0.88 |") in text
    assert "Figure 1 Forest plot" in text and "## References" in text
    assert "- Irgens A. Sex ratio. 1997. 111\n" in text  # an ID already in the citation is not repeated
    assert "- Magnuson A. Office work. 2007. (DOI 10.1080/x)" in text
    assert "access_level: full_text" in text
    assert [c.url.path.rsplit("/", 1)[-1] for c in calls] == ["search", "fullTextXML"]
    log = [json.loads(line) for line in (tmp_path / "work" / "outputs" / "annotation_queries.jsonl")
           .read_text(encoding="utf-8").splitlines()]
    assert log[0]["tool"] == "fulltext" and log[0]["db"] == "Europe PMC" and log[0]["release_or_version"] == "REST 6.9"
    full = json.loads((tmp_path / "work" / out["full_result"]).read_text(encoding="utf-8"))
    answer = full["results"][0]["answer"]
    assert "markdown" not in answer  # the text lives once, in .tmp/literature/
    assert answer["text_sha256"] == hashlib.sha256((tmp_path / "work" / row["text_file"]).read_bytes()).hexdigest()
    assert "결과물에 옮겨 싣지 않는다" in out["note"]


async def test_without_a_europe_pmc_copy_the_file_holds_the_abstract_and_cc_papers_offer_the_publisher_link(tmp_path):
    link = [{"url": "https://doi.org/10.1016/x", "availability": "Subscription required"}]
    table = {"EXT_ID:1 AND SRC:MED": [record("1", None, "N", "cc by-nc-nd", links=link)],
             "EXT_ID:2 AND SRC:MED": [record("2", None, "N", None, links=link)],
             "EXT_ID:3 AND SRC:MED": [record("3", None, "N", None, abstract="")]}
    annotator, calls = make(by_query(table), tmp_path)
    rows = (await annotator.fulltext(["1", "2", "3"]))["results"]
    assert [r["access_level"] for r in rows] == ["abstract", "abstract", "metadata"]
    assert rows[0]["publisher_copy"] == link and "publisher_copy" not in rows[1]
    assert not any(c.url.path.endswith("/fullTextXML") for c in calls)  # nothing open to fetch
    text = (tmp_path / "work" / ".tmp" / "literature" / "pmid-1.md").read_text(encoding="utf-8")
    assert "## Abstract" in text and "Boys were 51.2% of births." in text and "access_level: abstract" in text


async def test_an_unusable_body_falls_back_to_the_abstract_and_says_why(tmp_path):
    annotator, _ = make(by_query({"EXT_ID:123 AND SRC:MED": [record()]},
                                 xml='<!DOCTYPE a [<!ENTITY x "y">]><article><body/></article>'), tmp_path)
    row = (await annotator.fulltext(["123"]))["results"][0]
    assert row["access_level"] == "abstract" and "엔티티" in row["fulltext_error"]

    annotator, _ = make(by_query({"PMCID:PMC5": [record("5", "PMC5")]}), tmp_path, "work2")
    row = (await annotator.fulltext(["PMC5"]))["results"][0]
    assert row["access_level"] == "abstract"  # fullTextXML answered 404


async def test_a_failed_body_read_is_answered_with_the_abstract_but_not_cached(tmp_path):
    """A server error on fullTextXML is not "no copy": caching it would keep a key paper at the abstract for 30 days
    (PR #698 review). A 404 is a real absence and is cached."""
    def flaky(request):
        if request.url.path.endswith("/fullTextXML"):
            return httpx.Response(500)
        return search_answer([record()])

    first, _ = make(flaky, tmp_path)
    row = (await first.fulltext(["123"]))["results"][0]
    assert row["access_level"] == "abstract" and "HTTP 500" in row["fulltext_error"]
    second, calls = make(by_query({"EXT_ID:123 AND SRC:MED": [record()]}), tmp_path, "again")
    row = (await second.fulltext(["123"]))["results"][0]
    assert row["access_level"] == "full_text" and "cached_from" not in row
    assert [c.url.path.rsplit("/", 1)[-1] for c in calls] == ["search", "fullTextXML"]

    absent, _ = make(by_query({"PMCID:PMC5": [record("5", "PMC5")]}), tmp_path, "absent")
    await absent.fulltext(["PMC5"])  # 404: cached as abstract
    third, calls = make(lambda request: httpx.Response(500), tmp_path, "third")
    row = (await third.fulltext(["PMC5"]))["results"][0]
    assert calls == [] and row["access_level"] == "abstract" and row["cached_from"]


async def test_spent_retries_on_the_body_also_fall_back_to_an_uncached_abstract(tmp_path):
    """502-504 or a lost connection raise after the retries instead of returning a response (PR #698 review)."""
    def down(request):
        if request.url.path.endswith("/fullTextXML"):
            return httpx.Response(503)
        return search_answer([record()])

    annotator, calls = make(down, tmp_path)
    out = await annotator.fulltext(["123"])
    row = out["results"][0]
    assert row["ok"] and row["access_level"] == "abstract" and "본문 조회가 실패" in row["fulltext_error"]
    assert sum(c.url.path.endswith("/fullTextXML") for c in calls) == annot.MAX_ATTEMPTS
    again, calls = make(by_query({"EXT_ID:123 AND SRC:MED": [record()]}), tmp_path, "again")
    assert (await again.fulltext(["123"]))["results"][0]["access_level"] == "full_text"


async def test_a_direct_run_never_lists_a_paper_text_as_its_output(tmp_path):
    """A direct run lists every file under outputs/ as a result and the edition publishes reported results, so the
    third-party text stays under .tmp/ (PR #698 review)."""
    ws = direct_workspace(tmp_path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(by_query({"EXT_ID:123 AND SRC:MED": [record()]})))
    clock = FakeTime()
    annotator = lit.LiteratureAnnotator(client, ws.dir, tmp_path / "cache", sleep=clock.sleep, clock=clock.clock)
    row = (await annotator.fulltext(["123"]))["results"][0]
    assert (ws.dir / row["text_file"]).is_file()
    found, _note = ws.scan_outputs([], max_entries=100, max_depth=4)
    assert found and not [path for path in found if "literature" in path]  # the query records are outputs, the text is not


async def test_unknown_and_malformed_ids_do_not_stop_the_rest(tmp_path):
    annotator, _ = make(by_query({"EXT_ID:123 AND SRC:MED": [record()]}), tmp_path)
    rows = (await annotator.fulltext(["123", "999", "Irgens 1997"]))["results"]
    assert rows[0]["access_level"] == "full_text"
    assert rows[1] == {"input": "999", "ok": True, "found": False, "pmid": "999", "unread_reason": "no_copy",
                       "access_status": "not_obtained", "methods_locator": None, "qc_locator": None,
                       "supplementary_status": "undetermined"}
    assert rows[2]["ok"] is False and "PMID" in rows[2]["error"]


async def test_a_cached_answer_writes_the_text_again_without_asking(tmp_path):
    first, _ = make(by_query({"EXT_ID:123 AND SRC:MED": [record()]}), tmp_path)
    await first.fulltext(["123"])
    second, calls = make(lambda request: httpx.Response(500), tmp_path, "other")
    out = await second.fulltext(["PMID:123"])
    row = out["results"][0]
    assert calls == [] and row["cached_from"] and row["access_level"] == "full_text"
    assert (tmp_path / "other" / ".tmp" / "literature" / "pmid-123.md").is_file()
    assert out["source"]["release_or_version"] == "REST (cached answers)"


async def test_more_than_twenty_papers_in_one_call_is_refused(tmp_path):
    annotator, calls = make(lambda request: httpx.Response(500), tmp_path)
    with pytest.raises(ValueError, match="20편"):
        await annotator.fulltext([str(n) for n in range(1, 22)])
    assert calls == []


async def test_citation_chase_returns_both_directions_as_bibliographic_candidates_and_records_them(tmp_path):
    annotator, calls = make(chase_handler, tmp_path)
    out = await annotator.citation_chase("PMID:123", limit=50)
    row = out["results"][0]
    assert row["ok"] and row["seed"]["pmid"] == "123"
    assert row["backward"]["found_count"] == 2 and row["forward"]["found_count"] == 1
    backward = row["backward"]["candidates"]
    forward = row["forward"]["candidates"]
    assert backward[0] == {
        "source": {"code": "MED", "id": "111"}, "pmid": "111", "title": "Earlier study",
        "authors": "Lee A.", "journal": "Old J", "year": "1998", "access_level_candidate": "abstract",
    }
    assert backward[1]["pmcid"] == "PMC222" and backward[1]["access_level_candidate"] == "full_text"
    assert forward[0]["doi"] == "10.1000/forward" and forward[0]["access_level_candidate"] == "abstract"
    assert [c.url.path.rsplit("/", 1)[-1] for c in calls] == ["search", "references", "citations"]
    log = [json.loads(line) for line in (tmp_path / "work" / "outputs" / "annotation_queries.jsonl")
           .read_text(encoding="utf-8").splitlines()]
    assert log[-1]["tool"] == "citation_chase" and log[-1]["db"] == "Europe PMC"
    full = json.loads((tmp_path / "work" / out["full_result"]).read_text(encoding="utf-8"))
    assert full["results"][0]["answer"]["forward"]["found_count"] == 1
    cached, cached_calls = make(lambda request: httpx.Response(500), tmp_path, "chase-again")
    cached_row = (await cached.citation_chase("123", limit=50))["results"][0]
    assert cached_calls == [] and cached_row["cached_from"] and cached_row["forward"]["found_count"] == 1


async def test_preprint_search_uses_ppr_and_marks_every_result_as_unreviewed(tmp_path):
    def handler(request):
        assert request.url.path.endswith("/search")
        assert "SRC:PPR" in request.url.params["query"]
        assert 'PUBLISHER:"bioRxiv"' in request.url.params["query"]
        assert 'PUBLISHER:"medRxiv"' in request.url.params["query"]
        return httpx.Response(200, json={"version": "6.9", "hitCount": 2, "resultList": {"result": [
            {"source": "PPR", "id": "PPR1", "doi": "10.1101/1", "title": "A bioRxiv study",
             "authorString": "Fox A.", "bookOrReportDetails": {"publisher": "bioRxiv"},
             "pubYear": "2025", "abstractText": "A."},
            {"source": "PPR", "id": "PPR2", "doi": "10.1101/2", "title": "A medRxiv study",
             "authorString": "Bear B.", "bookOrReportDetails": {"publisher": "medRxiv"},
             "pubYear": "2026", "isOpenAccess": "Y"},
        ]}})

    annotator, calls = make(handler, tmp_path)
    out = await annotator.preprints("paternal exposure sex ratio", limit=10)
    row = out["results"][0]
    assert row["found_count"] == 2 and row["returned_count"] == 2
    assert {candidate["server"] for candidate in row["candidates"]} == {"bioRxiv", "medRxiv"}
    assert all(candidate["review_status"] == "심사 전" for candidate in row["candidates"])
    assert [candidate["access_level_candidate"] for candidate in row["candidates"]] == ["abstract", "full_text"]
    assert len(calls) == 1
    log = [json.loads(line) for line in (tmp_path / "work" / "outputs" / "annotation_queries.jsonl")
           .read_text(encoding="utf-8").splitlines()]
    assert log[-1]["tool"] == "preprints"
    cached, cached_calls = make(lambda request: httpx.Response(500), tmp_path, "preprints-again")
    cached_row = (await cached.preprints("paternal exposure sex ratio", limit=10))["results"][0]
    assert cached_calls == [] and cached_row["cached_from"] and cached_row["returned_count"] == 2


async def test_openalex_crossref_and_unpaywall_return_recorded_public_metadata(tmp_path):
    def handler(request):
        if request.url.host == "api.openalex.org":
            assert request.url.params["search"] == "paternal exposure"
            assert request.url.params["filter"] == "from_publication_date:2020-01-01"
            assert request.url.params["per-page"] == "5"
            return httpx.Response(200, json={"meta": {"count": 11}, "results": [{
                "id": "https://openalex.org/W123", "doi": "https://doi.org/10.1000/OA",
                "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/321"}, "title": "A broad study",
                "publication_year": 2025, "type": "article", "cited_by_count": 17,
                "cited_by_api_url": "https://api.openalex.org/works?filter=cites:W123",
                "referenced_works": ["https://openalex.org/W10", "https://openalex.org/W11"],
                "open_access": {"is_oa": True, "oa_status": "gold", "oa_url": "https://oa.example/a.pdf"},
                "best_oa_location": {"landing_page_url": "https://oa.example/a", "pdf_url": "https://oa.example/a.pdf",
                                     "license": "cc-by", "version": "publishedVersion",
                                     "source": {"display_name": "Open Journal"}},
                "authorships": [{"author": {"display_name": "Kim A."}}],
            }]})
        if request.url.host == "api.crossref.org":
            assert request.url.raw_path.endswith(b"/works/10.1000%2Foa")
            return httpx.Response(200, json={"message": {
                "DOI": "10.1000/OA", "title": ["A broad study"], "type": "journal-article",
                "publisher": "Test Publisher", "container-title": ["Open Journal"], "content-version": "vor",
                "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/", "start": {"date-time": "2025-01-01"}}],
                "update-to": [{"DOI": "10.1000/old", "type": "retraction", "updated": {"date-time": "2026-01-01"}}],
                "relation": {"is-version-of": [{"id": "10.1000/preprint", "id-type": "doi"}]},
            }})
        if request.url.host == "api.unpaywall.org":
            assert request.url.params["email"] == "owner@example.org"
            return httpx.Response(200, json={
                "doi": "10.1000/oa", "title": "A broad study", "is_oa": True, "oa_status": "gold",
                "best_oa_location": {"url": "https://oa.example/a", "url_for_pdf": "https://oa.example/a.pdf",
                                     "host_type": "publisher", "license": "cc-by", "version": "publishedVersion"},
                "oa_locations": [{"url": "https://repo.example/a", "url_for_pdf": "https://repo.example/a.pdf",
                                  "host_type": "repository", "license": "cc-by", "version": "acceptedVersion"}],
            })
        raise AssertionError(request.url)

    annotator, _calls = make(handler, tmp_path, unpaywall_email="owner@example.org")
    openalex = (await annotator.openalex_works(
        "paternal exposure", ["from_publication_date:2020-01-01"], 5))["results"][0]
    assert openalex["found_count"] == 11 and openalex["returned_count"] == 1
    candidate = openalex["candidates"][0]
    assert candidate["doi"] == "10.1000/oa" and candidate["pmid"] == "321" and candidate["is_oa"] is True
    assert candidate["best_oa_location"]["pdf_url"] == "https://oa.example/a.pdf"
    assert candidate["cited_by_count"] == 17 and candidate["references"] == ["W10", "W11"]

    crossref = (await annotator.crossref_doi(["10.1000/OA"]))["results"][0]
    assert crossref["version"] == "vor" and crossref["licenses"][0]["url"].startswith("https://creativecommons")
    assert crossref["updates"][0]["type"] == "retraction" and crossref["retracted"] is True

    unpaywall = await annotator.unpaywall(["https://doi.org/10.1000/OA"])
    assert unpaywall["enabled"] is True
    assert unpaywall["results"][0]["best_oa_location"]["pdf_url"] == "https://oa.example/a.pdf"
    assert unpaywall["results"][0]["oa_locations"][0]["host_type"] == "repository"

    log = [json.loads(line) for line in (tmp_path / "work" / "outputs" / "annotation_queries.jsonl")
           .read_text(encoding="utf-8").splitlines()]
    assert [row["tool"] for row in log] == ["openalex_works", "crossref_doi", "unpaywall"]


async def test_unpaywall_without_contact_email_is_explicitly_disabled_and_does_not_call_the_api(tmp_path):
    annotator, calls = make(lambda request: (_ for _ in ()).throw(AssertionError(request.url)), tmp_path)
    out = await annotator.unpaywall(["10.1000/test"])
    assert out["enabled"] is False and "annot.unpaywall_email" in out["disabled_reason"]
    assert out["results"][0]["enabled"] is False and calls == []
    logged = json.loads((tmp_path / "work" / "outputs" / "annotation_queries.jsonl").read_text(encoding="utf-8"))
    assert logged["tool"] == "unpaywall" and "email" not in json.dumps(logged)


async def test_fulltext_uses_a_legal_oa_pdf_after_europe_pmc_has_no_body(tmp_path):
    pdf = complete_fallback_pdf("Open access full text from the repository")

    def handler(request):
        if request.url.host == "www.ebi.ac.uk":
            return search_answer([record("123", None, "N", None)])
        if request.url.host == "api.unpaywall.org":
            return httpx.Response(200, json={
                "doi": "10.1000/test", "is_oa": True, "oa_status": "green",
                "best_oa_location": {"url_for_pdf": "http://93.184.216.34/paper.pdf", "url": "http://93.184.216.34/paper",
                                     "host_type": "repository", "license": "cc-by", "version": "acceptedVersion"},
                "oa_locations": [],
            })
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"meta": {"count": 0}, "results": []})
        if request.url.host == "93.184.216.34":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        raise AssertionError(request.url)

    annotator, _calls = make(handler, tmp_path, unpaywall_email="owner@example.org")
    row = (await annotator.fulltext(["PMID:123"]))["results"][0]
    assert row["access_level"] == "full_text" and row["read_source"] == "Unpaywall"
    assert row["oa_copy"]["url"] == "http://93.184.216.34/paper.pdf"
    text = (tmp_path / "work" / row["text_file"]).read_text(encoding="utf-8")
    assert "Open access full text from the repository." in text and "access_level: full_text" in text


def test_pdf_fallback_rejects_a_single_partial_literal():
    with pytest.raises(ValueError, match="PDF.*본문 구조"):
        lit._pdf_text(b"%PDF-1.4\nBT (Cover title only.) Tj ET\n%%EOF")


async def test_fulltext_tries_oa_providers_when_europe_pmc_has_no_record(tmp_path):
    pdf = complete_fallback_pdf("Open access paper outside Europe PMC")

    def handler(request):
        if request.url.host == "www.ebi.ac.uk":
            return search_answer([])
        if request.url.host == "api.unpaywall.org":
            return httpx.Response(200, json={
                "doi": "10.1000/outside", "is_oa": True, "oa_status": "gold",
                "best_oa_location": {"url_for_pdf": "http://93.184.216.34/paper.pdf",
                                     "host_type": "publisher", "license": "cc-by"},
                "oa_locations": [],
            })
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"meta": {"count": 0}, "results": []})
        if request.url.host == "93.184.216.34":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        raise AssertionError(request.url)

    annotator, calls = make(handler, tmp_path, unpaywall_email="owner@example.org")
    row = (await annotator.fulltext(["10.1000/outside"]))["results"][0]
    assert row["access_level"] == "full_text" and row["doi"] == "10.1000/outside"
    assert any(call.url.host == "api.unpaywall.org" for call in calls)


async def test_fulltext_tries_alternate_openalex_oa_locations(tmp_path):
    def handler(request):
        if request.url.host == "www.ebi.ac.uk":
            return search_answer([record("123", None, "Y", None)])
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"results": [{
                "id": "https://openalex.org/W1", "open_access": {"is_oa": True},
                "best_oa_location": {"is_oa": True, "pdf_url": "http://93.184.216.34/best.pdf"},
                "locations": [
                    {"is_oa": True, "pdf_url": "http://93.184.216.34/best.pdf"},
                    {"is_oa": True, "pdf_url": "http://93.184.216.34/repository.pdf",
                     "source": {"display_name": "Repository"}},
                    {"is_oa": False, "pdf_url": "http://93.184.216.34/closed.pdf"},
                ],
            }]})
        if request.url.path == "/best.pdf":
            return httpx.Response(403)
        if request.url.path == "/repository.pdf":
            return httpx.Response(200, content=complete_fallback_pdf(), headers={"content-type": "application/pdf"})
        raise AssertionError(request.url)

    annotator, calls = make(handler, tmp_path)
    row = (await annotator.fulltext(["123"]))["results"][0]
    assert row["access_level"] == "full_text"
    assert row["oa_copy"]["url"] == "http://93.184.216.34/repository.pdf"
    assert not any(call.url.path == "/closed.pdf" for call in calls)


@pytest.mark.parametrize("blocked_at", ["initial", "redirect"])
async def test_fulltext_never_requests_a_private_oa_destination(tmp_path, blocked_at):
    private_url = "http://169.254.169.254/latest/meta-data"
    public_url = "http://93.184.216.34/paper.pdf"

    def handler(request):
        if request.url.host == "www.ebi.ac.uk":
            return search_answer([record("123", None, "Y", None)])
        if request.url.host == "api.openalex.org":
            location = private_url if blocked_at == "initial" else public_url
            return httpx.Response(200, json={"results": [{
                "id": "https://openalex.org/W1", "open_access": {"is_oa": True},
                "best_oa_location": {"pdf_url": location, "source": {"display_name": "Repository"}},
            }]})
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"location": private_url})
        if request.url.host == "169.254.169.254":
            return httpx.Response(200, content=b"%PDF-1.4 private", headers={"content-type": "application/pdf"})
        raise AssertionError(request.url)

    annotator, calls = make(handler, tmp_path, workdir=blocked_at)
    row = (await annotator.fulltext(["123"]))["results"][0]
    assert row["access_level"] != "full_text"
    assert not any(call.url.host == "169.254.169.254" for call in calls)


async def test_fulltext_uses_a_new_cache_release_after_oa_fallback(tmp_path, monkeypatch):
    current_release = lit.FULLTEXT_CACHE_RELEASE
    calls = []
    cached_handler = by_query({'EXT_ID:123 AND SRC:MED': [record()]})

    def handler(request):
        calls.append(request)
        return cached_handler(request)

    monkeypatch.setattr(lit, "FULLTEXT_CACHE_RELEASE", lit.CACHE_RELEASE)
    first, _first_calls = make(handler, tmp_path)
    await first.fulltext(["123"])
    monkeypatch.setattr(lit, "FULLTEXT_CACHE_RELEASE", current_release)
    second, _second_calls = make(handler, tmp_path)
    out = await second.fulltext(["123"])
    assert out["n_cached"] == 0
    assert sum(call.url.path.endswith("/search") for call in calls) == 2


async def test_fulltext_cache_key_tracks_unpaywall_activation(tmp_path):
    pdf = complete_fallback_pdf("Enabled provider copy")

    def handler(request):
        if request.url.host == "www.ebi.ac.uk":
            return search_answer([record("123", None, "N", None)])
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"meta": {"count": 0}, "results": []})
        if request.url.host == "api.unpaywall.org":
            return httpx.Response(200, json={
                "doi": "10.1000/test", "is_oa": True, "oa_status": "green",
                "best_oa_location": {"url_for_pdf": "http://93.184.216.34/enabled.pdf"},
                "oa_locations": [],
            })
        if request.url.host == "93.184.216.34":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        raise AssertionError(request.url)

    disabled, _calls = make(handler, tmp_path)
    first = await disabled.fulltext(["123"])
    assert first["results"][0]["access_level"] == "abstract"

    enabled, calls = make(handler, tmp_path, unpaywall_email="owner@example.org")
    second = await enabled.fulltext(["123"])
    assert second["n_cached"] == 0
    assert second["results"][0]["access_level"] == "full_text"
    assert any(call.url.host == "api.unpaywall.org" for call in calls)


async def test_fulltext_stops_streaming_oa_copy_at_byte_limit(tmp_path, monkeypatch):
    class TrackedStream(httpx.AsyncByteStream):
        def __init__(self):
            self.yielded = 0

        async def __aiter__(self):
            for chunk in (b"12345", b"67890", b"must-not-be-read"):
                self.yielded += 1
                yield chunk

    stream = TrackedStream()
    monkeypatch.setattr(lit, "MAX_OA_COPY_BYTES", 8)

    def handler(request):
        if request.url.host == "www.ebi.ac.uk":
            return search_answer([record("123", None, "Y", None)])
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"results": [{
                "id": "https://openalex.org/W1", "open_access": {"is_oa": True},
                "best_oa_location": {"pdf_url": "http://93.184.216.34/large.pdf"},
            }]})
        if request.url.host == "93.184.216.34":
            return httpx.Response(200, stream=stream, headers={"content-type": "application/pdf"})
        raise AssertionError(request.url)

    annotator, _calls = make(handler, tmp_path)
    row = (await annotator.fulltext(["123"]))["results"][0]
    assert row["unread_reason"] == "format_failure"
    assert stream.yielded == 2


def test_pdf_text_caps_total_flate_expansion(monkeypatch):
    monkeypatch.setattr(lit, "MAX_PDF_EXPANDED_BYTES", 24)
    streams = [lit.zlib.compress(b"(first text) Tj"), lit.zlib.compress(b"(second text) Tj")]
    pdf = b"%PDF-1.4\n" + b"\n".join(
        b"<< /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream" for stream in streams)

    with pytest.raises(ValueError, match="PDF.*24"):
        lit._pdf_text(pdf)


async def test_fulltext_does_not_cache_oa_copy_server_errors(tmp_path):
    copy_calls = 0

    def handler(request):
        nonlocal copy_calls
        if request.url.host == "www.ebi.ac.uk":
            return search_answer([record("123", None, "Y", None)])
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"results": [{
                "id": "https://openalex.org/W1", "open_access": {"is_oa": True},
                "best_oa_location": {"pdf_url": "http://93.184.216.34/temporary.pdf"},
            }]})
        if request.url.host == "93.184.216.34":
            copy_calls += 1
            return httpx.Response(500)
        raise AssertionError(request.url)

    annotator, _calls = make(handler, tmp_path)
    first = await annotator.fulltext(["123"])
    second = await annotator.fulltext(["123"])
    assert "OpenAlex 사본 조회 HTTP 500" in first["results"][0].get("fulltext_error", "")
    assert second["n_cached"] == 0
    assert copy_calls == 2


async def test_fulltext_found_false_keeps_identifier_and_oa_failure(tmp_path):
    def handler(request):
        if request.url.host == "www.ebi.ac.uk":
            return search_answer([])
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"results": [{
                "id": "https://openalex.org/W1", "open_access": {"is_oa": True},
                "best_oa_location": {"pdf_url": "http://93.184.216.34/denied.pdf"},
            }]})
        if request.url.host == "93.184.216.34":
            return httpx.Response(403)
        raise AssertionError(request.url)

    annotator, _calls = make(handler, tmp_path)
    row = (await annotator.fulltext(["10.1000/outside"]))["results"][0]
    assert row["found"] is False and row["doi"] == "10.1000/outside"
    assert row["unread_reason"] == "access_denied" and row["unread_detail"]
    assert row["oa_locations"][0]["url"] == "http://93.184.216.34/denied.pdf"


@pytest.mark.parametrize(("mode", "reason"), [
    ("closed", "subscription_required"),
    ("denied", "access_denied"),
    ("broken", "format_failure"),
])
async def test_fulltext_records_why_a_legal_oa_copy_could_not_be_read(tmp_path, mode, reason):
    def handler(request):
        if request.url.host == "www.ebi.ac.uk":
            oa = "N" if mode == "closed" else "Y"
            links = [{"url": "https://example.org/paper", "availability": "Subscription required"}] if mode == "closed" else []
            return search_answer([record("123", None, oa, None, links=links)])
        if request.url.host == "api.unpaywall.org":
            location = None if mode == "closed" else {
                "url_for_pdf": f"http://93.184.216.34/{mode}.pdf", "host_type": "repository", "license": "cc-by"}
            return httpx.Response(200, json={"doi": "10.1000/test", "is_oa": mode != "closed",
                                             "oa_status": "closed" if mode == "closed" else "green",
                                             "best_oa_location": location, "oa_locations": []})
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"meta": {"count": 0}, "results": []})
        if request.url.host == "93.184.216.34":
            return (httpx.Response(403) if mode == "denied" else
                    httpx.Response(200, content=b"not a paper", headers={"content-type": "application/octet-stream"}))
        raise AssertionError(request.url)

    annotator, _calls = make(handler, tmp_path, workdir=mode, unpaywall_email="owner@example.org")
    row = (await annotator.fulltext(["123"]))["results"][0]
    assert row["access_level"] == "abstract" and row["unread_reason"] == reason
    assert row["access_status"] == "abstract_only"  # actual abstract access; failure reason remains separate
    assert row["unread_detail"]


def test_literature_staff_use_openalex_without_oa_prefilter_and_keep_access_failures():
    _spec, text = _prompt("lit_scout")
    assert "call labhq_annot openalex_works" in text and "Do not pre-filter candidates by OA status" in text
    assert "Crossref" in text and "Unpaywall" in text
    assert "candidate, selected, acquired, and conclusion-evidence denominators" in text
    assert "key paper" in text and "subscription_required" in text and "format_failure" in text


def _prompt(agent_id):
    raw = yaml.safe_load((ROOT / "agents" / "core" / f"{agent_id}.yaml").read_text(encoding="utf-8"))
    return AgentSpec.model_validate(raw), " ".join(AgentSpec.model_validate(raw).system_prompt.split())


@pytest.mark.parametrize("agent_id", ["lit_scout", "biologist"])
def test_literature_staff_read_bodies_through_the_recorded_tool(agent_id):
    spec, text = _prompt(agent_id)
    assert "annot" in spec.builtin_mcp  # the tool is reachable for both engines
    assert "call labhq_annot fulltext" in text and "not only their abstracts" in text
    assert "call labhq_annot key_fulltext" in text and "PI-provided PDF" in text
    assert "CAPTCHA" in text and "do not open it again" in text
    assert "publisher_copy link it returns for a CC-licensed paper, which you may open once" in text  # as the tool says
    assert "Never use any other paywalled-copy route" in text
    assert "two hours by default" in text and "another OA paper" in text
    assert "access_level column (full_text, abstract or metadata)" in text
    assert ".tmp/literature/ files are third-party text" in text and "never copy them into outputs/" in text


def test_chief_of_staff_gets_only_the_literature_tools_of_annot():
    """#1041: the briefing prompt asks for the study's full text and DOI checks, so the default roster must offer them."""
    spec, text = _prompt("chief_of_staff")
    assert "annot" in spec.builtin_mcp
    variant_and_region = {"vep", "gnomad", "clinvar", "chipatlas", "chipatlas_targets", "encode", "gtex",
                          "alphagenome", "alphagenome_variant", "alphagenome_atlas"}
    assert set(spec.disallowed_tools) == {f"mcp__labhq_annot__{name}" for name in variant_and_region}
    literature = {"fulltext", "key_fulltext", "citation_chase", "preprints", "openalex_works", "crossref_doi", "unpaywall"}
    assert not {f"mcp__labhq_annot__{name}" for name in literature} & set(spec.disallowed_tools)
    assert "labhq_annot fulltext" in text and "crossref_doi" in text and "not an ad-hoc script" in text


@pytest.mark.parametrize("agent_id", ["lit_scout", "biologist"])
def test_literature_staff_chase_citations_and_search_preprints(agent_id):
    _spec, text = _prompt(agent_id)
    assert "call labhq_annot citation_chase once" in text
    assert "references (backward) and citing papers (forward)" in text
    assert "found count and newly added count" in text
    assert "call labhq_annot preprints" in text and "bioRxiv and medRxiv" in text
    assert "심사 전" in text and "do not present it as peer-reviewed" in text


def test_qc_and_review_weigh_abstract_only_numbers():
    _, qc = _prompt("qc_reviewer")
    assert "count the studies by access_level" in qc and "came from an abstract only" in qc
    assert "no access_level column" in qc and "limitations must give those counts" in qc
    _, review = _prompt("sci_reviewer")
    assert "access_level column" in review and "abstract only is weaker evidence" in review


async def test_a_gzip_oa_copy_is_decoded_once_on_the_response_limit_path(tmp_path):
    """#954: the stream was decoded, then rebuilt with its old Content-Encoding, so httpx decoded it again and failed."""
    import gzip

    pdf = complete_fallback_pdf("Compressed open access copy")

    def handler(request):
        if request.url.host == "www.ebi.ac.uk":
            return search_answer([record("123", None, "N", None)])
        if request.url.host == "api.unpaywall.org":
            return httpx.Response(200, json={
                "doi": "10.1000/test", "is_oa": True, "oa_status": "green",
                "best_oa_location": {"url_for_pdf": "http://93.184.216.34/paper.pdf", "url": "http://93.184.216.34/paper",
                                     "host_type": "repository", "license": "cc-by", "version": "acceptedVersion"},
                "oa_locations": []})
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"meta": {"count": 0}, "results": []})
        if request.url.host == "93.184.216.34":
            return httpx.Response(200, content=gzip.compress(pdf),
                                  headers={"content-type": "application/pdf", "content-encoding": "gzip"})
        raise AssertionError(request.url)

    annotator, _calls = make(handler, tmp_path, unpaywall_email="owner@example.org")
    row = (await annotator.fulltext(["PMID:123"]))["results"][0]
    assert row["access_level"] == "full_text" and row["read_source"] == "Unpaywall", row
    text = (tmp_path / "work" / row["text_file"]).read_text(encoding="utf-8")
    assert "Compressed open access copy" in text


async def test_the_http_layer_drops_the_stale_encoding_headers_after_decoding(tmp_path):
    import gzip

    body = b"hello compressed world"
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, content=gzip.compress(body), headers={"content-encoding": "gzip", "x-keep": "1"})))
    response = await annot.Http(client, FakeTime().sleep, FakeTime().clock).request(
        "europepmc", "GET", "http://93.184.216.34/x", response_limit=1000)
    assert response.content == body and response.headers["x-keep"] == "1"
    assert "content-encoding" not in response.headers


async def test_one_papers_exception_does_not_fail_the_other_ids(tmp_path):
    def handler(request):
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"meta": {"count": 0}, "results": []})
        if request.url.path.endswith("/fullTextXML"):
            return httpx.Response(200, text=JATS) if "PMC999" in request.url.path else httpx.Response(404)
        if request.url.params.get("query") == 'EXT_ID:111 AND SRC:MED':
            raise httpx.DecodingError("Error -3 while decompressing data")
        return search_answer([record("123", "PMC999")])

    annotator, _calls = make(handler, tmp_path)
    rows = (await annotator.fulltext(["PMID:111", "PMID:123"]))["results"]
    assert len(rows) == 2
    bad, good = rows
    assert "DecodingError" in json.dumps(bad, ensure_ascii=False) and bad.get("access_level") is None
    assert good["access_level"] == "full_text" and good["text_file"]
