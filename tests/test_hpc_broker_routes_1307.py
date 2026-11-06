"""Authenticated scheduler routes and complete call budgets; no network, process or HPC access."""
import asyncio
import subprocess
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from labhq.hpc_session import QUEUE_WAIT_S, SessionConfig, call_budget_s, worst_case_s
from labhq.runner.approvals import Broker
from labhq.runner.daemon import Runner, labhq_mcp_timeout_s
from labhq.settings import Settings
from labhq.tools import scheduler as sched


def job(agent="a1", task="t1", request="r1", **fields):
    return {"agent_id": agent, "task_id": task, "request_id": request,
            "submitted_by_runner": True, "terminal": False, **fields}


@pytest.fixture
def routes(tmp_path, monkeypatch):
    runner = Runner.__new__(Runner)
    runner.s = Settings.model_validate({"hpc": {"scheduler": "slurm", "user": "fixture"}})
    runner.workspaces = {"t1": object()}
    runner.active_roots = {"t1": (False,)}
    runner.jobs = {"1": job(), "2": job(agent="other"), "3": job(terminal=True),
                   "4": job(task="previous"), "5": job(task="other", request="other"),
                   "6": job(submitted_by_runner=False), "7": job(request=None, task="other"),
                   "8": job(), "local-fixture": job()}
    calls = []

    def status(jid):
        calls.append(("status", jid))
        return sched.JobInfo(job_id=jid, state="completed" if jid == "3" else "running")

    def queue():
        calls.append(("queue",))
        return [sched.JobInfo(job_id=jid, state="running") for jid in ("1", "2", "4", "5", "6", "7", "8_2")]

    def cancel(jid):
        calls.append(("cancel", jid))
        return "ok"

    runner.scheduler = SimpleNamespace(status=status, queue=queue, cancel=cancel, redactor=lambda: lambda s: s)
    submitted = []

    async def submit(body):
        submitted.append(body)
        return {"submitted": True, "job_id": "9"}

    async def unused(_):
        raise AssertionError("unexpected approval or event")

    broker = Broker(0, unused, unused, submit, owns_job=runner._owns_job,
                    on_scheduler=runner._on_scheduler_request)
    token = broker.issue_task_token("t1", "a1", "r1")
    with TestClient(broker.app) as client:
        yield SimpleNamespace(client=client, headers={"X-Labhq-Token": token}, broker=broker,
                              runner=runner, calls=calls, submitted=submitted, token=token)


@pytest.mark.parametrize("op", ["status", "queue", "cancel"])
@pytest.mark.parametrize("token", ["", "wrong"])
def test_every_route_requires_a_live_token(routes, op, token):
    response = routes.client.post(f"/jobs/scheduler/{op}", json={"job_id": "1"},
                                  headers={"X-Labhq-Token": token})
    assert response.status_code == 401 and routes.calls == []


@pytest.mark.parametrize("op", ["status", "queue", "cancel"])
@pytest.mark.parametrize("field", ["task_id", "agent_id", "request_id"])
def test_every_route_rejects_forged_identity(routes, op, field):
    response = routes.client.post(f"/jobs/scheduler/{op}", json={field: "other", "job_id": "1"},
                                  headers=routes.headers)
    assert response.status_code == 403 and routes.calls == []


@pytest.mark.parametrize("op", ["status", "cancel"])
@pytest.mark.parametrize("jid", ["2", "5", "6", "7", "999", "local-fixture"])
def test_status_and_cancel_refuse_unowned_jobs_before_scheduler_access(routes, op, jid):
    response = routes.client.post(f"/jobs/scheduler/{op}", json={"job_id": jid}, headers=routes.headers)
    assert response.status_code == 200 and "was not submitted by you" in response.json()["error"]
    assert routes.calls == []


