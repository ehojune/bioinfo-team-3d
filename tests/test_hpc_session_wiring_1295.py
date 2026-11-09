"""The HPC session wired into the scheduler, the runner and the tools (#1295 C), against the fake gateway. No real HPC."""
import asyncio
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from labhq import hpc_login_budget as hb, hpc_session as hs, hpc_site
from labhq.runner.approvals import Broker
from labhq.runner.daemon import Runner
from labhq.runner.hpc_jobs import SharedStatusQueries
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

    async def owns(identity, **kwargs):
        return owned

    async def emit(event):
        runner.events.append(event)

    runner._owns_job, runner.emit = owns, emit
    runner._hpc_job_owned = lambda *args, **kwargs: owned
    return runner


def op(runner, name, **fields):
    body = {"op": name, "task_id": "t1", "agent_id": "a1", "request_id": "req_1", **fields}
    return asyncio.run(type(runner)._on_scheduler_op(runner, body))


def test_the_runner_answers_status_queue_and_cancel_through_its_shell(tmp_path, site):
    fake = site()
    runner = stub_runner(tmp_path, settings(tmp_path))
    job = runner.scheduler.submit("/work/job.sh", "align")
    runner.jobs[job] = {"submitted_by_runner": True, "terminal": False, "scheduler": "slurm"}
    assert op(runner, "status", job_id=job)["status"]["state"] == "running"
    queue = op(runner, "queue")
    assert "error" not in queue and [j["job_id"] for j in queue["queue"]] == [job]
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
    assert [p for p, _ in sent] == ["/jobs/scheduler/status", "/jobs/scheduler/queue", "/jobs/scheduler/cancel"]
    assert all("op" not in body for _, body in sent)
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
    monkeypatch.setenv("SLURM_FAKE_SBATCH_ERROR", "sbatch: error: bob@gw.example.org: connect to port 2222 refused (exit 22)")
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
    monkeypatch.setenv("SLURM_FAKE_FAIL_TEXT", "ssh: connect to host gw.example.org port 2222: Connection refused (bob)")
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


def test_a_truncated_submit_answer_is_reported_as_maybe_submitted(tmp_path, site, monkeypatch):
    site()
    scheduler = sched.Scheduler(settings(tmp_path).hpc)
    session = scheduler.session()
    from labhq import hpc_session as hs

    monkeypatch.setattr(session, "run", lambda *a, **k: hs.CommandResult(0, "x", truncated=True, dropped_chars=9))
    with pytest.raises(RuntimeError, match="may have been submitted"):
        scheduler.submit("/work/job.sh", "align")


def test_the_broker_waits_for_a_whole_reconnect(tmp_path, site, monkeypatch):
    import labhq.tools.hpc_mcp as hpc

    site()
    cfg = settings(tmp_path)
    scheduler = sched.Scheduler(cfg.hpc)
    monkeypatch.setattr(hpc, "S", cfg)
    monkeypatch.setattr(hpc, "SCHED", scheduler)
    monkeypatch.setattr(hpc, "SESSION_ERROR", None)
    session = scheduler.session()
    # 3 connects of 30 s, waits of 5 s and 10 s, then the 60 s command: more than the old command timeout plus 60 s
    assert session.worst_case_s(60) == 3 * 30 + 5 + 10 + 60
    # the runner's wait for its turn (60 s) comes first, then the reconnect and the command
    assert hpc._hpc_wait_s() == 60 + 165 and hpc._hpc_wait_s(2) == 2 * (60 + 165)


def test_a_warning_on_stderr_after_the_job_id_is_not_the_job_id(tmp_path, site, monkeypatch):
    monkeypatch.setenv("SLURM_FAKE_SBATCH_WARN", "warning: quota 80%")
    site()
    scheduler = sched.Scheduler(settings(tmp_path).hpc)
    assert scheduler.submit("/work/job.sh", "align") == "1000"  # not "80"
    assert scheduler.status("1000").state == "running"


def test_stderr_comes_back_separately_with_the_exit_status(tmp_path, site, monkeypatch):
    monkeypatch.setenv("SLURM_FAKE_SBATCH_ERROR", "sbatch: error: invalid account")
    site()
    scheduler = sched.Scheduler(settings(tmp_path).hpc)
    done = scheduler._run(["sbatch", "--parsable", "/work/job.sh"])
    assert (done.returncode, done.stdout, done.stderr) == (1, "", "sbatch: error: invalid account\n")


