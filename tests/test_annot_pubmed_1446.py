"""Synthetic PubMed pages only: persistence, admission, deadlines and actual input staging."""
import asyncio
import hashlib
import json
from urllib.parse import parse_qs
from xml.sax.saxutils import escape

import httpx
import pytest
import yaml

from labhq.adapters.owned import OwnedPathError
from labhq.tools import annot, annot_pubmed as pubmed, external_wait
from tests.test_annot import FakeTime
from tests.test_annotation_outputs_1446 import collect, workspace
from labhq.runner.step_inputs import stage_inputs
from tests.test_external_wait import mcp_module, limited, lookup_runtime  # noqa: F401


def article(pmid, abstract="가상 초록", book=False):
    tag, citation = ("PubmedBookArticle", "BookDocument") if book else ("PubmedArticle", "MedlineCitation")
    return (f'<{tag}><{citation}><PMID>{pmid}</PMID><Article><ArticleTitle>가상 <i>문헌</i> {pmid}</ArticleTitle>'
            f'<Journal><Title>Fixture journal</Title></Journal><Abstract><AbstractText Label="Results">'
            f'{escape(abstract)}</AbstractText></Abstract></Article></{citation}></{tag}>')


class Provider:
    def __init__(self, count=631, abstract="가상 초록 " * 300):
        self.count, self.abstract, self.calls = count, abstract, []
        self.fail_page = None
        self.rows = None

    def __call__(self, request):
        assert request.url.host == "eutils.ncbi.nlm.nih.gov"
        assert request.method == "POST"
        args = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((request.url.path, args))
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"count": str(self.count),
                "idlist": [str(i) for i in range(1, min(self.count, int(args["retmax"])) + 1)],
                "querytranslation": "fixture translated query"}})
        page = len([p for p, _ in self.calls if p.endswith("efetch.fcgi")])
        if page == self.fail_page:
            return httpx.Response(400)
        ids = args["id"].split(",")
        body = self.rows(page, ids) if self.rows else "".join(article(i, self.abstract) for i in ids)
        return httpx.Response(200, content=f"<PubmedArticleSet>{body}</PubmedArticleSet>".encode())


def make(tmp_path, provider, max_records=1000):
    tmp_path.mkdir(exist_ok=True)
    clock = FakeTime()
    client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    backend = annot.Annotator(client, tmp_path, None, sleep=clock.sleep, clock=clock.clock)
    return pubmed.PubmedCollection(backend, "합성 문헌[Title]", max_records), client, clock


def manifest(root, result):
    return json.loads((root / result["full_result"]).read_text(encoding="utf-8"))


async def test_two_maximum_searches_reach_next_step_without_losing_pages(tmp_path):
    upstream = workspace(tmp_path)
    results = []
    for _ in range(2):
        collection, client, _ = make(upstream.dir, Provider(10000, "short"), 10000)
        async with client:
            results.append(await collection.run())
    assert all(r["complete"] and r["fetched_count"] == 10000 for r in results)
    captured = collect(upstream)
    assert not captured.excluded
    assert len(captured.outputs) == 5
    upstream.update_run("source", ok=True)
    downstream = workspace(tmp_path, "consumer", "synthesis")
    staged, refused = stage_inputs(downstream, {"search": {
        "task_id": "source", "workdir": str(upstream.dir), "outputs": captured.outputs,
        "output_sha256": captured.hashes}}, tmp_path, [], [], 16 * 1024 * 1024, 32 * 1024 * 1024)
    assert staged and not refused
    for path in captured.outputs:
        copied = downstream.dir / "inputs/search" / path.removeprefix("outputs/")
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == captured.hashes[path]
    for result in results:
        saved = manifest(upstream.dir, result)
        count = 0
        for page in saved["pages"]:
            copied = downstream.dir / "inputs/search" / page["path"].removeprefix("outputs/")
            with copied.open("rb") as source:
                source.seek(page.get("byte_offset", 0))
                data = source.read(page["bytes"])
            assert hashlib.sha256(data).hexdigest() == page["sha256"]
            count += len(json.loads(data)["records"])
        assert count == saved["unique_count"] == 10000


