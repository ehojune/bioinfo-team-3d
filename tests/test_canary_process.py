import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from labhq import canary_process


def test_unsupported_rejects_before_start(monkeypatch):
    monkeypatch.setattr(canary_process, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(canary_process.subprocess, "Popen", lambda *a, **k: pytest.fail("started"))
    with pytest.raises(canary_process.BoundaryError, match="unsupported"):
        canary_process.run(None, "all")


@pytest.mark.parametrize("outcome", ["success", "timeout", "bootstrap", "cleanup", "assignment"])
def test_parent_barrier_and_cleanup(monkeypatch, outcome):
    events = []

    class Process:
        returncode = 0 if outcome != "bootstrap" else 1

        def communicate(self, payload, timeout):
            events.append("release")
            assert json.loads(payload)["selector"] == "worker"
            if outcome == "timeout":
                raise subprocess.TimeoutExpired("bootstrap", timeout)
            return b"[]", b""

        def poll(self):
            return None

        def kill(self):
            events.append("kill")

        def wait(self, timeout):
            events.append("wait")

    class Job:
        def __init__(self, process):
            events.append("assigned")
            if outcome == "assignment":
                raise canary_process.BoundaryError("assignment")

        def cleanup(self):
            events.append("cleanup")
            if outcome == "cleanup":
                raise canary_process.BoundaryError("cleanup")

        def close(self):
            events.append("close")

    monkeypatch.setattr(canary_process, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(canary_process.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(canary_process, "WindowsJob", Job)
    settings = SimpleNamespace(model_dump=lambda **kwargs: {})
    if outcome == "success":
        assert canary_process.run(settings, "worker") == []
    else:
        with pytest.raises(canary_process.BoundaryError):
            canary_process.run(settings, "worker")
    if outcome == "assignment":
        assert events == ["assigned", "kill", "wait"]
    else:
        assert events == ["assigned", "release", "cleanup", "close", "wait"]


@pytest.mark.skipif(os.name != "nt", reason="Windows Job integration")
def test_job_terminates_child_in_separate_process_group(tmp_path):
    marker = tmp_path / "started"
    code = ("import pathlib, subprocess, sys, time\n"
            "sys.stdin.buffer.read(1)\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
            "creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "pathlib.Path(sys.argv[1]).touch()\n"
            "time.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, "-c", code, str(marker)], stdin=subprocess.PIPE)
    job = None
    try:
        job = canary_process.WindowsJob(proc)
        proc.stdin.write(b"x")
        proc.stdin.flush()
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        assert job.active() >= 2
        job.cleanup()
        assert job.active() == 0
    finally:
        if job:
            job.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


@pytest.mark.parametrize("selector,roster,expected", [("worker", 5, 390), ("all", 3, 1170), ("all", 0, 390)])
def test_deadline_scales_with_selected_agents(monkeypatch, selector, roster, expected):
    from labhq import doctor

    seen = []

    class Process:
        returncode = 0

        def communicate(self, payload, timeout=None):
            seen.append(timeout)
            return b"[]", b""

        def wait(self, timeout=None):
            return 0

    class Job:
        def __init__(self, process):
            pass

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    monkeypatch.setattr(canary_process, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(canary_process.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(canary_process, "WindowsJob", Job)
    monkeypatch.setattr(doctor, "_roster", lambda settings: [object()] * roster)
    canary_process.run(SimpleNamespace(model_dump=lambda **kwargs: {}), selector)
    assert seen == [expected]
