"""Only coroutine clocks, owned temporary files and mocked HTTP; no process or service integration."""
import asyncio
import importlib
import inspect
import json
import re
from contextvars import ContextVar
from unittest.mock import AsyncMock
from types import SimpleNamespace

import httpx
import pytest

from labhq.adapters.owned import OwnedPathError, write_owned
from labhq.tools import annot, annot_keys, external_wait as wait
from labhq.tools._mcpcompat import NOT_EVIDENCE, ToolError
from labhq.settings import LiteratureSettings


# A deadlock watchdog, not the automatic lookup budget. Durable owned-file
# writes may delay an event loop; logical deadline assertions never use this.
DEADLOCK_WATCHDOG_S = 10


def records(root):
    return [json.loads(line) for line in (root / wait.STATUS_FILE).read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def lookup_runtime(monkeypatch):
    """Advance only the lookup's fake timers; notify and cancellation use real coroutine handshakes."""
    get_running_loop, lock_type = asyncio.get_running_loop, asyncio.Lock

    class Runtime:
        now = 100.0
        expire = False
        automatic = ContextVar("lookup_test_automatic", default=False)

        def __init__(self):
            self.bounds, self.progress_delays = [], []
            self.phases, self.timed_awaitables = [], set()
            self.handshake, self.notification_timeout = False, 5

        def timed(self, operation):
            # Explicit identity distinguishes an 80ms operation from its 80ms
            # notify wait; notification/HTTP/global timers are never guessed.
            def start():
                awaitable = operation()
                self.timed_awaitables.add(awaitable)
                return awaitable
            return start

        async def sleep(self, delay):
            assert delay == 60  # retain the production progress cadence
            self.progress_delays.append(delay)
            phase = self.phases[-1]
            phase.progress_delays.append(delay)
            phase.watches.add(asyncio.current_task())
            if len(phase.progress_delays) == 2:
                phase.heartbeat_recorded.set()  # preceding record/notify has completed
            await phase.tick.wait()
            phase.tick.clear()
            self.now += delay

        async def wait_for(self, awaitable, timeout):
            if awaitable in self.timed_awaitables:
                self.timed_awaitables.remove(awaitable)
            elif timeout <= 5:  # notification, not the automatic operation
                assert timeout == self.notification_timeout
                return await awaitable
            self.bounds.append(timeout)
            deadline = self.now + timeout
            phase = SimpleNamespace(started=asyncio.Event(), tick=asyncio.Event(),
                                    heartbeat_recorded=asyncio.Event(), progress_delays=[], watches=set(),
                                    deadline=deadline)
            self.phases.append(phase)
            async def marked_operation():
                token = self.automatic.set(True)
                try:
                    return await awaitable
                finally:
                    self.automatic.reset(token)
            task = asyncio.create_task(marked_operation())
            phase.task = task
            try:
                if self.handshake:
                    await phase.started.wait()
                else:
                    await asyncio.sleep(0)  # scheduling handoff, never a wall-clock polling delay
                if not self.expire:
                    assert task.done(), "non-timeout fixtures must complete without external waits"
                    return task.result()
                assert not task.done(), "timeout fixtures must actually suspend"
                if timeout >= 60:
                    phase.tick.set()
                    await phase.heartbeat_recorded.wait()
                else:
                    # An 80ms deadline precedes the unchanged 60s heartbeat.
                    assert not phase.heartbeat_recorded.is_set()
                self.now = deadline
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                raise asyncio.TimeoutError
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    runtime = Runtime()
    # Replace only this module's timer interface, never asyncio or the HTTP client's global timers.
    proxy = SimpleNamespace(**{name: getattr(asyncio, name) for name in
        ("create_task", "gather", "TimeoutError", "CancelledError")})
    proxy.get_running_loop, proxy.Lock = get_running_loop, lock_type
    proxy.sleep, proxy.wait_for = runtime.sleep, runtime.wait_for
    monkeypatch.setattr(wait, "asyncio", proxy)
    return runtime


async def test_lookup_runtime_preserves_real_step_slot_serialization(tmp_path, lookup_runtime):
    assert wait.asyncio.get_running_loop is asyncio.get_running_loop
    assert wait.asyncio.Lock is asyncio.Lock
    slot = wait._step_slot(tmp_path)
    assert isinstance(slot, asyncio.Lock)
    assert wait._step_slot(tmp_path) is slot
    entered = asyncio.Event()

    async def decision():
        entered.set()
        return "declined"

    queued = None
    try:
        async with slot:
            queued = asyncio.create_task(wait.exclude_human_wait(
                decision, workdir=tmp_path, clock=lambda: lookup_runtime.now))
            await asyncio.sleep(0)  # let the coroutine reach the held lock, never poll elapsed time
            assert not entered.is_set() and not queued.done()
        assert await asyncio.wait_for(queued, 1) == "declined"
        assert entered.is_set() and not slot.locked()
    finally:
        if queued is not None and not queued.done():
            queued.cancel()
            await asyncio.gather(queued, return_exceptions=True)


async def test_success_keeps_result_and_logs_progress_without_inputs(tmp_path):
    notifications = []
    notify = AsyncMock(side_effect=lambda data: notifications.append(data))
    answer = {"found": False, "meaning": "successfully checked"}
    operation = AsyncMock(return_value=answer)
    assert await wait.run_lookup(operation, tool="vep", workdir=tmp_path, notify=notify) == answer
    assert [r["status"] for r in records(tmp_path)] == ["waiting", "ok"]
    assert notifications[0]["elapsed_s"] >= 0
    assert "경과" in notifications[0]["text"]
    assert all(set(r) == {"tool", "lookup", "status", "elapsed_s", "timeout_s", "text"} for r in records(tmp_path))
    # The per-call id is random and the same for every record of this call; it carries no input (#676).
    assert len({r["lookup"] for r in records(tmp_path)}) == 1
    assert re.fullmatch(r"[0-9a-f]{12}", records(tmp_path)[0]["lookup"])


async def test_hung_lookup_times_out_without_a_process_and_next_work_continues(tmp_path, lookup_runtime):
    lookup_runtime.expire = True
    cancelled = asyncio.Event()
    async def hung():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    with pytest.raises(wait.LookupTimeout, match="조회 불가\\(시간 초과\\)"):
        await wait.run_lookup(hung, tool="chipatlas", workdir=tmp_path, timeout_s=120,
                              clock=lambda: lookup_runtime.now)
    assert lookup_runtime.bounds == [120]
    assert lookup_runtime.progress_delays == [60, 60]
    assert cancelled.is_set()
    rows = records(tmp_path)
    assert rows[-1]["status"] == "timeout"
    assert rows[-1]["elapsed_s"] == rows[-1]["timeout_s"] == 120
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


@pytest.mark.parametrize("decision", ["declined", "timed_out"])
async def test_key_fulltext_pdf_wait_is_excluded_without_resetting_shared_budget(
        tmp_path, monkeypatch, mcp_module, decision, lookup_runtime):

    async def short_budget(operation, **kwargs):
        return await wait.run_lookup(operation, timeout_s=120,
                                     clock=lambda: lookup_runtime.now, **kwargs)

    async def human_wait(operation, **kwargs):
        return await wait.exclude_human_wait(operation, clock=lambda: lookup_runtime.now, **kwargs)

    made = []

    class Backend:
        def __init__(self, *args, **kwargs):
            made.append(kwargs)

        async def fulltext(self, ids):
            return {"results": [{"access_level": "abstract"}]}

    async def approval(_summary, _detail, timeout_s):
        assert timeout_s == 7200
        assert not lookup_runtime.automatic.get()  # never wrap the human wait in an automatic timer
        lookup_runtime.now += 7200  # longer than the automatic deadline, without sleeping
        return {"approved": False, "state": decision}

    monkeypatch.setattr(mcp_module, "S", SimpleNamespace(
        literature=LiteratureSettings(external_model_allowed=True), annot=SimpleNamespace(unpaywall_email="")))
    monkeypatch.setattr(mcp_module, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(mcp_module.annot_literature, "LiteratureAnnotator", Backend)
    monkeypatch.setattr(mcp_module, "_request_pdf", approval)
    monkeypatch.setattr(mcp_module, "run_lookup", short_budget)
    monkeypatch.setattr(mcp_module, "exclude_human_wait", human_wait)
    monkeypatch.setattr(mcp_module, "_lookup_progress", AsyncMock())

    await short_budget(AsyncMock(return_value=True), tool="prior_service", workdir=tmp_path)
    lookup_runtime.now += 30  # Already spent budget must survive the PDF wait.
    result = json.loads(await mcp_module._run_key_fulltext([
        {"id": "10.1000/key", "title": "Public fixture", "why_key": "fixture", "needed": "table"}]))
    assert result["results"][0]["pdf_request_status"] == decision
    assert [kwargs.get("unpaywall_email") for kwargs in made] == [""]  # unset email: the old disabled default
    lookup_runtime.now += 75
    assert await short_budget(AsyncMock(return_value=True), tool="next_service", workdir=tmp_path)
    assert lookup_runtime.bounds == [120, 90, 15]
    lookup_runtime.now += 30
    operation = AsyncMock()
    with pytest.raises(wait.LookupTimeout):
        await short_budget(operation, tool="next_service", workdir=tmp_path)
    operation.assert_not_called()


async def test_cancelled_pdf_wait_preserves_spent_budget(tmp_path):
    now = [100.0]
    await wait.run_lookup(AsyncMock(), tool="fulltext", workdir=tmp_path, clock=lambda: now[0])
    now[0] += 60

    async def cancelled():
        now[0] += 7200
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await wait.exclude_human_wait(cancelled, workdir=tmp_path, clock=lambda: now[0])
    state = json.loads((tmp_path / wait.BUDGET_FILE).read_text(encoding="utf-8"))
    assert now[0] - state["started_at"] == 60


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
async def test_mcp_timeout_is_an_error_with_mock_board_events(
        tmp_path, monkeypatch, mcp_module, literature, lookup_runtime):
    lookup_runtime.expire = True
    cancelled = asyncio.Event()
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
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
    monkeypatch.setattr(mcp_module.annot_regulatory, "RegulatoryAnnotator", Backend)
    monkeypatch.setattr(mcp_module.annot_literature, "LiteratureAnnotator", Backend)
    async def short_budget(operation, **kwargs):
        return await wait.run_lookup(operation, timeout_s=120, clock=lambda: lookup_runtime.now, **kwargs)
    monkeypatch.setattr(mcp_module, "run_lookup", short_budget)
    call = mcp_module._run_literature if literature else mcp_module._run
    with pytest.raises(ToolError) as exc:
        await call("query", "public-example")
    assert cancelled.is_set()
    assert lookup_runtime.bounds == [120]
    assert lookup_runtime.progress_delays == [60, 60]
    assert "조회 불가(시간 초과)" in str(exc.value) and NOT_EVIDENCE in str(exc.value)
    assert posted and all(p["type"] == "agent.log" for p in posted)
    assert posted[-1]["data"]["external_lookup"]["status"] == "timeout"
    assert posted[-1]["data"]["external_lookup"]["elapsed_s"] == 120
    assert "경과" in posted[0]["data"]["text"]
    assert "public-example" not in str(posted)


def test_human_pdf_approval_path_is_not_wrapped(mcp_module):
    assert "run_lookup" not in inspect.getsource(mcp_module._request_pdf)


@pytest.mark.parametrize("email", ["pi@example.org", ""])
async def test_key_fulltext_backend_gets_the_same_unpaywall_email_as_the_literature_tools(
        tmp_path, monkeypatch, mcp_module, email):
    """#826: key papers check legal OA copies with the configured contact email, as `fulltext` does; unset stays
    disabled. The fake backend stops at construction: no lookup, coordinator, approval or request runs."""
    made = []

    class Built(Exception):
        pass

    def backend(*args, **kwargs):
        made.append(kwargs)
        raise Built

    monkeypatch.setattr(mcp_module, "S", SimpleNamespace(
        literature=LiteratureSettings(), annot=SimpleNamespace(unpaywall_email=email)))
    monkeypatch.setattr(mcp_module.annot_literature, "LiteratureAnnotator", backend)
    with pytest.raises(Built):
        await mcp_module._run_key_fulltext([{"id": "10.1000/key", "title": "Public fixture"}])
    with pytest.raises(Built):
        await mcp_module._run_literature("fulltext", ["10.1000/key"])
    assert [kwargs.get("unpaywall_email") for kwargs in made] == [email, email]


@pytest.mark.parametrize("hung_phase", ["fulltext", "limiter", "institution"])
async def test_key_fulltext_automatic_phases_use_task_deadline(
        tmp_path, monkeypatch, mcp_module, hung_phase, lookup_runtime):
    """Real coordinator control flow, fake transports only; never launch a browser or request approval."""
    cancelled = asyncio.Event()
    never_complete = asyncio.Event()
    phases_seen = []
    lookup_runtime.handshake = True
    lookup_runtime.notification_timeout = .08

    async def automatic(phase, value):
        assert lookup_runtime.automatic.get()
        phases_seen.append(phase)
        lookup_runtime.phases[-1].started.set()
        if phase == hung_phase:
            lookup_runtime.expire = True
            try:
                await never_complete.wait()
            finally:
                cancelled.set()
        lookup_runtime.now += .02  # completed phases spend the SAME task budget
        return value

    class Backend:
        def __init__(self, *args, **kwargs):
            pass

        async def fulltext(self, ids):
            return await automatic("fulltext", {"results": [{"access_level": "abstract"}]})

    class Institution:
        def __init__(self, *args, **kwargs):
            pass

        async def fetch(self, doi, destination):
            return await automatic("institution", SimpleNamespace(status="session_expired"))

    class Limiter:
        def __init__(self, *args, **kwargs):
            pass

        async def acquire(self):
            return await automatic("limiter", None)

    policy = LiteratureSettings(institution_enabled=True, institution_proxy="https://institution.invalid",
                                institution_tdm_allowed=True,
                                external_model_allowed=True)
    monkeypatch.setattr(mcp_module, "S", SimpleNamespace(literature=policy, annot=SimpleNamespace(unpaywall_email=""),
                                                       gateway=SimpleNamespace(report_browser=None)))
    monkeypatch.setattr(mcp_module, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(mcp_module.annot_literature, "LiteratureAnnotator", Backend)
    monkeypatch.setattr(mcp_module.annot_literature, "BrowserInstitutionTransport", Institution)
    monkeypatch.setattr(mcp_module.annot_literature, "InstitutionLimiter", Limiter)
    approval = AsyncMock(side_effect=AssertionError("automatic timeout must not open an approval"))
    monkeypatch.setattr(mcp_module, "_request_pdf", approval)
    progress = AsyncMock()
    monkeypatch.setattr(mcp_module, "_lookup_progress", progress)

    async def short_budget(operation, **kwargs):
        # Same 80ms logical budget, deterministic expiry after start/suspend;
        # the production progress cadence remains 60s, so no tick is due here.
        return await wait.run_lookup(lookup_runtime.timed(operation), timeout_s=.08,
                                     clock=lambda: lookup_runtime.now, **kwargs)

    monkeypatch.setattr(mcp_module, "run_lookup", short_budget)
    papers = [{"id": "10.1000/key", "title": "Public fixture", "why_key": "fixture", "needed": "table"}]
    coordinator = asyncio.create_task(mcp_module._run_key_fulltext(papers))
    try:
        with pytest.raises(ToolError, match="조회 불가\\(시간 초과\\)") as error:
            await asyncio.wait_for(coordinator, DEADLOCK_WATCHDOG_S)
    finally:
        if not coordinator.done():
            coordinator.cancel()
            await asyncio.gather(coordinator, return_exceptions=True)
        assert all(p.task.done() and all(w.done() for w in p.watches) for p in lookup_runtime.phases)
    assert NOT_EVIDENCE in str(error.value)
    assert cancelled.is_set()
    assert not never_complete.is_set()
    phase_order = ["fulltext", "limiter", "institution"]
    expected = phase_order[:phase_order.index(hung_phase) + 1]
    assert phases_seen == expected
    assert lookup_runtime.bounds == pytest.approx([.08 - .02 * i for i in range(len(expected))])
    assert all(p.started.is_set() and p.deadline == pytest.approx(100.08) for p in lookup_runtime.phases)
    assert lookup_runtime.phases[-1].task.cancelled()
    assert all(not p.task.cancelled() for p in lookup_runtime.phases[:-1])
    assert len({id(p.tick) for p in lookup_runtime.phases}) == len(expected)
    assert len({id(p.heartbeat_recorded) for p in lookup_runtime.phases}) == len(expected)
    assert all(p.progress_delays == [60] and not p.tick.is_set()
               and not p.heartbeat_recorded.is_set() for p in lookup_runtime.phases)
    assert not lookup_runtime.timed_awaitables
    approval.assert_not_called()
    assert progress.await_args_list[-1].args[0]["status"] == "timeout"
    rows = records(tmp_path)
    assert rows[-1]["status"] == "timeout"
    assert rows[-1]["elapsed_s"] == pytest.approx(.08)
    assert rows[-1]["timeout_s"] == .08
    expected_statuses = [status for _ in expected[:-1] for status in ("waiting", "ok")]
    assert [r["status"] for r in rows] == expected_statuses + ["waiting", "timeout"]
    assert [call.args[0] for call in progress.await_args_list] == rows


async def test_lookup_runtime_rejects_fake_timeout_of_an_operation_that_completed(tmp_path, lookup_runtime):
    lookup_runtime.handshake, lookup_runtime.expire = True, True

    async def completed():
        lookup_runtime.phases[-1].started.set()
        return "finished without suspending"

    with pytest.raises(AssertionError, match="timeout fixtures must actually suspend"):
        await asyncio.wait_for(wait.run_lookup(lookup_runtime.timed(completed), tool="fixture", workdir=tmp_path,
                                              timeout_s=.08, clock=lambda: lookup_runtime.now), DEADLOCK_WATCHDOG_S)
    assert lookup_runtime.phases[-1].task.done() and not lookup_runtime.phases[-1].task.cancelled()
    assert all(w.done() for p in lookup_runtime.phases for w in p.watches)
    assert records(tmp_path)[-1]["status"] == "failed"


async def test_lookup_runtime_parent_cancel_drains_suspended_operation_and_progress(tmp_path, lookup_runtime):
    lookup_runtime.handshake = True
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def hung_without_start_notification():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    caller = asyncio.create_task(wait.run_lookup(lookup_runtime.timed(hung_without_start_notification),
        tool="fixture", workdir=tmp_path, timeout_s=.08, clock=lambda: lookup_runtime.now))
    try:
        await asyncio.wait_for(entered.wait(), DEADLOCK_WATCHDOG_S)
        assert not lookup_runtime.phases[-1].task.done()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(caller, DEADLOCK_WATCHDOG_S)
    finally:
        if not caller.done():
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
    assert cancelled.is_set() and caller.cancelled()
    assert lookup_runtime.phases[-1].task.cancelled()
    assert all(w.done() for p in lookup_runtime.phases for w in p.watches)
    assert not lookup_runtime.timed_awaitables
    assert records(tmp_path)[-1]["status"] == "cancelled"


# ---------------- request-wide public lookup admission (#858, trial #739 B) ----------------

def test_admission_counts_each_attempt_per_trusted_request_and_survives_a_restart(tmp_path):
    from labhq.runner.lookup_admission import LookupAdmission
    from labhq.settings import AnnotSettings
    assert AnnotSettings().request_lookup_limit is None  # unset keeps today's uncounted lookups
    with pytest.raises(ValueError):
        AnnotSettings(request_lookup_limit=-1)
    path = tmp_path / "state" / "lookup_admission.sqlite3"
    ledger = LookupAdmission(path, 2, clock=lambda: 5.0)
    assert ledger.admit("r1", "t1", "lit", "fulltext") == {"admitted": True, "seq": 1, "used": 1, "limit": 2}
    assert ledger.admit("r1", "t2", "bio", "openalex_works")["seq"] == 2  # another agent and step, same request
    assert ledger.admit("r1", "t2", "bio", "fulltext") == {"admitted": False, "used": 2, "limit": 2,
                                                          "reason": "exhausted"}
    assert ledger.admit("r2", "t3", "lit", "fulltext")["seq"] == 1  # requests never share a count
    assert ledger.admit(None, "t4", "lit", "fulltext")["reason"] == "no trusted request identity"
    assert ledger.admit("r2", "t3", "lit", "../x")["reason"] == "unknown lookup tool"
    restarted = LookupAdmission(path, 2)
    assert restarted.admit("r1", "t5", "lit", "fulltext")["admitted"] is False
    assert restarted.finish("r1", "t1", 1, 3, "failed") is True
    assert restarted.finish("r1", "t9", 2, 1, "ok") is False  # another task cannot rewrite this receipt
    assert restarted.finish("r1", "t1", 1, 0, "ok") is False  # a finished receipt is not rewritten
    assert [(a["seq"], a["tool"], a["outbound_requests"], a["outcome"]) for a in restarted.attempts("r1")] == [
        (1, "fulltext", 3, "failed"), (2, "openalex_works", None, None)]


def test_concurrent_admission_never_exceeds_the_limit(tmp_path):
    import threading
    from labhq.runner.lookup_admission import LookupAdmission
    ledger = LookupAdmission(tmp_path / "lookup_admission.sqlite3", 9)
    start, answers = threading.Barrier(24), []

    def caller(index):
        start.wait()
        answers.append(LookupAdmission(ledger.path, 9).admit("r1", f"t{index}", "lit", "fulltext"))

    threads = [threading.Thread(target=caller, args=(i,)) for i in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(DEADLOCK_WATCHDOG_S)
    admitted = sorted(a["seq"] for a in answers if a["admitted"])
    assert len(answers) == 24 and admitted == list(range(1, 10))
    assert len(ledger.attempts("r1")) == 9


async def test_broker_admits_by_the_request_its_token_was_issued_for(tmp_path):
    from labhq.runner.approvals import Broker
    from labhq.runner.lookup_admission import LookupAdmission

    async def noop(*_args):
        return None

    broker = Broker(0, noop, noop, noop, lookup_admission=LookupAdmission(tmp_path / "ledger.sqlite3", 1))
    token = broker.issue_task_token("t1", "lit", "req-1")
    anonymous = broker.issue_task_token("t2", "lit", None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=broker.app), base_url="http://broker") as client:
        async def admit(key, **body):
            return await client.post("/lookup/admit", headers={"X-Labhq-Token": key}, json={"tool": "fulltext", **body})
        assert (await admit(token, request_id="req-other")).status_code == 403  # a named request is refused
        assert (await admit("forged")).status_code == 401
        first = (await admit(token)).json()
        assert first["admitted"] is True and first["seq"] == 1
        assert (await admit(token)).json()["reason"] == "exhausted"
        assert (await admit(anonymous)).json()["reason"] == "no trusted request identity"
        finish = await client.post("/lookup/finish", headers={"X-Labhq-Token": token},
                                   json={"seq": 1, "outbound_requests": 2, "outcome": "ok"})
        assert finish.json() == {"recorded": True}
    unlimited = Broker(0, noop, noop, noop)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=unlimited.app), base_url="http://broker") as client:
        response = await client.post("/lookup/admit", json={"tool": "fulltext"},
                                     headers={"X-Labhq-Token": unlimited.issue_task_token("t", "a", "r")})
        assert response.status_code == 409  # a limited MCP never runs against a runner without the ledger


@pytest.mark.parametrize("case", ["admitted", "failed", "exhausted", "broker-down", "no-token", "unset"])
async def test_annot_tools_take_one_admission_before_any_lookup_and_fail_closed(mcp_module, monkeypatch, case):
    events, original_client = [], httpx.AsyncClient

    def respond(request):
        if request.url.host == "broker.invalid":
            if case == "broker-down":
                raise httpx.ConnectError("broker unavailable", request=request)
            body = json.loads(request.content)
            if request.url.path == "/event":  # lookup progress lines, not admission
                return httpx.Response(200, json={"ok": True})
            events.append((request.url.path, body))
            if request.url.path == "/lookup/admit":
                return httpx.Response(200, json={"admitted": True, "seq": 4, "used": 4, "limit": 9}
                                      if case != "exhausted" else {"admitted": False, "limit": 9, "reason": "exhausted"})
            return httpx.Response(200, json={"recorded": True})
        events.append(("provider", request.url.host))
        return httpx.Response(503 if len([e for e in events if e[0] == "provider"]) == 1 else 200, json={})

    class Backend:
        def __init__(self, client, *args, **kwargs):
            events.append(("backend", None))
            self.client = client

        async def fulltext(self, ids):
            await self.client.get("https://provider.invalid/first")  # an internal retry is one more outbound request
            await self.client.get("https://provider.invalid/retry")
            if case == "failed":
                raise annot.LookupFailed("provider error")
            return {"ok": True}

    monkeypatch.setattr(mcp_module.httpx, "AsyncClient", lambda **kw: original_client(
        transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(mcp_module.annot_literature, "LiteratureAnnotator", Backend)
    monkeypatch.setattr(mcp_module, "LOOKUP_LIMITED", case != "unset")
    if case == "no-token":
        monkeypatch.setattr(mcp_module, "BROKER_TOKEN", "")
    if case in ("admitted", "unset"):
        assert json.loads(await mcp_module._run_literature("fulltext", ["10.1000/x"])) == {"ok": True}
    else:
        with pytest.raises(ToolError) as exc:
            await mcp_module._run_literature("fulltext", ["10.1000/x"])
        assert NOT_EVIDENCE in str(exc.value)
    kinds = [kind for kind, _ in events]
    if case == "unset":
        assert kinds == ["backend", "provider", "provider"]  # no broker call: the default is unchanged
    elif case in ("admitted", "failed"):
        assert kinds == ["/lookup/admit", "backend", "provider", "provider", "/lookup/finish"]
        assert events[0][1]["tool"] == "fulltext" and "request_id" not in events[0][1]
        finish = events[-1][1]
        assert (finish["seq"], finish["outbound_requests"], finish["outcome"]) == (
            4, 2, "ok" if case == "admitted" else "failed")
    else:  # refused before the backend exists: nothing is looked up
        assert "backend" not in kinds and "provider" not in kinds


@pytest.mark.parametrize("limit", [None, 9])
async def test_the_runner_turns_admission_on_for_every_task_when_the_limit_is_set(tmp_path, monkeypatch, limit):
    from labhq.models import Task
    from tests.test_private_paths import _capture_runner, _runner_settings
    settings = _runner_settings(tmp_path, None)
    settings.annot.request_lookup_limit = limit
    runner, seen = _capture_runner(settings, monkeypatch)
    result = await runner.run_task(Task(id="t1", agent_id="worker", request_id="r", prompt="q"))
    assert result.ok, result.error
    env = seen["ctx"].env
    if limit is None:
        assert runner.broker.lookup_admission is None and "LABHQ_LOOKUP_ADMISSION" not in env
    else:
        # In-memory settings give staff no config copy; the runner's flag still makes labhq_annot ask first.
        assert "LABHQ_CONFIG" not in env and env["LABHQ_LOOKUP_ADMISSION"] == "1"
        assert runner.broker.lookup_admission.limit == 9
        assert runner.broker.lookup_admission.path == settings.path(settings.runner.state_dir) / "lookup_admission.sqlite3"
