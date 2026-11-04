"""While a task runs, the runner halts it once when a drive it fills drops below the floor (#678)."""
import asyncio
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest

from labhq import disk_space
from labhq.disk_space import GIB
from labhq.models import Task
from labhq.runner import daemon
from labhq.runner.breaker import stage_text
from labhq.runner.daemon import Runner
from labhq.settings import Settings

Space = namedtuple("Space", "total used free")


def runner_with(tmp_path, *, watch_s=0.01, halt=True, floor=2.0, held=True):
    runner = Runner.__new__(Runner)  # no store, daemon, writer thread or host configuration is opened
    s = Settings()
    s.runner.disk_watch_s, s.runner.disk_watch_halt, s.runner.disk_fail_free_gb = watch_s, halt, floor
    s.runner.state_dir = str(tmp_path / "state")
    runner.s = s
    events, applied = [], []
    live = {"held": held}

    async def emit(event):
        events.append(event)

    async def apply(message, **kw):
        applied.append(message)
        live["held"] = False  # a halted task ends and the controls let go of it
        return {"paused": False, "halt": True, "action": "halt", "applied": True}

    runner.controls = SimpleNamespace(holds=lambda tid: live["held"], apply=apply)
    runner.emit = emit
    return runner, events, applied, live


def task():
    return Task(id="t1", agent_id="a1", request_id="r", prompt="fake; never execute")


AGENT = SimpleNamespace(id="a1")


def free_gib(value):
    return lambda folder: Space(100 * GIB, 0, int(value * GIB))


async def settle(watcher, timeout=2):
    await asyncio.wait_for(asyncio.gather(watcher, return_exceptions=True), timeout)


@pytest.mark.asyncio
async def test_a_drive_below_the_floor_halts_the_task_once(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path)
    monkeypatch.setattr(disk_space, "usage", free_gib(1.0))
    await settle(asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path])))
    assert applied == [{"task_id": "t1", "action": "halt"}]
    kinds = [e.type for e in events]
    assert kinds == ["agent.log", "agent.control", "agent.breaker"]
    record = events[-1].data
    assert record["signal"] == "disk" and record["enforcement"] == "halt" and record["free_gib"] == 1.0
    assert "episode" not in record  # not a breaker restriction the PI could release
    assert events[1].data["source"] == "disk"
    assert "멈췄어요" in stage_text(record)


@pytest.mark.asyncio
async def test_a_roomy_drive_never_halts(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path)
    monkeypatch.setattr(disk_space, "usage", free_gib(50))
    watcher = asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path]))
    await asyncio.sleep(0.1)
    live["held"] = False  # the task ended
    await settle(watcher)
    assert applied == [] and events == []


@pytest.mark.asyncio
async def test_the_first_low_drive_of_several_is_the_one_named(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path)
    low = tmp_path / "low"

    def usage(folder):
        return Space(100 * GIB, 0, int((0.5 if Path(folder) == low else 50) * GIB))

    monkeypatch.setattr(disk_space, "usage", usage)
    await settle(asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path, low])))
    assert events[-1].data["path"] == low.as_posix()


@pytest.mark.asyncio
async def test_an_unreadable_drive_is_skipped_and_never_halts(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path)

    def broken(folder):
        raise OSError("no such drive")

    monkeypatch.setattr(disk_space, "usage", broken)
    watcher = asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path]))
    await asyncio.sleep(0.1)
    live["held"] = False
    await settle(watcher)
    assert applied == [] and events == []


@pytest.mark.asyncio
async def test_with_halting_off_it_only_warns(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path, halt=False)
    monkeypatch.setattr(disk_space, "usage", free_gib(1.0))
    await settle(asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path])))
    assert applied == []
    assert [e.type for e in events] == ["agent.log", "agent.breaker"]
    assert events[-1].data["enforcement"] == "off" and "멈추지 않았어요" in stage_text(events[-1].data)


@pytest.mark.asyncio
async def test_a_failed_halt_is_retried_at_the_next_beat(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path)
    real_apply, failures = runner.controls.apply, []

    async def flaky(message, **kw):
        if len(failures) < 2:
            failures.append(message)
            raise OSError("control file locked")
        return await real_apply(message, **kw)

    runner.controls.apply = flaky
    monkeypatch.setattr(disk_space, "usage", free_gib(1.0))
    await settle(asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path])))
    assert len(failures) == 2 and applied == [{"task_id": "t1", "action": "halt"}]  # the third try went through
    assert events[-1].data["enforcement"] == "halt"  # reported once, when it worked


