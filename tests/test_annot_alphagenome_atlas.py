"""Atlas mocks need no SDK, key files, service calls or model inference."""

import asyncio
import importlib
import json
import threading
import uuid
from types import SimpleNamespace as NS

import httpx
import pytest

from labhq.tools import annot_alphagenome_atlas as atlas
from labhq.tools import annot_regulatory as reg
from labhq.tools.annot import LookupFailed


@pytest.mark.parametrize("source", ["parent", "engine", "task", "missing", "empty"])
def test_codex_never_forwards_atlas_key(tmp_path, monkeypatch, source):
    from labhq.adapters.base import RunContext
    from labhq.adapters.codex import CodexAdapter
    from labhq.models import AgentSpec, Engine, McpServerSpec, Task
    from labhq.settings import Settings

    secret = uuid.uuid4().hex
    monkeypatch.delenv(atlas.KEY_ENV, raising=False)
    settings = Settings()
    task_env = {"LABHQ_TASK_ID": "atlas-mock"}
    if source in ("parent", "empty"):
        monkeypatch.setenv(atlas.KEY_ENV, secret if source == "parent" else "")
    elif source == "engine":
        settings.engines.codex.env[atlas.KEY_ENV] = secret
    elif source == "task":
        task_env[atlas.KEY_ENV] = secret
    servers = [
        McpServerSpec(name="labhq_annot", command="python",
                      args=["-m", "labhq.tools.annot_mcp"],
                      env={"LABHQ_TASK_ID": "atlas-mock"}, auto_approve=True),
        McpServerSpec(name="other", command="python", env={"UNCHANGED": "literal"}),
    ]
    before = [s.model_dump() for s in servers]
    ctx = RunContext(task=Task(agent_id="worker", prompt="go"),
                     agent=AgentSpec(id="worker", name="Worker", role="test", engine=Engine.codex),
                     workdir=tmp_path, settings=settings, mcp_servers=servers,
                     env=task_env, emit=None, prompt="go")
    adapter = CodexAdapter(settings)
    adapter.prepare(ctx)
    argv = adapter.build_command(ctx)
    forwarded = next(v for v in argv if v.startswith("mcp_servers.labhq_annot.env_vars="))
    names = json.loads(forwarded.split("=", 1)[1])
    assert "LABHQ_TASK_ID" in names
    assert atlas.KEY_ENV not in names
    assert atlas.KEY_ENV not in adapter.staff_env(ctx)
    assert secret not in json.dumps(adapter.staff_env(ctx))
    assert all(atlas.KEY_ENV not in v for v in argv if v.startswith("mcp_servers.other."))
    assert 'mcp_servers.other.env={UNCHANGED = "literal"}' in argv
    assert [s.model_dump() for s in servers] == before
    assert secret not in json.dumps(argv)
    assert secret not in json.dumps([s.model_dump() for s in servers])
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8")


class Frame:
    def __init__(self, rows):
        self.rows = rows
        self.iloc = self

    def __getitem__(self, index):
        return NS(to_dict=lambda: self.rows[index])


class Matrix:
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, index):
        i, j = index
        return self.rows[i][j]


def scores(raw=None, quantiles=None, gene="GENE1"):
    raw = [[-2.0, 4.0]] if raw is None else raw
    quantiles = [[-0.99, 0.7]] if quantiles is None else quantiles
    return NS(n_obs=len(raw), n_vars=len(raw[0]), X=Matrix(raw),
              layers={"quantiles": Matrix(quantiles)},
              obs=Frame([{"gene_id": "ENSG00000000001", "gene_name": gene}] * len(raw)),
              var=Frame([{"biosample_name": "liver", "ontology_curie": "UBERON:0002107"},
                         {"biosample_name": "brain", "ontology_curie": "UBERON:0000955"}]))


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.delenv(atlas.KEY_ENV, raising=False)
    clients = []

    def create(data=None, error=None):
        calls = []
        secret = uuid.uuid4().hex

        def metadata():
            calls.append(("metadata",))
            return {"RNA_SEQ": NS(is_signed=True), "AVI": NS(is_signed=False)}

        def query(variant, **kwargs):
            calls.append(("variant", variant, kwargs))
            if error:
                raise error(secret)
            return {"RNA_SEQ": scores()} if data is None else data

        client = NS(scorer_metadata=metadata, query_variant=query)

        def connect(key, *, timeout):
            assert key == secret and timeout == reg.AG_CONNECT_TIMEOUT_S
            calls.append(("connect",))
            return client

        module = NS(create=connect, AtlasClient=NS(query_variant=query))
        backend = atlas.AtlasBackend(secret, atlas=module, genome=NS(Variant=lambda **kw: NS(**kw)), version="0.10.0")
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pytest.fail("unexpected HTTP")))
        clients.append(http)
        a = reg.RegulatoryAnnotator(http, tmp_path / "work", tmp_path / "cache", atlas=backend)
        a.workdir.mkdir(exist_ok=True)
        async def no_sleep(_):
            pass
        a.http.sleep = no_sleep
        return a, backend, calls, secret

    yield create
    for client in clients:
        asyncio.run(client.aclose())