def test_a_caller_with_a_deadline_gives_up_unsent_when_the_shell_stays_busy(tmp_path, site):
    import threading
    import time

    from labhq import hpc_session as hs

    site()
    session = sched.Scheduler(settings(tmp_path).hpc).session()
    session.run("echo up")
    slow = threading.Thread(target=lambda: session.run("sleep 2"))
    slow.start()
    time.sleep(0.3)
    started = time.monotonic()
    with hs.queue_wait(0.3), pytest.raises(hs.SessionBusy):
        session.run("echo late")  # never sent: it cannot run after the caller gave up
    assert time.monotonic() - started < 1.5
    slow.join(10)
    assert session.run("echo after").output == "after\n"  # and the shell never saw "late"


def test_close_ends_a_command_in_flight_at_once(tmp_path, site):
    import threading
    import time

    from labhq import hpc_session as hs

    site()
    session = sched.Scheduler(settings(tmp_path).hpc).session()
    session.run("echo up")
    seen = []

    def slow():
        try:
            session.run("sleep 20")
        except hs.SessionError as error:
            seen.append(error)

    thread = threading.Thread(target=slow)
    thread.start()
    time.sleep(0.4)
    started = time.monotonic()
    session.close()
    assert time.monotonic() - started < 4
    thread.join(10)
    assert seen and isinstance(seen[0], hs.SessionLost)


def test_the_connection_process_gets_only_the_sanitized_environment(tmp_path, site, monkeypatch):
    monkeypatch.setenv("LABHQ_TEST_API_KEY", "super-secret")
    monkeypatch.setenv("SLURM_TEST_KEEP", "kept")  # a scheduler variable scheduler commands may see
    site()
    session = sched.Scheduler(settings(tmp_path).hpc).session()
    assert session.run("printenv LABHQ_TEST_API_KEY").output.strip() == ""
    assert session.run("printenv SLURM_TEST_KEEP").output.strip() == "kept"


def test_a_local_job_does_not_count_as_an_open_hpc_job(tmp_path, site):
    from labhq.runner import local_jobs

    site()
    runner = stub_runner(tmp_path, settings(tmp_path))
    local_id = local_jobs.new_id()
    assert local_jobs.is_local(local_id)
    runner.jobs = {"1000": {"terminal": True}, local_id: {"terminal": False}}
    assert not runner._has_open_hpc_jobs()
    runner.jobs["1001"] = {"terminal": False}
    assert runner._has_open_hpc_jobs()


def test_the_staff_cli_waits_longer_than_the_runner_for_an_hpc_call(tmp_path):
    from labhq.hpc_session import QUEUE_WAIT_S, SessionConfig, worst_case_s
    from labhq.runner.daemon import labhq_mcp_timeout_s

    plain = settings(tmp_path)
    plain.hpc.site_profile = None
    with_session = settings(tmp_path)
    approval = max(with_session.policy.approvals.timeout_s, 1200)
    hpc = QUEUE_WAIT_S + worst_case_s(SessionConfig(argv=["x"]), with_session.hpc.command_timeout_s)
    assert labhq_mcp_timeout_s(with_session) >= max(with_session.policy.approvals.timeout_s, 1200) + hpc + 30 + 30
    assert labhq_mcp_timeout_s(plain) == labhq_mcp_timeout_s(Settings()) and approval  # no profile: unchanged


def test_shutdown_during_a_connect_ends_it_and_sends_nothing(tmp_path, site):
    import threading
    import time

    from labhq import hpc_session as hs

    site("--banner", "--lock-after", "30")
    session = sched.Scheduler(settings(tmp_path).hpc).session()
    outcome = []

    def first_command():
        try:
            session.run("echo never")
        except hs.SessionError as error:
            outcome.append(error)

    # slow the handshake so shutdown() lands while the connection is still opening
    original = session._exec

    def slow_exec(conn, command, timeout_s):
        if command == session.cfg.ready_command:
            time.sleep(0.6)
        return original(conn, command, timeout_s)

    session._exec = slow_exec
    thread = threading.Thread(target=first_command)
    thread.start()
    time.sleep(0.2)
    started = time.monotonic()
    session.shutdown()
    thread.join(15)
    assert not thread.is_alive() and outcome and time.monotonic() - started < 8
    assert "종료" in str(outcome[0]) and session.logins == 1  # no second attempt
    assert not session.connected
    with pytest.raises(hs.SessionError, match="종료"):
        session.run("echo after")


