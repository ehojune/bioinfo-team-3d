"""#675: long computations as local jobs the runner owns: submit, finish and wake, cancel with nothing left running,
restart adoption, and the detach refusal at submit."""
import asyncio
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.policy import evaluate_tool
from labhq.runner import local_jobs
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from tests.support.time_proxy import RuntimeProxy

# The tests run Python code through the job machinery with a shell-free stand-in for the OS shell: a cold PowerShell
# takes seconds on a loaded Windows runner (#886), which the job logic under test does not need.
SLEEP = "import time; time.sleep(60)"
QUICK = "print(42)"
FAILS = "import sys; sys.exit(3)"


@pytest.fixture(autouse=True)
def _python_as_the_shell(monkeypatch):
    monkeypatch.setattr(local_jobs, "shell_argv", lambda command: [sys.executable, "-c", command])


def _eventually(check, seconds=40.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    raise AssertionError("condition not reached in time")


def _wait_for_pid(path):
    pid = None

    def ready():
        nonlocal pid
        try:
            pid = int(path.read_text())
        except (FileNotFoundError, ValueError):
            return False
        return pid > 0

    _eventually(ready)  # file creation can precede the PID write; keep the same 40-second deadline
    return pid


def _runner(tmp_path):
    s = Settings()
    for key in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(s.runner, key, str(tmp_path / key))
    s.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "staff")}
    runner = Runner(s)
    runner.registry.agents = {"worker": AgentSpec(id="worker", name="worker", role="test", engine=Engine.claude_code,
                                                  builtin_mcp=[])}
    return s, runner


def test_pid_readiness_waits_for_missing_then_empty_then_valid(monkeypatch):
    reads = [FileNotFoundError(), "", "123\n"]
    clock = iter([0.0, 0.0, 0.1, 0.2])
    sleeps = []

    def read_text():
        value = reads.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(sys.modules[__name__], "time",
                        RuntimeProxy(time, monotonic=lambda: next(clock), sleep=sleeps.append))
    assert _wait_for_pid(SimpleNamespace(read_text=read_text)) == 123
    assert not reads  # the valid PID is retained without a second file read
    assert sleeps == [0.1, 0.1]


@pytest.mark.parametrize("value", [FileNotFoundError(), "", "invalid", "0", "-1"])
def test_pid_readiness_without_a_positive_pid_keeps_the_40_second_deadline(monkeypatch, value):
    clock = iter([0.0, 0.0, 39.9, 40.0])
    reads, sleeps = [], []

    def read_text():
        reads.append(True)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(sys.modules[__name__], "time",
                        RuntimeProxy(time, monotonic=lambda: next(clock), sleep=sleeps.append))
    with pytest.raises(AssertionError, match="condition not reached in time"):
        _wait_for_pid(SimpleNamespace(read_text=read_text))
    assert len(reads) == 2
    assert sleeps == [0.1, 0.1]


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

    async def run(body, request_id="r"):
        program["run"] = body
        task = Task(agent_id="worker", request_id=request_id, prompt="work")
        return task, await runner.run_task(task)

    def submit(command, **extra):
        return lambda identity: runner._on_local_job("submit", {**identity, "command": command, "name": "calc",
                                                                 "expected_minutes": 20, **extra})

    yield SimpleNamespace(settings=s, runner=runner, events=events, cards=cards, answer=answer, run=run,
                          submit=submit, kinds=lambda: [e.type for e in events], task_id=[])
    if runner._local_watch is not None:
        runner._local_watch.cancel()
    for job_id, row in list(runner.jobs.items()):  # nothing may outlive a test
        if local_jobs.is_local(job_id):
            local_jobs.kill(row.get("local") or {}, runner._local_procs.get(job_id))