@pytest.mark.parametrize("op,key", [("status", "status"), ("cancel", "result")])
@pytest.mark.parametrize("jid", ["1", "4", "8_2"])
def test_owned_task_request_and_array_jobs_reach_scheduler(routes, op, key, jid):
    response = routes.client.post(f"/jobs/scheduler/{op}", json={"job_id": jid}, headers=routes.headers)
    assert response.status_code == 200 and key in response.json()
    assert routes.calls == [(op, jid)]


def test_completed_jobs_are_readable_but_cannot_be_cancelled(routes):
    assert routes.client.post("/jobs/scheduler/status", json={"job_id": "3"},
                              headers=routes.headers).json()["status"]["state"] == "completed"
    assert "error" in routes.client.post("/jobs/scheduler/cancel", json={"job_id": "3"},
                                        headers=routes.headers).json()
    assert routes.calls == [("status", "3")]


def test_queue_returns_only_runner_recorded_owned_jobs_and_array_tasks(routes):
    response = routes.client.post("/jobs/scheduler/queue", json={}, headers=routes.headers)
    assert [j["job_id"] for j in response.json()["queue"]] == ["1", "4", "8_2"]
    assert routes.calls == [("queue",)]


def test_queue_ownership_runs_on_event_loop_during_concurrent_submission(routes, monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), threading.Event()
        loop, owner_thread = asyncio.get_running_loop(), threading.get_ident()
        owns = routes.runner._hpc_job_owned
        checked = []

        def ownership(*args, **kwargs):
            checked.append(threading.get_ident())
            assert threading.get_ident() == owner_thread
            return owns(*args, **kwargs)

        def queue():
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
            return [sched.JobInfo(job_id="10", state="running"), sched.JobInfo(job_id="2", state="running")]

        async def submit():
            await entered.wait()
            routes.runner.jobs["10"] = job()
            release.set()

        monkeypatch.setattr(routes.runner, "_hpc_job_owned", ownership)
        monkeypatch.setattr(routes.runner.scheduler, "queue", queue)
        _, reply = await asyncio.gather(submit(), routes.runner._on_scheduler_request(
            "queue", {"task_id": "t1", "agent_id": "a1", "request_id": "r1"}))
        assert [j["job_id"] for j in reply["queue"]] == ["10"]
        assert checked == [owner_thread, owner_thread]

    asyncio.run(scenario())


@pytest.mark.parametrize("op", ["status", "queue", "cancel"])
@pytest.mark.parametrize("state", ["read_only", "ended"])
def test_all_scheduler_routes_require_a_running_writable_task(routes, op, state):
    routes.runner.active_roots = {"t1": (True,)} if state == "read_only" else {}
    response = routes.client.post(f"/jobs/scheduler/{op}", json={"job_id": "1"}, headers=routes.headers)
    assert "error" in response.json() and routes.calls == []


@pytest.mark.parametrize("op", ["status", "queue", "cancel", "", None, False])
def test_submit_rejects_the_legacy_op_field_without_dispatch(routes, op):
    response = routes.client.post("/jobs/submit", json={"op": op, "job_id": "1"}, headers=routes.headers)
    assert response.status_code == 400 and routes.calls == routes.submitted == []
    assert "error" in asyncio.run(routes.runner._on_submit_raw({"op": op}))


def test_submit_still_dispatches_with_token_identity(routes):
    response = routes.client.post("/jobs/submit", json={"script": "fixture.sh"}, headers=routes.headers)
    assert response.json() == {"submitted": True, "job_id": "9"}
    assert routes.submitted[0]["task_id"] == "t1" and routes.submitted[0]["agent_id"] == "a1"
    assert routes.submitted[0]["request_id"] == "r1" and routes.calls == []


def test_route_operation_cannot_be_overridden_and_unknown_routes_fail(routes):
    assert routes.client.post("/jobs/scheduler/status", json={"op": "cancel", "job_id": "1"},
                              headers=routes.headers).status_code == 400
    assert routes.client.post("/jobs/scheduler/submit", json={}, headers=routes.headers).status_code == 404
    routes.broker.revoke_task_token(routes.token)
    assert routes.client.post("/jobs/scheduler/queue", json={}, headers=routes.headers).status_code == 401
    assert routes.calls == []