def test_polling_backs_off_only_where_every_command_is_a_login(tmp_path, site, monkeypatch):
    site()
    # a profile with a connect command and also hpc.ssh_host: the shell is used, ssh_host is not, so polls cost no login
    both = sched.Scheduler(settings(tmp_path, ssh_host="login.example").hpc)
    assert both.session() is not None and not both.polls_cost_logins()
    monkeypatch.setenv("LABHQ_SITES_DIR", str(tmp_path / "other-sites"))
    plain = sched.Scheduler(Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state")},
                                                     "hpc": {"scheduler": "slurm", "ssh_host": "login.example"}}).hpc)
    assert plain.polls_cost_logins()  # plain ssh_host: every poll is a login
    assert not sched.Scheduler(Settings.model_validate({"hpc": {"scheduler": "slurm"}}).hpc).polls_cost_logins()


def test_a_status_call_is_budgeted_for_its_two_commands(tmp_path, site, monkeypatch):
    import labhq.tools.hpc_mcp as hpc

    site()
    cfg = settings(tmp_path)
    monkeypatch.setattr(hpc, "S", cfg)
    monkeypatch.setattr(hpc, "SCHED", sched.Scheduler(cfg.hpc))
    monkeypatch.setattr(hpc, "SESSION_ERROR", None)
    monkeypatch.setattr(hpc, "VIA_RUNNER", True)
    seen = []

    async def broker(path, payload, timeout):
        seen.append((path.rsplit("/", 1)[-1], timeout))
        return {"status": {"state": "running"}, "queue": [], "result": "ok"}

    monkeypatch.setattr(hpc, "_broker", broker)
    asyncio.run(hpc.hpc_status("1"))
    asyncio.run(hpc.hpc_cancel("1"))
    timeouts = dict(seen)
    assert timeouts["status"] > timeouts["cancel"] and timeouts["status"] >= 2 * (60 + 60) + 105


def test_a_pbs_submit_through_a_site_profile_is_sent_to_the_pi_like_one_over_ssh(tmp_path, site):
    from labhq.runner.hpc_jobs import submit_job

    site()
    st = Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state")},
                                  "hpc": {"scheduler": "pbs", "user": "lab", "site_profile": "fake"}})
    (tmp_path / "jobs").mkdir()
    script = tmp_path / "jobs" / "job.sh"
    script.write_text("echo hi\n", encoding="utf-8")
    asked = []

    async def approve(payload):
        asked.append(payload)
        return {"approved": False, "reason": "test"}

    body = {"task_id": "t1", "agent_id": "a1", "script": str(script), "name": "j", "cores": 1, "mem": "1G",
            "walltime": "00:10:00"}
    st.policy.approvals.hpc_core_hours_threshold = 1000  # a cheap job: only the remote-directive reason can ask
    asyncio.run(submit_job(st, sched.Scheduler(st.hpc), tmp_path, body, approve))
    assert asked and asked[0]["detail"]["approval_reasons"] == ["remote PBS_DPREFIX cannot be inspected locally"]
    plain = Settings.model_validate({"hpc": {"scheduler": "pbs", "user": "lab"}})
    plain.policy.approvals.hpc_core_hours_threshold = 1000
    asked.clear()
    asyncio.run(submit_job(plain, sched.Scheduler(plain.hpc), tmp_path, body, approve))
    assert not asked or asked[0]["detail"]["approval_reasons"] == []  # on the login node the local check is enough