async def test_631_large_korean_pages_reach_next_step_with_exact_hashes(tmp_path):
    upstream = workspace(tmp_path)
    provider = Provider()
    collection, client, clock = make(upstream.dir, provider)
    async with client:
        result = await collection.run()
    assert result == {"full_result": collection.path, "query_log": annot.QUERY_LOG,
                      "found_count": 631, "fetched_count": 631, "unique_count": 631,
                      "complete": True, "stop_reason": "exhausted"}
    assert len(json.dumps(result, ensure_ascii=False)) < 600
    saved = manifest(upstream.dir, result)
    for info in saved["bundles"]:
        bundle = (upstream.dir / info["path"]).read_bytes()
        assert len(bundle) == info["bytes"]
        assert hashlib.sha256(bundle).hexdigest() == info["sha256"]
    assert len(saved["pages"]) == 7
    assert sum(p["bytes"] for p in saved["pages"]) > 2 * 1024 * 1024
    assert len(provider.calls) == 8
    assert clock.sleeps and all(s >= 1 / 3 - 1e-9 for s in clock.sleeps)
    captured = collect(upstream)  # staff outputs omitted, real collector and consumer
    assert not captured.excluded
    assert len(captured.outputs) == 3  # manifest, shared query log, one JSONL page bundle
    upstream.update_run("source", ok=True)
    downstream = workspace(tmp_path, "consumer", "synthesis")
    staged, refused = stage_inputs(downstream, {"search": {
        "task_id": "source", "workdir": str(upstream.dir), "outputs": captured.outputs,
        "output_sha256": captured.hashes}}, tmp_path, [], [], 16 * 1024 * 1024, 32 * 1024 * 1024)
    assert staged and not refused
    rows = []
    for page in saved["pages"]:
        copied = downstream.dir / "inputs/search" / page["path"].removeprefix("outputs/")
        with copied.open("rb") as source:
            source.seek(page["byte_offset"])
            data = source.read(page["bytes"])
        assert hashlib.sha256(data).hexdigest() == page["sha256"]
        rows.extend(json.loads(data)["records"])
    assert len(rows) == len({r["pmid"] for r in rows}) == 631
    assert rows[0]["abstract"][0]["text"] == provider.abstract.strip()
    assert provider.abstract in rows[0]["record_xml"]
    assert rows[0]["title"] == "가상 문헌 1"
    assert "Fixture journal" in rows[0]["record_xml"]
    assert all(r["access_level"] == "abstract" for r in rows)
    log = json.loads((upstream.dir / annot.QUERY_LOG).read_text(encoding="utf-8"))
    assert log["result_sha256"] == hashlib.sha256((upstream.dir / collection.path).read_bytes()).hexdigest()


@pytest.mark.parametrize("count,limit,fetched,reason", [
    (0, 1000, 0, "exhausted"), (631, 150, 150, "record_limit"),
    (10001, 10000, 10000, "record_limit"), (200, 200, 200, "exhausted")])
async def test_count_and_record_caps(tmp_path, count, limit, fetched, reason):
    provider = Provider(count, "short")
    collection, client, _ = make(tmp_path, provider, limit)
    async with client:
        result = await collection.run()
    assert result["found_count"] == count
    assert result["fetched_count"] == result["unique_count"] == fetched
    assert result["stop_reason"] == reason
    assert result["complete"] == (reason == "exhausted")
    assert len(manifest(tmp_path, result)["pages"]) <= 100


@pytest.mark.parametrize("fail_page", [None, 3])
async def test_bundles_rotate_below_export_cap_and_keep_partial_ranges(tmp_path, monkeypatch, fail_page):
    assert pubmed.BUNDLE_BYTES <= 50 * 1024 * 1024
    monkeypatch.setattr(pubmed, "BUNDLE_BYTES", 80000)
    upstream = workspace(tmp_path)
    provider = Provider(300, "short")
    provider.fail_page = fail_page
    collection, client, _ = make(upstream.dir, provider)
    async with client:
        result = await collection.run()
    saved = manifest(upstream.dir, result)
    assert result["complete"] == (fail_page is None)
    assert len(saved["bundles"]) == (3 if fail_page is None else 2)
    assert saved["fetched_count"] == (300 if fail_page is None else 200)
    for info in saved["bundles"]:
        data = (upstream.dir / info["path"]).read_bytes()
        assert len(data) == info["bytes"] <= pubmed.BUNDLE_BYTES
        assert hashlib.sha256(data).hexdigest() == info["sha256"]
    captured = collect(upstream)
    assert not captured.excluded
    upstream.update_run("source", ok=True)
    downstream = workspace(tmp_path, "consumer", "synthesis")
    staged, refused = stage_inputs(downstream, {"search": {
        "task_id": "source", "workdir": str(upstream.dir), "outputs": captured.outputs,
        "output_sha256": captured.hashes}}, tmp_path, [], [], 16 * 1024 * 1024, 32 * 1024 * 1024)
    assert staged and not refused
    for page in saved["pages"]:
        copied = downstream.dir / "inputs/search" / page["path"].removeprefix("outputs/")
        data = copied.read_bytes()[page["byte_offset"]:][:page["bytes"]]
        assert hashlib.sha256(data).hexdigest() == page["sha256"]
        assert len(json.loads(data)["records"]) == page["record_count"]


