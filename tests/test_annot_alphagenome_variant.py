"""Variant scores use an SDK-shaped fake; no credential or network access."""

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

from labhq.tools import annot_regulatory as reg
from labhq.tools.annot import LookupFailed


class Scorer:
    def __init__(self, name, signed=True, width=501):
        self.name = name
        self.is_signed = signed
        self.width = width

    def __str__(self):
        return f"{self.name}(width={self.width})"


class Frame:
    def __init__(self, rows):
        self.columns = list(dict.fromkeys(k for row in rows for k in row))
        self.rows = rows

    def itertuples(self, index=False, name=None):
        return iter(tuple(row.get(k) for k in self.columns) for row in self.rows)


def row(scorer="RNA_SEQ", raw=-2.5, quantile=-0.999, **kw):
    return dict(output_type=scorer, variant_scorer=f"{scorer}(width=501)",
                biosample_name="liver", ontology_curie="UBERON:0002107", gene_name="GENE1",
                raw_score=raw, quantile_score=quantile, **kw)


def setup(tmp_path, rows=None, fail=False):
    calls = []
    secret = uuid.uuid4().hex  # generated sentinel, never an actual API credential
    scorers = {name: Scorer(name, name not in {"SPLICE_SITES", "CONTACT_MAPS"})
               for name in ("RNA_SEQ", "CAGE", "ATAC", "DNASE", "CHIP_TF", "CHIP_HISTONE",
                            "SPLICE_SITES", "SPLICE_SITE_USAGE", "SPLICE_JUNCTIONS", "CONTACT_MAPS")}
    scorers["RNA_SEQ_ACTIVE"] = Scorer("RNA_SEQ_ACTIVE", False)

    def score_variant(**kwargs):
        calls.append(kwargs)
        if fail:
            raise RuntimeError(secret)
        return [object()]

    def create(key, *, timeout, model_version):
        assert key == secret
        assert model_version == "ALL_FOLDS"
        assert timeout == reg.AG_CONNECT_TIMEOUT_S
        return SimpleNamespace(score_variant=score_variant)

    dna = SimpleNamespace(create=create, ModelVersion=SimpleNamespace(ALL_FOLDS="ALL_FOLDS"),
                          Organism=SimpleNamespace(HOMO_SAPIENS="human"))
    genome = SimpleNamespace(Interval=lambda **kw: SimpleNamespace(**kw),
                             Variant=lambda **kw: SimpleNamespace(**kw))
    backend = reg.AlphaGenomeBackend(secret, dna_client=dna, genome=genome, version="0.9.0")

    def tidy(scores):
        assert len(scores) == 1 and isinstance(scores[0], list)
        return Frame(rows if rows is not None else [row()])

    backend._variant_scorers = SimpleNamespace(RECOMMENDED_VARIANT_SCORERS=scorers, tidy_scores=tidy)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pytest.fail("no HTTP")))
    annotator = reg.RegulatoryAnnotator(client, tmp_path / "work", tmp_path / "cache", alphagenome=backend)
    async def no_sleep(_):
        pass
    annotator.http.sleep = no_sleep
    return annotator, backend, calls, secret


async def test_scores_tidy_summary_and_source(tmp_path):
    a, backend, calls, secret = setup(tmp_path, [row(), row("ATAC", 1.2, 0.98),
                                              row("SPLICE_SITES", 0.4, 0.9999)])
    result = await a.alphagenome_variant(["chr1:1000000:A>G"], top=2)
    hit = result["results"][0]
    assert result["n_ok"] == 1
    assert hit["n_rows"] == 3 and len(hit["top"]) == 2
    assert hit["top"][1]["raw_score"] == -2.5
    assert hit["top"][1]["quantile_score"] == -0.999
    assert "SPLICE_SITES" in hit["summary_ko"] and "방향 미제공" in hit["summary_ko"]
    assert "liver" in hit["summary_ko"] and "GENE1" in hit["summary_ko"]
    assert "큰 효과" in hit["summary_ko"]
    assert "임상 근거" in result["note"]
    assert result["source"]["sdk_version"] == "0.9.0"
    assert result["source"]["model_version"] == "ALL_FOLDS"
    assert result["source"]["api_version"] == "v1"
    assert "비상업" in result["source"]["usage"]
    assert len(calls) == 1 and calls[0]["variant"].position == 1000000
    assert calls[0]["interval"].end - calls[0]["interval"].start == 2**20
    assert all("ACTIVE" not in str(s) for s in calls[0]["variant_scorers"])
    assert secret not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("raw,quantile,word", [(-2, -0.999, "감소"), (2, 0.999, "증가"),
                                               (0, 0, "변화 없음"), (1, None, "판정 보류")])