def test_the_real_shell_argv_wraps_the_command_and_a_powershell_job_reports_the_programs_exit_code(monkeypatch):
    monkeypatch.undo()  # the real shell_argv, not the stand-in
    argv = local_jobs.shell_argv("python gsea.py")
    if os.name == "nt":
        assert argv[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
        assert argv[4].startswith("python gsea.py\n") and "exit $__code" in argv[4]  # the program's code, not PowerShell's 1
    else:
        import shutil

        assert argv == [shutil.which("bash") or "/bin/sh", "-c", "python gsea.py"]  # judged as Bash, run with Bash
    assert local_jobs.shell_tool() == ("PowerShell" if os.name == "nt" else "Bash")


@pytest.mark.skipif(os.name == "nt" and bool(os.environ.get("CI")), reason="a cold PowerShell can take 30 s on a loaded runner")
def test_a_job_through_the_real_shell_reports_the_exit_code(tmp_path, monkeypatch):
    monkeypatch.undo()
    command = "exit 3" if os.name == "nt" else "exit 3"
    record, process = local_jobs.start(tmp_path, "local-shell", command, tmp_path, dict(os.environ))
    _eventually(lambda: local_jobs.status(record, process)[0] != "running", seconds=90)
    assert local_jobs.status(record, process) == ("failed", 3)


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
    command = f'print(1) > "{outside}"'  # judged as a shell command that writes outside the workspace
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


@pytest.mark.asyncio
async def test_codex_staff_cannot_submit_because_the_job_would_run_outside_their_sandbox(board):
    board.runner.registry.agents["worker"] = AgentSpec(id="worker", name="worker", role="test", engine=Engine.codex,
                                                       builtin_mcp=[])
    out = {}

    async def body(identity):
        out.update(await board.submit(QUICK)(identity))

    _, result = await board.run(body)
    assert "sandbox" in out["error"] and result.pending_jobs == []
    assert not [j for j in board.runner.jobs if local_jobs.is_local(j)]


@pytest.mark.asyncio
async def test_the_shared_environment_install_guard_applies_to_a_submitted_command(board, monkeypatch):
    seen = []

    def deny(tool, tool_input, **kwargs):
        seen.append((tool, tool_input["command"], kwargs["protected"]))
        return "no installs into the shared environment"

    monkeypatch.setattr("labhq.environment_install.shared_environment_install_denial", deny)
    out = {}

    async def body(identity):
        out.update(await board.submit(QUICK)(identity))

    await board.run(body)
    assert out == {"submitted": False, "reason": "no installs into the shared environment"}
    assert seen and seen[0][1] == QUICK
    assert not [j for j in board.runner.jobs if local_jobs.is_local(j)]


@pytest.mark.asyncio
async def test_a_cancel_that_lands_while_the_process_is_starting_still_ends_it(board, monkeypatch):
    real_start, started = local_jobs.start, []

    def start_then_cancel(*args, **kwargs):
        facts, process = real_start(*args, **kwargs)
        started.append((facts, process))
        board.runner.__dict__.setdefault("_jobs_cancelled", set()).add(board.task_id[0])  # the cancel arrives now
        return facts, process

    monkeypatch.setattr(local_jobs, "start", start_then_cancel)
    out = {}

    async def body(identity):
        board.task_id.append(identity["task_id"])
        out.update(await board.submit(SLEEP)(identity))

    await board.run(body)
    assert "cancelled" in out["error"] and len(started) == 1
    assert local_jobs.status(*started[0])[0] != "running"  # the process it had started is ended
    assert not [j for j in board.runner.jobs if local_jobs.is_local(j)]


@pytest.mark.asyncio
async def test_two_simultaneous_submits_cannot_both_take_the_last_slot(board):
    board.settings.runner.max_parallel = 1
    out = {}

    async def body(identity):
        out["both"] = await asyncio.gather(board.submit(SLEEP)(identity), board.submit(SLEEP)(identity))

    await board.run(body)
    started = [reply for reply in out["both"] if reply.get("submitted")]
    refused = [reply for reply in out["both"] if "error" in reply]
    assert len(started) == 1 and len(refused) == 1 and "already running" in refused[0]["error"]
    assert len([j for j in board.runner.jobs if local_jobs.is_local(j)]) == 1


@pytest.mark.asyncio
async def test_a_poll_that_read_the_status_before_a_cancel_does_not_overwrite_the_cancelled_job(board, monkeypatch):
    """#1144: the poll reads a job's status off the loop. A cancel that ends the job while it reads must stay the end:
    the poll wakes up holding "completed" and used to write it over "cancelled" and send a second job.state."""
    out = {}

    async def body(identity):
        out.update(await board.submit(SLEEP)(identity))

    task, _result = await board.run(body)
    job_id = out["job_id"]
    row = board.runner.jobs[job_id]
    entered, release = threading.Event(), threading.Event()

    def blocked_status(*_args, **_kwargs):
        entered.set()
        assert release.wait(20)
        return "completed", 0  # what the job looked like when the read began

    monkeypatch.setattr("labhq.runner.daemon.local_jobs.status", blocked_status)
    poller = asyncio.create_task(board.runner._poll_local_jobs())
    assert await asyncio.to_thread(entered.wait, 20)  # the poll is inside its status read
    await board.runner._on_message({"type": "task.jobs_cancel", "task_id": task.id})  # the cancel ends the job
    assert row["state"] == "cancelled" and row["terminal"]
    release.set()
    await asyncio.wait_for(poller, 20)
    assert row["state"] == "cancelled" and row["terminal"]  # the late "completed" was not written over it
    states = [e.data["state"] for e in board.events if e.type == "job.state" and e.data.get("job_id") == job_id]
    assert states == ["cancelled"], states  # one end, and it is the cancel
    assert "jobs.finished" not in board.kinds()  # a cancelled step is not woken


@pytest.mark.asyncio
async def test_cancelling_a_job_that_already_ended_keeps_how_it_ended(board):
    out = {}

    async def body(identity):
        out.update(await board.submit(QUICK)(identity))
        out["identity"] = identity

    await board.run(body)
    done = await _drive(board.runner, out["job_id"])
    assert done["state"] == "completed"
    again = await board.runner._on_local_job("cancel", {**out["identity"], "job_id": out["job_id"]})
    assert again["state"] == "completed" and again["exit_status"] == 0  # not overwritten with cancelled


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX")
def test_a_descendant_that_ignores_sigterm_does_not_outlive_the_kill(tmp_path):
    code = ("import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', "
            "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']); time.sleep(60)")
    record, process = local_jobs.start(tmp_path, "local-group", code, tmp_path, dict(os.environ))
    assert local_jobs.kill(record, process) is True
    assert not local_jobs._group_alive(record["pid"])  # the whole group is gone, not only the leader


@pytest.mark.asyncio
async def test_a_reply_lost_after_submit_tells_the_agent_to_check_before_resubmitting(monkeypatch):
    import httpx

    from labhq.tools import local_job_mcp

    class Lossy:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("the reply never came")

    monkeypatch.setattr(local_job_mcp.httpx, "AsyncClient", Lossy)
    with pytest.raises(Exception, match="check job_list before submitting it again"):
        await local_job_mcp._broker("submit", {"command": "x"})
    with pytest.raises(Exception) as other:
        await local_job_mcp._broker("status", {"job_id": "local-x"})
    assert "job_list" not in str(other.value)  # only a submit can have started something


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


@pytest.mark.asyncio
async def test_a_jobs_cancel_the_runner_has_not_confirmed_is_kept_and_sent_again(tmp_path):
    from labhq.gateway.server import create_app
    from labhq.gateway.server import RunnerUnavailable

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path)
    hub = create_app(settings).state.hub
    hub.requests["req"] = {"id": "req", "status": "running", "text": "t", "plan": {"steps": [{"id": "s1"}]},
                           "mode": "orchestrate", "created_at": time.time()}
    hub.store.put("task", "parked", {"completed": True, "request_id": "req", "step_id": "s1", "runner_id": "r1",
                                     "payload": {"agent_id": "worker"}})
    sent, online = [], {"up": False}

    async def send(runner_id, message):
        if not online["up"]:
            raise RunnerUnavailable(runner_id)
        sent.append((runner_id, message["type"], message["task_id"]))

    hub.send_runner = send
    await hub.cancel_request("req")
    assert sent == [] and hub.store.get("jobs_cancel", "parked")["runner_id"] == "r1"  # the runner was away: kept
    online["up"] = True
    await hub.flush_task_cancels("r1")  # it reconnects
    assert sent == [("r1", "task.jobs_cancel", "parked")]
    await hub.on_runner_message("r1", {"type": "task.jobs_cancelled", "task_id": "parked"})
    assert hub.store.get("jobs_cancel", "parked") is None  # confirmed: no more resending
    await hub.flush_task_cancels("r1")
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_the_runner_confirms_a_jobs_cancel_durably(board):
    out = {}

    async def body(identity):
        out.update(await board.submit(SLEEP)(identity))

    task, _ = await board.run(body)
    await board.runner._on_message({"type": "task.jobs_cancel", "task_id": task.id})
    queued = [row for row in board.runner.store.pending() if row.get("type") == "task.jobs_cancelled"]
    assert [row["task_id"] for row in queued] == [task.id]
    assert board.runner.jobs[out["job_id"]]["state"] == "cancelled"