async def test_success_filters_summary_records_and_cache(factory, tmp_path):
    a, backend, calls, secret = factory()
    kwargs = dict(ontology_terms=["UBERON:0002107"], gene_ids=["ENSG00000000001"])
    result = await a.alphagenome_atlas(["chr1:100:A>G", "1:100:A:G"], **kwargs)
    assert result["n_ok"] == 2
    hit = result["results"][0]
    assert hit["n_rows"] == 1 and hit["top"][0]["raw_score"] == -2
    assert "감소" in hit["summary_ko"] and "liver" in hit["summary_ko"]
    assert result["source"]["kind"] == "precomputed_atlas"
    assert result["source"]["dataset_release"] == "not exposed by Atlas SDK"
    assert "Cheng" in result["source"]["citation"] and "비상업" in result["note"]
    variants = [c for c in calls if c[0] == "variant"]
    assert len(variants) == 1
    assert variants[0][1].position == 100
    assert variants[0][2] == dict(requested_scorers=["RNA_SEQ"], **kwargs)
    second = await a.alphagenome_atlas(["chr1:100:A:G"], top=1, **kwargs)
    assert second["n_cached"] == 1 and len(calls) == 3
    assert (a.workdir / result["full_result"]).is_file()
    ledger = (a.workdir / result["query_log"]).read_text(encoding="utf-8")
    assert '"n_ok": 2' in ledger and "chr1" not in ledger
    written = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json*"))
    assert secret not in written
    backend.version = "0.10.1"
    await a.alphagenome_atlas(["chr1:100:A:G"], **kwargs)
    await a.alphagenome_atlas(["chr1:100:A:G"], ontology_terms=["UBERON:0000955"])
    assert len([c for c in calls if c[0] == "variant"]) == 3


async def test_env_only_missing_key_and_unsupported_sdk(factory, monkeypatch):
    from labhq.tools import annot_keys
    monkeypatch.setattr(annot_keys, "read_key", lambda *a: pytest.fail("key file read"))
    a, _, calls, _ = factory()
    a.atlas_backend = None
    with pytest.raises(LookupFailed, match="키 없음"):
        await a.alphagenome_atlas(["chr1:100:A:G"])
    assert not calls
    monkeypatch.setenv(atlas.KEY_ENV, uuid.uuid4().hex)
    backend = atlas.AtlasBackend.from_env()
    backend._atlas = NS(create=None)
    a.atlas_backend = backend
    with pytest.raises(LookupFailed, match="SDK 미지원"):
        await a.alphagenome_atlas(["chr1:100:A:G"])
    assert not calls


@pytest.mark.parametrize("kwargs", [{"top": 101}, {"top": True}, {"assembly": "GRCh37"},
    {"requested_scorers": []}, {"requested_scorers": ["RNA_SEQ"] * 11},
    {"requested_scorers": ['RNA_SEQ" OR *']}, {"ontology_terms": ["bad"]},
    {"ontology_terms": ["UBERON:1"] * 21}, {"gene_ids": ["BRCA1"]}])
