"""labhq_annot (#435 C ②): Ensembl VEP, gnomAD and ClinVar lookups with every HTTP call faked."""

import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, McpServerSpec, Task
from labhq.settings import Settings
from labhq.tools import annot
from labhq.tools._mcpcompat import list_tools

REPO = Path(__file__).resolve().parents[1]


class FakeTime:
    """Sleep advances a fake clock, so pacing and Retry-After waits are visible without waiting."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


def make(handler, tmp_path, cache=True):
    calls = []

    def record(request):
        calls.append(request)
        return handler(request)

    clock = FakeTime()
    (tmp_path / "work").mkdir(exist_ok=True)  # the runner's task workspace always exists
    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    annotator = annot.Annotator(client, tmp_path / "work", tmp_path / "cache" if cache else None,
                                sleep=clock.sleep, clock=clock.clock)
    return annotator, calls, clock


def vep_handler(release=115, refuse=(), throttle=None):
    """Echo VEP: one answer per entry. `refuse` entries make the whole POST a 400, like Ensembl does."""
    state = {"throttled": 0}

    def handler(request):
        if request.url.path == "/info/data":
            return httpx.Response(200, json={"releases": [release]})
        body = json.loads(request.content)
        (key, entries), = body.items()
        if throttle and state["throttled"] < throttle[0]:
            state["throttled"] += 1
            return httpx.Response(429, headers={"Retry-After": str(throttle[1])}, json={"error": "slow down"})
        if any(entry in refuse for entry in entries):
            return httpx.Response(400, json={"error": "Unable to parse HGVS notation"})
        out = []
        for entry in entries:
            ident = entry.split()[2] if key == "variants" else entry
            out.append({"id": ident, "input": entry, "seq_region_name": "17", "start": 43045712,
                        "allele_string": "T/C", "most_severe_consequence": "missense_variant",
                        "colocated_variants": [{"id": "rs80357906"}],
                        "transcript_consequences": [
                            {"gene_symbol": "BRCA1", "transcript_id": "ENST1", "consequence_terms": ["intron_variant"]},
                            {"gene_symbol": "BRCA1", "transcript_id": "ENST00000357654", "canonical": 1,
                             "mane_select": "NM_007294.4", "consequence_terms": ["missense_variant"],
                             "impact": "MODERATE", "hgvsc": "ENST00000357654.9:c.5096G>A",
                             "hgvsp": "ENSP00000350283.3:p.Arg1699Gln"}]})
        return httpx.Response(200, json=out)

    return handler


def test_parse_variant_formats():
    v = annot.parse_variant("chr17:43045712:t:c")
    assert (v.kind, v.key, v.chrom, v.pos, v.ref, v.alt) == ("vcf", "17:43045712:T:C", "17", 43045712, "T", "C")
    assert annot.parse_variant("chrM:3243:A:G").key == "MT:3243:A:G"
    assert annot.parse_variant("RS80357906").key == "rs80357906"
    assert annot.parse_variant("NM_007294.4:c.68_69del").kind == "hgvs"
    assert annot.parse_variant("NM_000546.6(TP53):c.215C>G").kind == "hgvs"
    assert annot.parse_variant("VCV000017661", clinvar_ids=True).key == "VCV000017661"
    assert annot.parse_variant("17661", clinvar_ids=True).kind == "uid"
    for bad in ("17-43045712-T-C", "23:1:A:G", "1:0:A:G", "1:5:A:A", "BRCA1", "", None, "17661"):
        with pytest.raises(ValueError):
            annot.parse_variant(bad)


async def test_vep_sends_batches_of_200_per_endpoint_and_keeps_input_order(tmp_path):
    annotator, calls, _clock = make(vep_handler(), tmp_path)
    variants = [f"1:{1000 + i}:A:G" for i in range(450)] + ["rs80357906", "NM_007294.4:c.5096G>A"]
    result = await annotator.vep(variants)
    posts = [c for c in calls if c.method == "POST"]
    assert [c.url.path for c in posts] == ["/vep/human/region"] * 3 + ["/vep/human/hgvs", "/vep/human/id"]
    assert [len(json.loads(c.content)["variants"]) for c in posts[:3]] == [200, 200, 50]
    assert json.loads(posts[0].content)["variants"][0] == "1 1000 labhq0 A G . . ."
    assert all(parse_qs(c.url.query.decode()) == {"canonical": ["1"], "hgvs": ["1"], "mane": ["1"]} for c in posts)
    assert result["n_inputs"] == result["n_ok"] == 452 and result["n_errors"] == 0
    assert [r["input"] for r in result["results"]] == variants
    first = result["results"][0]
    assert first["most_severe_consequence"] == "missense_variant" and first["genes"] == ["BRCA1"]
    assert first["transcript"]["mane_select"] == "NM_007294.4" and first["colocated_ids"] == ["rs80357906"]


async def test_vep_waits_out_429_and_paces_requests(tmp_path):
    annotator, calls, clock = make(vep_handler(throttle=(2, 1.5)), tmp_path)
    result = await annotator.vep(["1:100:A:G"])
    assert result["n_ok"] == 1
    assert len([c for c in calls if c.method == "POST"]) == 3
    assert clock.sleeps.count(1.5) == 2  # Retry-After honoured twice
    assert all(s > 0 for s in clock.sleeps)


async def test_pace_carries_across_annotators_that_share_it(tmp_path):
    """PR #449 review: the MCP server builds an Annotator per tool call; the pace must outlive it."""
    clock, pace, calls = FakeTime(), {}, []
    (tmp_path / "work").mkdir(exist_ok=True)

    def record(request):
        calls.append(request)
        return vep_handler()(request)

    async def one_call():
        client = httpx.AsyncClient(transport=httpx.MockTransport(record))
        annotator = annot.Annotator(client, tmp_path / "work", None, sleep=clock.sleep, clock=clock.clock, pace=pace)
        return await annotator.vep(["1:100:A:G"])

    await one_call()
    waited = len(clock.sleeps)
    await one_call()  # a second tool call right after: its first request still waits for Ensembl's interval
    assert len(clock.sleeps) > waited and clock.sleeps[waited] > 0
    assert set(pace) == {"ensembl"}


