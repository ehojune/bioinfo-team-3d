"""Atlas mocks need no SDK, key files, service calls or model inference."""

import asyncio
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace as NS
import uuid

import httpx
import pytest
import uvicorn
import yaml

from labhq.adapters.base import RunContext
from labhq.adapters.codex import CodexAdapter
from labhq.models import AgentSpec, Engine, McpServerSpec, Task
from labhq.runner.annot_broker import AnnotBroker, ENABLED_ENV, refuse_credential_copy
from labhq.runner.approvals import Broker
from labhq.settings import Settings, write_staff_config
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


async def noop(*args):
    pass


def context(tmp_path, settings=None):
    settings = settings or Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    return RunContext(task=Task(id="task-atlas", agent_id="worker", request_id="request-atlas", prompt="go"),
        agent=AgentSpec(id="worker", name="Worker", role="test", engine=Engine.codex, builtin_mcp=["annot"]),
        workdir=tmp_path, settings=settings, mcp_servers=[], env={}, emit=noop, prompt="go")


@pytest.fixture
def service(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    broker = Broker(0, noop, noop, noop)
    token = broker.issue_task_token(ctx.task.id, ctx.agent.id, ctx.task.request_id, workdir=str(tmp_path))
    service = AnnotBroker(broker, ctx.settings)
    key = uuid.uuid4().hex
    service.register(ctx, key)
    seen = []

    def setup(backend):
        def create(received, **kwargs):
            seen.append(received)
            return NS(scorer_metadata=lambda: {"RNA_SEQ": NS(is_signed=True)},
                      query_variant=lambda *a, **k: {})
        backend._atlas = NS(create=create)
        backend._genome = NS(Variant=lambda **kwargs: kwargs)
        return "fixture-sdk"

    monkeypatch.setattr(atlas.AtlasBackend, "setup", setup)
    return ctx, broker, service, token, key, seen


def client(broker):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=broker.app), base_url="http://broker")


async def test_lookup_receives_key_and_records_without_disclosing_it(service):
    ctx, broker, service, token, key, seen = service
    async with client(broker) as c:
        response = await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                                json={"variants": ["chr1:100:A:G"]})
    assert response.status_code == 200 and response.json()["n_ok"] == 1
    assert seen == [key]
    assert key not in response.text + repr(service.grants[ctx.task.id])
    files = list(ctx.workdir.rglob("*.json*"))
    assert files and all(key not in p.read_text(encoding="utf-8") for p in files)
    assert '"status": "ok"' in (ctx.workdir / "outputs/external_lookup_status.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("body,status", [
    ({"task_id": "other"}, 403), ({"agent_id": "other"}, 403), ({"request_id": "other"}, 403),
    ({"url": "https://example.invalid"}, 400), ({"command": "anything"}, 400),
    ({"workdir": "elsewhere"}, 400), ({"method": "alphagenome_variant"}, 400),
])
async def test_owner_and_allowlist_cannot_be_overridden(service, body, status):
    _, broker, _, token, _, seen = service
    async with client(broker) as c:
        response = await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                                json={"variants": ["chr1:100:A:G"], **body})
    assert response.status_code == status and not seen


async def test_token_admission_and_finished_task_refuse_before_sdk(service):
    ctx, broker, service, token, _, seen = service
    async with client(broker) as c:
        assert (await c.post("/annot/atlas", json={})).status_code == 401
        broker.lookup_admission = object()
        assert (await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                            json={"variants": ["chr1:100:A:G"]})).status_code == 403
        broker.lookup_admission = None
        await service.finish(ctx.task.id)
        assert (await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                            json={"variants": ["chr1:100:A:G"]})).status_code == 403
        broker.revoke_task_token(token)
        assert (await c.post("/annot/atlas", headers={"X-Labhq-Token": token}, json={})).status_code == 401
    assert not seen


@pytest.mark.parametrize("payload,status", [(b"x" * 16385, 413), (b"[1]", 400), (b"{", 400)])
async def test_bad_payload_is_bounded(service, payload, status):
    _, broker, _, token, _, seen = service
    async with client(broker) as c:
        response = await c.post("/annot/atlas", headers={"X-Labhq-Token": token}, content=payload)
    assert response.status_code == status and not seen


