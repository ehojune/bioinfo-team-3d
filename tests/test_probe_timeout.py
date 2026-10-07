import os
import sys
import threading
import time

import pytest

from labhq.runner import versions
from labhq.runner.versions import _probe

# A child that starts a grandchild holding the same output handles, then sleeps: a probe must not wait for output
# handles another process still holds.
HANGER = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])\n"
    "time.sleep(20)\n"
)


def test_a_probe_that_hangs_with_a_grandchild_returns_at_the_timeout():
    started = time.monotonic()
    code, raw = _probe([sys.executable, "-c", HANGER], {}, timeout=1)
    assert (code, raw) == (None, "")
    assert time.monotonic() - started < 1 + versions._JOIN_SLACK + 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups; Windows ends the tree with taskkill /T")
def test_a_timed_out_probe_ends_the_grandchild_it_started(tmp_path):
    marker = tmp_path / "grandchild.pid"
    script = ("import subprocess, sys, time\n"
              "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
              f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
              "time.sleep(30)\n")
    assert _probe([sys.executable, "-c", script], {}, timeout=1) == (None, "")
    pid = int(marker.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _running(pid):
            return
        time.sleep(0.1)
    raise AssertionError(f"grandchild {pid} still runs after the probe timed out")


def _running(pid: int) -> bool:
    """A zombie has ended: where PID 1 does not reap orphans (a container), a killed grandchild stays one (#486)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def test_a_probe_returns_even_when_ending_the_child_never_returns(monkeypatch):
    # 2026-10-08: starting the Python Install Manager's `python3` alias never returned from CreateProcess inside Popen
    # and hung both runners before they connected. Whatever blocks inside, the caller gets (None, "") just after the timeout.
    release = threading.Event()
    monkeypatch.setattr(versions, "_run_probe", lambda argv, env, timeout: release.wait() and (0, "never"))
    started = time.monotonic()
    try:
        assert _probe(["python3", "--version"], {}, timeout=0.2) == (None, "")
        assert time.monotonic() - started < 0.2 + versions._JOIN_SLACK + 1
    finally:
        release.set()


def test_a_probe_reports_stdout_or_else_stderr():
    assert _probe([sys.executable, "-c", "print('python 3.12.10')"], {}, timeout=10) == (0, "python 3.12.10")
    code, raw = _probe([sys.executable, "-c", "import sys; sys.stderr.write('Python 3.12.10'); sys.exit(0)"], {},
                       timeout=10)
    assert (code, raw) == (0, "Python 3.12.10")
    assert _probe(["no-such-executable-labhq"], {}, timeout=5) == (None, "")