async def test_vep_spent_quota_fails_instead_of_waiting_an_hour(tmp_path):
    annotator, _calls, clock = make(vep_handler(throttle=(1, 3600)), tmp_path)
    with pytest.raises(annot.LookupFailed, match="요청 한도"):
        await annotator.vep(["1:100:A:G"])
    assert max(clock.sleeps, default=0) < annot.MAX_WAIT_S


async def test_vep_isolates_entries_ensembl_refuses_and_malformed_items(tmp_path):
    bad = "NM_007294.4:c.99999999del"
    annotator, calls, _clock = make(vep_handler(refuse={bad}), tmp_path)
    variants = ["not a variant", "NM_007294.4:c.5096G>A", bad, "NM_007294.4:c.68_69del", "1:100:A:G"]
    result = await annotator.vep(variants)
    rows = result["results"]
    assert [r["ok"] for r in rows] == [False, True, False, True, True]
    assert "형식" in rows[0]["error"] and "Unable to parse" in rows[2]["error"]
    hgvs_posts = [json.loads(c.content)["hgvs_notations"] for c in calls if c.url.path.endswith("/hgvs")]
    assert hgvs_posts[0] == variants[1:4] and [bad] in hgvs_posts  # split until the refused entry stands alone


async def test_vep_records_release_and_logs_only_counts_and_hash(tmp_path):
    annotator, _calls, _clock = make(vep_handler(release=115), tmp_path)
    result = await annotator.vep(["17:43045712:T:C", "zzz-not-variant"])
    source = result["source"]
    assert source["db"] == "Ensembl VEP" and source["release_or_version"] == "Ensembl 115 (GRCh38)"
    assert source["url"] == "https://rest.ensembl.org/vep/homo_sapiens" and source["queried_at"].endswith("Z")
    assert len(source["request_sha256"]) == 64
    log = (tmp_path / "work" / annot.QUERY_LOG).read_text(encoding="utf-8")
    line = json.loads(log.strip())
    assert line["request_sha256"] == source["request_sha256"] and line["release_or_version"] == "Ensembl 115 (GRCh38)"
    assert (line["n_inputs"], line["n_ok"], line["n_errors"]) == (2, 1, 1)
    assert "43045712" not in log and "zzz" not in log  # the variant list stays out of the log
    full = json.loads((tmp_path / "work" / result["full_result"]).read_text(encoding="utf-8"))
    assert full["source"] == source and full["results"][0]["answer"]["most_severe_consequence"] == "missense_variant"
    await annotator.vep(["17:43045712:T:C"])
    assert len((tmp_path / "work" / annot.QUERY_LOG).read_text(encoding="utf-8").splitlines()) == 2


