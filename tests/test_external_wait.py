"""Only coroutine clocks, owned temporary files and mocked HTTP; no process or service integration."""
import asyncio
import importlib
import inspect
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from labhq.adapters.owned import OwnedPathError, write_owned
from labhq.tools import annot, annot_keys, external_wait as wait
from labhq.tools._mcpcompat import NOT_EVIDENCE, ToolError


def records(root):
    return [json.loads(line) for line in (root / wait.STATUS_FILE).read_text(encoding="utf-8").splitlines()]


async def test_success_keeps_result_and_logs_progress_without_inputs(tmp_path):
    notifications = []
    notify = AsyncMock(side_effect=lambda data: notifications.append(data))
    answer = {"found": False, "meaning": "successfully checked"}
    operation = AsyncMock(return_value=answer)
    assert await wait.run_lookup(operation, tool="vep", workdir=tmp_path, notify=notify) == answer
    assert [r["status"] for r in records(tmp_path)] == ["waiting", "ok"]
    assert notifications[0]["elapsed_s"] >= 0
    assert "경과" in notifications[0]["text"]
    assert all(set(r) == {"tool", "status", "elapsed_s", "timeout_s", "text"} for r in records(tmp_path))


async def test_hung_lookup_times_out_without_a_process_and_next_work_continues(tmp_path):
    cancelled = asyncio.Event()
    async def hung():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    with pytest.raises(wait.LookupTimeout, match="조회 불가\\(시간 초과\\)"):
        await wait.run_lookup(hung, tool="chipatlas", workdir=tmp_path, timeout_s=.08,
                              progress_s=.005, clock=lambda: 100.0)
    assert cancelled.is_set()
    rows = records(tmp_path)
    assert rows[-1]["status"] == "timeout"
    assert len([r for r in rows if r["status"] == "waiting"]) > 1
    # The caller survives and can produce the rest of its step; no false negative result was returned.
    write_owned(tmp_path, "outputs/remaining.txt", "remaining work complete")
    assert (tmp_path / "outputs/remaining.txt").is_file()


async def test_retries_different_services_and_restart_do_not_reset_deadline(tmp_path):
    now = [100.0]
    operation = AsyncMock(return_value={"ok": True})
    await wait.run_lookup(operation, tool="chipatlas", workdir=tmp_path, clock=lambda: now[0])
    now[0] += wait.TIMEOUT_S + 1
    after_restart = AsyncMock(return_value={"ok": True})
    with pytest.raises(wait.LookupTimeout):
        await wait.run_lookup(after_restart, tool="gtex", workdir=tmp_path, clock=lambda: now[0])
    after_restart.assert_not_called()
    assert records(tmp_path)[-1]["status"] == "timeout"


async def test_transient_failure_is_recorded_but_not_absence(tmp_path):
    operation = AsyncMock(side_effect=annot.LookupFailed("server unavailable"))
    with pytest.raises(annot.LookupFailed, match="server unavailable"):
        await wait.run_lookup(operation, tool="gtex", workdir=tmp_path)
    assert records(tmp_path)[-1]["status"] == "failed"


async def test_short_backend_deadline_is_classified_as_timeout(tmp_path):
    operation = AsyncMock(side_effect=annot.LookupTimedOut("조회 불가(시간 초과)"))
    with pytest.raises(annot.LookupTimedOut):
        await wait.run_lookup(operation, tool="chipatlas", workdir=tmp_path)
    assert records(tmp_path)[-1]["status"] == "timeout"


async def test_display_outage_does_not_reset_or_block_lookup(tmp_path):
    operation = AsyncMock(return_value="result")
    notify = AsyncMock(side_effect=httpx.ConnectError("mock broker unavailable"))
    assert await wait.run_lookup(operation, tool="gtex", workdir=tmp_path, notify=notify) == "result"
    assert records(tmp_path)[-1]["status"] == "ok"


async def test_cancellation_stays_cancellation(tmp_path):
    operation = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await wait.run_lookup(operation, tool="gtex", workdir=tmp_path)
    assert records(tmp_path)[-1]["status"] == "cancelled"


async def test_untrusted_owned_path_never_runs_the_operation(tmp_path, monkeypatch):
    operation = AsyncMock()
    def deny(*args):
        raise OwnedPathError("fixture owned-path refusal")
    monkeypatch.setattr(wait, "write_owned", deny)
    with pytest.raises(OwnedPathError):
        await wait.run_lookup(operation, tool="gtex", workdir=tmp_path)
    operation.assert_not_called()


@pytest.mark.parametrize("stamp", [float("nan"), float("inf"), 10**20])
async def test_invalid_budget_does_not_start_a_lookup(tmp_path, stamp):
    write_owned(tmp_path, wait.BUDGET_FILE, json.dumps({"started_at": stamp}))
    operation = AsyncMock()
    with pytest.raises(ValueError):
        await wait.run_lookup(operation, tool="gtex", workdir=tmp_path)
    operation.assert_not_called()


@pytest.fixture
def mcp_module(tmp_path, monkeypatch):
    # Import-time optional key discovery and config resolution are faked before importing this server.
    monkeypatch.setattr(annot_keys, "read_key", lambda _settings: None)
    monkeypatch.delenv("LABHQ_CONFIG", raising=False)
    module = importlib.import_module("labhq.tools.annot_mcp")
    monkeypatch.setattr(module, "WORKDIR", tmp_path)
    monkeypatch.setattr(module, "ALPHAGENOME", None)
    monkeypatch.setattr(module, "BROKER_TOKEN", "fixture-broker-token")
    monkeypatch.setattr(module, "BROKER", "http://broker.invalid")
    return module


@pytest.mark.parametrize("literature", [False, True])
async def test_mcp_timeout_is_an_error_with_mock_board_events(tmp_path, monkeypatch, mcp_module, literature):
    posted = []
    original_client = httpx.AsyncClient
    def respond(request):
        assert request.url.host == "broker.invalid"
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})
    monkeypatch.setattr(mcp_module.httpx, "AsyncClient", lambda **kw: original_client(
        transport=httpx.MockTransport(respond), **kw))
    class Backend:
        def __init__(self, *args, **kwargs):
            pass
        async def query(self, *args, **kwargs):
            await asyncio.Event().wait()
    monkeypatch.setattr(mcp_module.annot_regulatory, "RegulatoryAnnotator", Backend)
    monkeypatch.setattr(mcp_module.annot_literature, "LiteratureAnnotator", Backend)
    async def short_budget(operation, **kwargs):
        return await wait.run_lookup(operation, timeout_s=.08, progress_s=.005, clock=lambda: 100.0, **kwargs)
    monkeypatch.setattr(mcp_module, "run_lookup", short_budget)
    call = mcp_module._run_literature if literature else mcp_module._run
    with pytest.raises(ToolError) as exc:
        await call("query", "public-example")
    assert "조회 불가(시간 초과)" in str(exc.value) and NOT_EVIDENCE in str(exc.value)
    assert posted and all(p["type"] == "agent.log" for p in posted)
    assert posted[-1]["data"]["external_lookup"]["status"] == "timeout"
    assert "경과" in posted[0]["data"]["text"]
    assert "public-example" not in str(posted)


def test_human_pdf_approval_path_is_not_wrapped(mcp_module):
    assert "run_lookup" not in inspect.getsource(mcp_module._run_key_fulltext)
    assert "run_lookup" not in inspect.getsource(mcp_module._request_pdf)