# ---- Three concurrent employees sharing the authenticated runner (#1332) ----
def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "fixture did not reach the expected state"
        time.sleep(0.005)


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    monkeypatch.setenv("LABHQ_SITES_DIR", str(tmp_path / "sites"))
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("SLURM_FAKE_SANDBOX", str(sandbox))
    counter, state = tmp_path / "starts.txt", tmp_path / "state.json"
    hpc_site.save_profile(hpc_site.SiteProfile(
        name="fake", scheduler="slurm", access_method="gateway_tunnel", remote_exec=False,
        remote_workdir="/hpc/work", connect_argv=[sys.executable, "-I",
            str(Path(__file__).with_name("fake_hpc_gateway.py")), "--slurm-state", str(state),
            "--counter", str(counter), "--interactive-prompts"]))
    cfg = Settings.model_validate({"runner": {"state_dir": str(tmp_path / "runner")},
                                   "hpc": {"scheduler": "slurm", "user": "fixture", "site_profile": "fake",
                                           "login_limit_per_day": 1}})
    cfg.policy.approvals.hpc_core_hours_threshold = 1000
    yield SimpleNamespace(s=cfg, sandbox=sandbox, state=state,
                          logins=lambda: len(counter.read_text().splitlines()) if counter.exists() else 0)
    sched.close_sessions()


def runner_fixture(tmp_path, cluster):
    r = Runner.__new__(Runner)
    r.s, r.scheduler = cluster.s, sched.Scheduler(cluster.s.hpc)
    r.jobs, r.task_req, r.workspaces, r.active_roots = {}, {}, {}, {}
    r.events, r.submitting, r.notified, r.tasks = [], {}, set(), {}
    r.store = SimpleNamespace(put=lambda *args: None)

    async def emit(event):
        r.events.append(event)

    async def emit_all(events, **kwargs):
        r.events.extend(events)

    async def unused(*args):
        raise AssertionError("unexpected approval")

    r.emit, r.emit_all = emit, emit_all
    r.broker = Broker(0, unused, unused, r._on_submit, owns_job=r._owns_job,
                      on_scheduler=r._on_scheduler_request)
    tokens = []
    for i in range(3):
        tid, aid = f"t{i}", f"employee{i}"
        work = tmp_path / tid
        (work / "jobs").mkdir(parents=True)
        (work / "jobs" / "run.sh").write_text(sched.build_script(f"echo result-{i}", str(work)), encoding="utf-8")
        r.workspaces[tid] = SimpleNamespace(dir=work, append_job=lambda *args: None,
                                           task=SimpleNamespace(meta={}))
        r.active_roots[tid], r.task_req[tid] = (False,), "request"
        tokens.append({"X-Labhq-Token": r.broker.issue_task_token(tid, aid, "request")})
    return r, tokens


def test_three_employees_submit_query_cancel_and_fetch_isolated_files_on_one_login(tmp_path, cluster, monkeypatch):
    r, tokens = runner_fixture(tmp_path, cluster)
    batch_calls = []
    statuses = r.scheduler.statuses

    def batch(ids):
        batch_calls.append(tuple(ids))
        return statuses(ids)

    monkeypatch.setattr(r.scheduler, "statuses", batch)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=r.broker.app), base_url="http://fixture") as c:
            replies = await asyncio.gather(*(c.post("/jobs/submit", headers=tokens[i], json={
                "script": str(r.workspaces[f"t{i}"].dir / "jobs/run.sh"), "name": f"employee{i}",
                "walltime": "00:01:00"}) for i in range(3)))
            rows = [reply.json() for reply in replies]
            assert all(row.get("submitted") for row in rows), rows
            ids = [row["job_id"] for row in rows]
            assert len(set(ids)) == 3 and cluster.logins() == 1
            assert len({row["transfer"]["work"] for row in rows}) == 3
            for i, row in enumerate(rows):
                remote = cluster.sandbox / "hpc/work" / row["transfer"]["script"]
                assert f"result-{i}" in remote.read_text(encoding="utf-8")
                for other in set(range(3)) - {i}:
                    assert f"result-{other}" not in remote.read_text(encoding="utf-8")

            replies = await asyncio.gather(*(c.post("/jobs/scheduler/status", headers=tokens[i],
                json={"job_id": ids[i]}) for i in range(3)))
            assert [x.json()["status"]["job_id"] for x in replies] == ids
            assert all(x.json()["status"]["state"] == "running" for x in replies)
            assert len(batch_calls) == 1 and set(batch_calls[0]) == set(ids)
            denied = (await c.post("/jobs/scheduler/status", headers=tokens[0], json={"job_id": ids[1]})).json()
            assert "requires an owned job" in denied["error"] and len(batch_calls) == 1
            assert "error" not in (await c.post("/jobs/scheduler/cancel", headers=tokens[2],
                                                json={"job_id": ids[2]})).json()
            data = json.loads(cluster.state.read_text())
            for jid in ids[:2]:
                data["jobs"][jid].update(state="COMPLETED", exit="0:0")
            cluster.state.write_text(json.dumps(data))
            for i, row in enumerate(rows):
                remote = cluster.sandbox / "hpc/work" / row["transfer"]["work"]
                (remote / "outputs").mkdir()
                (remote / "outputs" / "answer.txt").write_text(f"employee-{i}")
                # Fake sbatch does not run scripts; the fixture writes the remote job's result and logs.
                for log in row["transfer"]["logs"]:
                    path = cluster.sandbox / "hpc/work" / log
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"log-{i}")
            await r._poll_jobs()
            assert all(j["terminal"] and j["fetched"] for j in r.jobs.values())
            assert r.jobs[ids[2]]["state"] == "cancelled"
            # Three independent recoveries also interleave over the same command FIFO, with separate destinations.
            manifests = await asyncio.gather(*(asyncio.to_thread(r._fetch_job_files, r.jobs[jid],
                                                   r.jobs[jid]["transfer"]) for jid in ids))
            assert all(m["fetched"] and not m["skipped"] for m in manifests)
            for i in range(3):
                assert (r.workspaces[f"t{i}"].dir / "outputs/answer.txt").read_text() == f"employee-{i}"
            assert cluster.logins() == 1

    asyncio.run(scenario())