async def test_vep_grch37_uses_the_grch37_server(tmp_path):
    annotator, calls, _clock = make(vep_handler(release=115), tmp_path)
    result = await annotator.vep(["17:41197732:T:C"], assembly="GRCh37")
    assert {c.url.host for c in calls} == {"grch37.rest.ensembl.org"}
    assert result["source"]["release_or_version"] == "Ensembl 115 (GRCh37)"
    with pytest.raises(ValueError):
        await annotator.vep(["1:1:A:G"], assembly="hg19")


async def test_vep_cache_hits_until_the_release_changes(tmp_path):
    first, calls, _clock = make(vep_handler(release=115), tmp_path)
    await first.vep(["1:100:A:G", "rs1"])
    again, calls2, _clock = make(vep_handler(release=115), tmp_path)
    result = await again.vep(["1:100:A:G", "rs1", "1:200:C:T"])
    posts = [json.loads(c.content) for c in calls2 if c.method == "POST"]
    assert posts == [{"variants": ["1 200 labhq0 C T . . ."]}]  # only the new variant goes out
    assert result["n_cached"] == 2 and "cached_from" in result["results"][0] and "cached_from" not in result["results"][2]
    newer, calls3, _clock = make(vep_handler(release=116), tmp_path)
    result = await newer.vep(["1:100:A:G"])
    assert result["n_cached"] == 0 and len([c for c in calls3 if c.method == "POST"]) == 1
    assert result["source"]["release_or_version"] == "Ensembl 116 (GRCh38)"


def gnomad_handler(seen):
    def handler(request):
        body = json.loads(request.content)
        seen.append(body["variables"])
        variables = body["variables"]
        if "mitochondrial_variant" in body["query"]:  # gnomAD's mtDNA query (live answer for m.3243A>G, 2026-10-06)
            if variables.get("variantId") == "M-3243-A-G":
                return httpx.Response(200, json={"data": {"mitochondrial_variant": {
                    "variant_id": "M-3243-A-G", "reference_genome": "GRCh38", "pos": 3243, "ref": "A", "alt": "G",
                    "rsids": ["rs199474657"], "filters": [], "an": 56383, "ac_het": 6, "ac_hom": 0,
                    "max_heteroplasmy": 0.464}}})
            return httpx.Response(200, json={"errors": [{"message": "Variant not found"}],
                                             "data": {"mitochondrial_variant": None}})
        if variables.get("variantId", "").startswith("M-"):
            return httpx.Response(200, json={"errors": [{"message": "Variant not found"}], "data": {"variant": None}})
        if variables.get("variantId") == "1-55051215-G-GA" or variables.get("rsid") == "rs121908120":
            population = [{"id": "afr", "ac": 1, "an": 100, "homozygote_count": 0, "hemizygote_count": 0},
                          {"id": "XX", "ac": 1, "an": 60, "homozygote_count": 0, "hemizygote_count": 0},
                          {"id": "afr_XX", "ac": 1, "an": 50, "homozygote_count": 0, "hemizygote_count": 0}]
            return httpx.Response(200, json={"data": {"variant": {
                "variant_id": "1-55051215-G-GA", "reference_genome": "GRCh38", "chrom": "1", "pos": 55051215,
                "ref": "G", "alt": "GA", "rsids": ["rs121908120"],
                "exome": {"ac": 3, "an": 1000, "homozygote_count": 0, "hemizygote_count": 0, "filters": [],
                          "populations": population},
                "genome": {"ac": 1, "an": 1000, "homozygote_count": 0, "hemizygote_count": 0, "filters": [],
                           "populations": population}}}})
        if variables.get("variantId") == "2-1-A-G":
            return httpx.Response(429, headers={"Retry-After": "10"})
        return httpx.Response(200, json={"errors": [{"message": "Variant not found"}], "data": {"variant": None}})
    return handler