async def test_signed_direction_and_missing_quantile(tmp_path, raw, quantile, word):
    a, *_ = setup(tmp_path, [row(raw=raw, quantile=quantile)])
    result = await a.alphagenome_variant(["chr1:1000000:A:G"])
    assert word in result["results"][0]["summary_ko"]


async def test_filter_and_cache_sdk_scorer_model_options(tmp_path):
    a, backend, calls, _ = setup(tmp_path)
    kwargs = dict(ontology_terms=["UBERON:0002107"], tissues=["liver"])
    first = await a.alphagenome_variant(["chr1:1000000:A>G", "chr1:1000000:A:G"], **kwargs)
    assert first["n_ok"] == 2 and len(calls) == 1
    second = await a.alphagenome_variant(["chr1:1000000:A:G"], top=1, **kwargs)
    assert second["n_cached"] == 1 and len(calls) == 1
    backend._version = "0.10.0"
    await a.alphagenome_variant(["chr1:1000000:A:G"], **kwargs)
    backend._variant_scorers.RECOMMENDED_VARIANT_SCORERS["ATAC"].width = 503
    await a.alphagenome_variant(["chr1:1000000:A:G"], **kwargs)
    assert len(calls) == 3
    empty = await a.alphagenome_variant(["chr1:1000000:A:G"], tissues=["brain"])
    assert empty["results"][0]["n_rows"] == 0
    assert "없음" in empty["results"][0]["summary_ko"]
    assert "ontology_terms" not in calls[0]  # score_variant has no server-side ontology filter


async def test_no_key_and_errors_never_expose_key(tmp_path, capsys, caplog):
    a, backend, calls, secret = setup(tmp_path, fail=True)
    a.alphagenome_backend = None
    with pytest.raises(LookupFailed, match="키 없음"):
        await a.alphagenome_variant(["chr1:1:A:G"])
    assert not calls
    a.alphagenome_backend = backend
    with pytest.raises(LookupFailed) as caught:
        await a.alphagenome_variant(["chr1:1000000:A:G"])
    assert secret not in str(caught.value)
    a, backend, calls, secret = setup(tmp_path, [row(gene_id="placeholder")])
    backend._variant_scorers.tidy_scores = lambda scores: Frame([row(gene_id=secret)])
    result = await a.alphagenome_variant(["chr1:1000000:A:G"])
    written = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json*"))
    captured = capsys.readouterr()
    assert secret not in written + json.dumps(result) + captured.out + captured.err + caplog.text


@pytest.mark.parametrize("kwargs", [{"sequence_length": "2MB"}, {"top": 101}, {"top": 0},
                                    {"ontology_terms": ["UBERON:1"] * 21}, {"assembly": "GRCh37"}])
async def test_input_bounds_before_sdk(tmp_path, kwargs):
    a, _, calls, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        await a.alphagenome_variant(["chr1:1000000:A:G"], **kwargs)
    assert not calls


async def test_variant_call_and_tidy_size_limits(tmp_path):
    a, _, calls, _ = setup(tmp_path, [row()] * 1005)
    result = await a.alphagenome_variant([f"chr1:{1000000+i}:A:G" for i in range(11)], top=100)
    assert len(calls) == 10 and result["n_errors"] == 1
    hit = result["results"][0]
    assert hit["n_rows"] == 1005 and len(hit["top"]) == 100 and hit["n_saved"] == 1000
    assert hit["truncated"]
    result = await a.alphagenome_variant(["chr1:1-100", "rs123", "chr1:1:" + "A"*2000 + ">G"])
    assert result["n_errors"] == 3


