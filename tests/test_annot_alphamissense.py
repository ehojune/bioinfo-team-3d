"""#621 A: AlphaMissense through the Ensembl VEP tool, with every HTTP call faked.

The shapes below are trimmed from live answers of POST https://rest.ensembl.org/vep/human/region with the query option
`AlphaMissense=1` (BRAF V600E at 7:140753336 A>T and a BRCA1 frameshift, checked 2026-10-15). Every test fails on main,
where the option is never sent and the summary has no `alphamissense`."""

import copy
import json
from urllib.parse import parse_qs

import httpx
import pytest

from labhq.tools import annot
from tests.test_annot import make

MANE = {"transcript_id": "ENST00000646891", "gene_symbol": "BRAF", "canonical": 1, "mane_select": "NM_004333.6",
        "consequence_terms": ["missense_variant"], "impact": "MODERATE", "hgvsc": "ENST00000646891.2:c.1799T>A",
        "hgvsp": "ENSP00000493543.1:p.Val600Glu",
        "alphamissense": {"am_class": "likely_pathogenic", "am_pathogenicity": 0.9927}}
OTHER = {"transcript_id": "ENST00000288602", "gene_symbol": "BRAF", "consequence_terms": ["missense_variant"],
         "alphamissense": {"am_pathogenicity": 0.1, "am_class": "likely_benign"}}
INTRON = {"transcript_id": "ENST00000867090", "gene_symbol": "BRAF", "consequence_terms": ["intron_variant"]}
FRAMESHIFT = {"transcript_id": "ENST00000357654", "gene_symbol": "BRCA1", "canonical": 1, "mane_select": "NM_007294.4",
              "consequence_terms": ["frameshift_variant"], "impact": "HIGH"}


def answer(transcripts, query, consequence="missense_variant"):
    """What Ensembl sends: AlphaMissense fields only when the option was asked for."""
    copies = copy.deepcopy(transcripts)
    if "AlphaMissense" not in query:
        for transcript in copies:
            transcript.pop("alphamissense", None)
    return {"seq_region_name": "7", "start": 140753336, "allele_string": "A/T", "most_severe_consequence": consequence,
            "transcript_consequences": copies}


def handler(transcripts, *, fail_with=None, seen=None, consequence="missense_variant"):
    def respond(request):
        if request.url.path == "/info/data":
            return httpx.Response(200, json={"releases": [115]})
        query = parse_qs(request.url.query.decode())
        if seen is not None:
            seen.append(query)
        if fail_with and "AlphaMissense" in query:
            return httpx.Response(fail_with, json={"error": "AlphaMissense plugin failed"})
        (key, entries), = json.loads(request.content).items()
        out = []
        for entry in entries:
            row = answer(transcripts, query, consequence)
            row.update(id=entry.split()[2] if key == "variants" else entry, input=entry)
            out.append(row)
        return httpx.Response(200, json=out)
    return respond


def fresh(tmp_path, name):
    """make() needs its folder to exist."""
    (tmp_path / name).mkdir()
    return tmp_path / name


async def summary_of(tmp_path, transcripts, consequence="missense_variant"):
    tmp_path.mkdir(exist_ok=True)
    annotator, _calls, _clock = make(handler(transcripts, consequence=consequence), tmp_path)
    result = await annotator.vep(["7:140753336:A:T"])
    return result["results"][0]


async def test_alphamissense_is_requested_for_human_grch38_only(tmp_path):
    seen: list[dict] = []
    annotator, _calls, _clock = make(handler([MANE], seen=seen), tmp_path)
    await annotator.vep(["7:140753336:A:T"])
    await annotator.vep(["7:140753336:A:T"], species="homo_sapiens")
    assert seen[0] == {"canonical": ["1"], "hgvs": ["1"], "mane": ["1"], "AlphaMissense": ["1"]} and seen[1] == seen[0]
    other = []
    annotator, _calls, _clock = make(handler([MANE], seen=other), fresh(tmp_path, "other"))
    await annotator.vep(["7:140453136:A:T"], assembly="GRCh37")
    await annotator.vep(["7:140753336:A:T"], species="mus_musculus")
    assert all("AlphaMissense" not in query for query in other)  # the GRCh37 server uses other class names (verified)


async def test_score_and_class_are_parsed_and_translated_to_plain_korean(tmp_path):
    row = await summary_of(tmp_path, [MANE])
    am = row["alphamissense"]
    assert (am["status"], am["am_pathogenicity"], am["am_class"]) == ("ok", 0.9927, "likely_pathogenic")
    assert am["class_ko"] == "병원성 가능성 높음" and am["text"] == "AlphaMissense: 0.99 (병원성 가능성 높음)"
    assert (am["transcript_id"], am["gene_symbol"]) == ("ENST00000646891", "BRAF")
    assert row["transcript"]["hgvsp"] == "ENSP00000493543.1:p.Val600Glu"  # the rest of the summary is untouched


