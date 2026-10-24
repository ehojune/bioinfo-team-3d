"""#675: long computations as local jobs the runner owns: submit, finish and wake, cancel with nothing left running,
restart adoption, and the detach refusal at submit."""
import asyncio
import os
import sys
import time
from types import SimpleNamespace

import pytest

from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.policy import evaluate_tool
from labhq.runner import local_jobs
from labhq.runner.daemon import Runner
from labhq.settings import Settings

PY = f'"{sys.executable}"'
CALL = "& " if os.name == "nt" else ""  # PowerShell needs the call operator to run a quoted program path
SLEEP = f'{CALL}{PY} -c "import time; time.sleep(60)"'
QUICK = f'{CALL}{PY} -c "print(42)"'
FAILS = f'{CALL}{PY} -c "import sys; sys.exit(3)"'


def _eventually(check, seconds=40.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    raise AssertionError("condition not reached in time")


def _runner(tmp_path):
    s = Settings()
    for key in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(s.runner, key, str(tmp_path / key))
    s.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "staff")}
    runner = Runner(s)
    runner.registry.agents = {"worker": AgentSpec(id="worker", name="worker", role="test", engine=Engine.claude_code,
                                                  builtin_mcp=[])}
    return s, runner


async def _drive(runner, job_id, seconds=40.0):
    """Poll the way the watch loop does until the job is over."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await runner._poll_local_jobs()
        if runner.jobs[job_id]["terminal"]:
            return runner.jobs[job_id]
        await asyncio.sleep(0.2)
    raise AssertionError("job did not end in time")


@pytest.fixture
def board(tmp_path, monkeypatch):
    s, runner = _runner(tmp_path)
    events, cards, answer = [], [], {"approved": True}
    program = {}
    monkeypatch.setattr("labhq.doctor._paper2agent_row", lambda *a, **kw: {"status": "ok"})

    async def approve(req):
        cards.append(req)
        return dict(answer)

    monkeypatch.setattr(runner.broker, "request_approval", approve)
    original = runner.emit_all

    async def record(batch, **kwargs):
        events.extend(batch)
        return await original(batch, **kwargs)

    monkeypatch.setattr(runner, "emit_all", record)

    class Adapter:
        async def run(self, ctx):
            identity = {"task_id": ctx.task.id, "agent_id": ctx.agent.id, "request_id": ctx.task.request_id}
            await program["run"](identity)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, cost_usd=0, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *a: Adapter())

    async def run(body):
        program["run"] = body
        task = Task(agent_id="worker", request_id="r", prompt="work")
        return task, await runner.run_task(task)

    def submit(command, **extra):
        return lambda identity: runner._on_local_job("submit", {**identity, "command": command, "name": "calc",
                                                                 "expected_minutes": 20, **extra})

    yield SimpleNamespace(settings=s, runner=runner, events=events, cards=cards, answer=answer, run=run,
                          submit=submit, kinds=lambda: [e.type for e in events])
    if runner._local_watch is not None:
        runner._local_watch.cancel()
    for job_id, row in list(runner.jobs.items()):  # nothing may outlive a test
        if local_jobs.is_local(job_id):
            local_jobs.kill(row.get("local") or {}, runner._local_procs.get(job_id))


def test_the_process_helpers_report_completion_failure_and_leave_nothing_after_a_kill(tmp_path):
    env = dict(os.environ)
    ok, ok_proc = local_jobs.start(tmp_path, "local-ok", QUICK, tmp_path, env)
    _eventually(lambda: local_jobs.status(ok, ok_proc)[0] != "running")
    assert local_jobs.status(ok, ok_proc) == ("completed", 0) and "42" in local_jobs.tail(ok["out"])
    bad, bad_proc = local_jobs.start(tmp_path, "local-bad", FAILS, tmp_path, env)
    _eventually(lambda: local_jobs.status(bad, bad_proc)[0] != "running")
    assert local_jobs.status(bad, bad_proc) == ("failed", 3)
    slow, slow_proc = local_jobs.start(tmp_path, "local-slow", SLEEP, tmp_path, env)
    assert local_jobs.status(slow, slow_proc)[0] == "running" and local_jobs.alive(slow["pid"], slow["created"])
    assert local_jobs.kill(slow, slow_proc)
    assert local_jobs.status(slow, slow_proc)[0] != "running"  # nothing of it is left running


@pytest.mark.asyncio
async def test_a_submitted_job_parks_its_step_then_finishes_and_wakes_it(board):
    out = {}

    async def body(identity):
        out.update(await board.submit(QUICK)(identity))

    task, result = await board.run(body)
    job_id = out["job_id"]
    assert out["submitted"] is True and result.ok and result.pending_jobs == [job_id]
    row = board.runner.jobs[job_id]
    assert row["scheduler"] == "local" and row["submitted_by_runner"] and row["local"]["expected_minutes"] == 20
    assert "job.submitted" in board.kinds()
    done = await _drive(board.runner, job_id)
    assert done["state"] == "completed" and done["exit_status"] == 0
    woke = [e for e in board.events if e.type == "jobs.finished"]
    assert len(woke) == 1 and woke[0].task_id == task.id and woke[0].data["jobs"][0]["state"] == "completed"


@pytest.mark.asyncio
async def test_a_failing_job_ends_failed_with_its_exit_status(board):
    out = {}

    async def body(identity):
        out.update(await board.submit(FAILS)(identity))

    await board.run(body)
    done = await _drive(board.runner, out["job_id"])
    assert (done["state"], done["exit_status"]) == ("failed", 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["task.jobs_cancel", "task.cancel"])
async def test_cancelling_the_step_ends_the_job_leaves_nothing_running_and_does_not_wake(board, message):
    out = {}

    async def body(identity):
        out.update(await board.submit(SLEEP)(identity))

    task, result = await board.run(body)
    job_id = out["job_id"]
    row = board.runner.jobs[job_id]
    proc = board.runner._local_procs[job_id]
    assert local_jobs.status(row["local"], proc)[0] == "running"
    await board.runner._on_message({"type": message, "task_id": task.id})
    assert row["state"] == "cancelled" and row["terminal"]
    assert local_jobs.status(row["local"], proc)[0] != "running"  # the process tree is gone
    await board.runner._poll_local_jobs()
    assert "jobs.finished" not in board.kinds()  # a cancelled step is not resumed


@pytest.mark.asyncio
async def test_the_agent_sees_status_with_logs_lists_and_cancels_its_own_job(board):
    seen = {}

    async def body(identity):
        out = await board.submit(SLEEP)(identity)
        job_id = out["job_id"]
        seen["status"] = await board.runner._on_local_job("status", {**identity, "job_id": job_id})
        seen["list"] = await board.runner._on_local_job("list", identity)
        seen["other"] = await board.runner._on_local_job("cancel", {**identity, "agent_id": "someone-else",
                                                                    "job_id": job_id})
        seen["cancel"] = await board.runner._on_local_job("cancel", {**identity, "job_id": job_id})
        seen["job_id"] = job_id

    await board.run(body)
    assert seen["status"]["state"] == "running" and "stdout_tail" in seen["status"]
    assert [row["job_id"] for row in seen["list"]["jobs"]] == [seen["job_id"]]
    assert "error" in seen["other"]  # a job is cancelled only by the agent that submitted it
    assert seen["cancel"]["state"] == "cancelled"


@pytest.mark.asyncio
async def test_a_restarted_runner_adopts_a_running_job_and_can_still_end_it(board, tmp_path):
    out = {}

    async def body(identity):
        out.update(await board.submit(SLEEP)(identity))

    task, _ = await board.run(body)
    job_id = out["job_id"]
    board.runner.store.close()  # the runner stops; its job keeps running on its own
    _, second = _runner(tmp_path)
    assert second._local_procs == {} and second.jobs[job_id]["state"] != "completed"
    await second._poll_local_jobs()
    assert second.jobs[job_id]["state"] == "running" and not second.jobs[job_id]["terminal"]  # adopted by identity
    await second._cancel_local_jobs(task.id)
    assert second.jobs[job_id]["state"] == "cancelled"
    local = second.jobs[job_id]["local"]
    assert not local_jobs.alive(local["pid"], local["created"])
    second.store.close()


@pytest.mark.asyncio
async def test_a_restarted_runner_reads_how_a_job_ended_from_its_exit_file(board, tmp_path):
    out = {}

    async def body(identity):
        out.update(await board.submit(QUICK)(identity))

    task, _ = await board.run(body)
    job_id = out["job_id"]
    _eventually(lambda: os.path.exists(board.runner.jobs[job_id]["local"]["exit_file"]))
    board.runner.store.close()
    _, second = _runner(tmp_path)
    done = await _drive(second, job_id)
    assert (done["state"], done["exit_status"]) == ("completed", 0)
    second.store.close()


@pytest.mark.asyncio
async def test_a_detaching_command_is_refused_at_submit_without_a_card_or_a_process(board):
    out = {}

    async def body(identity):
        out.update(await board.submit("nohup python gsea.py > log 2>&1 &")(identity))

    _, result = await board.run(body)
    assert out["submitted"] is False and "job_submit" in out["reason"]
    assert board.cards == [] and result.pending_jobs == []
    assert not [j for j in board.runner.jobs if local_jobs.is_local(j)]


@pytest.mark.asyncio
async def test_submit_needs_a_running_task_a_command_and_a_free_slot(board):
    assert "not running" in (await board.runner._on_local_job(
        "submit", {"task_id": "nobody", "agent_id": "worker", "command": QUICK}))["error"]
    seen = {}
    board.settings.runner.max_parallel = 1

    async def body(identity):
        seen["empty"] = await board.runner._on_local_job("submit", {**identity, "command": "  "})
        seen["bad_minutes"] = await board.submit(QUICK, expected_minutes="soon")(identity)
        seen["first"] = await board.submit(SLEEP)(identity)
        seen["second"] = await board.submit(SLEEP)(identity)

    await board.run(body)
    assert "command" in seen["empty"]["error"] and "expected_minutes" in seen["bad_minutes"]["error"]
    assert seen["first"]["submitted"] is True and "already running" in seen["second"]["error"]


@pytest.mark.asyncio
async def test_a_command_the_gate_would_ask_about_waits_for_the_pi_and_a_refusal_starts_nothing(board, tmp_path):
    outside = tmp_path / "elsewhere" / "x.txt"
    command = f'{PY} -c "open(r\'{outside}\', \'w\').write(\'x\')" > "{outside}"'
    probe = evaluate_tool(local_jobs.shell_tool(), {"command": command}, board.settings.policy,
                          allowed_roots=[str(tmp_path / "workspace_root")], workdir=str(tmp_path / "workspace_root"))
    if probe.action != "ask":
        pytest.skip("this platform's gate does not ask about a write outside the workspace")
    board.answer["approved"] = False
    out = {}

    async def body(identity):
        out.update(await board.submit(command)(identity))

    await board.run(body)
    assert out["submitted"] is False and len(board.cards) == 1 and board.cards[0].kind == "tool_permission"
    assert not [j for j in board.runner.jobs if local_jobs.is_local(j)]


def test_the_job_tool_is_wired_for_agents_that_list_it_even_without_an_hpc_scheduler(tmp_path):
    from labhq.gateway.server import Hub
    from labhq.tools import local_job_mcp

    s, runner = _runner(tmp_path)
    s.hpc.scheduler = "none"
    agent = AgentSpec(id="analyst2", name="a", role="test", engine=Engine.claude_code, builtin_mcp=["approval", "job", "hpc"])
    names = [server.name for server in runner._mcp_servers(agent, {})]
    assert "labhq_job" in names and "labhq_hpc" not in names
    assert "labhq_job" in Hub._agent_mcp({"builtin_mcp": ["approval", "job"], "engine": "claude_code"})
    plain = AgentSpec(id="plain", name="p", role="test", engine=Engine.claude_code, builtin_mcp=["approval"])
    assert "labhq_job" not in [server.name for server in runner._mcp_servers(plain, {})]
    assert all(callable(getattr(local_job_mcp, tool, None)) for tool in ("job_submit", "job_status", "job_list", "job_cancel"))
    runner.store.close()


@pytest.mark.asyncio
async def test_the_broker_endpoint_takes_identity_from_the_token_and_submits_for_that_task(board):
    import httpx

    out = {}

    async def body(identity):
        token = board.runner.broker.issue_task_token(identity["task_id"], identity["agent_id"], identity["request_id"])
        transport = httpx.ASGITransport(app=board.runner.broker.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
            headers = {"X-Labhq-Token": token}
            out["unknown_op"] = (await client.post("/localjobs/explode", json={}, headers=headers)).status_code
            out["bad_token"] = (await client.post("/localjobs/list", json={}, headers={"X-Labhq-Token": "nope"})).status_code
            out["other_task"] = (await client.post("/localjobs/list", json={"task_id": "someone-else"},
                                                   headers=headers)).status_code
            reply = await client.post("/localjobs/submit", json={"command": QUICK, "name": "via-broker"}, headers=headers)
            out["submit"] = reply.json()
            listed = await client.post("/localjobs/list", json={}, headers=headers)
            out["list"] = listed.json()
        board.runner.broker.revoke_task_token(token)

    _, result = await board.run(body)
    assert (out["unknown_op"], out["bad_token"], out["other_task"]) == (404, 401, 403)
    assert out["submit"]["submitted"] is True and result.pending_jobs == [out["submit"]["job_id"]]
    assert [row["job_id"] for row in out["list"]["jobs"]] == [out["submit"]["job_id"]]


@pytest.mark.asyncio
async def test_cancelling_a_request_tells_the_runner_to_end_the_jobs_of_a_step_parked_on_them(tmp_path):
    from labhq.gateway.server import create_app

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    hub = create_app(settings).state.hub
    hub.requests["req"] = {"id": "req", "status": "running", "text": "t", "plan": {"steps": [{"id": "s1"}]},
                           "mode": "orchestrate", "created_at": time.time()}
    hub.store.put("task", "parked", {"completed": True, "request_id": "req", "step_id": "s1", "runner_id": "r1",
                                     "payload": {"agent_id": "worker"}})
    sent = []

    async def send(runner_id, message):
        sent.append((runner_id, message))

    hub.send_runner = send
    await hub.cancel_request("req")
    kinds = {(message["type"], message["task_id"]) for _runner, message in sent}
    assert ("task.jobs_cancel", "parked") in kinds  # no running task to cancel: its jobs are ended directly
    assert ("task.cancel", "parked") not in kinds  # it reported a result: only its jobs remain
