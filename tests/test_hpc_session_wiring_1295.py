"""The HPC session wired into the scheduler, the runner and the tools (#1295 C), against the fake gateway. No real HPC."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from labhq import hpc_login_budget as hb
from labhq import hpc_site
from labhq.settings import Settings
from labhq.tools import scheduler as sched

GATEWAY = str(Path(__file__).with_name("fake_hpc_gateway.py"))


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A site profile whose connect command is the fake gateway (a Slurm site with no remote command)."""
    monkeypatch.setenv("LABHQ_SITES_DIR", str(tmp_path / "sites"))
    counter, state = tmp_path / "starts.txt", tmp_path / "slurm.json"

    def make(*flags, **profile):
        argv = [sys.executable, "-I", GATEWAY, "--slurm-state", str(state), "--counter", str(counter), *flags]
        hpc_site.save_profile(hpc_site.SiteProfile(name="fake", access_method="gateway_tunnel", connect_argv=argv,
                                                   remote_exec=False, scheduler="slurm", **profile))
        return SimpleNamespace(counter=counter, state=state, logins=lambda: len(counter.read_text().split())
                               if counter.exists() else 0)

    yield make
    sched.close_sessions()


def settings(tmp_path, **hpc):
    return Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state")},
                                    "hpc": {"scheduler": "slurm", "user": "lab", "site_profile": "fake", **hpc}})


def finish(state, job_id, final="COMPLETED", exit_code="0:0"):
    data = json.loads(Path(state).read_text())
    data["jobs"][job_id].update(state=final, exit=exit_code)
    Path(state).write_text(json.dumps(data))


def test_submit_status_queue_cancel_all_use_one_login(tmp_path, site):
    fake = site()
    scheduler = sched.Scheduler(settings(tmp_path).hpc)
    assert scheduler.needs_login()  # nothing is open yet
    job = scheduler.submit("/work/job.sh", "align", stdout="/work/o.log", stderr="/work/e.log")
    assert job == "1000" and not scheduler.needs_login()
    assert scheduler.status(job).state == "running"
    assert [j.job_id for j in scheduler.queue()] == [job]
    other = scheduler.submit("/work/job2.sh", "call")
    assert scheduler.statuses([job, other])[other].state == "running"
    assert scheduler.cancel(other) == ""  # scancel prints nothing
    finish(fake.state, job)
    info = scheduler.status(job)
    assert (info.state, info.exit_status) == ("completed", 0)
    assert fake.logins() == 1  # six or more scheduler commands, one connection


def test_one_session_is_shared_by_every_scheduler_of_a_profile(tmp_path, site):
    site()
    cfg = settings(tmp_path).hpc
    first, second = sched.Scheduler(cfg), sched.Scheduler(cfg)
    assert first.session() is second.session() is not None


def test_a_profile_without_a_connect_command_keeps_the_old_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LABHQ_SITES_DIR", str(tmp_path / "sites"))
    hpc_site.save_profile(hpc_site.SiteProfile(name="fake", scheduler="slurm"))
    scheduler = sched.Scheduler(settings(tmp_path, ssh_host="login.example").hpc)
    assert scheduler.session() is None and scheduler.needs_login()  # plain ssh_host: every command is a login
    assert not sched.Scheduler(settings(tmp_path).hpc).needs_login()  # on the login node: no login at all


def test_an_unreadable_profile_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("LABHQ_SITES_DIR", str(tmp_path / "sites"))
    scheduler = sched.Scheduler(settings(tmp_path, ssh_host="login.example").hpc)
    with pytest.raises(RuntimeError, match="site_profile"):
        scheduler.status("1000")  # no silent fall back to ssh_host
    assert scheduler.needs_login()


def test_the_login_counter_sees_the_connection_and_a_limit_refuses_it(tmp_path, site):
    fake = site()
    cfg = settings(tmp_path, login_limit_per_day=1).hpc
    scheduler = sched.Scheduler(cfg)
    with hb.request_scope("req_1"):
        job = scheduler.submit("/work/job.sh", "align")
    budget = hb.budget_for(cfg)
    assert budget.usage("req_1")["request"] == 1 and fake.logins() == 1
    scheduler.session().close()  # the shell dropped; the next command needs a login the day limit does not allow
    with hb.request_scope("req_1"), pytest.raises(hb.LoginLimitReached):
        scheduler.status(job)
    assert fake.logins() == 1 and budget.usage()["today"] == 1  # nothing was opened or counted
    assert scheduler.needs_login()


def test_a_command_timeout_is_a_timeout_and_a_lost_shell_may_have_submitted(tmp_path, site, monkeypatch):
    site()
    scheduler = sched.Scheduler(settings(tmp_path, command_timeout_s=60).hpc)
    session = scheduler.session()
    from labhq import hpc_session as hs

    def timeout(*a, **k):
        raise hs.CommandTimeout("too slow")

    monkeypatch.setattr(session, "run", timeout)
    with pytest.raises(RuntimeError, match="timed out"):
        scheduler.submit("/work/job.sh", "align")

    def lost(*a, **k):
        raise hs.SessionLost("dropped")

    monkeypatch.setattr(session, "run", lost)
    with pytest.raises(RuntimeError, match="may have been submitted"):
        scheduler.submit("/work/job.sh", "align")