@pytest.mark.parametrize("am_class, score, korean", [
    ("likely_pathogenic", 0.93, "병원성 가능성 높음"), ("ambiguous", 0.45, "불확실"),
    ("likely_benign", 0.05, "양성 가능성 높음"),
    ("pathogenic", 0.97, "병원성 가능성 높음"), ("benign", 0.02, "양성 가능성 높음"),  # GRCh37 server's names
])
async def test_every_alphamissense_class_has_a_korean_reading(tmp_path, am_class, score, korean):
    transcript = {**MANE, "alphamissense": {"am_pathogenicity": score, "am_class": am_class}}
    am = (await summary_of(tmp_path, [transcript]))["alphamissense"]
    assert am["class_ko"] == korean and am["text"] == f"AlphaMissense: {score:.2f} ({korean})"


async def test_the_mane_then_canonical_transcript_is_the_one_read(tmp_path):
    canonical = {**OTHER, "transcript_id": "ENST_CANON", "canonical": 1,
                 "alphamissense": {"am_pathogenicity": 0.62, "am_class": "likely_pathogenic"}}
    mane = {**MANE, "alphamissense": {"am_pathogenicity": 0.8, "am_class": "likely_pathogenic"}}
    am = (await summary_of(tmp_path, [OTHER, canonical, mane]))["alphamissense"]
    assert (am["transcript_id"], am["am_pathogenicity"]) == ("ENST00000646891", 0.8)  # MANE wins over canonical
    am = (await summary_of(fresh(tmp_path, "b"), [OTHER, canonical]))["alphamissense"]
    assert (am["transcript_id"], am["am_pathogenicity"]) == ("ENST_CANON", 0.62)  # canonical wins over the first one
    am = (await summary_of(fresh(tmp_path, "c"), [OTHER]))["alphamissense"]
    assert am["am_pathogenicity"] == 0.1  # no MANE or canonical: the first scored transcript


async def test_another_transcript_score_is_separate_from_the_shown_transcript(tmp_path):
    unscored = {key: value for key, value in MANE.items() if key != "alphamissense"}
    row = await summary_of(tmp_path, [unscored, OTHER])
    am = row["alphamissense"]
    assert row["transcript"]["transcript_id"] == am["transcript_id"] == "ENST00000646891"
    assert am["status"] == "no_score" and "점수 없음 (이 transcript)" in am["text"]
    assert "am_pathogenicity" not in am
    other = row["alphamissense_other"]
    assert (other["transcript_id"], other["am_pathogenicity"], other["am_class"]) == (
        "ENST00000288602", 0.1, "likely_benign")
    assert other["text"] == "다른 transcript(ENST00000288602)에는 0.10 (양성 가능성 높음)"


async def test_a_variant_that_is_not_missense_reads_not_applicable(tmp_path):
    row = await summary_of(tmp_path, [FRAMESHIFT, INTRON], consequence="frameshift_variant")
    assert row["alphamissense"]["status"] == "not_applicable" and "해당 없음" in row["alphamissense"]["text"]
    assert "am_pathogenicity" not in row["alphamissense"]
    row = await summary_of(fresh(tmp_path, "b"), [], consequence="intergenic_variant")
    assert "해당 없음" in row["alphamissense"]["text"]


async def test_a_missense_variant_without_a_score_is_not_called_not_applicable(tmp_path):
    unscored = {key: value for key, value in MANE.items() if key != "alphamissense"}
    am = (await summary_of(tmp_path, [unscored]))["alphamissense"]
    assert am["status"] == "no_score" and "점수 없음" in am["text"] and "해당 없음" not in am["text"]


@pytest.mark.parametrize("bad", ["high", None, 7, -0.1, True, [0.9]])
async def test_a_malformed_field_is_no_score_and_the_rest_of_the_answer_stays(tmp_path, bad):
    transcript = {**MANE, "alphamissense": {"am_pathogenicity": bad, "am_class": "likely_pathogenic"}}
    row = await summary_of(tmp_path, [transcript])
    assert row["ok"] and row["alphamissense"]["status"] == "no_score"
    assert row["most_severe_consequence"] == "missense_variant" and row["transcript"]["mane_select"] == "NM_004333.6"
    garbage = await summary_of(fresh(tmp_path, "b"), [{**MANE, "alphamissense": "n/a"}])
    assert garbage["alphamissense"]["status"] == "no_score" and garbage["genes"] == ["BRAF"]


async def test_an_unknown_class_name_is_shown_as_it_came(tmp_path):
    transcript = {**MANE, "alphamissense": {"am_pathogenicity": 0.5, "am_class": "Maybe"}}
    am = (await summary_of(tmp_path, [transcript]))["alphamissense"]
    assert am["text"] == "AlphaMissense: 0.50 (분류 maybe)"