async def test_gnomad_frequencies_absence_and_pacing(tmp_path):
    seen = []
    annotator, calls, clock = make(gnomad_handler(seen), tmp_path)
    result = await annotator.gnomad(["chr1:55051215:G:GA", "3:100:C:T", "rs121908120", "NM_1.1:c.1A>G", "x"],
                                    dataset="gnomad_r4")
    rows = result["results"]
    assert result["source"]["release_or_version"] == "gnomad_r4" and result["source"]["db"] == "gnomAD"
    assert rows[0]["found"] is True and rows[0]["af_exome_plus_genome"] == pytest.approx(4 / 2000)
    assert [p["id"] for p in rows[0]["exome"]["populations"]] == ["afr"]
    assert rows[1]["ok"] and rows[1]["found"] is False  # not in gnomAD: a successful lookup, not an error
    assert rows[2]["found"] is True and not rows[3]["ok"] and "HGVS" in rows[3]["error"] and not rows[4]["ok"]
    assert seen[0] == {"dataset": "gnomad_r4", "variantId": "1-55051215-G-GA"}
    assert seen[2] == {"dataset": "gnomad_r4", "rsid": "rs121908120"}
    assert "DatasetId!" in json.loads(calls[0].content)["query"]
    assert clock.sleeps == [pytest.approx(6.0)] * 2  # 10 queries a minute
    with pytest.raises(ValueError):
        await annotator.gnomad(["1:1:A:G"], dataset="gnomad_r4) { x }")


async def test_gnomad_caps_new_lookups_per_call_and_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(annot, "GNOMAD_MAX_PER_CALL", 2)
    seen = []
    annotator, _calls, clock = make(gnomad_handler(seen), tmp_path)
    result = await annotator.gnomad(["3:1:A:G", "3:2:A:G", "3:3:A:G"])
    assert [r["ok"] for r in result["results"]] == [True, True, False] and "note" in result
    assert "다시 호출" in result["results"][2]["error"] and len(seen) == 2
    again = await annotator.gnomad(["3:1:A:G", "3:2:A:G", "3:3:A:G"])
    assert again["n_cached"] == 2 and again["n_ok"] == 3 and len(seen) == 3
    with pytest.raises(annot.LookupFailed):  # 429 every time: a failure, never "not found"
        await annotator.gnomad(["2:1:A:G"])
    assert clock.sleeps.count(10.0) == annot.MAX_ATTEMPTS - 1


async def test_gnomad_sends_mitochondrial_variants_to_the_mtdna_query(tmp_path):
    seen = []
    annotator, calls, _clock = make(gnomad_handler(seen), tmp_path)
    result = await annotator.gnomad(["chrM:3243:A:G", "MT:3244:A:T", "1:55051215:G:GA"])
    melas, absent, nuclear = result["results"]
    assert melas["found"] is True and melas["rsids"] == ["rs199474657"]
    assert melas["mitochondrial"]["ac_het"] == 6 and melas["mitochondrial"]["af_het"] == pytest.approx(6 / 56383)
    assert melas["mitochondrial"]["max_heteroplasmy"] == 0.464 and melas["mitochondrial"]["af_hom"] == 0
    assert absent["ok"] and absent["found"] is False and nuclear["found"] is True
    queries = [json.loads(c.content)["query"] for c in calls]
    assert ["mitochondrial_variant(" in q for q in queries] == [True, True, False]
    assert seen[0] == {"dataset": "gnomad_r4", "variantId": "M-3243-A-G"}


