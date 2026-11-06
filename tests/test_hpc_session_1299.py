"""The HPC session manager against a fake gateway (#1299). No real HPC, no real ssh."""
import sys
import time
from pathlib import Path

import pytest

from labhq import hpc_session as hs
from labhq.hpc_login_budget import LoginBudget, LoginLimitReached

GATEWAY = str(Path(__file__).with_name("fake_hpc_gateway.py"))


def argv(*flags):
    return [sys.executable, "-I", GATEWAY, *flags]


def session(*flags, budget=None, **cfg):
    cfg.setdefault("connect_timeout_s", 20)
    cfg.setdefault("command_timeout_s", 10)
    cfg.setdefault("reconnect_wait_s", 0.01)
    return hs.HpcSession(hs.SessionConfig(argv=argv(*flags), **cfg), budget=budget, sleep=lambda s: None)


@pytest.fixture
def made():
    sessions = []

    def make(*flags, **kw):
        s = session(*flags, **kw)
        sessions.append(s)
        return s

    yield make
    for s in sessions:
        s.close()


def test_runs_commands_in_one_shell_and_reads_the_exit_code(made):
    s = made()
    assert s.run("echo hello world").output == "hello world\n"
    result = s.run("fail 3")
    assert (result.exit_code, result.output) == (3, "")
    missing = s.run("nosuchcommand")
    assert missing.exit_code == 127 and "command not found" in missing.output
    assert s.logins == 1  # one connection served all three commands


def test_a_gateway_that_ignores_remote_commands_still_works(made):
    s = made("--remote-command-that-is-ignored", "uname", "-a")
    assert s.run("echo shell-only").output == "shell-only\n"


def test_banner_and_terminal_echo_are_not_output(made):
    s = made("--banner", "--echo")
    assert s.run("echo clean").output == "clean\n"
    assert s.run("nonl").output == "tail-without-newline"


def test_a_command_must_be_one_line(made):
    with pytest.raises(ValueError):
        made().run("echo a\necho b")


def test_a_big_output_is_capped_and_the_next_command_is_clean(made):
    s = made(max_output=1000)
    result = s.run("big 50000")
    assert result.truncated and len(result.output) == 1000 and result.dropped_chars > 40000
    assert s.run("echo after").output == "after\n"
    assert s.logins == 1


def test_a_timeout_closes_the_session_and_the_next_command_reconnects(made):
    s = made()
    assert s.run("echo up").exit_code == 0
    with pytest.raises(hs.CommandTimeout):
        s.run("sleep 5", timeout_s=0.5)
    assert not s.connected
    assert s.run("echo again").output == "again\n"
    assert s.logins == 2


def test_a_dropped_connection_is_reported_then_reopened(made):
    s = made("--drop-after", "2")  # the ready probe is the first command, so this drops during the second
    assert s.run("echo one").output == "one\n"
    with pytest.raises(hs.SessionLost):
        s.run("echo two")
    assert s.run("echo three").output == "three\n"
    assert s.logins == 2


def test_the_heartbeat_keeps_an_idle_locking_site_connected(made):
    s = made("--lock-after", "0.8", heartbeat_s=0.1, idle_lock_s=1.0)
    s.start(tick_s=0.05)
    assert s.run("echo first").output == "first\n"
    time.sleep(2.0)  # longer than twice the site's lock: without a heartbeat the gateway would have locked
    assert s.connected
    assert s.run("echo still").output == "still\n"
    assert s.logins == 1


def test_without_a_heartbeat_the_gateway_locks_and_the_manager_reconnects(made):
    s = made("--lock-after", "0.4")
    assert s.run("echo first").output == "first\n"
    time.sleep(1.0)
    assert s.run("echo late").output == "late\n"
    assert s.logins == 2


def test_heartbeat_must_be_shorter_than_the_idle_lock():
    with pytest.raises(ValueError):
        hs.SessionConfig(argv=["x"], idle_lock_s=60, heartbeat_s=60)
    cfg = hs.SessionConfig(argv=["x"], idle_lock_s=3 * 3600)
    assert cfg.heartbeat_s < 3 * 3600 and cfg.heartbeat_s == 900
    assert hs.SessionConfig(argv=["x"], idle_lock_s=120).heartbeat_s == 30
    with pytest.raises(ValueError):
        hs.SessionConfig(argv=[])