async def test_middle_failure_preserves_first_page_and_original_count(tmp_path):
    provider = Provider()
    provider.fail_page = 2
    collection, client, _ = make(tmp_path, provider)
    async with client:
        result = await collection.run()
    assert not result["complete"] and result["stop_reason"] == "lookup_failed"
    assert result["found_count"] == 631 and result["unique_count"] == result["fetched_count"] == 100
    assert len(manifest(tmp_path, result)["pages"]) == 1
    assert len(provider.calls) == 3  # no later pages


async def test_same_query_keeps_prior_manifests_and_pages(tmp_path):
    results = []
    for _ in range(2):
        collection, client, _ = make(tmp_path, Provider(1, "short"))
        async with client:
            results.append(await collection.run())
    assert results[0]["full_result"] != results[1]["full_result"]
    assert all(manifest(tmp_path, r)["complete"] for r in results)
    logs = [json.loads(s) for s in (tmp_path / annot.QUERY_LOG).read_text(encoding="utf-8").splitlines()]
    assert len(logs) == 2
    for row in logs:
        assert row["result_sha256"] == hashlib.sha256((tmp_path / row["result_file"]).read_bytes()).hexdigest()


@pytest.mark.parametrize("tamper", ["replace", "resize", "hardlink"])
async def test_changed_bundle_is_refused_before_another_page_is_appended(tmp_path, tamper):
    provider = Provider(200, "short")
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b"untouched")
    def respond(request):
        if request.url.path.endswith("efetch.fcgi") and b"id=101" in request.content:
            path = tmp_path / collection.bundle
            if tamper == "resize":
                with path.open("ab") as target:
                    target.write(b"changed")
            else:
                previous = path.read_bytes()
                path.unlink()
                if tamper == "hardlink":
                    import os
                    os.link(outside, path)
                else:
                    path.write_bytes(previous)
        return provider(request)
    collection, client, _ = make(tmp_path, respond)
    async with client:
        with pytest.raises(OwnedPathError):
            await collection.run()
    assert outside.read_bytes() == b"untouched"
    saved = json.loads((tmp_path / collection.path).read_bytes())
    assert not saved["complete"] and saved["fetched_count"] == 100


@pytest.mark.parametrize("mode,unique", [("repeated_page", 100), ("within_page", 100), ("missing", 199)])
async def test_duplicate_and_incomplete_pages_never_claim_complete(tmp_path, mode, unique):
    provider = Provider(200, "short")
    def rows(page, ids):
        if page == 2:
            ids = ([str(i) for i in range(1, 101)] if mode == "repeated_page" else
                   ["1"] * 100 if mode == "within_page" else ids[:-1])
        return "".join(article(i) for i in ids)
    provider.rows = rows
    collection, client, _ = make(tmp_path, provider)
    async with client:
        result = await collection.run()
    assert not result["complete"] and result["stop_reason"] == "page_id_mismatch"
    assert result["unique_count"] == unique
    assert result["fetched_count"] == (199 if mode == "missing" else 200)
    assert len(manifest(tmp_path, result)["pages"]) == 2


@pytest.mark.parametrize("kind", ["page", "total", "response"])
async def test_byte_caps_save_only_pages_that_fit(tmp_path, monkeypatch, kind):
    provider = Provider(200, "short")
    collection, client, _ = make(tmp_path, provider)
    if kind == "response":
        provider.abstract = "long" * 10000
        monkeypatch.setattr(pubmed, "PAGE_BYTES", 1000)
    elif kind == "page":
        monkeypatch.setattr(pubmed, "PAGE_BYTES", 60000)  # XML fits; JSON + projection does not
        provider.abstract = "x" * 200
    else:
        monkeypatch.setattr(pubmed, "TOTAL_BYTES", 70000)
    async with client:
        result = await collection.run()
    assert not result["complete"]
    assert result["stop_reason"] == ("response_byte_limit" if kind == "response" else "byte_limit")
    assert result["fetched_count"] == (100 if kind == "total" else 0)