async def test_nonfinite_scores_and_missing_gene_are_not_invented(tmp_path):
    a, backend, _, _ = setup(tmp_path)
    backend._variant_scorers.tidy_scores = lambda scores: Frame([
        {**row(), "gene_name": float("nan"), "raw_score": float("inf"), "quantile_score": float("nan")},
    ])
    result = await a.alphagenome_variant(["chr1:1000000:A:G"])
    hit = result["results"][0]
    assert hit["top"][0]["raw_score"] is None and hit["top"][0]["gene_name"] is None
    assert "유전자 지정 없음" in hit["summary_ko"] and "판정 보류" in hit["summary_ko"]
    json.dumps(result, allow_nan=False)


async def test_timeout_stops_remaining_calls(tmp_path, monkeypatch):
    import asyncio
    a, _, calls, _ = setup(tmp_path)
    attempts = []
    async def timeout(*args):
        attempts.append(1)
        raise asyncio.TimeoutError
    monkeypatch.setattr(reg, "_alphagenome_call", timeout)
    with pytest.raises(LookupFailed):
        await a.alphagenome_variant(["chr1:1000000:A:G", "chr1:1000001:A:G"])
    assert len(attempts) == 1 and not calls


async def test_missing_scoring_sdk_gives_safe_guidance(tmp_path):
    a, backend, calls, secret = setup(tmp_path)
    class Missing:
        @property
        def RECOMMENDED_VARIANT_SCORERS(self):
            raise ImportError(secret)
    backend._variant_scorers = Missing()
    with pytest.raises(LookupFailed, match="SDK 설치") as caught:
        await a.alphagenome_variant(["chr1:1000000:A:G"])
    assert not calls and secret not in str(caught.value)


@pytest.mark.parametrize("length,width", list(reg.AG_LENGTHS.items()))
async def test_supported_windows_and_filter_normalization(tmp_path, length, width):
    a, _, calls, _ = setup(tmp_path)
    result = await a.alphagenome_variant(["chr1:1:A>G"], sequence_length=length, tissues=[" Liver "])
    assert result["n_ok"] == 1 and result["results"][0]["n_rows"] == 1
    interval = calls[0]["interval"]
    assert interval.start == 0 and interval.end == width


def test_doctor_without_optional_sdk(tmp_path, monkeypatch):
    from labhq import doctor
    from labhq.settings import Settings
    from labhq.tools import annot_keys
    monkeypatch.setattr(annot_keys, "has_key", lambda settings: True)
    monkeypatch.setattr(reg, "alphagenome_client_available", lambda: False)
    result = doctor._alphagenome_row(Settings())
    assert result["status"] == "warn" and "labhq[alphagenome]" in str(result)


async def test_the_window_is_centered_on_the_ref_span_and_a_one_base_ref_is_unchanged(tmp_path):
    # #621: a many-base REF (a deletion) is centered in its window; a one-base REF keeps the old start.
    a, _, calls, _ = setup(tmp_path)
    width = reg.AG_LENGTHS["16KB"]
    ref = "A" * 1000
    await a.alphagenome_variant([f"chr1:1000000:{ref}>A"], sequence_length="16KB")
    deletion = calls[-1]["interval"]
    ref_start0, ref_end = 999999, 999999 + 1000
    assert deletion.end - deletion.start == width
    assert (deletion.start + deletion.end) // 2 == (ref_start0 + ref_end) // 2  # the REF's middle is the window's middle
    assert deletion.start <= ref_start0 and ref_end <= deletion.end            # the whole REF is inside, evenly
    assert (ref_start0 - deletion.start) - (deletion.end - ref_end) in (0, 1)   # the same room on both sides
    await a.alphagenome_variant(["chr1:1000000:A>G"], sequence_length="16KB")
    snv = calls[-1]["interval"]
    assert snv.start == 999999 - width // 2 and snv.end == snv.start + width    # unchanged for a one-base REF
    await a.alphagenome_variant(["chr1:3:AC>A"], sequence_length="16KB")        # two bases near the chromosome start
    assert calls[-1]["interval"].start == 0                                    # still clamped at 0