async def test_gnomad_cache_ages_out_because_the_dataset_id_is_its_only_release(tmp_path):
    seen = []
    first, _calls, _clock = make(gnomad_handler(seen), tmp_path)
    await first.gnomad(["3:1:A:G", "3:2:A:G"])
    again, _calls, _clock = make(gnomad_handler(seen), tmp_path)
    assert (await again.gnomad(["3:1:A:G", "3:2:A:G"]))["n_cached"] == 2 and len(seen) == 2
    entries = sorted((tmp_path / "cache" / "gnomad").rglob("*.json"))
    assert len(entries) == 2
    stale = json.loads(entries[0].read_text(encoding="utf-8"))
    stale["queried_at"] = "2026-01-01T00:00:00Z"  # older than the 30-day limit
    entries[0].write_text(json.dumps(stale), encoding="utf-8")
    later, _calls, _clock = make(gnomad_handler(seen), tmp_path)
    result = await later.gnomad(["3:1:A:G", "3:2:A:G"])
    assert result["n_cached"] == 1 and len(seen) == 3  # only the aged entry is asked again
    assert annot.CACHE_MAX_AGE_S["gnomad"] == 30 * 24 * 3600 and "clinvar" not in annot.CACHE_MAX_AGE_S


def clinvar_handler(lastupdate="2026/10/05 03:12", terms=None):
    docs = {
        "17661": {"uid": "17661", "accession": "VCV000017661", "title": "NM_007294.4(BRCA1):c.5096G>A (p.Arg1699Gln)",
                  "genes": [{"symbol": "BRCA1"}],
                  "germline_classification": {"description": "Pathogenic", "review_status":
                                              "reviewed by expert panel", "last_evaluated": "2016/08/02",
                                              "trait_set": [{"trait_name": "Hereditary breast ovarian cancer"}]},
                  "variation_set": [{"canonical_spdi": "NC_000017.11:43045711:T:C"}]},
        "99": {"uid": "99", "accession": "VCV000000099", "title": "other allele at the same position",
               "germline_classification": {"description": "Benign"},
               "variation_set": [{"canonical_spdi": "NC_000017.11:43045711:T:G"}]},
        # BRCA1 c.68_69del as ClinVar holds it (live 2026-10-06): fully justified over the CTCT repeat.
        "17662": {"uid": "17662", "accession": "VCV000017662", "title": "NM_007294.4(BRCA1):c.68_69del (p.Glu23fs)",
                  "germline_classification": {"description": "Pathogenic"},
                  "variation_set": [{"canonical_spdi": "NC_000017.11:43124027:CTCT:CT"}]},
        "55667": {"uid": "55667", "accession": "VCV000055667", "title": "another variant at the padding base",
                  "germline_classification": {"description": "Uncertain significance"},
                  "variation_set": [{"canonical_spdi": "NC_000017.11:43124026:A:G"}]},
    }
    span = ["55667", "17662"]

    def handler(request):
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        assert query["retmode"] == "json" and query["tool"] == "labhq" and query["db"] == "clinvar"
        name = request.url.path.rsplit("/", 1)[-1]
        if name == "einfo.fcgi":
            return httpx.Response(200, json={"einforesult": {"dbinfo": [
                {"dbname": "clinvar", "dbbuild": "Build261005-0312.1", "lastupdate": lastupdate}]}})
        if name == "esearch.fcgi":
            if terms is not None:
                terms.append(query["term"])
            ids = {"17[chr] AND 43045712[chrpos38]": ["17661", "99"], "rs80357906": ["17661"],
                   "VCV000017661": ["17661"],
                   # The padding base alone (43124027) does not find 17662; the span does.
                   "17[chr] AND 43124027[chrpos38]": ["55667"],
                   "17[chr] AND 43124027:43124030[chrpos38]": span,
                   "17[chr] AND 43124029:43124032[chrpos38]": span,
                   "17[chr] AND 41276044:41276047[chrpos37]": span}.get(query["term"], [])
            return httpx.Response(200, json={"esearchresult": {"count": str(len(ids)), "idlist": ids}})
        if name == "esummary.fcgi":
            ids = query["id"].split(",")
            return httpx.Response(200, json={"result": {"uids": ids, **{i: docs[i] for i in ids if i in docs}}})
        raise AssertionError(request.url)
    return handler