@pytest.mark.asyncio
async def test_a_task_that_ended_during_the_halt_stops_the_watch_quietly(tmp_path, monkeypatch):
    runner, events, applied, live = runner_with(tmp_path)

    async def gone(message, **kw):
        return None  # the controls let go of the task meanwhile

    runner.controls.apply = gone
    monkeypatch.setattr(disk_space, "usage", free_gib(1.0))
    await settle(asyncio.ensure_future(runner._disk_watch(task(), AGENT, [tmp_path])))
    assert events == []


@pytest.mark.asyncio
async def test_watch_starts_per_task_only_when_enabled(tmp_path):
    runner, events, applied, live = runner_with(tmp_path)
    runner._start_disk_watch(task(), AGENT, tmp_path / "ws")
    assert "t1" in runner._disk_watchers
    runner._start_disk_watch(task(), AGENT, tmp_path / "ws")  # a second call does not start a second watcher
    assert len(runner._disk_watchers) == 1
    live["held"] = False
    await settle(runner._disk_watchers["t1"])
    await asyncio.sleep(0)
    assert runner._disk_watchers == {}  # it ends with the task and leaves nothing behind

    for kwargs in ({"watch_s": 0}, {"floor": 0}):
        quiet, *_ = runner_with(tmp_path, **kwargs)
        quiet._start_disk_watch(task(), AGENT, tmp_path / "ws")
        assert quiet.__dict__.get("_disk_watchers", {}) == {}
    fix = Task(id="t2", agent_id="a1", request_id="r", prompt="x", meta={"kind": "facilities_fix"})
    runner._start_disk_watch(fix, AGENT, tmp_path / "ws")
    assert "t2" not in runner._disk_watchers  # clearing a full workspace is what a fix is for


@pytest.mark.asyncio
async def test_the_bulk_data_root_is_watched_when_set(tmp_path):
    runner, events, applied, live = runner_with(tmp_path)
    runner.s.runner.bulk_data_root = str(tmp_path / "bulk")
    seen = []
    real = runner._disk_watch

    async def spy(task_, agent, folders):
        seen.extend(Path(f).as_posix() for f in folders)
        live["held"] = False

    runner._disk_watch = spy
    runner._start_disk_watch(task(), AGENT, tmp_path / "ws")
    await asyncio.sleep(0.05)
    assert (tmp_path / "bulk").as_posix() in seen and (tmp_path / "ws").as_posix() in seen
    assert Path(runner.s.path(runner.s.runner.state_dir)).as_posix() in seen
    assert real is not None and daemon.Runner is Runner


@pytest.mark.asyncio
async def test_writable_project_folders_are_watched_too(tmp_path):
    runner, events, applied, live = runner_with(tmp_path)
    seen = []

    async def spy(task_, agent, folders):
        seen.extend(Path(f).as_posix() for f in folders)
        live["held"] = False

    runner._disk_watch = spy
    project = tmp_path / "project"
    runner._start_disk_watch(task(), AGENT, tmp_path / "ws", [str(project), str(project)])
    await asyncio.sleep(0.05)
    assert seen.count(project.as_posix()) == 1  # on another drive it is the one that fills


@pytest.mark.asyncio
async def test_a_read_only_run_does_not_watch_the_bulk_data_root(tmp_path):
    runner, events, applied, live = runner_with(tmp_path)
    runner.s.runner.bulk_data_root = str(tmp_path / "bulk")
    seen = []

    async def spy(task_, agent, folders):
        seen.extend(Path(f).as_posix() for f in folders)
        live["held"] = False

    runner._disk_watch = spy
    runner._start_disk_watch(task(), AGENT, tmp_path / "ws", read_only=True)
    await asyncio.sleep(0.05)
    assert (tmp_path / "ws").as_posix() in seen  # its own workspace is still watched
    assert (tmp_path / "bulk").as_posix() not in seen  # a drive it cannot write is no reason to halt it


# ----- a local job gets the same disk check as a shell command (#1167) -----