@pytest.mark.parametrize("status", [400, 500])
async def test_a_failing_plugin_does_not_break_the_rest_of_the_vep_result(tmp_path, status):
    seen: list[dict] = []
    annotator, _calls, _clock = make(handler([MANE], fail_with=status, seen=seen), tmp_path)
    result = await annotator.vep(["7:140753336:A:T", "NM_004333.6:c.1799T>A"])
    assert result["n_ok"] == 2 and result["n_errors"] == 0
    for row in result["results"]:
        assert row["most_severe_consequence"] == "missense_variant" and row["transcript"]["mane_select"] == "NM_004333.6"
        assert row["alphamissense"]["status"] == "unavailable" and "점수 없이" in row["alphamissense"]["text"]
    assert "2개" in result["note"] and "AlphaMissense" in result["note"]
    assert ["AlphaMissense" in query for query in seen] == [True, False, True, False]  # asked, then asked again without
    again, calls, _clock = make(handler([MANE], fail_with=status), tmp_path)
    assert (await again.vep(["7:140753336:A:T"]))["n_cached"] == 0  # an answer without the plugin is never cached
    assert len([c for c in calls if c.method == "POST"]) == 2


async def test_an_entry_ensembl_refuses_is_still_an_error_when_the_plugin_is_on(tmp_path):
    def refuse_one(request):
        if request.url.path != "/info/data" and b"BAD" in request.content:
            return httpx.Response(400, json={"error": "Unable to parse HGVS notation"})
        return handler([MANE])(request)

    annotator, _calls, _clock = make(refuse_one, tmp_path)
    result = await annotator.vep(["NM_004333.6:c.1799T>A", "NM_BAD:c.1del"])
    rows = result["results"]
    assert [r["ok"] for r in rows] == [True, False] and "Unable to parse" in rows[1]["error"]
    assert rows[0]["alphamissense"]["status"] == "ok"  # the good entry kept its score: the plugin was not blamed


async def test_the_cache_key_carries_the_plugin_option_so_an_old_answer_is_not_complete(tmp_path):
    before = {"assembly": "GRCh38", "species": "human", **annot.VEP_OPTIONS}
    after = {**before, **annot.VEP_PLUGIN_OPTIONS}
    assert annot.VEP_PLUGIN_OPTIONS == {"AlphaMissense": 1}
    assert annot.Cache.key("vep", "r", before, "1:1:A:G") != annot.Cache.key("vep", "r", after, "1:1:A:G")
    # an answer cached before AlphaMissense existed sits under the old key; the new request must not read it
    old_answer = answer([{key: value for key, value in MANE.items() if key != "alphamissense"}], {})
    annotator, calls, _clock = make(handler([MANE]), tmp_path)
    annotator.cache.put("vep", annot.Cache.key("vep", "Ensembl 115 (GRCh38)", before, "7:140753336:A:T"),
                        old_answer, "2026-10-01T00:00:00Z")
    result = await annotator.vep(["7:140753336:A:T"])
    assert result["n_cached"] == 0 and len([c for c in calls if c.method == "POST"]) == 1
    assert result["results"][0]["alphamissense"]["status"] == "ok"
    again, calls2, _clock = make(handler([MANE]), tmp_path)  # the complete answer is cached under the new key
    cached = await again.vep(["7:140753336:A:T"])
    assert cached["n_cached"] == 1 and not [c for c in calls2 if c.method == "POST"]
    assert cached["results"][0]["alphamissense"]["text"] == "AlphaMissense: 0.99 (병원성 가능성 높음)"


async def test_license_and_usage_note_are_recorded_with_the_release(tmp_path):
    annotator, _calls, _clock = make(handler([MANE]), tmp_path)
    result = await annotator.vep(["7:140753336:A:T"])
    (extra,) = result["source"]["additional_sources"]
    assert extra["db"].startswith("AlphaMissense") and "CC BY 4.0" in extra["license"]
    assert "임상 근거가 아니" in extra["usage"] and "10.1126/science.adg7492" in extra["citation"]
    assert "Ensembl 115" in result["source"]["release_or_version"]  # the Ensembl release is still the cache's release
    assert "임상 근거가 아닙니다" in result["note"]
    logged = json.loads((tmp_path / "work" / annot.QUERY_LOG).read_text(encoding="utf-8"))
    assert logged["additional_sources"] == result["source"]["additional_sources"]
    full = json.loads((tmp_path / "work" / result["full_result"]).read_text(encoding="utf-8"))
    assert full["source"]["additional_sources"][0]["license"] == extra["license"]
    plain, _calls, _clock = make(handler([MANE]), fresh(tmp_path, "grch37"))
    older = await plain.vep(["7:140453136:A:T"], assembly="GRCh37")
    assert "additional_sources" not in older["source"] and "note" not in older
    assert older["results"][0]["alphamissense"]["status"] == "not_requested"  # honest about why there is no score