async def test_clinvar_classifications_with_version_and_match(tmp_path):
    terms = []
    annotator, calls, clock = make(clinvar_handler(terms=terms), tmp_path)
    result = await annotator.clinvar(["17:43045712:T:C", "rs80357906", "VCV000017661", "17661", "3:5:A:G", "??"])
    assert result["source"]["release_or_version"] == "ClinVar Build261005-0312.1, last update 2026/10/05 03:12"
    rows = result["results"]
    assert [r["match"] for r in rows[0]["records"]] == ["exact", "other_allele"] and rows[0]["exact_match"] is True
    top = rows[0]["records"][0]
    assert top["germline_classification"]["description"] == "Pathogenic" and top["genes"] == ["BRCA1"]
    assert top["traits"] == ["Hereditary breast ovarian cancer"]
    assert all(r["records"][0]["accession"] == "VCV000017661" for r in rows[1:4])
    assert rows[4]["ok"] and rows[4]["found"] is False and not rows[5]["ok"]
    assert terms == ["17[chr] AND 43045712[chrpos38]", "rs80357906", "VCV000017661", "3[chr] AND 5[chrpos38]"]
    summaries = [c for c in calls if c.url.path.endswith("esummary.fcgi")]
    assert len(summaries) == 1  # one esummary for every distinct ID
    assert all(s == pytest.approx(1 / 3) for s in clock.sleeps) and clock.sleeps  # 3 requests a second


async def test_clinvar_vcf_deletion_searches_its_span_and_matches_the_justified_allele(tmp_path):
    terms = []
    annotator, _calls, _clock = make(clinvar_handler(terms=terms), tmp_path)
    # Left-aligned and right-shifted forms of c.68_69del, then a 1 bp deletion in the same repeat.
    result = await annotator.clinvar(["17:43124027:ACT:A", "17:43124029:TCT:T", "17:43124027:ACT:AC"])
    assert terms == ["17[chr] AND 43124027:43124030[chrpos38]", "17[chr] AND 43124029:43124032[chrpos38]",
                     "17[chr] AND 43124027:43124030[chrpos38]"]
    left, right, other = result["results"]
    for row in (left, right):
        assert row["exact_match"] is True and "meaning" not in row
        assert [(r["accession"], r["match"]) for r in row["records"]] == [("VCV000017662", "exact"),
                                                                        ("VCV000055667", "other_allele")]
    assert other["found"] is True and other["exact_match"] is False and "같은 ClinVar 항목은 없습니다" in other["meaning"]
    assert {r["match"] for r in other["records"]} == {"other_allele"}
    grch37 = await annotator.clinvar(["17:41276044:ACT:A"], assembly="GRCh37")
    row = grch37["results"][0]
    assert terms[-1] == "17[chr] AND 41276044:41276047[chrpos37]"
    assert {r["match"] for r in row["records"]} == {"not_compared"} and "exact_match" not in row
    assert "GRCh37" in row["meaning"]


