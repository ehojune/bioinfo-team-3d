"""labhq_annot regional tools (#435 C ③): ChIP-Atlas, ENCODE cCREs, GTEx and AlphaGenome, with every HTTP call and the
AlphaGenome client faked. Also the AlphaGenome key: asked by `labhq init`, kept in an owner-only file, never shown."""

import enum
import gzip
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
import yaml
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from labhq import doctor, init_wizard as wizard, private_paths
from labhq.models import AgentSpec, Engine, McpServerSpec
from labhq.settings import Settings
from labhq.tools import annot, annot_keys, annot_regulatory as reg
from labhq.tools._mcpcompat import list_tools
from tests.test_annot import FakeTime

REPO = Path(__file__).resolve().parents[1]
FAKE_KEY = "AIzaFAKE-test-key-0123456789"
REAL_LABHQ_ENTRIES = private_paths._labhq_entries  # conftest replaces it for every test


def make(handler, tmp_path, cache=True, alphagenome=None):
    calls = []

    def record(request):
        calls.append(request)
        return handler(request)

    clock = FakeTime()
    (tmp_path / "work").mkdir(exist_ok=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    annotator = reg.RegulatoryAnnotator(client, tmp_path / "work", tmp_path / "cache" if cache else None,
                                        sleep=clock.sleep, clock=clock.clock, alphagenome=alphagenome)
    return annotator, calls, clock


def _log(tmp_path):
    path = tmp_path / "work" / "outputs" / "annotation_queries.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _full(tmp_path, result):
    return json.loads((tmp_path / "work" / result["full_result"]).read_text(encoding="utf-8"))


def test_parse_region_and_gene():
    region = reg.parse_region("12:53,380,000-53,385,000")
    assert (region.key, region.chrom, region.start0, region.end) == ("chr12:53380000-53385000", "chr12",
                                                                     53379999, 53385000)
    assert reg.parse_region("chrMT:100").key == "chrM:100-100"
    for bad in ("chr12:5-1", "chr12", "chrQ:1-2", "chr1:0-5"):
        with pytest.raises(ValueError):
            reg.parse_region(bad)
    with pytest.raises(ValueError, match="bp보다"):
        reg.parse_region("chr1:1-3000001", 2_000_000)
    assert reg.parse_gene("sort1").key == "SORT1" and reg.parse_gene("ENSG00000134243.12").key == "ENSG00000134243.12"
    with pytest.raises(ValueError):
        reg.parse_gene("not a gene")


# ----- ChIP-Atlas -----
RID = "wabi_chipatlas_2026-1006-1801-10-306-248071"
EA_TSV = "\n".join([
    "SRX100\tTFs and others\tPOU5F1\tPluripotent stem cell\tH1\t20000\t6/8\t500/20000\t-12.5\t-10.2\t15.3",
    "SRX101\tTFs and others\tNANOG\tPluripotent stem cell\tH9\t15000\t5/8\t800/20000\t-8.1\t-6.0\t9.8",
    "SRX102\tTFs and others\tPOU5F1\tPluripotent stem cell\tH9\t9000\t4/8\t700/20000\t-5.0\t-3.1\t8.1",
    "SRX103\tTFs and others\tCTCF\tBlood\tK562\t50000\t1/8\t5000/20000\t-0.1\t0\t0.5"]) + "\n"


def chipatlas_handler(polls=2, tsv=EA_TSV, state=None):
    state = state if state is not None else {}
    state.update(posts=[], gets=0)

    def handler(request):
        if request.method == "HEAD" and request.url.path.endswith("/metadata/experimentList.tab"):
            return httpx.Response(200, headers={"Last-Modified": "Mon, 28 Sep 2026 08:00:16 GMT"})
        if request.method == "POST":
            state["posts"].append({k: v[0] for k, v in parse_qs(request.content.decode(),
                                                                 keep_blank_values=True).items()})
            return httpx.Response(200, text=f"requestId: {RID}\nparameters: null\ncurrent-state: \n")
        if request.url.path == f"/wabi/chipatlas/{RID}":
            assert dict(request.url.params) == {"info": "result", "format": "tsv"}
            state["gets"] += 1
            if state["gets"] <= polls:
                return httpx.Response(404, json={"Message": "Error ( Result tsv file of your request id have been "
                                                            "NOT FOUND, or still running.)"})
            return httpx.Response(200, text=tsv)
        raise AssertionError(f"unexpected {request.method} {request.url}")
    return handler, state


async def test_chipatlas_gene_list_submits_waits_and_records(tmp_path):
    handler, state = chipatlas_handler(polls=2)
    annotator, _calls, clock = make(handler, tmp_path)
    result = await annotator.chipatlas(["POU5F1", "nanog", "SOX2", "not a gene", "POU5F1"], cell_class="All cell types")
    form = state["posts"][0]
    assert form["typeA"] == "gene" and form["typeB"] == "refseq" and form["bedBFile"] == "empty"
    assert form["bedAFile"] == "POU5F1\nnanog\nSOX2" and form["genome"] == "hg38" and form["threshold"] == "50"
    assert form["distanceUp"] == form["distanceDown"] == "5000" and form["antigenClass"] == "TFs and others"
    assert state["gets"] == 3 and clock.sleeps.count(reg.CHIPATLAS_POLL_S) == 2  # two "still running" answers
    assert result["source"]["release_or_version"] == "ChIP-Atlas (experimentList.tab 2026-09-28)"
    assert result["n_ok"] == 3 and result["n_errors"] == 1 and result["input_errors"][0]["input"] == "not a gene"
    assert result["request_id"] == RID and result["n_experiments"] == 4 and result["n_q_below_0_05"] == 3
    assert [a["antigen"] for a in result["top_antigens"]] == ["POU5F1", "NANOG", "CTCF"]
    assert result["top_antigens"][0]["n_experiments"] == 2 and result["top_experiments"][0]["srx"] == "SRX100"
    log = _log(tmp_path)[0]
    assert log["tool"] == "chipatlas" and "POU5F1" not in json.dumps(log) and log["n_inputs"] == 4
    full = _full(tmp_path, result)
    assert full["inputs"] == ["POU5F1", "NANOG", "SOX2"] and len(full["answer"]["rows"]) == 4
    assert full["answer"]["rows"][0]["fold_enrichment"] == 15.3
    # The same set again (any order) comes from the cache: nothing is submitted.
    again, _calls, _clock = make(chipatlas_handler(polls=0, state=state)[0], tmp_path)
    cached = await again.chipatlas(["SOX2", "POU5F1", "NANOG"])
    assert cached["n_cached"] == 3 and state["posts"] == []


async def test_chipatlas_regions_use_bed_and_random_background_and_refuse_mixing(tmp_path):
    handler, state = chipatlas_handler(polls=0)
    annotator, _calls, _clock = make(handler, tmp_path)
    result = await annotator.chipatlas(["chr1:1001-2000", "chrX:5", "SOX2"], antigen_class="Histone",
                                       cell_class="Blood", threshold=100, permutations=10)
    form = state["posts"][0]
    assert form["typeA"] == "bed" and form["typeB"] == "rnd" and form["permTime"] == "10"
    assert form["bedAFile"] == "chr1\t1000\t2000\nchrX\t4\t5" and form["cellClass"] == "Blood"
    assert result["input_errors"] == [{"input": "SOX2", "error": "한 호출에 유전자와 영역을 섞을 수 없습니다. 따로 부르세요"}]
    with pytest.raises(ValueError, match="antigen_class"):
        await annotator.chipatlas(["SOX2"], antigen_class="TF")
    with pytest.raises(ValueError, match="조회할"):
        await annotator.chipatlas(["???"])


async def test_chipatlas_times_out_with_a_request_id_and_resumes_without_resubmitting(tmp_path):
    handler, state = chipatlas_handler(polls=10_000)
    annotator, _calls, clock = make(handler, tmp_path)
    with pytest.raises(annot.LookupFailed, match=RID) as failure:
        await annotator.chipatlas(["SOX2"], wait_s=60)
    assert "60초" in str(failure.value) and len(state["posts"]) == 1
    assert sum(clock.sleeps) <= 60
    assert not (tmp_path / "work" / "outputs" / "annotation_queries.jsonl").exists()
    handler, state = chipatlas_handler(polls=1)
    resumed, _calls, _clock = make(handler, tmp_path)
    result = await resumed.chipatlas(["SOX2"], request_id=RID)
    assert state["posts"] == [] and result["n_experiments"] == 4
    with pytest.raises(ValueError, match="request_id"):
        await resumed.chipatlas(["SOX2"], request_id="../../etc")


async def test_chipatlas_job_error_and_refused_submission_fail_the_call(tmp_path):
    handler, _state = chipatlas_handler(polls=0, tsv="[ERROR] no peaks for this genome\n")
    annotator, _calls, _clock = make(handler, tmp_path)
    with pytest.raises(annot.LookupFailed, match="분석 실패"):
        await annotator.chipatlas(["SOX2"])

    def refuse(request):
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Last-Modified": "Mon, 28 Sep 2026 08:00:16 GMT"})
        return httpx.Response(200, text="error: genome not supported\n")
    annotator, _calls, _clock = make(refuse, tmp_path)
    with pytest.raises(annot.LookupFailed, match="받지 않았습니다"):
        await annotator.chipatlas(["SOX2"])


def targets_handler(state):
    table = ("Target_genes\tPOU5F1|Average\tSRX1|H1\tSRX2|H9\tSTRING\n"
             "REST\t333.1\t300\t366\t0\nNANOG\t192.8\t180\t205\t651\nGAPDH\t1.5\t0\t3\t0\n")

    def handler(request):
        state.setdefault("gets", 0)
        if request.url.path == "/data/hg38/target/POU5F1.5.tsv":
            headers = {"Last-Modified": "Sat, 26 Sep 2026 10:54:25 GMT", "Content-Length": str(len(table))}
            if request.method == "HEAD":
                return httpx.Response(200, headers=headers)
            state["gets"] += 1
            return httpx.Response(200, text=table, headers={"Last-Modified": headers["Last-Modified"]})
        return httpx.Response(404, text="Not Found")
    return handler


async def test_chipatlas_targets_rank_genes_and_report_absence(tmp_path):
    state = {}
    annotator, _calls, _clock = make(targets_handler(state), tmp_path)
    result = await annotator.chipatlas_targets(["POU5F1", "NOPE1", "bad name!"], genes=["nanog", "SOX2"], top=2)
    found, absent, bad = result["results"]
    assert found["found"] and found["n_experiments"] == 2 and found["n_genes"] == 3
    assert [g["gene"] for g in found["top"]] == ["REST", "NANOG"] and found["top"][1]["string_score"] == 651.0
    assert found["requested_genes"] == [{"gene": "NANOG", "average_score": 192.8, "string_score": 651.0},
                                        {"gene": "SOX2", "average_score": None}]
    assert absent["ok"] and absent["found"] is False and not bad["ok"]
    assert result["source"]["release_or_version"] == "ChIP-Atlas target genes (파일 2026-09-26)"
    again, _calls, _clock = make(targets_handler(state), tmp_path)
    assert (await again.chipatlas_targets(["POU5F1"]))["n_cached"] == 1 and state["gets"] == 1
    with pytest.raises(ValueError, match="distance_kb"):
        await again.chipatlas_targets(["POU5F1"], distance_kb=3)


# ----- ENCODE -----
CCRE_BED = "\n".join([
    "chr1\t100\t200\tEH38E0000001\t0\t.\t100\t200\t255,0,0\tdELS",
    "chr12\t53379679\t53380029\tEH38E3018688\t0\t.\t53379679\t53380029\t255,167,0\tpELS",
    "chr12\t53380037\t53380206\tEH38E3018689\t0\t.\t53380037\t53380206\t255,0,0\tPLS",
    "chr12\t60000000\t60000100\tEH38E9999999\t0\t.\t60000000\t60000100\t0,176,240\tCA-CTCF"]) + "\n"
CCRE_GZ = gzip.compress(CCRE_BED.encode())


def encode_handler(state, md5=None):
    state.setdefault("downloads", 0)

    def handler(request):
        params = request.url.params
        if request.url.host == "www.encodeproject.org" and request.url.path == "/search/":
            assert params["format"] == "json" and request.headers["Accept"] == "application/json"
            if params["type"] == "Annotation":
                assert params["assembly"] == "GRCh38" and params["biosample_ontology.term_name!"] == "*"
                return httpx.Response(200, json={"@graph": [
                    {"accession": "ENCSR487PRC", "encyclopedia_version": ["ENCODE v3", "current"],
                     "date_released": "2022-02-15", "description": "candidate Cis-Regulatory Elements for GRCh38"},
                    {"accession": "ENCSR800VNX", "encyclopedia_version": ["ENCODE v4"], "date_released": "2023-03-27",
                     "description": "agnostic candidate Cis-Regulatory Elements for GRCh38"}]})
            assert params["dataset"] == "/annotations/ENCSR800VNX/"
            return httpx.Response(200, json={"@graph": [{"accession": "ENCFF420VPZ", "file_size": len(CCRE_GZ),
                                                         "href": "/files/ENCFF420VPZ/@@download/ENCFF420VPZ.bed.gz",
                                                         "md5sum": md5 or hashlib.md5(CCRE_GZ).hexdigest()}]})
        if request.url.path == "/files/ENCFF420VPZ/@@download/ENCFF420VPZ.bed.gz":
            return httpx.Response(307, headers={"Location": "https://encode-public.s3.amazonaws.com/x/ENCFF420VPZ.bed.gz"})
        if request.url.host == "encode-public.s3.amazonaws.com":
            state["downloads"] += 1
            return httpx.Response(200, content=CCRE_GZ)
        raise AssertionError(f"unexpected {request.url}")
    return handler


async def test_encode_overlaps_the_newest_registry_file_and_keeps_it(tmp_path):
    state = {}
    annotator, _calls, _clock = make(encode_handler(state), tmp_path)
    result = await annotator.encode(["chr12:53380000-53385000", "1:150", "chr2:1-1000", "chr1:5-1"])
    assert result["source"]["release_or_version"] == ("ENCODE cCRE registry V4 (ENCSR800VNX, file ENCFF420VPZ, "
                                                      "released 2023-03-27)")
    first, second, empty, bad = result["results"]
    assert first["n_ccres"] == 2 and first["classes"] == {"pELS": 1, "PLS": 1}
    assert first["ccres"][0] == {"accession": "EH38E3018688", "chrom": "chr12", "start": 53379680, "end": 53380029,
                                 "class": "pELS"}
    assert second["n_ccres"] == 1 and empty["n_ccres"] == 0 and "meaning" in empty and not bad["ok"]
    assert (tmp_path / "cache" / "encode" / "ENCFF420VPZ.bed.gz").read_bytes() == CCRE_GZ
    # A new region reuses the downloaded registry; a known one comes from the answer cache.
    again, _calls, _clock = make(encode_handler(state), tmp_path)
    result = await again.encode(["chr12:59999990-60000010", "chr12:53380000-53385000"])
    assert state["downloads"] == 1 and result["n_cached"] == 1 and result["results"][0]["classes"] == {"CA-CTCF": 1}


async def test_encode_refuses_a_file_whose_md5_differs(tmp_path):
    annotator, _calls, _clock = make(encode_handler({}, md5="0" * 32), tmp_path, cache=False)
    with pytest.raises(annot.LookupFailed, match="md5"):
        await annotator.encode(["chr1:150"])
    with pytest.raises(ValueError, match="assembly"):
        await annotator.encode(["chr1:150"], assembly="hg19")


def test_scan_ccres_handles_long_regions_and_many_chromosomes(tmp_path):
    path = tmp_path / "r.bed.gz"
    path.write_bytes(CCRE_GZ)
    regions = [reg.parse_region("chr12:1-60000001"), reg.parse_region("chr12:53380100"), reg.parse_region("chr1:201")]
    hits = reg.scan_ccres(path, regions)
    assert [h["accession"] for h in hits["chr12:1-60000001"]] == ["EH38E3018688", "EH38E3018689", "EH38E9999999"]
    assert [h["accession"] for h in hits["chr12:53380100-53380100"]] == ["EH38E3018689"]
    assert hits["chr1:201-201"] == []  # BED end is exclusive: chr1:100-200 covers 1-based 101..200


# ----- GTEx -----
def gtex_handler(state):
    state.setdefault("paths", [])

    def handler(request):
        path = request.url.path.removeprefix("/api/v2/")
        params = dict(request.url.params)
        state["paths"].append((path, params))
        if path == "metadata/dataset":
            return httpx.Response(200, json=[{"datasetId": "gtex_v10", "displayName": "GTEx Analysis v10",
                                              "gencodeVersion": "v39", "genomeBuild": "GRCh38/hg38",
                                              "dbSnpBuild": 155}])
        if path == "dataset/tissueSiteDetail":
            return httpx.Response(200, json={"data": [{"tissueSiteDetailId": "Liver"},
                                                      {"tissueSiteDetailId": "Whole_Blood"}]})
        if path == "reference/gene":
            assert params["gencodeVersion"] == "v39"
            if params["geneId"].upper() == "SORT1":
                return httpx.Response(200, json={"data": [{"gencodeId": "ENSG00000134243.12", "geneSymbol": "SORT1",
                                                           "geneSymbolUpper": "SORT1", "chromosome": "chr1"}]})
            return httpx.Response(200, json={"data": []})
        if path == "expression/medianGeneExpression":
            return httpx.Response(200, json={"data": [
                {"tissueSiteDetailId": "Liver", "median": 2.4, "unit": "TPM"},
                {"tissueSiteDetailId": "Whole_Blood", "median": 0.5, "unit": "TPM"}],
                "paging_info": {"numberOfPages": 1, "totalNumberOfItems": 2}})
        if path == "association/singleTissueEqtl":
            page = int(params["page"])
            rows = [{"variantId": f"chr1_{i}_G_T_b38", "snpId": f"rs{i}", "tissueSiteDetailId": "Liver",
                     "pValue": 10.0 ** -i, "nes": 1.0} for i in ((5, 9) if page == 0 else (7,))]
            return httpx.Response(200, json={"data": rows, "paging_info": {"numberOfPages": 2,
                                                                           "totalNumberOfItems": 3}})
        if path == "dataset/variant":
            if params.get("snpId") == "rs12740374" or params.get("variantId") == "chr1_109274968_G_T_b38":
                return httpx.Response(200, json={"data": [{"variantId": "chr1_109274968_G_T_b38",
                                                           "snpId": "rs12740374", "maf01": True}]})
            return httpx.Response(200, json={"data": []})
        raise AssertionError(f"unexpected {path}")
    return handler


async def test_gtex_gene_and_variant_with_dataset_release(tmp_path):
    state = {}
    annotator, _calls, _clock = make(gtex_handler(state), tmp_path)
    result = await annotator.gtex(["SORT1", "rs12740374", "1:109274968:G:T", "NOPE1", "1:5:G:T",
                                   "NM_000001.1:c.1A>G"], tissues=["liver"])
    assert result["source"]["release_or_version"] == "GTEx Analysis v10 (gtex_v10, GENCODE v39, dbSNP 155)"
    gene, rsid, vcf, nope, unknown, hgvs = result["results"]
    assert gene["gene"]["gencodeId"] == "ENSG00000134243.12"
    assert gene["expression_tpm_median"] == [{"tissue": "Liver", "median": 2.4}]
    assert gene["eqtl"]["n_total"] == 3 and [r["snpId"] for r in gene["eqtl"]["top"]] == ["rs9", "rs7", "rs5"]
    assert rsid["variants"][0]["variantId"] == "chr1_109274968_G_T_b38" and rsid["eqtl"]["n_total"] == 3
    assert vcf["variants"] == rsid["variants"]
    assert nope["found"] is False and unknown["found"] is False and not hgvs["ok"]
    eqtl_calls = [p for p, q in state["paths"] if p == "association/singleTissueEqtl"]
    assert len(eqtl_calls) == 6  # two pages each for the gene and both spellings of the variant
    assert all(q.get("tissueSiteDetailId") == "Liver" for p, q in state["paths"] if p == "association/singleTissueEqtl")
    with pytest.raises(ValueError, match="Kidney"):
        await annotator.gtex(["SORT1"], tissues=["Kidney"])
    with pytest.raises(ValueError, match="dataset"):
        await annotator.gtex(["SORT1"], dataset="v10")


async def test_gtex_reads_rs1_as_a_gene_not_an_rsid(tmp_path):
    state = {}
    annotator, _calls, _clock = make(gtex_handler(state), tmp_path)
    await annotator.gtex(["RS1"], eqtl=False)
    asked = [p for p, _q in state["paths"]]
    assert "reference/gene" in asked and "dataset/variant" not in asked


# ----- AlphaGenome -----
OutputType = enum.Enum("OutputType", list(reg.AG_OUTPUTS))
Organism = enum.Enum("Organism", ["HOMO_SAPIENS", "MUS_MUSCULUS"])


class FakeModel:
    def __init__(self, log, fail=None):
        self.log, self.fail = log, fail

    def _track(self, interval, shift):
        values = [[1.0, 2.0] for _ in range(8)]
        values[3] = [1.0 + shift, 2.0 - shift / 2]
        return SimpleNamespace(values=values, resolution=1, interval=interval,
                               metadata=[{"name": "liver RNA", "ontology_curie": "UBERON:0002107", "strand": "+"},
                                         {"name": "blood RNA", "ontology_curie": "UBERON:0000178", "strand": "-"}])

    def predict_variant(self, interval, variant, organism, requested_outputs, ontology_terms):
        self.log.append(("variant", interval, variant, organism, requested_outputs, ontology_terms))
        if self.fail:
            raise RuntimeError(self.fail)
        ref = {ot: self._track(interval, 0.0) for ot in requested_outputs}
        alt = {ot: self._track(interval, 4.0) for ot in requested_outputs}
        return SimpleNamespace(reference=SimpleNamespace(get=ref.get), alternate=SimpleNamespace(get=alt.get))

    def predict_interval(self, interval, organism, requested_outputs, ontology_terms):
        self.log.append(("interval", interval, organism, requested_outputs, ontology_terms))
        tracks = {ot: self._track(interval, 5.0) for ot in requested_outputs}
        return SimpleNamespace(get=tracks.get)


def fake_backend(log, fail=None):
    keys = []

    def create(key):
        keys.append(key)
        return FakeModel(log, fail)
    dna_client = SimpleNamespace(create=create, OutputType=OutputType, Organism=Organism)
    genome = SimpleNamespace(Interval=lambda **kw: SimpleNamespace(**kw), Variant=lambda **kw: SimpleNamespace(**kw))
    return reg.AlphaGenomeBackend(FAKE_KEY, dna_client=dna_client, genome=genome, version="0.9.0"), keys


async def test_alphagenome_variant_and_interval_summaries_with_release(tmp_path):
    log = []
    backend, keys = fake_backend(log)
    annotator, _calls, clock = make(lambda r: pytest.fail("no HTTP"), tmp_path, alphagenome=backend)
    result = await annotator.alphagenome(["chr22:36201698:A:C", "chr22:100-299", "rs123", "chr22:1-2000000"],
                                         outputs=["RNA_SEQ", "DNASE"], ontology_terms=["UBERON:0002107"],
                                         sequence_length="16KB")
    assert keys == [FAKE_KEY]
    assert result["source"]["release_or_version"] == "AlphaGenome API (alphagenome client 0.9.0, default model ALL_FOLDS)"
    variant, interval, rsid, too_long = result["results"]
    kind, sent, sent_variant, organism, requested, terms = log[0]
    assert kind == "variant" and sent.chromosome == "chr22" and sent.end - sent.start == 2 ** 14
    assert sent.start == 36201697 - 2 ** 13 and sent_variant.position == 36201698
    assert sent_variant.reference_bases == "A" and organism is Organism.HOMO_SAPIENS
    assert requested == [OutputType.RNA_SEQ, OutputType.DNASE] and terms == ["UBERON:0002107"]
    top = variant["RNA_SEQ"]["top"][0]
    assert top["name"] == "liver RNA" and top["max_abs_change"] == 4.0
    assert top["max_change_position"] == sent.start + 3 + 1 and top["alt_minus_ref_sum"] == 4.0
    assert variant["RNA_SEQ"]["top"][1]["max_abs_change"] == 2.0 and variant["interval"].startswith("chr22:")
    assert interval["DNASE"]["top"][0]["max"] == 6.0 and log[1][0] == "interval"
    assert not rsid["ok"] and "rsID" in rsid["error"] and not too_long["ok"]
    full = (tmp_path / "work" / result["full_result"]).read_text(encoding="utf-8")
    assert FAKE_KEY not in full and FAKE_KEY not in json.dumps(_log(tmp_path))
    again, _calls, _clock = make(lambda r: pytest.fail("no HTTP"), tmp_path, alphagenome=backend)
    cached = await again.alphagenome(["chr22:36201698:A:C"], outputs=["RNA_SEQ", "DNASE"],
                                     ontology_terms=["UBERON:0002107"], sequence_length="16KB")
    assert cached["n_cached"] == 1 and len(log) == 2


async def test_alphagenome_errors_never_carry_the_key(tmp_path):
    backend, _keys = fake_backend([], fail=f"PERMISSION_DENIED: API key {FAKE_KEY} not valid")
    annotator, _calls, _clock = make(lambda r: pytest.fail("no HTTP"), tmp_path, alphagenome=backend)
    with pytest.raises(annot.LookupFailed) as failure:
        await annotator.alphagenome(["chr1:1000:A:G"])
    assert FAKE_KEY not in str(failure.value) and "***" in str(failure.value)
    with pytest.raises(ValueError, match="outputs"):
        await annotator.alphagenome(["chr1:1000:A:G"], outputs=["CONTACT_MAPS"])
    no_key, _calls, _clock = make(lambda r: pytest.fail("no HTTP"), tmp_path)
    with pytest.raises(annot.LookupFailed, match="키"):
        await no_key.alphagenome(["chr1:1000:A:G"])


def test_track_stats_use_numpy_arrays_when_the_client_returns_them():
    np = pytest.importorskip("numpy")
    meta = [{"name": "a"}, {"name": "b"}]
    ref = SimpleNamespace(values=np.zeros((4, 2)), resolution=128, interval=SimpleNamespace(start=1000), metadata=meta)
    alt = SimpleNamespace(values=np.array([[0, 0], [0, -3.0], [1.0, 0], [0, 0]]), resolution=128,
                          interval=SimpleNamespace(start=1000), metadata=meta)
    stats = reg.variant_track_stats(ref, alt)
    assert stats[0]["name"] == "b" and stats[0]["max_abs_change"] == 3.0 and stats[0]["max_change_position"] == 1129


# ----- MCP server and key handling -----
FAKE_PACKAGE = {
    "alphagenome/__init__.py": "",
    "alphagenome/data/__init__.py": "",
    "alphagenome/data/genome.py": (
        "class Interval:\n"
        "    def __init__(self, chromosome, start, end):\n"
        "        self.chromosome, self.start, self.end = chromosome, start, end\n"
        "class Variant:\n"
        "    def __init__(self, chromosome, position, reference_bases, alternate_bases):\n"
        "        self.chromosome, self.position = chromosome, position\n"),
    "alphagenome/models/__init__.py": "",
    "alphagenome/models/dna_client.py": (
        "import enum, hashlib, os, types\n"
        f"OutputType = enum.Enum('OutputType', {list(reg.AG_OUTPUTS)!r})\n"
        "Organism = enum.Enum('Organism', ['HOMO_SAPIENS', 'MUS_MUSCULUS'])\n"
        "class _Model:\n"
        "    def predict_variant(self, interval, variant, organism, requested_outputs, ontology_terms):\n"
        "        def track(shift):\n"
        "            return types.SimpleNamespace(values=[[0.0], [shift], [0.0]], resolution=1, interval=interval,\n"
        "                                         metadata=[{'name': 'liver'}])\n"
        "        ref = {o: track(0.0) for o in requested_outputs}\n"
        "        alt = {o: track(2.5) for o in requested_outputs}\n"
        "        return types.SimpleNamespace(reference=types.SimpleNamespace(get=ref.get),\n"
        "                                     alternate=types.SimpleNamespace(get=alt.get))\n"
        "def create(api_key):\n"
        "    if hashlib.sha256(api_key.encode()).hexdigest() != os.environ['FAKE_AG_KEY_SHA256']:\n"
        "        raise PermissionError('wrong key')\n"
        "    return _Model()\n"),
    "alphagenome-0.0.1.dist-info/METADATA": "Metadata-Version: 2.1\nName: alphagenome\nVersion: 0.0.1\n",
}


def _server_env(tmp_path, key_file, with_package):
    config = tmp_path / "labhq.yaml"
    config.write_text(yaml.safe_dump({"runner": {"state_dir": str(tmp_path / "state")},
                                      "annot": {"alphagenome_key_file": str(key_file)}}), encoding="utf-8")
    paths = [str(REPO)]
    if with_package:
        package = tmp_path / "fakepkg"
        for name, text in FAKE_PACKAGE.items():
            (package / name).parent.mkdir(parents=True, exist_ok=True)
            (package / name).write_text(text, encoding="utf-8")
        paths.insert(0, str(package))
    return {"PYTHONPATH": os.pathsep.join(paths), "LABHQ_WORKDIR": str(tmp_path / "work"),
            "LABHQ_CONFIG": str(config), "FAKE_AG_KEY_SHA256": hashlib.sha256(FAKE_KEY.encode()).hexdigest()}


BASE_TOOLS = {"vep", "gnomad", "clinvar", "chipatlas", "chipatlas_targets", "encode", "gtex"}


@pytest.mark.parametrize("key,package", [(False, True), (True, False)])
async def test_without_a_key_or_client_alphagenome_is_not_registered(tmp_path, key, package):
    if not package and reg.alphagenome_client_available():
        pytest.skip("the real alphagenome client is installed here")
    key_file = tmp_path / "secrets" / "ag"
    if key:
        annot_keys.write_key(key_file, FAKE_KEY)
    env = _server_env(tmp_path, key_file, package)
    tools = await list_tools(McpServerSpec(name="annot", command=sys.executable,
                                           args=["-m", "labhq.tools.annot_mcp"], env=env))
    assert set(tools) == BASE_TOOLS


async def test_with_a_key_the_server_offers_alphagenome_and_keeps_the_key_out_of_everything(tmp_path):
    key_file = tmp_path / "secrets" / "ag"
    annot_keys.write_key(key_file, FAKE_KEY)
    (tmp_path / "work").mkdir()
    env = _server_env(tmp_path, key_file, True)
    spec = McpServerSpec(name="annot", command=sys.executable, args=["-m", "labhq.tools.annot_mcp"], env=env)
    assert FAKE_KEY not in json.dumps(spec.model_dump(mode="json"))  # neither the command line nor its env
    assert set(await list_tools(spec)) == BASE_TOOLS | {"alphagenome"}
    params = StdioServerParameters(command=sys.executable, args=spec.args, env={**os.environ, **env})
    async with stdio_client(params) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            answer = await session.call_tool("alphagenome", {"variants_or_intervals": ["chr1:1000:A:G"],
                                                             "sequence_length": "16KB"})
            assert not answer.is_error, answer.content[0].text
            text = answer.content[0].text
    payload = json.loads(text)
    assert payload["results"][0]["RNA_SEQ"]["top"][0]["max_abs_change"] == 2.5
    assert payload["source"]["release_or_version"].startswith("AlphaGenome API (alphagenome client 0.0.1")
    written = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "work").rglob("*") if p.is_file())
    assert FAKE_KEY not in text and FAKE_KEY not in written and "alphagenome" in written