def test_an_idle_session_closes_unless_a_job_is_open():
    now = [0.0]
    jobs = [True]
    s = hs.HpcSession(hs.SessionConfig(argv=argv(), idle_close_s=100, heartbeat_s=30, idle_lock_s=300),
                      has_open_jobs=lambda: jobs[0], clock=lambda: now[0], sleep=lambda x: None)
    try:
        s.run("true")
        now[0] = 40
        assert s.tick() == "heartbeat"
        now[0] = 200
        assert s.tick() == "heartbeat" and s.connected  # an open job keeps it alive
        jobs[0] = False
        now[0] = 400
        assert s.tick() == "idle_close" and not s.connected
        assert s.tick() == "none"
        assert s.run("echo reopened").output == "reopened\n" and s.logins >= 2  # a loaded CI machine may retry a connect
    finally:
        s.close()


def test_every_connection_goes_through_the_login_counter(tmp_path, made):
    budget = LoginBudget(tmp_path / "logins.sqlite3", per_day=2)
    s = made(budget=budget)
    notices = []
    s.on_notice = lambda kind, text: notices.append(kind)
    s.run("echo 1")
    s.close()
    s.run("echo 2")
    s.close()
    assert budget.usage()["today"] == 2
    with pytest.raises(LoginLimitReached):
        s.run("echo 3")
    assert not s.connected and s.logins == 2 and notices == ["login_limit"]
    assert budget.usage()["today"] == 2  # a refused login is not counted


def test_reconnect_attempts_are_limited_then_a_cooldown_makes_no_login(tmp_path):
    now = [0.0]
    sleeps = []
    counter = tmp_path / "starts.txt"
    budget = LoginBudget(tmp_path / "logins.sqlite3")
    notices = []
    s = hs.HpcSession(hs.SessionConfig(argv=argv("--drop-after", "0", "--counter", str(counter)), reconnect_attempts=3,
                                       reconnect_wait_s=2, reconnect_cooldown_s=60, connect_timeout_s=5),
                      budget=budget, clock=lambda: now[0], sleep=sleeps.append,
                      on_notice=lambda kind, text: notices.append(kind))
    try:
        with pytest.raises(hs.ReconnectFailed):
            s.run("echo x")
        assert budget.usage()["today"] == 3 and sleeps == [2, 4] and notices == ["reconnect_failed"]
        with pytest.raises(hs.ReconnectFailed):
            s.run("echo y")  # inside the cooldown
        assert budget.usage()["today"] == 3
        now[0] = 100
        with pytest.raises(hs.ReconnectFailed):
            s.run("echo z")
        assert budget.usage()["today"] == 6
    finally:
        s.close()


def test_a_changed_host_key_stops_everything_until_reset(tmp_path):
    budget = LoginBudget(tmp_path / "logins.sqlite3")
    notices = []
    s = hs.HpcSession(hs.SessionConfig(argv=argv("--hostkey-changed"), connect_timeout_s=5), budget=budget,
                      sleep=lambda x: None, on_notice=lambda kind, text: notices.append(kind))
    with pytest.raises(hs.HostKeyChanged):
        s.run("echo x")
    with pytest.raises(hs.HostKeyChanged):
        s.run("echo y")
    assert budget.usage()["today"] == 1 and notices == ["host_key_changed"]  # no retry, no second login
    s.reset()
    s.cfg.argv = argv()
    assert s.run("echo ok").output == "ok\n"
    s.close()


def test_a_missing_tunnel_asks_a_person_and_waits(tmp_path):
    budget = LoginBudget(tmp_path / "logins.sqlite3")
    notices = []
    s = hs.HpcSession(hs.SessionConfig(argv=argv("--tunnel-down"), connect_timeout_s=5, tunnel_texts=("Connection refused",)),
                      budget=budget, sleep=lambda x: None, on_notice=lambda kind, text: notices.append(kind))
    with pytest.raises(hs.NeedsHuman):
        s.run("echo x")
    with pytest.raises(hs.NeedsHuman):
        s.run("echo y")  # waiting: nothing is tried
    assert budget.usage()["today"] == 1 and notices == ["needs_human"]
    s.cfg.argv = argv()  # the person opened the tunnel
    s.resume()
    assert s.run("echo up").output == "up\n"
    s.close()


