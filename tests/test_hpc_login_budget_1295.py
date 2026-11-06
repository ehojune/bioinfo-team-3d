"""#1295: every HPC login is counted and the user's limits stop a new one.

A fake ssh only: no real cluster, no network. No institution's number appears here: the limits are test values.
"""

import subprocess
import threading
from subprocess import CompletedProcess

import pytest
import yaml

from labhq import hpc_login_budget as hb
from labhq.settings import HpcSettings, Settings
from labhq.tools.scheduler import Scheduler


class Clock:
    def __init__(self, start=1_700_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


def budget(tmp_path, per_request=None, per_day=None, clock=None):
    return hb.LoginBudget(tmp_path / hb.FILE, per_request, per_day, clock=clock or Clock())


# ---- the counter --------------------------------------------------------------------------------------------------

def test_every_login_is_counted_even_without_a_limit(tmp_path):
    b = budget(tmp_path)
    for _ in range(5):
        b.admit("qstat", "req_1")
    b.admit("qsub", "req_2")
    usage = b.usage("req_1")
    assert usage["today"] == 6 and usage["request"] == 5 and usage["per_request"] is None and usage["per_day"] is None
    assert usage["requests_today"] == {"req_1": 5, "req_2": 1}


def test_the_request_limit_stops_that_request_only(tmp_path):
    b = budget(tmp_path, per_request=2)
    b.admit("qstat", "req_1"), b.admit("qstat", "req_1")
    with pytest.raises(hb.LoginLimitReached) as stopped:
        b.admit("qstat", "req_1")
    assert stopped.value.scope == "request" and stopped.value.limit == 2 and stopped.value.used == 2
    assert b.admit("qstat", "req_2")["request"] == 1  # another request is untouched
    assert b.usage("req_1")["request"] == 2  # the refused login was not counted


def test_the_day_limit_stops_every_request_until_the_next_day(tmp_path):
    clock = Clock()
    b = budget(tmp_path, per_day=3, clock=clock)
    b.admit("qstat", "req_1"), b.admit("qstat", "req_2"), b.admit("qsub", None)
    with pytest.raises(hb.LoginLimitReached) as stopped:
        b.admit("qstat", "req_3")
    assert stopped.value.scope == "day" and stopped.value.used == 3
    clock.now += 24 * 3600
    assert b.admit("qstat", "req_3")["day"] == 1  # a new local day starts at zero
    assert b.usage("req_1")["request"] == 1  # a request's own count does not reset with the day


def test_a_login_with_no_request_counts_against_the_day_only(tmp_path):
    b = budget(tmp_path, per_request=1, per_day=10)
    for _ in range(4):
        b.admit("qstat")  # no request id: the request limit has nothing to count
    assert b.usage()["today"] == 4


def test_the_count_survives_a_restart_and_is_shared_between_instances(tmp_path):
    first = budget(tmp_path, per_request=3)
    first.admit("qstat", "req_1"), first.admit("qstat", "req_1")
    second = budget(tmp_path, per_request=3)  # a restarted runner, or the staff MCP process
    second.admit("qstat", "req_1")
    with pytest.raises(hb.LoginLimitReached):
        first.admit("qstat", "req_1")


def test_concurrent_logins_never_pass_the_limit(tmp_path):
    b = budget(tmp_path, per_request=10)
    outcomes = []

    def login():
        try:
            b.admit("qstat", "req_1")
            outcomes.append("ok")
        except hb.LoginLimitReached:
            outcomes.append("refused")

    threads = [threading.Thread(target=login) for _ in range(25)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count("ok") == 10 and outcomes.count("refused") == 15


def test_request_scope_names_the_request_and_nests_back(tmp_path):
    b = budget(tmp_path, per_request=1)
    with hb.request_scope("req_1"):
        b.admit("qstat")
        with pytest.raises(hb.LoginLimitReached):
            b.admit("qstat")
        with hb.request_scope("req_2"):
            b.admit("qstat")
        assert hb.current_request() == "req_1"
    assert hb.current_request() is None


def test_exhausted_reports_the_limit_without_counting(tmp_path):
    b = budget(tmp_path, per_request=1, per_day=5)
    assert b.exhausted("req_1") is None
    b.admit("qstat", "req_1")
    stop = b.exhausted("req_1")
    assert stop is not None and stop.scope == "request"
    assert b.usage("req_1")["request"] == 1


# ---- the user's answers -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [("", None), ("  ", None), ("none", None), ("제한 없음", None), ("7", 7), (" 12 ", 12)])
def test_parse_limit_reads_a_number_or_nothing(text, expected):
    assert hb.parse_limit(text) == expected


@pytest.mark.parametrize("text", ["0", "-3", "abc", "2.5"])
def test_parse_limit_refuses_anything_else(text):
    with pytest.raises(ValueError):
        hb.parse_limit(text)


def test_ask_limits_stores_numbers_and_treats_a_blank_as_final(capsys):
    answers = iter(["abc", "4", ""])  # a bad answer is asked again; a blank means no limit
    hpc = {"scheduler": "slurm", "login_limit_per_day": 99}
    hb.ask_limits(hpc, lambda label, default: next(answers))
    assert hpc["login_limit_per_request"] == 4
    assert "login_limit_per_day" not in hpc  # the blank removed the old value: no limit
    assert hpc["login_limits_decided"] is True
    assert "정수" in capsys.readouterr().out


def test_nothing_in_the_repository_defaults_to_an_institutions_limits():
    hpc = HpcSettings()
    assert hpc.login_limit_per_request is None and hpc.login_limit_per_day is None and hpc.login_limits_decided is False


def test_the_limits_must_be_positive_whole_numbers():
    with pytest.raises(ValueError):
        HpcSettings(login_limit_per_request=0)
    assert HpcSettings(login_limit_per_day=30).login_limit_per_day == 30


# ---- the scheduler ------------------------------------------------------------------------------------------------

def scheduler_with(tmp_path, monkeypatch, **hpc):
    settings = Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state")},
                                        "hpc": {"scheduler": "slurm", "ssh_host": "login.example", "user": "lab", **hpc}})
    spawned = []

    def run(argv, **kwargs):
        spawned.append(list(argv))
        return CompletedProcess(argv, 0, "11|RUNNING|None|a\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    return Scheduler(settings.hpc), spawned, settings


def test_every_ssh_command_is_one_counted_login_and_the_limit_refuses_the_next_without_spawning(tmp_path, monkeypatch):
    backend, spawned, settings = scheduler_with(tmp_path, monkeypatch, login_limit_per_request=2)
    with hb.request_scope("req_1"):
        backend.status("11")
        backend.status("11")
        with pytest.raises(hb.LoginLimitReached):
            backend.status("11")
    assert len(spawned) == 2 and all(argv[0] == "ssh" for argv in spawned)  # the third never reached subprocess
    assert hb.budget_for(settings.hpc).usage("req_1")["request"] == 2


def test_the_limit_reaches_submit_and_cancel_as_a_readable_error(tmp_path, monkeypatch):
    backend, spawned, _ = scheduler_with(tmp_path, monkeypatch, login_limit_per_day=1)
    backend._run(["squeue"])  # the one allowed login
    with pytest.raises(RuntimeError, match="HPC 로그인 상한에 닿았습니다") as submit:
        backend.submit("/tmp/job.sh", "align")
    assert isinstance(submit.value, hb.LoginLimitReached) and "오늘의" in str(submit.value)
    with pytest.raises(hb.LoginLimitReached):
        backend.cancel("11")
    assert len(spawned) == 1


def test_a_batch_status_reports_the_limit_for_every_job(tmp_path, monkeypatch):
    backend, _, _ = scheduler_with(tmp_path, monkeypatch, login_limit_per_day=1)
    backend._run(["squeue"])
    results = backend.statuses(["11", "12"])
    assert all(isinstance(v, hb.LoginLimitReached) for v in results.values())


def test_without_ssh_host_nothing_is_counted(tmp_path, monkeypatch):
    settings = Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state")},
                                        "hpc": {"scheduler": "slurm", "user": "lab", "login_limit_per_day": 1}})
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: CompletedProcess(argv, 0, "", ""))
    backend = Scheduler(settings.hpc)
    for _ in range(3):
        backend._run(["squeue"])  # the runner is on the login node: no ssh, no login
    assert hb.budget_for(settings.hpc).usage()["today"] == 0


def test_a_scheduler_built_from_settings_alone_finds_the_ledger_in_the_state_folder(tmp_path):
    settings = Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state")},
                                        "hpc": {"scheduler": "sge", "ssh_host": "login.example"}})
    assert hb.budget_for(settings.hpc).path == tmp_path / "state" / hb.FILE
    assert hb.budget_for(HpcSettings()) is None  # a bare HpcSettings has no state folder: it cannot count


def test_the_example_config_documents_the_limits_without_a_value():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "config" / "labhq.example.yaml").read_text(encoding="utf-8")
    assert "login_limit_per_request" in text and "login_limit_per_day" in text
    hpc = yaml.safe_load(text)["hpc"]
    assert hpc.get("login_limit_per_request") is None and hpc.get("login_limit_per_day") is None
