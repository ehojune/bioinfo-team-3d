"""The request deadline ends the staff CLI and every process it started (#1232, from the #877 rev6 driver).

A real gateway and runner run as processes. The CLI is a fake (tests/fixtures/fake_deadline_cli.py, no model call) that
starts a child and a grandchild. The gateway's own deadline loop (`enforce_request_deadlines`) cancels the request, the
runner ends the process tree, and `deadline_receipt` says the stop is confirmed. A process outside the request is not
touched. Only the 60 s floor of `request_wall_clock_s` and the 15 s look interval are shortened, in the gateway process.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
import yaml

from labhq.settings import IsolatedEngineBin, Settings
from scripts import repeat_fake_fixture as fixture

ROOT = Path(__file__).resolve().parents[1]
FAKE_CLI = ROOT / "tests" / "fixtures" / "fake_deadline_cli.py"
LIMIT_S = 6
SLEEPER = "import time; time.sleep(3600)"

GATEWAY_PATCH = (
    "from labhq.gateway import server as g\n"
    "g.REQUEST_DEADLINE_TICK_S = 1.0\n"
    f"g.Hub._new_wall_clock = lambda self, created_at: {{'enabled': True, 'limit_s': {LIMIT_S}, 'held_s': 0.0,"
    f" 'deadline_at': created_at + {LIMIT_S}}}\n")


def alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"


def kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


class Instance:
    """A gateway and a runner started the way `labhq gateway` and `labhq runner` start, under one empty directory."""

    def __init__(self, root: Path):
        self.root, self.fake = root, root / "fake"
        settings = fixture.configure(Settings(), root)
        settings.gateway.client_token = uuid.uuid4().hex
        settings.gateway.runner_token = uuid.uuid4().hex
        settings.runner.force_engine = None
        settings.runner.task_timeout_s = 900
        settings.engines.claude_code = IsolatedEngineBin(
            bin=sys.executable, prefix_args=[str(FAKE_CLI)], env={"FAKE_DIR": str(self.fake)}, isolate_user_config=False)
        self.config = root / "config.yaml"
        self.config.write_text(yaml.safe_dump(settings.model_dump(mode="json")), encoding="utf-8")
        self.base = f"http://127.0.0.1:{settings.gateway.port}"
        self.headers = {"Authorization": f"Bearer {settings.gateway.client_token}", "Content-Type": "application/json"}
        self.procs: list[subprocess.Popen] = []

    def role(self, name: str) -> subprocess.Popen:
        code = (f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n" + (GATEWAY_PATCH if name == "gateway" else "") +
                f"from labhq import cli\nsys.argv = ['labhq', '-c', {str(self.config)!r}, {name!r}]\ncli.main()\n")
        log = open(self.root / f"{name}.log", "wb")
        proc = subprocess.Popen([sys.executable, "-X", "utf8", "-c", code], stdout=log, stderr=subprocess.STDOUT,
                                cwd=str(self.root), env={**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT)})
        self.procs.append(proc)
        return proc

    def call(self, path: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                         method="GET" if body is None else "POST", headers=self.headers)
        with urllib.request.urlopen(request, timeout=30) as reply:
            return json.loads(reply.read().decode())

    def wait_gateway(self, seconds: float = 90.0) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                self.call("/api/requests")
                return
            except Exception:
                time.sleep(0.5)
        raise AssertionError("the gateway did not come up: " + (self.root / "gateway.log").read_text(errors="replace")[-1500:])

    def pids(self, pattern: str) -> dict[int, str]:
        return {int(p.read_text()): p.name for p in self.fake.glob(pattern)} if self.fake.exists() else {}

    def stop(self) -> None:
        for proc in reversed(self.procs):
            proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                kill(proc.pid)
        for pid in self.pids("*.pid"):
            if alive(pid):
                kill(pid)


def wait_for(predicate, seconds: float, what: str):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"timed out after {seconds:.0f} s waiting for {what}")


def test_the_deadline_ends_the_cli_and_every_process_it_started_and_only_those():
    with tempfile.TemporaryDirectory(prefix="deadline1232-") as tmp:
        instance = Instance(Path(tmp))
        control = subprocess.Popen([sys.executable, "-c", SLEEPER])  # an unrelated process the deadline must leave alone
        try:
            instance.role("gateway")
            instance.wait_gateway()
            instance.role("runner")
            body = {"text": "Say hello", "mode": "direct", "agent_id": "analyst", "route": "auto",
                    "work_kind": "simple", "default_references": False, "budget_usd": 1.0}
            reply = instance.call("/api/requests", body)
            rid = reply.get("id") or reply.get("request_id")
            started = time.monotonic()

            def tree_up():
                seen = {kind: instance.pids(f"{kind}-*.pid") for kind in ("cli", "child", "grandchild")}
                ready = all(seen.values()) and (os.name != "nt" or (instance.fake / "escape-result.json").exists())
                return seen if ready else None

            seen = wait_for(tree_up, 60, "the fake CLI to start its child and grandchild")
            everyone = {pid for group in seen.values() for pid in group} | set(instance.pids("great-*.pid"))
            assert len(everyone) >= 3 and all(alive(pid) for pid in everyone)
            assert instance.call(f"/api/requests/{rid}")["status"] != "cancelled"

            def receipt():
                view = instance.call(f"/api/requests/{rid}")
                return view if view["status"] == "cancelled" and view.get("deadline_receipt") else None

            view = wait_for(receipt, 60, "the deadline cancel and its receipt")
            assert time.monotonic() - started >= LIMIT_S - 1  # not cut before the limit
            assert view["cancel_reason"] == "request_deadline"
            assert view["deadline_receipt"]["termination"] == "confirmed"
            assert view["deadline_receipt"]["cancelled_tasks"] and not view["deadline_receipt"]["unconfirmed_tasks"]
            everyone |= set(instance.pids("great-*.pid"))
            wait_for(lambda: not any(alive(pid) for pid in everyone), 30,
                     "every descendant to end: " + str({pid: alive(pid) for pid in everyone}))
            assert control.poll() is None and alive(control.pid)
        finally:
            control.kill()
            instance.stop()
