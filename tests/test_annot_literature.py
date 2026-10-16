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
from labhq.tools import annot_literature as lit
from tests.test_annot import FakeTime

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
    </table-wrap></p>
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


def make(handler, tmp_path, workdir="work"):
    calls = []

    def recorder(request):
        calls.append(request)
        return handler(request)

    clock = FakeTime()
    (tmp_path / workdir).mkdir(exist_ok=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    annotator = lit.LiteratureAnnotator(client, tmp_path / workdir, tmp_path / "cache", sleep=clock.sleep,
                                        clock=clock.clock)
    return annotator, calls


def search_answer(records):
    return httpx.Response(200, json={"version": "6.9", "hitCount": len(records), "resultList": {"result": records}})


def by_query(table, xml=JATS):
    """Search answers keyed by the query string; fullTextXML answers `xml` for PMC999 and 404 for anything else."""
    def handler(request):
        if request.url.path.endswith("/fullTextXML"):
            return httpx.Response(200, text=xml) if "PMC999" in request.url.path else httpx.Response(404)
        return search_answer(table.get(request.url.params["query"], []))
    return handler


def test_paper_ids_are_read_in_the_forms_staff_write_them():
    assert lit.parse_paper_id("31868601") == lit.PaperId("pmid", "31868601")
    assert lit.parse_paper_id("PMID: 31868601") == lit.PaperId("pmid", "31868601")
    assert lit.parse_paper_id("pmc13248341") == lit.PaperId("pmcid", "PMC13248341")
    assert lit.parse_paper_id("https://doi.org/10.1016/S2542-5196(19)30239-6") == lit.PaperId(
        "doi", "10.1016/s2542-5196(19)30239-6")
    assert lit.parse_paper_id("doi:10.1080/X").query() == 'DOI:"10.1080/x"'
    assert lit.PaperId("doi", "10.1016/s2542-5196(19)30239-6").file_name() == "doi-10.1016_s2542-5196_19_30239-6.md"
    with pytest.raises(ValueError, match="PMID"):
        lit.parse_paper_id("Irgens 1997")


async def test_an_open_access_paper_is_written_as_markdown_and_the_record_keeps_only_its_path(tmp_path):
    annotator, calls = make(by_query({"EXT_ID:123 AND SRC:MED": [record()]}), tmp_path)
    out = await annotator.fulltext(["123"])
    row = out["results"][0]
    assert row["ok"] and row["access_level"] == "full_text" and row["pmcid"] == "PMC999"
    assert row["sections"] == ["Introduction", "Results"]
    assert (row["n_tables"], row["n_figures"], row["n_references"]) == (1, 1, 2)
    assert row["text_file"] == "outputs/literature/pmid-123.md"
    text = (tmp_path / "work" / row["text_file"]).read_text(encoding="utf-8")
    assert "## Results" in text and "### Main model" in text and "The odds ratio was 1.02." in text
    assert "| Group | Boys | aOR | 95% CI |  |" in text  # the spanning header keeps the columns aligned
    assert "| Office | 510 | 1.02 | 0.99 | 1\\|05 |" in text
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
    assert "markdown" not in answer  # the text lives once, in outputs/literature/
    assert answer["text_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
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
    text = (tmp_path / "work" / "outputs" / "literature" / "pmid-1.md").read_text(encoding="utf-8")
    assert "## Abstract" in text and "Boys were 51.2% of births." in text and "access_level: abstract" in text


async def test_an_unusable_body_falls_back_to_the_abstract_and_says_why(tmp_path):
    annotator, _ = make(by_query({"EXT_ID:123 AND SRC:MED": [record()]},
                                 xml='<!DOCTYPE a [<!ENTITY x "y">]><article><body/></article>'), tmp_path)
    row = (await annotator.fulltext(["123"]))["results"][0]
    assert row["access_level"] == "abstract" and "엔티티" in row["fulltext_error"]

    annotator, _ = make(by_query({"PMCID:PMC5": [record("5", "PMC5")]}), tmp_path, "work2")
    row = (await annotator.fulltext(["PMC5"]))["results"][0]
    assert row["access_level"] == "abstract"  # fullTextXML answered 404


async def test_unknown_and_malformed_ids_do_not_stop_the_rest(tmp_path):
    annotator, _ = make(by_query({"EXT_ID:123 AND SRC:MED": [record()]}), tmp_path)
    rows = (await annotator.fulltext(["123", "999", "Irgens 1997"]))["results"]
    assert rows[0]["access_level"] == "full_text"
    assert rows[1] == {"input": "999", "ok": True, "found": False}
    assert rows[2]["ok"] is False and "PMID" in rows[2]["error"]


async def test_a_cached_answer_writes_the_text_again_without_asking(tmp_path):
    first, _ = make(by_query({"EXT_ID:123 AND SRC:MED": [record()]}), tmp_path)
    await first.fulltext(["123"])
    second, calls = make(lambda request: httpx.Response(500), tmp_path, "other")
    out = await second.fulltext(["PMID:123"])
    row = out["results"][0]
    assert calls == [] and row["cached_from"] and row["access_level"] == "full_text"
    assert (tmp_path / "other" / "outputs" / "literature" / "pmid-123.md").is_file()
    assert out["source"]["release_or_version"] == "REST (cached answers)"


async def test_more_than_twenty_papers_in_one_call_is_refused(tmp_path):
    annotator, calls = make(lambda request: httpx.Response(500), tmp_path)
    with pytest.raises(ValueError, match="20편"):
        await annotator.fulltext([str(n) for n in range(1, 22)])
    assert calls == []


def _prompt(agent_id):
    raw = yaml.safe_load((ROOT / "agents" / "core" / f"{agent_id}.yaml").read_text(encoding="utf-8"))
    return AgentSpec.model_validate(raw), " ".join(AgentSpec.model_validate(raw).system_prompt.split())


@pytest.mark.parametrize("agent_id", ["lit_scout", "biologist"])
def test_literature_staff_read_bodies_through_the_recorded_tool(agent_id):
    spec, text = _prompt(agent_id)
    assert "annot" in spec.builtin_mcp  # the tool is reachable for both engines
    assert "call labhq_annot fulltext" in text and "not only their abstracts" in text
    assert "CAPTCHA" in text and "do not open it again" in text
    assert "Never fetch a paywalled paper from anywhere else" in text
    assert "access_level column (full_text, abstract or metadata)" in text
    assert "never list them as outputs" in text


def test_qc_and_review_weigh_abstract_only_numbers():
    _, qc = _prompt("qc_reviewer")
    assert "count the studies by access_level" in qc and "came from an abstract only" in qc
    assert "no access_level column" in qc and "limitations must give those counts" in qc
    _, review = _prompt("sci_reviewer")
    assert "access_level column" in review and "abstract only is weaker evidence" in review