def test_the_site_profile_gives_the_session_its_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LABHQ_SITES_DIR", str(tmp_path / "sites"))
    hpc_site.save_profile(hpc_site.SiteProfile(
        name="fake", access_method="ssh", connect_argv=["ssh", "-tt", "login"], idle_lock_minutes=180,
        tunnel_texts=["tunnel is down"], remote_exec=True))
    config = hpc_site.session_config(hpc_site.load_profile("fake"), 45)
    assert (config.argv, config.idle_lock_s, config.heartbeat_s) == (["ssh", "-tt", "login"], 10800.0, 900.0)
    assert config.tunnel_texts == ("tunnel is down",) and config.command_timeout_s == 45.0


# ---- the runner side: attach, notices and the tools' route ----

def stub_runner(tmp_path, cfg, owned=True):
    from labhq.runner.daemon import Runner

    runner = Runner.__new__(Runner)
    runner.s = cfg
    runner.scheduler = sched.Scheduler(cfg.hpc)
    runner.workspaces, runner.active_roots = {"t1": object()}, {"t1": (False,)}
    runner.jobs, runner.task_req, runner.events = {}, {"t1": "req_1"}, []

    async def owns(identity):
        return owned

    async def emit(event):
        runner.events.append(event)

    runner._owns_job, runner.emit = owns, emit
    return runner


def op(runner, name, **fields):
    body = {"op": name, "task_id": "t1", "agent_id": "a1", "request_id": "req_1", **fields}
    return asyncio.run(type(runner)._on_scheduler_op(runner, body))


def test_the_runner_answers_status_queue_and_cancel_through_its_shell(tmp_path, site):
    fake = site()
    runner = stub_runner(tmp_path, settings(tmp_path))
    job = runner.scheduler.submit("/work/job.sh", "align")
    assert op(runner, "status", job_id=job)["status"]["state"] == "running"
    assert [j["job_id"] for j in op(runner, "queue")["queue"]] == [job]
    assert "error" not in op(runner, "cancel", job_id=job)
    assert op(runner, "status", job_id=job)["status"]["state"] in {"cancelled", "missing"}
    assert fake.logins() == 1


def test_cancel_needs_ownership_and_a_running_writable_task(tmp_path, site):
    site()
    runner = stub_runner(tmp_path, settings(tmp_path), owned=False)
    assert "was not submitted by you" in op(runner, "cancel", job_id="1000")["error"]
    assert "unknown HPC operation" in op(runner, "format_disk")["error"]
    runner.active_roots = {"t1": (True,)}
    assert "read-only" in op(runner, "status", job_id="1000")["error"]
    runner.active_roots = {}
    assert "not running" in op(runner, "status", job_id="1000")["error"]


def test_a_login_limit_comes_back_to_the_tool_as_the_reason(tmp_path, site):
    site()
    cfg = settings(tmp_path, login_limit_per_day=1)
    runner = stub_runner(tmp_path, cfg)
    job = runner.scheduler.submit("/work/job.sh", "align")
    runner.scheduler.session().close()
    assert "HPC 로그인 상한에 닿았습니다" in op(runner, "status", job_id=job)["error"]


def test_a_live_shell_is_not_stopped_by_a_used_up_request_limit(tmp_path, site):
    site()
    cfg = settings(tmp_path, login_limit_per_request=1)
    runner = stub_runner(tmp_path, cfg)
    with hb.request_scope("req_1"):
        runner.scheduler.submit("/work/job.sh", "align")  # the connection used the request's one login
    assert runner._login_limit("req_1") is None  # more commands cost no login
    runner.scheduler.session().close()
    assert runner._login_limit("req_1") is not None  # a new login would be refused


def test_session_notices_reach_the_feed_as_alerts_but_the_login_limit_is_left_to_the_poller(tmp_path, site):
    site()
    runner = stub_runner(tmp_path, settings(tmp_path))
    runner.jobs = {"1000": {"task_id": "t1", "agent_id": "a1", "terminal": False}}
    runner._loop = None
    runner._hpc_session_notice("host_key_changed", "host key가 바뀌었습니다")  # no loop yet: only logged
    asyncio.run(runner._emit_hpc_notice("needs_human", "터널을 열어 주세요"))
    event = runner.events[0]
    assert event.data["level"] == "alert" and event.agent_id == "a1" and event.request_id == "req_1"
    assert "터널을 열어 주세요" in event.data["text"]
    runner._hpc_session_notice("login_limit", "x")
    assert len(runner.events) == 1