async def test_options_rejected_before_transport(factory, kwargs):
    a, _, calls, _ = factory()
    with pytest.raises(ValueError):
        await a.alphagenome_atlas(["chr1:100:A:G"], **kwargs)
    assert not calls


@pytest.mark.parametrize("variants", [[], ["chr1:1:A:G"] * 11, {}, None])
async def test_batch_bound_before_transport(factory, variants):
    a, _, calls, _ = factory()
    with pytest.raises(ValueError):
        await a.alphagenome_atlas(variants)
    assert not calls


async def test_invalid_inputs_do_not_echo_secrets_or_call_inference(factory, tmp_path):
    a, _, calls, secret = factory()
    result = await a.alphagenome_atlas([secret, "chr1:2:AA:G", "chr1:2:A:A", "chr1:100:A:G"])
    assert result["n_errors"] == 3 and result["n_ok"] == 1
    assert len([c for c in calls if c[0] == "variant"]) == 1
    assert secret not in json.dumps(result)
    assert secret not in "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json*"))


async def test_empty_is_not_no_effect_and_unsigned_has_no_direction(factory):
    a, *_ = factory(data={})
    result = await a.alphagenome_atlas(["chr1:100:A:G"])
    assert result["n_ok"] == 1
    assert not result["results"][0]["has_scores"]
    assert "효과 없음" in result["results"][0]["summary_ko"]
    a, *_ = factory(data={"AVI": scores()})
    result = await a.alphagenome_atlas(["chr1:101:A:G"], requested_scorers=["AVI"])
    assert "방향 미제공" in result["results"][0]["summary_ko"]


@pytest.mark.parametrize("error,message,expected", [(PermissionError, "권한", 1),
    (TimeoutError, "시간 초과", 1), (NotImplementedError, "지원", 1),
    (RuntimeError, "조회 실패", 1), (ValueError, "미수록", 2), (IndexError, "미수록", 2)])
async def test_errors_redacted_and_bounded(factory, error, message, expected, tmp_path, caplog, capsys):
    a, _, calls, secret = factory(error=error)
    result = await a.alphagenome_atlas(["chr1:100:A:G", "chr1:101:A:G"])
    assert result["n_errors"] == 2 and message in result["results"][0]["error"]
    assert len([c for c in calls if c[0] == "variant"]) == expected
    captured = capsys.readouterr()
    assert secret not in json.dumps(result) + caplog.text + captured.out + captured.err
    assert secret not in "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json*"))


async def test_unknown_scorer_and_response_bound(factory):
    a, _, calls, _ = factory()
    result = await a.alphagenome_atlas(["chr1:100:A:G"], requested_scorers=["NO_SUCH_SCORER"])
    assert result["n_errors"] == 1 and not any(c[0] == "variant" for c in calls)
    a, *_ = factory(data={"RNA_SEQ": NS(n_obs=1001, n_vars=1000)})
    result = await a.alphagenome_atlas(["chr1:101:A:G"])
    assert "상한" in result["results"][0]["error"]


async def test_truncation_nonfinite_and_row_redaction(factory, tmp_path):
    a, backend, calls, secret = factory()
    data = scores([[-i, float("nan")] for i in range(600)],
                  [[i / 600, float("inf")] for i in range(600)], gene=secret)
    backend._atlas.create = lambda *a, **kw: NS(
        scorer_metadata=lambda: {"RNA_SEQ": NS(is_signed=True)},
        query_variant=lambda *a, **kw: {"RNA_SEQ": data})
    result = await a.alphagenome_atlas(["chr1:100:A:G"], top=100)
    hit = result["results"][0]
    assert hit["n_rows"] == 1200 and hit["n_saved"] == 1000 and hit["truncated"]
    assert len(hit["top"]) == 100 and hit["top"][0]["raw_score"] == -599
    full = json.loads((a.workdir / result["full_result"]).read_text(encoding="utf-8"))
    rows = full["results"][0]["answer"]["rows"]
    assert rows[-1]["raw_score"] is None and rows[-1]["quantile_score"] is None
    assert secret not in json.dumps(full) + json.dumps(result)