def job_runner(tmp_path, monkeypatch, *, approve=True, free_gib=0.5):
    """A runner with just what `_submit_local_job` reads, a fake broker and a fake process start."""
    runner = Runner.__new__(Runner)
    s = Settings()
    s.runner.state_dir = str(tmp_path / "state")
    s.runner.disk_fail_free_gb = 2.0
    runner.s = s
    work = tmp_path / "ws"
    work.mkdir()
    runner.workspaces = {"t1": SimpleNamespace(dir=work)}
    runner.active_roots = {"t1": (False, [])}
    runner.tool_contexts = {}
    runner.submitting, runner.jobs, runner._local_procs = {}, {}, {}
    cards, events, started = [], [], []

    async def request_approval(request):
        cards.append(request)
        return {"approved": approve, "note": "no" if not approve else ""}

    async def emit(event):
        events.append(event)

    async def track(*args, **kwargs):
        return None

    def start(state_dir, job_id, command, cwd, env):
        started.append(command)
        return {"pid": 1}, object()

    runner.broker = SimpleNamespace(request_approval=request_approval)
    runner.emit, runner._track_job, runner._ensure_local_watch = emit, track, lambda: None
    monkeypatch.setattr(daemon.local_jobs, "start", start)
    monkeypatch.setattr(disk_space, "usage", lambda path: Space(100 * GIB, 0, int(free_gib * GIB)))
    return runner, cards, events, started


def submit(runner, command):
    return runner._submit_local_job({"task_id": "t1", "agent_id": "a", "request_id": "r", "command": command, "name": "j"})


@pytest.mark.asyncio
async def test_a_local_job_that_unpacks_onto_a_full_drive_asks_before_it_starts(tmp_path, monkeypatch):
    runner, cards, events, started = job_runner(tmp_path, monkeypatch, approve=False)
    answer = await submit(runner, "unzip raw.zip -d out")
    assert answer["submitted"] is False and "no" in answer["reason"] and started == []
    assert len(cards) == 1 and "여유" in cards[0].summary and cards[0].kind == "tool_permission"


@pytest.mark.asyncio
async def test_a_pi_approval_starts_the_local_job_that_the_disk_check_asked_about(tmp_path, monkeypatch):
    runner, cards, events, started = job_runner(tmp_path, monkeypatch, approve=True)
    answer = await submit(runner, "unzip raw.zip -d out")
    assert answer["submitted"] is True and started == ["unzip raw.zip -d out"] and len(cards) == 1


@pytest.mark.asyncio
async def test_a_local_job_on_a_roomy_drive_starts_without_a_card(tmp_path, monkeypatch):
    runner, cards, events, started = job_runner(tmp_path, monkeypatch, free_gib=500)
    answer = await submit(runner, "unzip raw.zip -d out")
    assert answer["submitted"] is True and cards == [] and started


@pytest.mark.asyncio
async def test_a_local_job_with_no_download_or_unpack_is_never_asked_about(tmp_path, monkeypatch):
    runner, cards, events, started = job_runner(tmp_path, monkeypatch, free_gib=0.5)
    answer = await submit(runner, "python -c 'print(1)'")
    assert answer["submitted"] is True and cards == []


@pytest.mark.asyncio
async def test_a_low_drive_with_an_unknown_size_only_warns_and_the_job_starts(tmp_path, monkeypatch):
    runner, cards, events, started = job_runner(tmp_path, monkeypatch, free_gib=6)  # under the warning level, over the floor
    answer = await submit(runner, "curl -O https://example.org/big.bin")
    assert answer["submitted"] is True and cards == [] and started
    assert any(getattr(e, "data", {}).get("policy_rule") == "disk_space" for e in events), events


@pytest.mark.asyncio
async def test_a_command_the_policy_already_asks_about_gets_one_card_not_two(tmp_path, monkeypatch):
    """The disk check only turns an allow into an ask: a command the gate already asks about is not asked about twice."""
    from labhq.policy import evaluate_tool
    runner, cards, events, started = job_runner(tmp_path, monkeypatch, approve=False)
    command = f'unzip raw.zip -d "{tmp_path / "elsewhere" / "out"}"'  # outside the workspace and onto a full drive
    probe = evaluate_tool("Bash", {"command": command}, runner.s.policy, allowed_roots=[str(tmp_path / "ws")],
                          workdir=str(tmp_path / "ws"))
    if probe.action == "allow":
        pytest.skip("this platform's gate does not ask about a write outside the workspace")
    answer = await submit(runner, command)
    assert len(cards) == 1 and started == []
    assert answer["submitted"] is False or probe.action == "deny"


@pytest.mark.asyncio
async def test_a_probe_failure_never_stops_a_local_job_the_policy_allowed(tmp_path, monkeypatch):
    runner, cards, events, started = job_runner(tmp_path, monkeypatch)

    def broken(command, workdir, **kwargs):
        raise RuntimeError("probe bug")

    monkeypatch.setattr(disk_space, "shell_check", broken)
    answer = await submit(runner, "unzip raw.zip -d out")
    assert answer["submitted"] is True and cards == []