def test_a_job_whose_identity_cannot_be_read_is_ended_not_left_running(tmp_path, monkeypatch):
    started = []
    real_popen = local_jobs.subprocess.Popen

    def recording_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        started.append(process)
        return process

    def refuse(pid):
        raise RuntimeError("access denied")

    monkeypatch.setattr(local_jobs.subprocess, "Popen", recording_popen)
    monkeypatch.setattr(local_jobs, "_created", refuse)
    with pytest.raises(OSError, match="identity could not be read"):
        local_jobs.start(tmp_path, "local-noid", SLEEP, tmp_path, dict(os.environ))
    assert len(started) == 1
    _eventually(lambda: started[0].poll() is not None)  # the process it had started is gone


@pytest.mark.asyncio
async def test_a_cancel_whose_processes_survive_is_not_recorded_or_confirmed(board, monkeypatch):
    out = {}

    async def body(identity):
        out.update(await board.submit(SLEEP)(identity))
        out["identity"] = identity

    task, _ = await board.run(body)
    job_id, real_kill = out["job_id"], local_jobs.kill
    monkeypatch.setattr(local_jobs, "kill", lambda local, process=None: False)  # the tree would not die
    reply = await board.runner._on_local_job("cancel", {**out["identity"], "job_id": job_id})
    assert "could not be ended" in reply["error"] and not board.runner.jobs[job_id]["terminal"]
    await board.runner._on_message({"type": "task.jobs_cancel", "task_id": task.id})
    assert not [row for row in board.runner.store.pending() if row.get("type") == "task.jobs_cancelled"]  # not confirmed
    assert board.runner.jobs[job_id]["state"] == "running"  # the watcher keeps checking it
    monkeypatch.setattr(local_jobs, "kill", real_kill)  # then it can still be ended
    await board.runner._on_message({"type": "task.jobs_cancel", "task_id": task.id})
    assert board.runner.jobs[job_id]["state"] == "cancelled"
    assert [row["task_id"] for row in board.runner.store.pending() if row.get("type") == "task.jobs_cancelled"] == [task.id]