async def test_track_measurement_and_gene_junction_identity_survive(factory):
    data = scores()
    data.obs = Frame([{"gene_id": "ENSG00000000001", "strand": "-",
                       "junction_Start": 100, "junction_End": 200}])
    data.var = Frame([{"name": "delta_psi5", "strand": "+"},
                      {"name": "delta_psi3", "strand": "+"}])
    a, *_ = factory(data={"RNA_SEQ": data})
    result = await a.alphagenome_atlas(["chr1:100:A:G"])
    rows = result["results"][0]["top"]
    assert {row["track_metadata"]["name"] for row in rows} == {"delta_psi5", "delta_psi3"}
    assert {row["track_index"] for row in rows} == {0, 1}
    for row in rows:
        assert row["gene_metadata"]["strand"] == "-"
        assert row["track_metadata"]["strand"] == "+"
        assert row["gene_metadata"]["junction_Start"] == 100
        assert row["gene_metadata"]["junction_End"] == 200


async def test_wait_timeout_stops_batch_without_late_records(factory, monkeypatch):
    a, backend, calls, _ = factory()
    release = threading.Event()
    entered = threading.Event()
    old_query = backend.query

    def blocked(*args):
        entered.set()
        release.wait(2)
        return old_query(*args)

    monkeypatch.setattr(backend, "query", blocked)
    monkeypatch.setattr(reg, "AG_TIMEOUT_S", 0.02)
    try:
        result = await a.alphagenome_atlas(["chr1:100:A:G", "chr1:101:A:G"])
        assert entered.is_set() and result["n_errors"] == 2
        before = sorted(a.workdir.rglob("*.json*"))
    finally:
        release.set()
    await asyncio.sleep(0.05)
    assert sorted(a.workdir.rglob("*.json*")) == before
    assert not calls  # A timed-out worker must not start a new RPC after being released.


async def test_cancel_during_metadata_never_starts_variant_rpc(factory):
    a, backend, calls, _ = factory()
    release, entered = threading.Event(), threading.Event()
    def metadata():
        entered.set()
        release.wait(2)
        return {"RNA_SEQ": NS(is_signed=True)}
    backend._client = NS(scorer_metadata=metadata, query_variant=lambda *a, **kw: calls.append("unexpected"))
    task = asyncio.create_task(a.alphagenome_atlas(["chr1:100:A:G"]))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    await asyncio.sleep(0.05)
    assert not calls and not list(a.workdir.rglob("*.json*"))


@pytest.mark.parametrize("parent,engine,present", [
    (None, "fake-engine-key", True),
    (None, None, False),
    ("fake-parent-key", "", False),
    ("fake-parent-key", "   ", False),
    ("", "fake-engine-key", True),
    ("fake-parent-key", None, True),
    (None, "${ATLAS_TEST_KEY}", True),
])
def test_doctor_atlas_uses_codex_environment(monkeypatch, parent, engine, present):
    from labhq import doctor
    from labhq.settings import Settings

    monkeypatch.delenv(atlas.KEY_ENV, raising=False)
    monkeypatch.setenv("ATLAS_TEST_KEY", "fake-expanded-key")
    if parent is not None:
        monkeypatch.setenv(atlas.KEY_ENV, parent)
    settings = Settings()
    if engine is not None:
        settings.engines.codex.env[atlas.KEY_ENV] = engine
    monkeypatch.setattr(atlas, "sdk_available", lambda: True)
    row = doctor._alphagenome_atlas_row(settings)
    assert row["status"] == ("ok" if present else "skip")
    for value in ("fake-engine-key", "fake-parent-key", "fake-expanded-key"):
        assert value not in json.dumps(row)


def test_doctor_reports_presence_without_reading_key_file(monkeypatch):
    from labhq import doctor
    monkeypatch.delenv(atlas.KEY_ENV, raising=False)
    assert doctor._alphagenome_atlas_row()["status"] == "skip"
    secret = uuid.uuid4().hex
    monkeypatch.setenv(atlas.KEY_ENV, secret)
    monkeypatch.setattr(atlas, "sdk_available", lambda: False)
    assert doctor._alphagenome_atlas_row()["status"] == "warn"
    monkeypatch.setattr(atlas, "sdk_available", lambda: True)
    row = doctor._alphagenome_atlas_row()
    assert "미검증" in row["detail"] and secret not in json.dumps(row)