@pytest.mark.parametrize("answer", [None, {}, {"count": "3", "idlist": ["1", "1", "2"]},
    {"count": "2", "idlist": ["1"]}, {"count": "0", "idlist": [], "ERROR": "bad"}])
async def test_bad_search_answer_is_not_empty_success(tmp_path, answer):
    collection, client, _ = make(tmp_path, lambda r: httpx.Response(200, json={"esearchresult": answer}))
    async with client:
        result = await collection.run()
    assert not result["complete"] and result["fetched_count"] == 0
    assert result["stop_reason"] in {"invalid_search_ids", "invalid_response"}


@pytest.mark.parametrize("body", [b"<bad", b"<ERROR>failure</ERROR>",
    b"<PubmedArticleSet><Unexpected/></PubmedArticleSet>"])
async def test_bad_fetch_answer_keeps_manifest(tmp_path, body):
    provider = Provider(1)
    def respond(request):
        return provider(request) if request.url.path.endswith("esearch.fcgi") else httpx.Response(200, content=body)
    collection, client, _ = make(tmp_path, respond)
    async with client:
        result = await collection.run()
    assert not result["complete"] and result["stop_reason"] == "invalid_response"
    assert result["found_count"] == 1


async def test_book_and_metadata_records_do_not_become_full_text(tmp_path):
    provider = Provider(1)
    provider.rows = lambda page, ids: article("1", book=True).replace(
        '<Abstract><AbstractText Label="Results">가상 초록</AbstractText></Abstract>', "")
    collection, client, _ = make(tmp_path, provider)
    async with client:
        result = await collection.run()
    page = manifest(tmp_path, result)["pages"][0]
    row = json.loads((tmp_path / page["path"]).read_bytes()[page["byte_offset"]:][:page["bytes"]])["records"][0]
    assert result["complete"] and row["access_level"] == "metadata" and not row["abstract"]


@pytest.mark.parametrize("query,limit", [("", 1000), ("a\nb", 1000), ("x" * 4001, 1000),
    ("x", 0), ("x", 10001), ("x", True), ("x", 1.5)])
def test_invalid_arguments_do_not_send(tmp_path, query, limit):
    with pytest.raises(ValueError):
        pubmed.PubmedCollection(annot.Annotator(None, tmp_path, None), query, limit)


async def test_missing_workdir_refuses_before_http():
    with pytest.raises(ValueError, match="WORKDIR"):
        pubmed.PubmedCollection(annot.Annotator(None, None, None), "fixture")