async def test_finish_cancels_query_and_prevents_late_sdk_work(service, monkeypatch):
    ctx, broker, service, token, _, _ = service
    entered = asyncio.Event()
    backends = []

    async def blocked(annotator, **kwargs):
        backends.append(annotator.atlas_backend)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("labhq.runner.annot_broker.RegulatoryAnnotator.alphagenome_atlas", blocked)
    async with client(broker) as c:
        pending = asyncio.create_task(c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                                            json={"variants": ["chr1:100:A:G"]}))
        await asyncio.wait_for(entered.wait(), 5)
        busy = await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                            json={"variants": ["chr1:100:A:G"]})
        assert busy.status_code == 409
        await service.finish(ctx.task.id)
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert backends[0].cancelled.is_set() and not service.grants
    assert '"status": "cancelled"' in (ctx.workdir / "outputs/external_lookup_status.jsonl").read_text(encoding="utf-8")


async def test_all_errors_fail_status_without_raw_exception(service, monkeypatch):
    ctx, broker, _, token, key, _ = service
    async def failed(*a, **k):
        return {"n_ok": 0, "results": [{"error": key}]}
    monkeypatch.setattr("labhq.runner.annot_broker.RegulatoryAnnotator.alphagenome_atlas", failed)
    async with client(broker) as c:
        response = await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                                json={"variants": ["chr1:100:A:G"]})
    assert response.status_code == 502 and key not in response.text
    status = (ctx.workdir / "outputs/external_lookup_status.jsonl").read_text(encoding="utf-8")
    assert '"status": "failed"' in status and '"status": "ok"' not in status and key not in status


async def test_deadline_stops_backend_and_records_timeout(service, monkeypatch):
    from labhq.tools.external_wait import run_lookup
    ctx, broker, _, token, _, _ = service
    backends = []
    async def blocked(annotator, **kwargs):
        backends.append(annotator.atlas_backend)
        await asyncio.Event().wait()
    async def short_deadline(*args, **kwargs):
        return await run_lookup(*args, **kwargs, timeout_s=0.05)
    monkeypatch.setattr("labhq.runner.annot_broker.RegulatoryAnnotator.alphagenome_atlas", blocked)
    monkeypatch.setattr("labhq.runner.annot_broker.run_lookup", short_deadline)
    async with client(broker) as c:
        response = await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                                json={"variants": ["chr1:100:A:G"]})
    assert response.status_code == 502 and backends[0].cancelled.is_set()
    assert '"status": "timeout"' in (ctx.workdir / "outputs/external_lookup_status.jsonl").read_text(encoding="utf-8")


async def test_disconnected_mcp_cancels_backend(service, monkeypatch):
    from starlette.requests import Request
    _, broker, service, token, _, _ = service
    entered = asyncio.Event()
    backends = []
    async def blocked(annotator, **kwargs):
        backends.append(annotator.atlas_backend)
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr("labhq.runner.annot_broker.RegulatoryAnnotator.alphagenome_atlas", blocked)
    sent = False
    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b'{"variants":["chr1:100:A:G"]}', "more_body": False}
        await entered.wait()
        return {"type": "http.disconnect"}
    request = Request({"type": "http", "method": "POST", "path": "/annot/atlas", "headers": []}, receive)
    endpoint = next(route.endpoint for route in broker.app.routes if route.path == "/annot/atlas")
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(endpoint(request, token), 5)
    assert backends[0].cancelled.is_set()
    assert service.grants["task-atlas"].pending is None


@pytest.mark.parametrize("read_only,builtin", [(True, ["annot"]), (False, [])])
async def test_unavailable_staff_cannot_register(service, read_only, builtin):
    ctx, broker, service, token, key, seen = service
    await service.finish(ctx.task.id)
    ctx.read_only, ctx.agent.builtin_mcp = read_only, builtin
    service.register(ctx, key)
    async with client(broker) as c:
        response = await c.post("/annot/atlas", headers={"X-Labhq-Token": token},
                                json={"variants": ["chr1:100:A:G"]})
    assert response.status_code == 403 and not seen