def test_fifo_commands_timeout_unsent_and_heartbeat_cannot_overtake(monkeypatch):
    s = hs.HpcSession(hs.SessionConfig(argv=["fixture"]))
    monkeypatch.setattr(s, "_ensure_open", lambda: object())
    order = []
    monkeypatch.setattr(s, "_exec", lambda conn, cmd, timeout: order.append(cmd) or hs.CommandResult(0, cmd))
    with ThreadPoolExecutor(max_workers=4) as pool:
        s._lock.acquire()
        try:
            futures = []
            for i in range(3):
                futures.append(pool.submit(s.run, f"echo {i}"))
                wait_for(lambda: len(s._lock._waiting) == i + 1)
            def short_wait():
                with hs.queue_wait(0.03), pytest.raises(hs.SessionBusy):
                    s.run("echo expired")
            pool.submit(short_wait).result(2)
            assert len(s._lock._waiting) == 3
            assert pool.submit(s.tick).result(2) == "busy"
        finally:
            s._lock.release()
        assert [f.result(2).output for f in futures] == ["echo 0", "echo 1", "echo 2"]
    assert order == ["echo 0", "echo 1", "echo 2"]


def test_command_timeout_releases_waiters_and_refuses_reconnect_at_shared_limit(tmp_path, cluster):
    s = sched.Scheduler(cluster.s.hpc).session()
    s.run("echo ready")
    with pytest.raises(hs.CommandTimeout):
        s.run("sleep 10", timeout_s=0.03)
    assert not s.connected
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(s.run, f"echo employee-{i}") for i in range(3)]
        for f in futures:
            with pytest.raises(hb.LoginLimitReached):
                f.result(3)
    assert cluster.logins() == 1


def test_all_employees_wait_at_limit_with_one_pi_notice_and_fetch_attempts_preserved(tmp_path, cluster):
    r, tokens = runner_fixture(tmp_path, cluster)
    s = r.scheduler.session()
    s.run("echo ready")
    s.close()  # loss of the sole connection means the next command must pass login admission
    for i in range(3):
        r.jobs[str(i + 1)] = {"task_id": f"t{i}", "agent_id": f"employee{i}", "scheduler": "slurm",
                             "submitted_by_runner": True, "terminal": False, "state": "running"}

    async def scenario():
        replies = await asyncio.gather(*(r._on_scheduler_request("status", {
            "task_id": f"t{i}", "agent_id": f"employee{i}", "request_id": "request", "job_id": str(i + 1)
        }) for i in range(3)))
        assert all("로그인 상한" in x["error"] for x in replies)
        assert len([e for e in r.events if "hpc_login_limit" in e.data]) == 1
        r.jobs["1"].update(terminal=True, transfer={"work": "fixture"})
        for _ in range(r.FETCH_TRIES + 1):
            await r._fetch_finished_jobs()
        assert r.jobs["1"].get("fetch_tries", 0) == 0
        assert not r._fetch_settled(r.jobs["1"])
        assert not any(e.type == "jobs.finished" for e in r.events)

    asyncio.run(scenario())
    assert cluster.logins() == 1