async def test_cancelled_fetch_retains_incomplete_checkpoint(tmp_path):
    provider = Provider(200, "short")
    ready = asyncio.Event()
    async def respond(request):
        if request.url.path.endswith("efetch.fcgi") and b"id=101" in request.content:
            ready.set()
            await asyncio.Event().wait()
        return provider(request)
    collection, client, _ = make(tmp_path, respond)
    async with client:
        task = asyncio.create_task(collection.run())
        await asyncio.wait_for(ready.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    saved = json.loads((tmp_path / collection.path).read_bytes())
    assert saved["fetched_count"] == 100 and not saved["complete"]
    assert saved["stop_reason"] == "interrupted"


@pytest.mark.parametrize("expired", [False, True])
async def test_mcp_shared_timeout_returns_partial_manifest(tmp_path, monkeypatch, mcp_module, expired, lookup_runtime):
    lookup_runtime.expire = True
    lookup_runtime.handshake = not expired
    provider = Provider(200, "short")
    original_client = httpx.AsyncClient
    async def respond(request):
        if request.url.path.endswith("efetch.fcgi") and b"id=101" in request.content:
            lookup_runtime.phases[-1].started.set()
            await asyncio.Event().wait()
        return provider(request)
    monkeypatch.setattr(mcp_module.httpx, "AsyncClient", lambda **kw: original_client(
        transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(mcp_module, "BROKER_TOKEN", "")
    monkeypatch.setattr(mcp_module, "LOOKUP_LIMITED", False)
    monkeypatch.setattr(mcp_module, "PACE", {})
    monkeypatch.setattr(annot.Http, "_pace", lambda *a: asyncio.sleep(0))
    async def short(operation, **kwargs):
        return await external_wait.run_lookup(operation, timeout_s=30, clock=lambda: lookup_runtime.now, **kwargs)
    monkeypatch.setattr(mcp_module, "run_lookup", short)
    if expired:
        from labhq.adapters.owned import write_owned
        write_owned(tmp_path, external_wait.BUDGET_FILE, '{"started_at": 1}')
    result = json.loads(await mcp_module.pubmed_search("fixture"))
    assert result["stop_reason"] == "timeout" and not result["complete"]
    assert result["fetched_count"] == (0 if expired else 100)
    assert manifest(tmp_path, result)["stop_reason"] == "timeout"
    states = [json.loads(s)["status"] for s in
              (tmp_path / external_wait.STATUS_FILE).read_text(encoding="utf-8").splitlines()]
    assert states[-1] == "timeout"


async def test_mcp_admission_refuses_fourth_send_and_keeps_partial_pages(tmp_path, monkeypatch, mcp_module, limited):
    provider = Provider(631, "short")
    limited.provider = provider
    monkeypatch.setattr(annot.Http, "_pace", lambda *a: asyncio.sleep(0))
    result = json.loads(await mcp_module.pubmed_search("fixture"))
    assert result["fetched_count"] == 200 and result["found_count"] == 631
    assert not result["complete"] and result["stop_reason"] == "lookup_failed"
    assert len(provider.calls) == 3 and limited.admits == 4
    assert len(manifest(tmp_path, result)["pages"]) == 2
    states = [json.loads(s) for s in (tmp_path / external_wait.STATUS_FILE).read_text(encoding="utf-8").splitlines()]
    assert states[-1]["status"] == "failed"


@pytest.mark.parametrize("phase", ["search", "second_page"])
async def test_shared_timeout_preserves_timeout_in_admission_receipt(
        tmp_path, monkeypatch, mcp_module, lookup_runtime, phase):
    from labhq.runner.lookup_admission import LookupAdmission
    ledger = LookupAdmission(tmp_path / "timeout-ledger.sqlite3", 100)
    provider = Provider(200, "short")
    original_client = httpx.AsyncClient
    async def respond(request):
        if request.url.host == "broker.invalid":
            body = json.loads(request.content)
            if request.url.path == "/lookup/admit":
                return httpx.Response(200, json=ledger.admit("req", "task", "worker", body["tool"], body["host"]))
            if request.url.path == "/lookup/finish":
                return httpx.Response(200, json={"recorded": ledger.finish("req", "task", body["sends"], body["outcome"])})
            return httpx.Response(200, json={"ok": True})
        if phase == "search" or (request.url.path.endswith("efetch.fcgi") and b"id=101" in request.content):
            lookup_runtime.phases[-1].started.set()
            await asyncio.Event().wait()
        return provider(request)
    monkeypatch.setattr(mcp_module.httpx, "AsyncClient", lambda **kw: original_client(
        transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(mcp_module, "LOOKUP_LIMITED", True)
    monkeypatch.setattr(annot.Http, "_pace", lambda *a: asyncio.sleep(0))
    lookup_runtime.expire = lookup_runtime.handshake = True
    async def deadline(operation, **kwargs):
        return await external_wait.run_lookup(operation, timeout_s=30, clock=lambda: lookup_runtime.now, **kwargs)
    monkeypatch.setattr(mcp_module, "run_lookup", deadline)
    result = json.loads(await mcp_module.pubmed_search("fixture"))
    assert not result["complete"] and result["stop_reason"] == "timeout"
    assert result["fetched_count"] == (0 if phase == "search" else 100)
    rows = ledger.sends("req")
    assert len(rows) == (1 if phase == "search" else 3)
    assert rows[-1]["http_status"] is None and rows[-1]["outcome"] == "timeout"
    assert all(row["http_status"] == 200 and row["outcome"] == "ok" for row in rows[:-1])
    states = [json.loads(s) for s in (tmp_path / external_wait.STATUS_FILE).read_text(encoding="utf-8").splitlines()]
    assert states[-1]["status"] == "timeout"


@pytest.mark.parametrize("search_failure", [False, True])
async def test_partial_transport_failure_keeps_failed_admission_receipts(
        tmp_path, monkeypatch, mcp_module, limited, search_failure):
    provider = Provider(200, "short")
    limited.ledger.limit = 100
    limited.ledger.transient_limit = 100
    monkeypatch.setattr(annot, "MAX_ATTEMPTS", 3)
    monkeypatch.setattr(annot.Http, "_pace", lambda *a: asyncio.sleep(0))
    def respond(request):
        if ((search_failure and request.url.path.endswith("esearch.fcgi"))
                or (not search_failure and request.url.path.endswith("efetch.fcgi")
                    and b"id=101" in request.content)):
            raise httpx.ConnectError("synthetic outage", request=request)
        return provider(request)
    limited.provider = respond
    result = json.loads(await mcp_module.pubmed_search("fixture"))
    assert not result["complete"] and result["stop_reason"] == "lookup_failed"
    assert result["fetched_count"] == (0 if search_failure else 100)
    rows = limited.ledger.sends("req-1")
    unanswered = [row for row in rows if row["http_status"] is None]
    assert len(unanswered) == 3 and {r["outcome"] for r in unanswered} == {"failed"}
    assert all(r["outcome"] == "ok" for r in rows if r["http_status"] == 200)
    assert "eutils.ncbi.nlm.nih.gov" in limited.ledger.paused_hosts("req-1")
    next_send = limited.ledger.admit("req-1", "later-task", "worker", "pubmed_search", "eutils.ncbi.nlm.nih.gov")
    assert not next_send["admitted"] and next_send["reason"] == "host_paused"
    states = [json.loads(s) for s in (tmp_path / external_wait.STATUS_FILE).read_text(encoding="utf-8").splitlines()]
    assert states[-1]["status"] == "failed"


@pytest.mark.parametrize("mode,expected", [("http", "failed"), ("invalid", "failed"),
                                          ("record_limit", "ok"), ("exhausted", "ok")])
async def test_mcp_lookup_status_distinguishes_partial_failure_from_planned_cap(
        tmp_path, monkeypatch, mcp_module, limited, mode, expected):
    provider = Provider(200 if mode == "record_limit" else 100, "short")
    limited.provider = (lambda r: httpx.Response(400)) if mode == "http" else (
        (lambda r: httpx.Response(200, json={})) if mode == "invalid" else provider)
    monkeypatch.setattr(annot.Http, "_pace", lambda *a: asyncio.sleep(0))
    result = json.loads(await mcp_module.pubmed_search("fixture", 100))
    assert result["complete"] == (mode == "exhausted")
    assert result["stop_reason"] == {"http": "lookup_failed", "invalid": "invalid_response",
                                     "record_limit": "record_limit", "exhausted": "exhausted"}[mode]
    states = [json.loads(s) for s in (tmp_path / external_wait.STATUS_FILE).read_text(encoding="utf-8").splitlines()]
    assert states[-1]["status"] == expected
    if mode == "http":
        rows = limited.ledger.sends("req-1")
        assert len(rows) == 1 and rows[0]["http_status"] == 400 and rows[0]["outcome"] == "failed"


async def test_successful_crossref_absence_keeps_404_receipt_ok(tmp_path, monkeypatch, mcp_module, limited):
    limited.provider = lambda request: httpx.Response(404)
    monkeypatch.setattr(annot.Http, "_pace", lambda *a: asyncio.sleep(0))
    result = json.loads(await mcp_module.crossref_doi(["10.1000/missing"]))
    assert not result["results"][0]["found"]
    rows = limited.ledger.sends("req-1")
    assert len(rows) == 1 and rows[0]["http_status"] == 404 and rows[0]["outcome"] == "ok"
    states = [json.loads(s) for s in (tmp_path / external_wait.STATUS_FILE).read_text(encoding="utf-8").splitlines()]
    assert states[-1]["status"] == "ok"


async def test_owned_parent_link_is_refused_without_touching_target(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "work"
    root.mkdir()
    (root / "outputs").mkdir()
    try:
        (root / "outputs/annotation").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation requires Windows privilege")
    collection, client, _ = make(root, Provider(0))
    async with client:
        with pytest.raises(OwnedPathError):
            await collection.run()
    assert list(outside.iterdir()) == []


def test_lit_scout_keeps_external_pubmed_and_uses_file_collector():
    from pathlib import Path
    agent = yaml.safe_load((Path(__file__).parents[1] / "agents/core/lit_scout.yaml").read_text(encoding="utf-8"))
    assert any(m["name"] == "pubmed" for m in agent["mcp"])
    assert "pubmed_search" in agent["system_prompt"] and "complete=false" in agent["system_prompt"]
