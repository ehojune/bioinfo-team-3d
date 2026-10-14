"""Tree-kill never follows a stale parent PID (#589).

After `labhq up` exits, the gateway and runner launchers keep their old parent PID. Windows hands that PID to some
short-lived process, and a tree collected by parent PID (`taskkill /T`) ends the PI's gateway and runner with it. The
fake process tables below use PIDs no real machine hands out, and every site test replaces the process starters, so
a regression here cannot touch a real process.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import labhq.adapters.base as base
from labhq import cli, proc_tree
from labhq.proc_tree import Proc
from labhq.runner import versions

N = 910_000_000  # far above any PID Windows allocates
ROOT, KID, GRANDKID = N + 300, N + 301, N + 302
# The PI's gateway launcher and its children: created long before ROOT, yet their recorded parent PID is ROOT's
# because ROOT's PID once belonged to the `up` that started them.
ORPHAN, ORPHAN_CHILD, ORPHAN_GRANDCHILD = N + 400, N + 401, N + 402
BYSTANDER = N + 999


def _table() -> dict[int, tuple[int, int | None]]:
    return {
        ROOT: (N + 1, 1000), KID: (ROOT, 1100), GRANDKID: (KID, 1200),
        ORPHAN: (ROOT, 500), ORPHAN_CHILD: (ORPHAN, 510), ORPHAN_GRANDCHILD: (ORPHAN_CHILD, 520),
        BYSTANDER: (N + 1, 100),
    }


class World:
    """A fake process table: kill_tree reads `snapshot`, and kills land on the live table."""

    def __init__(self, rows):
        self.live = {pid: Proc(pid, ppid, created) for pid, (ppid, created) in rows.items()}
        self.killed: list[int] = []
        self.terminate_calls: list[tuple[int, int | None]] = []
        self.after_snapshot = None  # called once the snapshot is taken, to change the live table
        self.refuse: dict[int, str] = {}

    def snapshot(self):
        table = list(self.live.values())
        if self.after_snapshot:
            self.after_snapshot(self)
        return table

    def terminate(self, pid, created):
        self.terminate_calls.append((pid, created))
        if pid in self.refuse:
            raise OSError(self.refuse[pid])
        live = self.live.get(pid)
        if live is None:
            return proc_tree.GONE
        if created is not None and live.created != created:
            return proc_tree.REUSED
        del self.live[pid]
        self.killed.append(pid)
        return proc_tree.KILLED


@pytest.fixture
def world(monkeypatch):
    fake = World(_table())
    monkeypatch.setattr(proc_tree, "_snapshot", fake.snapshot)
    monkeypatch.setattr(proc_tree, "_terminate", fake.terminate)
    return fake


def test_a_process_older_than_its_claimed_parent_is_not_a_descendant():
    found = proc_tree.validated_descendants(ROOT, World(_table()).live.values())
    assert [p.pid for p in found] == [KID, GRANDKID]


def test_creation_time_equal_to_the_parents_counts_and_an_unreadable_one_does_not():
    table = World({10: (1, 100), 11: (10, 100), 12: (10, None), 13: (12, 500), 14: (11, 99)}).live.values()
    assert [p.pid for p in proc_tree.validated_descendants(10, table)] == [11]  # 12 unverifiable, 13 under it, 14 older


def test_nothing_is_validated_without_the_roots_own_creation_time():
    table = World({10: (1, None), 11: (10, 500)}).live.values()
    assert proc_tree.validated_descendants(10, table) == []
    assert proc_tree.validated_descendants(404, table) == []


def test_kill_tree_ends_real_descendants_leaves_first_and_leaves_the_stale_parent_orphan_alone(world):
    result = proc_tree.kill_tree(ROOT)
    assert result.killed == [GRANDKID, KID, ROOT] and not result.skipped and not result.failed
    assert world.killed == [GRANDKID, KID, ROOT]
    assert set(world.live) == {ORPHAN, ORPHAN_CHILD, ORPHAN_GRANDCHILD, BYSTANDER}
    assert not {ORPHAN, ORPHAN_CHILD, ORPHAN_GRANDCHILD} & {pid for pid, _ in world.terminate_calls}


def test_every_kill_carries_the_creation_time_seen_in_the_snapshot(world):
    proc_tree.kill_tree(ROOT)
    assert world.terminate_calls == [(GRANDKID, 1200), (KID, 1100), (ROOT, 1000)]


def test_a_pid_reused_between_snapshot_and_kill_is_skipped_and_the_rest_still_end(world):
    def reuse(w):
        w.live[KID] = Proc(KID, N + 1, 9999)  # KID exited and its PID went to a new, unrelated process

    world.after_snapshot = reuse
    result = proc_tree.kill_tree(ROOT)
    assert result.killed == [GRANDKID, ROOT] and result.skipped == [KID]
    assert world.live[KID].created == 9999  # the newcomer was not touched


def test_a_process_that_ends_between_snapshot_and_kill_is_skipped(world):
    world.after_snapshot = lambda w: w.live.pop(GRANDKID)
    result = proc_tree.kill_tree(ROOT)
    assert result.killed == [KID, ROOT] and result.skipped == [GRANDKID]


def test_a_root_whose_pid_now_belongs_to_another_process_is_left_alone(world):
    result = proc_tree.kill_tree(ROOT, created=7)  # the record said created=7, the live ROOT was created at 1000
    assert world.killed == [] and result.skipped == [ROOT] and not result.failed
    assert proc_tree.kill_tree(ROOT, created=1000).killed == [GRANDKID, KID, ROOT]


def test_a_missing_root_kills_nothing(world):
    result = proc_tree.kill_tree(N + 12345)
    assert world.terminate_calls == [] and result.skipped == [N + 12345]


def test_an_unreadable_process_table_ends_only_the_root_with_its_identity(world, monkeypatch):
    monkeypatch.setattr(proc_tree, "_snapshot", lambda: None)
    result = proc_tree.kill_tree(ROOT, created=1000)
    assert result.killed == [ROOT] and world.terminate_calls == [(ROOT, 1000)]


def test_one_process_that_refuses_to_die_is_reported_and_the_others_still_end(world):
    world.refuse[KID] = "Access is denied"
    result = proc_tree.kill_tree(ROOT)
    assert result.failed == {KID: "Access is denied"} and result.killed == [GRANDKID, ROOT]


def test_a_root_can_be_spared_while_a_descendant_refuses_to_die(world):
    world.refuse[KID] = "Access is denied"
    result = proc_tree.kill_tree(ROOT, spare_root_on_failure=True)
    assert result.failed == {KID: "Access is denied"} and result.killed == [GRANDKID]
    assert ROOT in world.live and ROOT not in {pid for pid, _ in world.terminate_calls}
    world.refuse.clear()
    assert proc_tree.kill_tree(ROOT, spare_root_on_failure=True).killed == [KID, ROOT]  # a retry finishes the tree


def test_child_pids_lists_only_validated_direct_children(world):
    world.live[N + 303] = Proc(N + 303, ROOT, 1300)
    assert proc_tree.child_pids(ROOT) == [KID, N + 303]
    assert proc_tree.child_pids(ROOT, created=1000) == [KID, N + 303]
    assert proc_tree.child_pids(ROOT, created=7) == []  # ROOT's PID is another process now
    assert proc_tree.child_pids(N + 5) == []


def test_child_pids_is_empty_when_the_table_cannot_be_read(monkeypatch):
    monkeypatch.setattr(proc_tree, "_snapshot", lambda: None)
    assert proc_tree.child_pids(ROOT) == []


@pytest.mark.parametrize("value, ticks", [("windows:123", 123), ("windows:", None), ("windows:x", None),
                                          ("proc:123", None), (None, None)])
def test_parse_created_reads_only_the_windows_identity(value, ticks):
    assert proc_tree.parse_created(value) == ticks


# --- the three kill sites -------------------------------------------------------------------------------------


class _Probe:
    """A timed-out version probe: Popen-shaped, and its own kill is a no-op."""

    def __init__(self, pid):
        self.pid = pid
        self.killed = False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 1


def _no_process_starts(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail(f"no process may be started to end a tree, got {args[:1]}")

    async def refuse_async(*args, **kwargs):
        pytest.fail(f"no process may be started to end a tree, got {args[:1]}")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse_async)


def test_a_timed_out_version_probe_ends_its_tree_through_the_helper_and_spares_the_orphan(world, monkeypatch):
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(versions, "os", SimpleNamespace(name="nt"))
    probe = _Probe(ROOT)
    versions._kill_tree(probe)
    assert world.killed == [GRANDKID, KID, ROOT] and probe.killed
    assert {ORPHAN, ORPHAN_CHILD, ORPHAN_GRANDCHILD} <= set(world.live)


class _StaffCli:
    """An asyncio subprocess-shaped staff CLI."""

    def __init__(self, pid):
        self.pid = pid
        self.returncode = None
        self.kill_called = False

    def kill(self):
        self.kill_called = True
        self.returncode = 1

    async def wait(self):
        return self.returncode


async def test_an_adapter_timeout_ends_the_cli_tree_through_the_helper_and_spares_the_orphan(world, monkeypatch):
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(base, "os", SimpleNamespace(name="nt"))
    proc = _StaffCli(ROOT)
    await base.AgentAdapter._kill(proc)
    assert world.killed == [GRANDKID, KID, ROOT] and proc.kill_called
    assert {ORPHAN, ORPHAN_CHILD, ORPHAN_GRANDCHILD} <= set(world.live)


async def test_an_adapter_kill_still_ends_the_cli_when_the_helper_fails(monkeypatch):
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(base, "os", SimpleNamespace(name="nt"))

    def broken(pid, **kwargs):
        raise MemoryError

    monkeypatch.setattr(proc_tree, "kill_tree", broken)
    proc = _StaffCli(ROOT)
    await base.AgentAdapter._kill(proc)
    assert proc.kill_called


def test_no_taskkill_tree_flag_is_left_in_the_package():
    root = Path(base.__file__).resolve().parents[1]
    pattern = re.compile(r"taskkill[^\n]*[\"']/T[\"']", re.IGNORECASE)
    offenders = [str(path.relative_to(root)) for path in root.rglob("*.py")
                 if pattern.search(path.read_text(encoding="utf-8"))]
    assert offenders == []


def test_cli_terminate_tree_uses_the_helper_with_the_recorded_identity(world, monkeypatch):
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    cli._terminate_tree(ROOT, created="windows:1000")
    assert world.killed == [GRANDKID, KID, ROOT]
    assert {ORPHAN, ORPHAN_CHILD, ORPHAN_GRANDCHILD} <= set(world.live)


def test_cli_terminate_tree_leaves_a_reused_pid_alone_without_an_error(world, monkeypatch):
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    cli._terminate_tree(ROOT, created="windows:7")
    assert world.killed == []


def test_cli_terminate_tree_refuses_an_identity_it_cannot_read(world, monkeypatch):
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    with pytest.raises(OSError, match="읽을 수 없어"):
        cli._terminate_tree(ROOT, created="proc:123")
    assert world.terminate_calls == []


def test_cli_terminate_tree_raises_when_the_recorded_process_cannot_be_ended(world, monkeypatch):
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli, "_process_created", lambda pid: "alive")
    world.refuse[ROOT] = "Access is denied"
    with pytest.raises(OSError, match="Access is denied"):
        cli._terminate_tree(ROOT, created="windows:1000")


def test_cli_terminate_tree_raises_when_a_descendant_cannot_be_ended_and_keeps_the_root_for_a_retry(world, monkeypatch):
    """The bot's P1 on #597: only a failure on the root counted, so a venv interpreter that refused to die under a
    launcher that did die let `down` drop the PID record and report success."""
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    world.refuse[KID] = "Access is denied"
    with pytest.raises(OSError) as raised:
        cli._terminate_tree(ROOT, created="windows:1000")
    assert f"PID {KID}: Access is denied" in str(raised.value) and f"PID {ROOT}" in str(raised.value)
    assert world.killed == [GRANDKID] and ROOT in world.live  # the record still names a live process: `down` can retry


def test_cli_terminate_tree_names_every_process_it_could_not_end(world, monkeypatch):
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    world.refuse[GRANDKID] = "Access is denied"
    world.refuse[ROOT] = "should not be tried"
    with pytest.raises(OSError, match=rf"PID {GRANDKID}: Access is denied"):
        cli._terminate_tree(ROOT)
    assert ROOT not in {pid for pid, _ in world.terminate_calls}


# --- a real Windows parent and child --------------------------------------------------------------------------

_PAIR = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.argv[2], '-c', 'import time; time.sleep(120)'], creationflags=0x00000008)\n"
    "open(sys.argv[1], 'w').write(str(child.pid))\n"
    "time.sleep(120)\n"
)
_DETACHED = 0x00000008  # DETACHED_PROCESS: no console, so no conhost.exe child to confuse the count


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree")
def test_the_helper_ends_exactly_a_real_parent_and_child_and_not_a_sibling(tmp_path):
    python = getattr(sys, "_base_executable", sys.executable)  # not a venv launcher, which would add a process
    marker = tmp_path / "child.pid"
    sibling = subprocess.Popen([python, "-c", "import time; time.sleep(120)"], creationflags=_DETACHED)
    parent = subprocess.Popen([python, "-c", _PAIR, str(marker), python], creationflags=_DETACHED)
    child_pid = child_created = None
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (marker.is_file() and marker.read_text().strip()):
            time.sleep(0.05)
        child_pid = int(marker.read_text())
        child_created = cli._process_created(child_pid)
        parent_created = cli._process_created(parent.pid)
        assert child_created and parent_created

        result = proc_tree.kill_tree(parent.pid, created=proc_tree.parse_created(parent_created))

        assert result.killed == [child_pid, parent.pid] and not result.failed
        assert parent.wait(timeout=15) is not None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and cli._process_created(child_pid) is not None:
            time.sleep(0.05)
        assert cli._process_created(child_pid) is None
        assert sibling.poll() is None  # not part of the pair's tree
    finally:
        for popen in (sibling, parent):
            if popen.poll() is None:
                popen.kill()
            popen.wait(timeout=15)
        if child_pid is not None and child_created:
            proc_tree.kill_tree(child_pid, created=proc_tree.parse_created(child_created))