def test_commands_from_several_threads_run_one_at_a_time(made):
    import threading

    s = made()
    results = {}

    def work(n):
        results[n] = s.run(f"echo job{n}").output

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert results == {n: f"job{n}\n" for n in range(8)} and s.logins == 1


def test_a_marker_and_a_big_output_in_one_read_are_found(made):
    s = made()
    s.run("echo up")
    # the gateway answers a short command with the begin marker, ~100 KB and the end marker in a burst
    result = s.run("big 100000")
    assert result.exit_code == 0 and result.dropped_chars == 0 and len(result.output) > 99000
    assert s.run("echo next").output == "next\n"


def test_start_after_close_runs_a_fresh_ticker(made):
    s = made("--lock-after", "0.8", heartbeat_s=0.1, idle_lock_s=1.0)
    s.start(tick_s=0.05)
    s.close()
    s.start(tick_s=0.05)
    assert s.run("echo first").output == "first\n"
    time.sleep(1.6)
    assert s.connected and s.run("echo still").output == "still\n" and s.logins == 1


def test_exec_finds_begin_and_end_markers_that_arrive_in_a_single_read(monkeypatch):
    import queue
    import types

    monkeypatch.setattr(hs.uuid, "uuid4", lambda: types.SimpleNamespace(hex="a" * 32))
    token = "a" * 12
    burst = f"banner\n__LABHQ_BEGIN_{token}__\n" + "x" * 5000 + f"\n\n__LABHQ_END_{token}_7__\n"
    chunks = queue.Queue()
    chunks.put(burst[:40])  # the begin marker split across two reads, then everything else at once
    chunks.put(burst[40:])
    stdin = types.SimpleNamespace(write=lambda data: None, flush=lambda: None)
    conn = hs._Conn(types.SimpleNamespace(stdin=stdin, poll=lambda: None), chunks, None)
    result = hs.HpcSession(hs.SessionConfig(argv=["x"]))._exec(conn, "anything", 5)
    assert result.exit_code == 7 and result.output == "x" * 5000 + "\n"


def test_heartbeats_do_not_count_as_activity_so_an_idle_session_still_closes():
    now = [0.0]
    s = hs.HpcSession(hs.SessionConfig(argv=argv(), idle_close_s=100, heartbeat_s=30, idle_lock_s=300),
                      clock=lambda: now[0], sleep=lambda x: None)
    try:
        s.run("true")
        seen = []
        for t in (40, 80, 90, 120):
            now[0] = t
            seen.append(s.tick())
        assert seen == ["heartbeat", "heartbeat", "none", "idle_close"] and not s.connected
    finally:
        s.close()


def test_an_ordinary_connection_refusal_is_retried_not_parked_for_a_person(tmp_path):
    budget = LoginBudget(tmp_path / "logins.sqlite3")
    s = hs.HpcSession(hs.SessionConfig(argv=argv("--tunnel-down"), connect_timeout_s=5, reconnect_attempts=3),
                      budget=budget, sleep=lambda x: None)
    with pytest.raises(hs.ReconnectFailed):
        s.run("echo x")
    assert budget.usage()["today"] == 3  # all attempts were made; nobody is asked to open a tunnel


def test_after_the_wait_for_a_person_the_next_command_tries_again(tmp_path):
    now = [0.0]
    budget = LoginBudget(tmp_path / "logins.sqlite3")
    s = hs.HpcSession(hs.SessionConfig(argv=argv("--tunnel-down"), connect_timeout_s=5, tunnel_texts=("Connection refused",),
                                       human_wait_s=300), budget=budget, clock=lambda: now[0], sleep=lambda x: None)
    with pytest.raises(hs.NeedsHuman):
        s.run("echo x")
    now[0] = 100
    with pytest.raises(hs.NeedsHuman):
        s.run("echo y")  # still waiting: no login
    assert budget.usage()["today"] == 1
    s.cfg.argv = argv()  # the person opened the tunnel
    now[0] = 400
    assert s.run("echo up").output == "up\n" and budget.usage()["today"] == 2
    s.close()