def test_the_tools_route_status_queue_and_cancel_through_the_runner(tmp_path, monkeypatch):
    import labhq.tools.hpc_mcp as hpc

    sent = []

    async def broker(path, payload, timeout):
        sent.append((path, payload))
        return {"status": {"job_id": "7", "state": "running"}, "queue": [{"job_id": "7"}], "result": "ok"}

    monkeypatch.setattr(hpc, "_broker", broker)
    monkeypatch.setattr(hpc, "VIA_RUNNER", True)
    monkeypatch.setattr(hpc, "SESSION_ERROR", None)
    monkeypatch.setattr(hpc, "TASK", "t1")
    monkeypatch.setattr(hpc, "AGENT", "a1")
    assert json.loads(asyncio.run(hpc.hpc_status("7")))["state"] == "running"
    assert json.loads(asyncio.run(hpc.hpc_queue())) == [{"job_id": "7"}]
    assert json.loads(asyncio.run(hpc.hpc_cancel("7")))["result"] == "ok"
    assert [(p, b["op"]) for p, b in sent] == [("/jobs/submit", "status"), ("/jobs/submit", "queue"),
                                               ("/jobs/submit", "cancel")]
    monkeypatch.setattr(hpc, "SESSION_ERROR", "hpc.site_profile 'x' cannot be used")
    with pytest.raises(Exception, match="cannot be used"):
        asyncio.run(hpc.hpc_status("7"))


# ---- the site's host, port and user never reach a message the agent sees ----

SITE_WORDS = ("gw.example.org", "bob", "2222")


def hidden(text):
    return all(word not in text for word in SITE_WORDS)


def leaky_site(tmp_path, monkeypatch, *flags):
    monkeypatch.setenv("LABHQ_SITES_DIR", str(tmp_path / "sites"))
    argv = [sys.executable, "-I", GATEWAY, "--slurm-state", str(tmp_path / "slurm.json"), *flags,
            "-p", "2222", "-o", "User=bob", "bob@gw.example.org"]  # words the fake gateway ignores, as a real ssh would use them
    hpc_site.save_profile(hpc_site.SiteProfile(name="fake", access_method="ssh", connect_argv=argv, remote_exec=True,
                                               scheduler="slurm"))


def test_a_submit_error_hides_the_host_port_and_user(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_HPC_SBATCH_ERROR", "sbatch: error: bob@gw.example.org: connect to port 2222 refused (exit 22)")
    leaky_site(tmp_path, monkeypatch)
    scheduler = sched.Scheduler(settings(tmp_path).hpc)
    try:
        with pytest.raises(RuntimeError) as caught:
            scheduler.submit("/work/job.sh", "align")
    finally:
        sched.close_sessions()
    message = str(caught.value)
    assert hidden(message) and "sbatch failed (1)" in message and "exit 22" in message  # the exit code is not a port


def test_a_connect_failure_message_hides_the_site(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_HPC_FAIL_TEXT", "ssh: connect to host gw.example.org port 2222: Connection refused (bob)")
    leaky_site(tmp_path, monkeypatch)
    scheduler = sched.Scheduler(settings(tmp_path).hpc)
    notices = []
    scheduler.session().on_notice = lambda kind, text: notices.append(text)
    try:
        with pytest.raises(RuntimeError) as caught:
            scheduler.status("1000")
    finally:
        sched.close_sessions()
    assert hidden(str(caught.value)) and all(hidden(text) for text in notices)


def test_the_plain_ssh_path_hides_the_host_too(tmp_path, monkeypatch):
    from subprocess import CompletedProcess

    monkeypatch.setattr(sched.subprocess, "run", lambda args, **k: CompletedProcess(
        args, 255, "", "ssh: Could not resolve hostname login.example.org: Name or service not known\n"))
    scheduler = sched.Scheduler(Settings.model_validate({
        "runner": {"state_dir": str(tmp_path / "state")},
        "hpc": {"scheduler": "slurm", "user": "lab", "ssh_host": "bob@login.example.org"}}).hpc)
    with pytest.raises(RuntimeError) as caught:
        scheduler.submit("/work/job.sh", "align")
    assert "login.example.org" not in str(caught.value) and "bob" not in str(caught.value)
    assert "Could not resolve hostname" in str(caught.value)
    with pytest.raises(RuntimeError) as cancelled:
        scheduler.cancel("1000")
    assert "login.example.org" not in str(cancelled.value)


def test_redaction_words_and_ports():
    from labhq import hpc_session as hs

    words = hs.redaction_words(["ssh", "-tt", "-p", "2222", "-Jjump.example.org", "--flag", "-o", "User=bob",
                                "bob@login.example.org"])
    assert set(words) == {"2222", "jump.example.org", "bob", "login.example.org"}
    text = hs.redact("host Login.Example.org:2222 port 2222 user bob exit 22", words)
    assert text == "host <host>:<port> port <port> user <host> exit 22"