def test_same_allele_handles_repeats_duplications_and_distinct_edits():
    # ClinVar NC_000017.11:43057062:GGG:GGGG (c.5266dup) against a left- or right-aligned VCF insertion.
    dup = (43057062, "GGG", "GGGG")
    assert annot._same_allele(dup, annot._spdi_of(annot.parse_variant("17:43057062:T:TG")))
    assert annot._same_allele(dup, (43057065, "", "G"))
    assert not annot._same_allele(dup, (43057062, "", "GG"))
    assert not annot._same_allele(dup, (43057062, "", "C"))
    longer = (100, "CTCTCT", "CTCT")  # a CT deletion anywhere in a three-unit repeat
    assert annot._same_allele(longer, (100, "CT", "")) and annot._same_allele(longer, (104, "CT", ""))
    assert not annot._same_allele(longer, (101, "T", ""))  # a 1 bp deletion is another variant
    assert not annot._same_allele((10, "A", "G"), (20, "A", "G"))  # edits that do not touch
    assert not annot._same_allele((10, "A", "G"), (10, "C", "G"))  # the two disagree on the reference
    assert annot._spdi_of(annot.parse_variant("1:100:ACT:AT")) == (100, "C", "")  # prefix and suffix trimmed


async def test_clinvar_cache_follows_the_clinvar_update(tmp_path):
    terms = []
    first, _calls, _clock = make(clinvar_handler(terms=terms), tmp_path)
    await first.clinvar(["rs80357906"])
    same, _calls, _clock = make(clinvar_handler(terms=terms), tmp_path)
    assert (await same.clinvar(["rs80357906"]))["n_cached"] == 1 and len(terms) == 1
    updated, _calls, _clock = make(clinvar_handler(lastupdate="2026/10/12 03:10", terms=terms), tmp_path)
    assert (await updated.clinvar(["rs80357906"]))["n_cached"] == 0 and len(terms) == 2