def test_runner_passes_no_key_to_the_annot_server(tmp_path):
    from labhq.runner.daemon import Runner

    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.annot.alphagenome_key_file = str(tmp_path / "secrets" / "ag")
    annot_keys.write_key(Path(settings.annot.alphagenome_key_file), FAKE_KEY)
    runner = Runner(settings)
    try:
        agent = AgentSpec(id="a", name="A", role="r", engine=Engine.codex, builtin_mcp=["annot"])
        spec = next(s for s in runner._mcp_servers(agent, {"LABHQ_WORKDIR": str(tmp_path)}) if s.name == "labhq_annot")
        assert FAKE_KEY not in json.dumps(spec.model_dump(mode="json"))
    finally:
        runner.store.close()


def _owner_only(path: Path) -> None:
    if os.name == "nt":
        account, _sid = annot_keys.current_account()
        entries = annot_keys.acl_entries(path)
        assert entries and all(name.lower() == account.lower() and "(I)" not in rights for name, rights in entries)
    else:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_write_key_leaves_an_owner_only_file(tmp_path):
    path = tmp_path / "secrets" / "alphagenome_api_key"
    annot_keys.write_key(path, FAKE_KEY)
    assert path.read_text(encoding="utf-8").strip() == FAKE_KEY
    _owner_only(path)
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temporary file left behind


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for key in ("HOME", "USERPROFILE", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(home))
    for key in ("LABHQ_CONFIG", "BIOINFO_AGENT_DIR", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LABHQ_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(wizard.shutil, "which", lambda *a, **kw: None)
    monkeypatch.setattr(doctor, "_probe", lambda *a: (_ for _ in ()).throw(AssertionError("unexpected CLI")))
    monkeypatch.setattr(wizard, "_hpc_query", lambda argv: None)
    monkeypatch.setattr("builtins.input", lambda *_: "")
    monkeypatch.chdir(tmp_path)
    return home


def test_init_asks_once_and_stores_the_key_only_in_the_owner_only_file(home, tmp_path, monkeypatch, capsys):
    prompts = []
    monkeypatch.setattr(wizard, "_interactive", lambda: True)
    monkeypatch.setattr(wizard.getpass, "getpass", lambda prompt: prompts.append(prompt) or f"  {FAKE_KEY}  ")
    result = wizard.run()
    assert len(prompts) == 1 and "AlphaGenome API 키가 있으면 붙여 넣으세요, 없으면 Enter" in prompts[0]
    key_file = home / ".labhq" / "secrets" / "alphagenome_api_key"
    assert key_file.read_text(encoding="utf-8").strip() == FAKE_KEY
    _owner_only(key_file)
    out = capsys.readouterr()
    config = (tmp_path / "config" / "labhq.yaml").read_text(encoding="utf-8")
    assert FAKE_KEY not in out.out + out.err + config + json.dumps(result)
    assert str(key_file) not in out.out
    row = next(r for r in result["checks"] if r["name"] == "AlphaGenome")
    assert row["status"] in ("ok", "warn") and row["detail"].startswith("키 있음")
    # A key is stored: running init again does not ask.
    wizard.run()
    assert len(prompts) == 1


def test_init_skip_leaves_no_file_and_asks_again_next_time(home, tmp_path, monkeypatch, capsys):
    prompts = []
    monkeypatch.setattr(wizard, "_interactive", lambda: True)
    monkeypatch.setattr(wizard.getpass, "getpass", lambda prompt: prompts.append(prompt) or "")
    result = wizard.run()
    assert len(prompts) == 1 and not (home / ".labhq" / "secrets").exists()
    assert "건너뜀" in capsys.readouterr().out
    row = next(r for r in result["checks"] if r["name"] == "AlphaGenome")
    assert (row["status"], row["detail"]) == ("skip", "키 없음 (도구 미등록)")
    wizard.run()
    assert len(prompts) == 2


def test_init_never_asks_without_a_terminal_or_with_yes(home, monkeypatch):
    monkeypatch.setattr(wizard.getpass, "getpass", lambda prompt: pytest.fail("asked"))
    wizard.run()  # pytest's stdin is not a terminal
    monkeypatch.setattr(wizard, "_interactive", lambda: True)
    wizard.run(yes=True)
    wizard.run(dry_run=True)


def test_doctor_shows_only_whether_a_key_exists(tmp_path, monkeypatch):
    settings = Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state"),
                                                   "workspace_root": str(tmp_path / "runs")},
                                        "gateway": {"state_dir": str(tmp_path / "gw")},
                                        "annot": {"alphagenome_key_file": str(tmp_path / "s" / "ag")}})
    monkeypatch.setattr(doctor, "_probe", lambda *a: (1, ""))
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    assert doctor._alphagenome_row(settings)["detail"] == "키 없음 (도구 미등록)"
    annot_keys.write_key(tmp_path / "s" / "ag", FAKE_KEY)
    monkeypatch.setattr(reg, "alphagenome_client_available", lambda: True)
    row = doctor._alphagenome_row(settings)
    assert (row["status"], row["detail"]) == ("ok", "키 있음")
    rendered = doctor.render(doctor.collect(settings))
    assert FAKE_KEY not in rendered and str(tmp_path / "s") not in rendered and "AlphaGenome" in rendered


def test_the_key_file_is_a_private_path_for_staff(tmp_path):
    home = tmp_path / "home"
    key = home / ".labhq" / "secrets" / "alphagenome_api_key"
    settings = Settings.model_validate({"annot": {"alphagenome_key_file": "~/.labhq/secrets/alphagenome_api_key"},
                                        "gateway": {"state_dir": str(tmp_path / "gw")}})
    assert not any("alphagenome" in label for _path, label in REAL_LABHQ_ENTRIES(settings, str(home)))
    annot_keys.write_key(key, FAKE_KEY)
    entries = REAL_LABHQ_ENTRIES(settings, str(home))
    assert (str(key), "~/.labhq/secrets/alphagenome_api_key") in entries