@pytest.mark.parametrize("source", ["parent", "engine", "task", "alias", "case", "empty_override"])
def test_real_child_shell_has_no_key(tmp_path, monkeypatch, source):
    ctx = context(tmp_path)
    key = uuid.uuid4().hex
    monkeypatch.delenv(atlas.KEY_ENV, raising=False)
    if source in {"parent", "alias", "empty_override"}:
        monkeypatch.setenv(atlas.KEY_ENV, key)
    if source == "alias":
        monkeypatch.setenv("ATLAS_TEST_ALIAS", key)
    if source in {"engine", "case", "empty_override"}:
        ctx.settings.engines.codex.env[atlas.KEY_ENV.lower() if source == "case" else atlas.KEY_ENV] = (
            "" if source == "empty_override" else key)
    if source == "task":
        ctx.env[atlas.KEY_ENV] = key
    env = CodexAdapter(ctx.settings).staff_env(ctx)
    assert key not in json.dumps(env)
    if os.name == "nt":
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                   "if (Test-Path Env:ALPHAGENOME_API_KEY) {exit 7}; "
                   "if (Test-Path Env:ATLAS_TEST_ALIAS) {exit 8}; Write-Output 'ATLAS_SHELL_CLEAR'"]
    else:
        command = ["/bin/sh", "-c", 'test -z "${ALPHAGENOME_API_KEY+x}" && '
                   'test -z "${ATLAS_TEST_ALIAS+x}" && echo ATLAS_SHELL_CLEAR']
    result = subprocess.run(command, env=env, capture_output=True, timeout=15, check=True)
    assert key.encode() not in result.stdout + result.stderr
    assert atlas.KEY_ENV.lower().encode() not in result.stdout.lower()


@pytest.mark.parametrize("override", ["set", "mcp", "alias", "prefix"])
async def test_cli_overrides_refused_before_generated_files(tmp_path, monkeypatch, override):
    ctx = context(tmp_path)
    key = uuid.uuid4().hex
    monkeypatch.setenv(atlas.KEY_ENV, key)
    monkeypatch.setenv("ATLAS_TEST_ALIAS", key)
    engine = ctx.settings.engines.codex
    if override == "set":
        engine.extra_args = ["-c", f'shell_environment_policy.set.OTHER="{key}"']
    elif override == "mcp":
        engine.extra_args = ["-c", 'mcp_servers.other.env_vars=["ALPHAGENOME_API_KEY"]']
    elif override == "alias":
        engine.extra_args = ["-c", 'shell_environment_policy.set.OTHER="${ATLAS_TEST_ALIAS}"']
    else:
        engine.prefix_args = ["${ATLAS_TEST_ALIAS}"]
    result = await CodexAdapter(ctx.settings).run(ctx)
    assert not result.ok and "Atlas" in result.error and key not in result.error
    assert not list(tmp_path.rglob("*"))