@pytest.mark.asyncio
async def test_a_start_time_cancel_that_cannot_end_the_tree_keeps_the_job_for_the_watcher(board, monkeypatch):
    real_start, real_kill, started = local_jobs.start, local_jobs.kill, []

    def start_then_cancel(*args, **kwargs):
        facts, process = real_start(*args, **kwargs)
        started.append((facts, process))
        board.runner.__dict__.setdefault("_jobs_cancelled", set()).add(board.task_id[0])
        return facts, process

    monkeypatch.setattr(local_jobs, "start", start_then_cancel)
    monkeypatch.setattr(local_jobs, "kill", lambda local, process=None: False)  # the tree would not die
    out = {}

    async def body(identity):
        board.task_id.append(identity["task_id"])
        out.update(await board.submit(SLEEP)(identity))

    await board.run(body)
    assert "keeps trying" in out["error"]
    rows = [j for jid, j in board.runner.jobs.items() if local_jobs.is_local(jid)]
    assert len(rows) == 1 and not rows[0]["terminal"] and rows[0]["cancel_requested"]  # tracked, not ownerless
    monkeypatch.setattr(local_jobs, "kill", real_kill)
    await board.runner._poll_local_jobs()  # the watcher ends it
    assert rows[0]["state"] == "cancelled" and rows[0]["terminal"]
    assert local_jobs.status(*started[0])[0] != "running"