async def test_repeated_request_keeps_each_answer_its_log_line_cites(tmp_path):
    first, _calls, _clock = make(clinvar_handler(), tmp_path)
    one = await first.clinvar(["rs80357906"])
    updated, _calls, _clock = make(clinvar_handler(lastupdate="2026/10/12 03:10"), tmp_path)
    two = await updated.clinvar(["rs80357906"])
    assert one["source"]["request_sha256"] == two["source"]["request_sha256"]
    assert one["full_result"] != two["full_result"]
    lines = [json.loads(line) for line in
             (tmp_path / "work" / annot.QUERY_LOG).read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    for line in lines:
        text = (tmp_path / "work" / line["result_file"]).read_text(encoding="utf-8")
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == line["result_sha256"]
        assert json.loads(text)["source"]["release_or_version"] == line["release_or_version"]
    assert "2026/10/05" in lines[0]["release_or_version"] and "2026/10/12" in lines[1]["release_or_version"]


async def test_unreachable_service_fails_the_call(tmp_path):
    def down(request):
        raise httpx.ConnectError("no route")
    annotator, _calls, clock = make(down, tmp_path)
    with pytest.raises(annot.LookupFailed, match="연결하지 못했습니다"):
        await annotator.clinvar(["rs1"])
    assert len(clock.sleeps) == annot.MAX_ATTEMPTS - 1


def test_runner_wires_annot_without_per_call_approval(tmp_path):
    from labhq.runner.daemon import Runner

    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    runner = Runner(settings)
    try:
        with_annot = AgentSpec(id="a", name="A", role="r", engine=Engine.claude_code, builtin_mcp=["approval", "annot"])
        servers = runner._mcp_servers(with_annot, {"LABHQ_WORKDIR": str(tmp_path)})
        assert [s.name for s in servers] == ["labhq_approval", "labhq_annot", "labhq_ask"]
        spec = servers[1]
        assert spec.args == ["-m", "labhq.tools.annot_mcp"] and spec.auto_approve and spec.timeout_s
        without = AgentSpec(id="b", name="B", role="r", engine=Engine.codex, builtin_mcp=[])
        assert "labhq_annot" not in [s.name for s in runner._mcp_servers(without, {})]
    finally:
        runner.store.close()


async def _emit(kind, data):
    pass


@pytest.mark.parametrize("engine", ["claude_code", "codex"])
def test_both_engines_run_annot_tools_without_asking(tmp_path, engine):
    settings = Settings()
    agent = AgentSpec(id="a", name="A", role="r", engine=Engine(engine), builtin_mcp=["approval", "annot"])
    spec = McpServerSpec(name="labhq_annot", command=sys.executable, args=["-m", "labhq.tools.annot_mcp"],
                         auto_approve=True, timeout_s=900)
    ctx = RunContext(task=Task(agent_id="a", prompt="x"), agent=agent, workdir=tmp_path, settings=settings,
                     mcp_servers=[spec], env={}, emit=_emit, prompt="x", claude_settings={})
    ctx.meta_dir.mkdir(parents=True, exist_ok=True)
    adapter = get_adapter(agent.engine, settings)
    if engine == "claude_code":
        adapter.prepare(ctx)
        cmd = adapter.build_command(ctx)
        assert "mcp__labhq_annot" in cmd[cmd.index("--allowedTools"):]
        servers = json.loads((ctx.meta_dir / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        assert servers["labhq_annot"]["timeout"] == 900 * 1000
    else:
        cmd = adapter.build_command(ctx)
        assert 'mcp_servers.labhq_annot.default_tools_approval_mode="approve"' in cmd
        assert "mcp_servers.labhq_annot.tool_timeout_sec=900" in cmd


def test_core_staff_with_annot_and_roster_line(tmp_path):
    from labhq.gateway.server import Hub
    from labhq.orchestrator.cso import format_roster
    from labhq.registry import Registry

    registry = Registry(REPO / "agents", tmp_path / "talent")
    registry.load()
    with_annot = {a.id for a in registry.agents.values() if "annot" in a.builtin_mcp}
    assert with_annot == {"analyst", "bioinfo-agent", "biologist", "lit_scout"}
    analyst = registry.get("analyst").summary()
    assert "labhq_annot" in Hub._agent_mcp(analyst)
    assert "labhq_annot=yes" in format_roster([analyst])
    assert "labhq_annot" not in format_roster([registry.get("qc_reviewer").summary()])


async def test_stdio_server_lists_tools_and_answers_malformed_items_offline(tmp_path):
    env = {"PYTHONPATH": str(REPO), "LABHQ_WORKDIR": str(tmp_path / "work"),
           "LABHQ_STATE_DIR": str(tmp_path / "state")}
    (tmp_path / "work").mkdir()
    tools = await list_tools(McpServerSpec(name="annot", command=sys.executable,
                                           args=["-m", "labhq.tools.annot_mcp"], env=env))
    assert set(tools) == {"vep", "gnomad", "clinvar"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "labhq.tools.annot_mcp"],
                                   env={**os.environ, **env})
    async with stdio_client(params) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            # Every item malformed: nothing goes out to gnomAD, and the call still answers and logs.
            answer = await session.call_tool("gnomad", {"variants": ["not-a-variant"]})
            assert not answer.is_error
            payload = json.loads(answer.content[0].text)
            assert payload["n_errors"] == 1 and payload["source"]["release_or_version"] == "gnomad_r4"
            bad = await session.call_tool("gnomad", {"variants": ["1:1:A:G"], "dataset": "nope"})
            assert bad.is_error and "증거" in bad.content[0].text
    log = (tmp_path / "work" / "outputs" / "annotation_queries.jsonl").read_text(encoding="utf-8")
    assert json.loads(log)["tool"] == "gnomad"


async def test_without_a_workdir_the_answer_says_it_was_not_logged(tmp_path):
    annotator = annot.Annotator(httpx.AsyncClient(transport=httpx.MockTransport(vep_handler())), None, None)
    result = await annotator.gnomad(["not-a-variant"])
    assert "record_error" in result and "full_result" not in result


async def test_cache_schema_change_asks_again(tmp_path, monkeypatch):
    terms = []
    first, _calls, _clock = make(clinvar_handler(terms=terms), tmp_path)
    await first.clinvar(["rs80357906"])
    monkeypatch.setattr(annot, "CACHE_SCHEMA", annot.CACHE_SCHEMA + 1)
    again, _calls, _clock = make(clinvar_handler(terms=terms), tmp_path)
    assert (await again.clinvar(["rs80357906"]))["n_cached"] == 0 and len(terms) == 2