async def test_mcp_registration_deadline_and_lookup_admission(monkeypatch, tmp_path):
    from labhq.tools import annot_keys
    monkeypatch.setattr(annot_keys, "read_key", lambda *a: "")
    monkeypatch.setenv("LABHQ_ATLAS_BROKER_ENABLED", "1")
    from labhq.tools import annot_mcp
    mcp = importlib.reload(annot_mcp)
    monkeypatch.setattr(mcp, "WORKDIR", tmp_path)
    monkeypatch.setattr(mcp, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(mcp, "LOOKUP_LIMITED", False)
    called = []

    monkeypatch.setattr(mcp, "BROKER_TOKEN", "test-broker")
    async def post(self, url, **kwargs):
        called.append((url, kwargs))
        return httpx.Response(200, json={"mock": True}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    assert json.loads(await mcp.alphagenome_atlas(["chr1:100:A:G"])) == {"mock": True}
    assert called[0][0].endswith("/annot/atlas")
    assert called[0][1]["json"]["variants"] == ["chr1:100:A:G"]
    monkeypatch.setattr(mcp, "LOOKUP_LIMITED", True)
    monkeypatch.setattr(mcp, "BROKER_TOKEN", "test-broker")
    with pytest.raises(Exception, match="상한"):
        await mcp.alphagenome_atlas(["chr1:100:A:G"])
    assert len(called) == 1


async def test_mcp_all_error_status_is_failed(monkeypatch, tmp_path):
    from labhq.tools import annot_mcp as mcp
    monkeypatch.setattr(mcp, "WORKDIR", tmp_path)
    monkeypatch.setattr(mcp, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(mcp, "LOOKUP_LIMITED", False)
    monkeypatch.setattr(mcp, "BROKER_TOKEN", "test-broker")
    async def post(self, url, **kwargs):
        return httpx.Response(502, json={"detail": "untrusted upstream text"},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(Exception, match="runner lookup failed"):
        await mcp.alphagenome_atlas(["chr1:100:A:G"])


async def test_installed_sdk_proto_roundtrip_without_network(factory):
    """Optional dependency compatibility, run locally with the actual released wheel."""
    sdk = pytest.importorskip("alphagenome.atlas.atlas")
    from alphagenome.data import genome
    from alphagenome.protos import atlas_service_pb2 as proto
    from alphagenome.protos.v1 import dna_model_pb2 as dna
    import struct

    calls = []

    class Stub:
        def ListVariantScoresMetadata(self, request, metadata):
            return proto.ListVariantScoresMetadataResponse(variant_scorer_metadata=[
                proto.VariantScorerMetadata(variant_scorer=proto.VariantScorerInfo(name="RNA_SEQ", is_signed=True))])

        def GetDenseVariantScores(self, request, metadata):
            calls.append(request)
            return proto.DenseVariantScores(variant=request.variant, scores=[proto.DenseVariantScore(
                variant_scorer=proto.VariantScorerInfo(name="RNA_SEQ", is_signed=True), shape=[1, 1],
                scores=struct.pack("f", -2), calibrated_scores=struct.pack("f", -0.99),
                metadata=[proto.Metadata(tracks=dna.TracksMetadata(metadata=[
                    dna.TrackMetadata(name="test track", strand=dna.STRAND_POSITIVE)]))])])

    a, backend, _, _ = factory()
    backend._atlas = sdk
    backend._genome = genome
    backend._client = sdk.AtlasClient(Stub())
    # Call through the real released SDK before the wrapper, so schema incompatibilities are visible here.
    backend.query(reg._alphagenome_query("chr1:100:A:G", 1), ["RNA_SEQ"], [], [])
    calls.clear()
    result = await a.alphagenome_atlas(["chr1:100:A:G"])
    assert result["n_ok"] == 1 and result["results"][0]["top"][0]["raw_score"] == -2
    assert result["results"][0]["top"][0]["track_metadata"]["name"] == "test track"
    assert len(calls) == 1 and calls[0].variant.position == 100
    assert 'scores.variant_scorer.name = "RNA_SEQ"' in calls[0].filter