def test_shared_batch_survives_one_cancelled_waiter_and_never_reuses_completed_results():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def statuses(ids):
        calls.append(ids)
        entered.set()
        assert release.wait(5)
        return {jid: sched.JobInfo(jid, "running") for jid in ids}

    async def scenario():
        query = SharedStatusQueries(SimpleNamespace(statuses=statuses))
        first = asyncio.create_task(query.get("request", ["1", "2", "3"]))
        await asyncio.to_thread(entered.wait, 3)
        second = asyncio.create_task(query.get("request", ["2"]))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (await second)["2"].state == "running" and len(calls) == 1
        await query.get("request", ["2"])
        assert len(calls) == 2

    asyncio.run(scenario())


def test_every_staff_backend_uses_broker_and_never_its_scheduler(monkeypatch):
    from labhq.tools import hpc_mcp as hpc

    def forbidden(*args, **kwargs):
        raise AssertionError("employee tried to run a scheduler command")

    monkeypatch.setattr(hpc, "SCHED", SimpleNamespace(session=lambda: None, status=forbidden,
                                                     queue=forbidden, cancel=forbidden))
    monkeypatch.setattr(hpc, "SESSION_ERROR", None)
    monkeypatch.setattr(hpc, "VIA_RUNNER", False)  # the old bypass flag must no longer enable direct execution
    seen = []

    async def broker(path, payload, timeout):
        seen.append(path)
        return {"status": {"job_id": "1"}, "queue": [], "result": "ok"}

    monkeypatch.setattr(hpc, "_broker", broker)
    async def scenario():
        await hpc.hpc_status("1")
        await hpc.hpc_queue()
        await hpc.hpc_cancel("1")
    asyncio.run(scenario())
    assert seen == ["/jobs/scheduler/status", "/jobs/scheduler/queue", "/jobs/scheduler/cancel"]


def test_shared_status_fallback_stops_at_the_whole_call_deadline(tmp_path, cluster):
    s = sched.Scheduler(cluster.s.hpc).session()
    s.run("echo ready")
    with hs.command_deadline(0.03):
        with pytest.raises(hs.CommandTimeout):
            s.run("sleep 10")
        with pytest.raises(hs.SessionBusy, match="deadline"):
            s.run("echo must-not-reconnect")
    assert cluster.logins() == 1 and not s.connected


def test_poller_and_staff_share_a_batch_in_the_same_request_only():
    entered, release = threading.Event(), threading.Event()
    calls = []
    def statuses(ids):
        calls.append((hb.current_request(), ids))
        entered.set()
        assert release.wait(5)
        return {jid: sched.JobInfo(jid, "running") for jid in ids}

    async def scenario():
        r = Runner.__new__(Runner)
        r.scheduler = SimpleNamespace(statuses=statuses)
        poll = asyncio.create_task(r._shared_hpc_statuses("r1", ["1", "2", "3"]))
        await asyncio.to_thread(entered.wait, 3)
        staff = asyncio.create_task(r._shared_hpc_statuses("r1", ["2"]))
        other = asyncio.create_task(r._shared_hpc_statuses("r2", ["2"]))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(poll, staff, other)
        assert len(calls) == 2 and {row[0] for row in calls} == {"r1", "r2"}
    asyncio.run(scenario())


def test_window_limit_notice_resumes_automatically_and_alerts_again_for_a_later_window(tmp_path, cluster, monkeypatch):
    r, _ = runner_fixture(tmp_path, cluster)
    now = [1000.0]
    monkeypatch.setattr("labhq.runner.daemon.time.time", lambda: now[0])
    stop = hb.LoginLimitReached("window", 1, 1)
    stop.retry_after_s = 60
    async def scenario():
        for _ in range(2):
            await r._notify_login_limit("request", stop, [{"task_id": "t0"}])
        assert len(r.events) == 1
        assert "자동으로" in r.events[0].data["text"] and "상한을 올리면" not in r.events[0].data["text"]
        assert r.events[0].data["hpc_login_limit"]["retry_after_s"] == 60
        now[0] += 61
        await r._notify_login_limit("request", stop, [{"task_id": "t0"}])
        assert len(r.events) == 2
    asyncio.run(scenario())