def test_dedicated_route_hides_scheduler_error_text(routes):
    routes.runner.scheduler.redactor = lambda: lambda text: text.replace("fixture-host", "<host>")

    def fail(_):
        raise RuntimeError("fixture-host unavailable")

    routes.runner.scheduler.status = fail
    response = routes.client.post("/jobs/scheduler/status", json={"job_id": "1"}, headers=routes.headers)
    assert response.json() == {"error": "<host> unavailable"}


@pytest.mark.parametrize("attempts", [1, 2, 4])
@pytest.mark.parametrize("commands", [1, 2])
def test_each_command_gets_queue_reconnect_and_execution_budget(attempts, commands):
    cfg = SessionConfig(argv=["fixture"], reconnect_attempts=attempts)
    timeout = 60
    elapsed = 0
    # Deterministic clock: both status commands can wait for the shell and reconnect independently.
    for _ in range(commands):
        elapsed += QUEUE_WAIT_S + worst_case_s(cfg, timeout)
    budget = sched.scheduler_call_budget_s(cfg, timeout, commands)
    assert budget == elapsed
    if commands == 2 and attempts > 1:
        assert call_budget_s(cfg, timeout, commands) < elapsed  # reproduce #1306's undercount
    assert sched.scheduler_call_budget_s(None, timeout, commands) == commands * timeout


def test_default_status_reproduces_450_vs_375_seconds_and_tool_waits_480(monkeypatch):
    from labhq.tools import hpc_mcp as hpc

    cfg = SessionConfig(argv=["fixture"])
    assert call_budget_s(cfg, 60, 2) == 345  # old tool added 30: 375 < runner's 450
    assert sched.scheduler_call_budget_s(cfg, 60, 2) == 450
    monkeypatch.setattr(hpc, "S", Settings())
    monkeypatch.setattr(hpc, "SCHED", SimpleNamespace(session=lambda: SimpleNamespace(cfg=cfg)))
    monkeypatch.setattr(hpc, "SESSION_ERROR", None)
    seen = []
    elapsed = 0
    scheduler = sched.Scheduler(Settings.model_validate({"hpc": {"scheduler": "slurm", "user": "fixture"}}).hpc)
    commands = []

    def run(args):
        nonlocal elapsed
        elapsed += QUEUE_WAIT_S + worst_case_s(cfg, 60)
        commands.append(args[0])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(scheduler, "_run", run)

    async def broker(path, payload, timeout):
        seen.append((path, timeout))
        status = scheduler.status(payload["job_id"])
        assert timeout > elapsed
        return {"status": status.to_dict()}

    monkeypatch.setattr(hpc, "_broker", broker)
    asyncio.run(hpc._runner_op("status", job_id="1"))
    assert seen == [("/jobs/scheduler/status", 480)]
    assert commands == ["squeue", "sacct"] and elapsed == 450


def test_unreadable_profile_does_not_break_staff_cli_timeout_configuration(monkeypatch):
    def unavailable(_):
        raise RuntimeError("profile unavailable")

    monkeypatch.setattr(sched.Scheduler, "session", unavailable)
    settings = Settings.model_validate({"hpc": {"site_profile": "fixture", "scheduler": "slurm"}})
    assert labhq_mcp_timeout_s(settings) >= 450 + 90


def test_staff_cli_includes_the_actual_profiles_command_budget(monkeypatch):
    cfg = SessionConfig(argv=["fixture"], reconnect_attempts=4, connect_timeout_s=90)
    monkeypatch.setattr(sched.Scheduler, "session", lambda _: SimpleNamespace(cfg=cfg))
    settings = Settings.model_validate({"hpc": {"site_profile": "fixture", "scheduler": "slurm"}})
    assert labhq_mcp_timeout_s(settings) >= sched.scheduler_call_budget_s(cfg, 60, 2) + 90