def test_staff_config_refuses_literal_but_preserves_env_reference(tmp_path, monkeypatch):
    key = uuid.uuid4().hex
    config = tmp_path / "source.yaml"
    settings = Settings(config_path=str(config))
    config.write_text(yaml.safe_dump({"engines": {"codex": {"env": {atlas.KEY_ENV: key}}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="environment"):
        refuse_credential_copy(settings, {key})
    assert not (tmp_path / "staff").exists()
    config.write_text(yaml.safe_dump({"engines": {"codex": {"env": {atlas.KEY_ENV: "${ATLAS_TEST_ALIAS}"}}}}),
                      encoding="utf-8")
    refuse_credential_copy(settings, {key})
    name = write_staff_config(settings, tmp_path / "staff")
    assert key not in Path(name).read_text(encoding="utf-8")


async def test_real_mcp_process_calls_runner_without_key(service, monkeypatch):
    ctx, broker, service, token, key, seen = service
    monkeypatch.setenv(atlas.KEY_ENV, key)
    # Hold a bound socket until uvicorn owns it; no free-port probe race.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(broker.app, log_level="error", lifespan="off"))
    server.install_signal_handlers = lambda: None
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    proc = None
    try:
        for _ in range(500):
            if server.started:
                break
            if serving.done():
                await serving
            await asyncio.sleep(0.01)
        assert server.started
        ctx.env = {"LABHQ_BROKER_URL": f"http://127.0.0.1:{port}", "LABHQ_BROKER_TOKEN": token,
                   "LABHQ_TASK_ID": ctx.task.id, "LABHQ_AGENT_ID": ctx.agent.id,
                   "LABHQ_WORKDIR": str(ctx.workdir), ENABLED_ENV: "1"}
        env = CodexAdapter(ctx.settings).staff_env(ctx)
        script = ("import asyncio,os; from labhq.tools import annot_mcp as m; "
                  "assert 'ALPHAGENOME_API_KEY' not in os.environ; "
                  "assert m.ATLAS_ENABLED; print(asyncio.run(m.alphagenome_atlas(['chr1:100:A:G'])))")
        proc = await asyncio.create_subprocess_exec(sys.executable, "-X", "utf8", "-c", script, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 20)
        assert proc.returncode == 0, stderr.decode(errors="replace")
        assert json.loads(stdout)["n_ok"] == 1 and seen == [key]
        assert key.encode() not in stdout + stderr
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        await service.finish(ctx.task.id)
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        sock.close()


@pytest.mark.skipif(os.name != "nt" or os.environ.get("LABHQ_ATLAS_CODEX_SANDBOX_TEST") != "1",
                    reason="explicit local installed-Codex sandbox probe; no model or service calls")
def test_installed_codex_sandbox_shell_has_no_key(tmp_path, monkeypatch):
    executable = os.environ.get("LABHQ_ATLAS_CODEX_BIN") or shutil.which("codex")
    assert executable
    ctx = context(tmp_path)
    key = uuid.uuid4().hex
    monkeypatch.setenv(atlas.KEY_ENV, key)
    monkeypatch.setenv("ATLAS_TEST_ALIAS", key)
    ctx.env["CODEX_HOME"] = str(tmp_path / "codex-home")
    Path(ctx.env["CODEX_HOME"]).mkdir()
    env = CodexAdapter(ctx.settings).staff_env(ctx)
    # Isolated HOME and unelevated sandbox: no login, account setup or model turn.
    command = [executable, "sandbox", "-C", str(tmp_path), "-P", ":workspace",
        "-c", 'windows.sandbox="unelevated"', "-c", 'shell_environment_policy.inherit="all"',
        "--", "cmd.exe", "/d", "/c",
        "if defined ALPHAGENOME_API_KEY (exit /b 7) & "
        "if defined ATLAS_TEST_ALIAS (exit /b 8) & echo ATLAS_SHELL_CLEAR"]
    proc = subprocess.Popen(command, env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = proc.communicate(timeout=30)
        result = subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
    finally:
        if proc.poll() is None:
            from labhq.proc_tree import kill_tree
            kill_tree(proc.pid)
            proc.communicate(timeout=10)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"ATLAS_SHELL_CLEAR" in result.stdout
    assert key.encode() not in result.stdout + result.stderr
    assert key not in json.dumps(command)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert key.encode() not in path.read_bytes()


@pytest.mark.parametrize("outcome", ["ok", "error", "cancelled", "cleanup_cancelled"])
async def test_runner_wiring_and_grant_cleanup(service, tmp_path, monkeypatch, outcome):
    from labhq.models import TaskResult
    from tests.test_observed_outputs import _runner, _task
    _, _, _, _, key, seen = service
    monkeypatch.setenv(atlas.KEY_ENV, key)
    runner = _runner(tmp_path / "runner", monkeypatch, lambda _: None, engine=Engine.codex)
    task = _task()
    runner._resolve_agent(task).builtin_mcp = ["annot"]
    contexts = []

    class Staff:
        async def run(self, ctx):
            contexts.append(ctx)
            assert ctx.env[ENABLED_ENV] == "1"
            assert atlas.KEY_ENV not in CodexAdapter(ctx.settings).staff_env(ctx)
            assert key not in json.dumps([s.model_dump() for s in ctx.mcp_servers])
            assert runner.annot_broker.grants[ctx.task.id].key == key
            if outcome == "error":
                raise RuntimeError("fixture failure")
            if outcome == "cancelled":
                raise asyncio.CancelledError
            async with client(runner.broker) as c:
                response = await c.post("/annot/atlas", headers={"X-Labhq-Token": ctx.env["LABHQ_BROKER_TOKEN"]},
                                        json={"variants": ["chr1:100:A:G"]})
            assert response.status_code == 200
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *a: Staff())
    if outcome == "cleanup_cancelled":
        finish = runner.annot_broker.finish
        async def interrupted_finish(task_id):
            await finish(task_id)
            raise asyncio.CancelledError
        monkeypatch.setattr(runner.annot_broker, "finish", interrupted_finish)
    if outcome == "ok":
        result = await runner.run_task(task)
        assert result.ok and seen == [key]
    else:
        with pytest.raises(RuntimeError if outcome == "error" else asyncio.CancelledError):
            await runner.run_task(task)
    assert contexts and not runner.annot_broker.grants and not runner.broker.identities
    assert not runner.tool_contexts and not runner.active_roots
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert key.encode() not in path.read_bytes()