@pytest.mark.asyncio
async def test_a_failed_kill_is_retried_by_the_watcher_until_the_tree_is_gone(board, monkeypatch):
    out = {}

    async def body(identity):
        out.update(await board.submit(SLEEP)(identity))

    task, _ = await board.run(body)
    job_id, real_kill = out["job_id"], local_jobs.kill
    monkeypatch.setattr(local_jobs, "kill", lambda local, process=None: False)
    await board.runner._on_message({"type": "task.cancel", "task_id": task.id})  # the result of this is not enough
    row = board.runner.jobs[job_id]
    assert not row["terminal"] and row["cancel_requested"]
    assert board.runner.store.get("job", job_id)["cancel_requested"]  # kept with the row: it survives a restart
    monkeypatch.setattr(local_jobs, "kill", real_kill)
    await board.runner._poll_local_jobs()
    assert row["state"] == "cancelled" and row["terminal"]


def test_a_kept_jobs_cancel_outlives_the_longest_local_job():
    from labhq.gateway import server

    assert server.JOBS_CANCEL_KEEP_S > 7 * 86400


@pytest.mark.asyncio
async def test_offline_request_cancel_replayed_to_restarted_runner_ends_only_its_job(board, tmp_path):
    """Real, inert Python process trees; gateway/runner transport is an in-memory stand-in."""
    from labhq.gateway.server import create_app, RunnerUnavailable

    submitted = []
    child_file = tmp_path / "cancelled-child.pid"

    async def body(identity):
        command = (f"import os, pathlib; pathlib.Path({str(child_file)!r}).write_text(str(os.getpid())); " + SLEEP
                   if identity["request_id"] == "cancel-me" else SLEEP)
        submitted.append(await board.submit(command)(identity))

    cancelled, _ = await board.run(body, request_id="cancel-me")
    preserved, _ = await board.run(body, request_id="keep-me")
    ended_pid = board.runner.jobs[submitted[0]["job_id"]]["local"]["pid"]
    child_pid = _wait_for_pid(child_file)
    tree = [(pid, local_jobs._created(pid)) for pid in [ended_pid, child_pid]]
    assert all(created for _, created in tree)
    board.runner._local_watch.cancel()
    await asyncio.gather(board.runner._local_watch, return_exceptions=True)
    board.runner.store.close()
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "gateway")
    hub = create_app(settings).state.hub
    for task in (cancelled, preserved):
        hub.requests[task.request_id] = {
            "id": task.request_id, "status": "running", "text": "test",
            "mode": "orchestrate", "created_at": time.time(), "plan": {"steps": [{"id": "s1"}]},
        }
        hub.store.put("task", task.id, {"completed": True, "request_id": task.request_id,
                                       "step_id": "s1", "runner_id": board.settings.runner.id,
                                       "payload": {"agent_id": "worker"}})

    async def offline(*args):
        raise RunnerUnavailable("offline")

    hub.send_runner = offline
    await hub.cancel_request("cancel-me")
    assert hub.store.get("jobs_cancel", cancelled.id)
    assert hub.store.get("jobs_cancel", preserved.id) is None
    _, recovered = _runner(tmp_path)
    ended, kept = [item["job_id"] for item in submitted]
    try:
        await recovered._poll_local_jobs()  # failed connection does not itself cancel either job
        assert all(not recovered.jobs[jid]["terminal"] for jid in (ended, kept))

        async def reconnected(runner_id, message):
            assert runner_id == board.settings.runner.id
            await recovered._on_message(message)

        hub.send_runner = reconnected
        await hub.flush_task_cancels(board.settings.runner.id)
        await recovered._poll_local_jobs()
        assert recovered.jobs[ended]["state"] == "cancelled"
        local = recovered.jobs[ended]["local"]
        assert not local_jobs.alive(local["pid"], local["created"])
        assert all(not local_jobs.alive(pid, created) for pid, created in tree)
        assert recovered.jobs[kept]["state"] == "running"
        assert not recovered.jobs[kept]["terminal"]
        assert not [event for event in recovered.store.pending() if event.get("type") == "jobs.finished"]
        assert cancelled.id in recovered.notified
    finally:
        for jid, row in recovered.jobs.items():
            local_jobs.kill(row["local"], recovered._local_procs.get(jid))
        recovered.store.close()
        hub.store.close()
