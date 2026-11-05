"""Tree-kill never follows a stale parent PID (#589).

After `labhq up` exits, the gateway and runner launchers keep their old parent PID. Windows hands that PID to some
short-lived process, and a tree collected by parent PID alone ends the PI's gateway and runner with it. The
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
from tests.proc_table_fake import FakeProcessTable

N = 910_000_000  # far above any PID Windows allocates
ROOT, KID, GRANDKID = N + 300, N + 301, N + 302
SIB, SIBKID = N + 303, N + 304  # a second branch under ROOT
# The PI's gateway launcher and its children: created long before ROOT, yet their recorded parent PID is ROOT's
# because ROOT's PID once belonged to the `up` that started them.
ORPHAN, ORPHAN_CHILD, ORPHAN_GRANDCHILD = N + 400, N + 401, N + 402
BYSTANDER = N + 999
ORPHANS = {ORPHAN, ORPHAN_CHILD, ORPHAN_GRANDCHILD}


def _table() -> dict[int, tuple[int, int | None]]:
    return {
        ROOT: (N + 1, 1000), KID: (ROOT, 1100), GRANDKID: (KID, 1200),
        ORPHAN: (ROOT, 500), ORPHAN_CHILD: (ORPHAN, 510), ORPHAN_GRANDCHILD: (ORPHAN_CHILD, 520),
        BYSTANDER: (N + 1, 100),
    }


def _two_branches() -> dict[int, tuple[int, int | None]]:
    """ROOT -> KID -> GRANDKID, and ROOT -> SIB -> SIBKID."""
    return {
        ROOT: (N + 1, 1000), KID: (ROOT, 1100), GRANDKID: (KID, 1200),
        SIB: (ROOT, 1150), SIBKID: (SIB, 1250),
    }


@pytest.fixture
def world(monkeypatch):
    return FakeProcessTable(_table()).install(monkeypatch)


# --- which processes count as descendants ---------------------------------------------------------------------


def test_a_process_older_than_its_claimed_parent_is_not_a_descendant():
    found = proc_tree.validated_descendants(ROOT, FakeProcessTable(_table()).live.values())
    assert [p.pid for p in found] == [KID, GRANDKID]


def test_creation_time_equal_to_the_parents_counts_and_an_unreadable_one_does_not():
    rows = {10: (1, 100), 11: (10, 100), 12: (10, None), 13: (12, 500), 14: (11, 99)}
    table = FakeProcessTable(rows).live.values()
    assert [p.pid for p in proc_tree.validated_descendants(10, table)] == [11]  # 12 unverifiable, 13 under it, 14 older


def test_nothing_is_validated_without_the_roots_own_creation_time():
    table = FakeProcessTable({10: (1, None), 11: (10, 500)}).live.values()
    assert proc_tree.validated_descendants(10, table) == []
    assert proc_tree.validated_descendants(404, table) == []


def test_child_pids_lists_only_validated_direct_children(world):
    world.live[N + 305] = Proc(N + 305, ROOT, 1300)
    assert proc_tree.child_pids(ROOT) == [KID, N + 305]
    assert proc_tree.child_pids(ROOT, created=1000) == [KID, N + 305]
    assert proc_tree.child_pids(ROOT, created=7) == []  # ROOT's PID is another process now
    assert proc_tree.child_pids(N + 5) == []


def test_child_pids_is_empty_when_the_table_cannot_be_read(monkeypatch):
    monkeypatch.setattr(proc_tree, "_snapshot", lambda: None)
    assert proc_tree.child_pids(ROOT) == []


@pytest.mark.parametrize("value, ticks", [("windows:123", 123), ("windows:", None), ("windows:x", None),
                                          ("proc:123", None), (None, None)])
def test_parse_created_reads_only_the_windows_identity(value, ticks):
    assert proc_tree.parse_created(value) == ticks


# --- kill_tree ------------------------------------------------------------------------------------------------


def test_kill_tree_ends_real_descendants_leaves_first_and_leaves_the_stale_parent_orphan_alone(world):
    result = proc_tree.kill_tree(ROOT)
    assert result.killed == [GRANDKID, KID, ROOT] and not result.skipped and not result.failed
    assert world.killed == [GRANDKID, KID, ROOT]
    assert set(world.live) == ORPHANS | {BYSTANDER}


def test_every_candidate_is_opened_once_and_every_handle_is_closed(world):
    proc_tree.kill_tree(ROOT)
    assert sorted(world.opened) == sorted([ROOT, KID, GRANDKID, *ORPHANS])  # the orphans are pinned, then dropped
    assert len(set(world.opened)) == len(world.opened) and world.leaked_handles == 0


def test_a_pid_reused_before_it_is_opened_is_not_killed(world):
    """The bot's P1 on #597: between the table read and OpenProcess, KID exited and its PID went to a new process
    created after ROOT. The first read's parent PID (ROOT) joined with the newcomer's creation time passed validation,
    and the kill-time identity check agreed because it read the same newcomer."""
    newcomer = Proc(KID, N + 1, 9999)  # a different parent, created after ROOT
    world.hooks[1] = lambda w: w.live.__setitem__(KID, newcomer)
    result = proc_tree.kill_tree(ROOT)
    assert world.live[KID] is newcomer  # untouched
    assert world.killed == [ROOT] and result.killed == [ROOT]
    assert GRANDKID in world.live  # its recorded parent is the dead KID, so it cannot be told from a stale orphan
    assert world.leaked_handles == 0


def test_a_process_that_exited_before_it_is_opened_is_skipped(world):
    world.hooks[1] = lambda w: w.live.pop(GRANDKID)
    result = proc_tree.kill_tree(ROOT)
    assert result.killed == [KID, ROOT] and GRANDKID not in [h.pid for h in world.opened_handles]


def test_a_pid_that_changes_hands_after_the_handles_are_open_is_still_killed_through_its_handle(world):
    """An open handle pins its process, so even a PID that is handed over later cannot redirect the kill."""
    newcomer = Proc(KID, N + 1, 9999)
    world.hooks[2] = lambda w: w.live.__setitem__(KID, newcomer)  # after the second read: the handles are all open
    result = proc_tree.kill_tree(ROOT)
    assert world.live[KID] is newcomer
    assert [p.created for p in world.killed_procs] == [1200, 1100, 1000]  # the originals, not the newcomer
    assert result.killed == [GRANDKID, KID, ROOT]


def test_a_root_whose_pid_now_belongs_to_another_process_is_left_alone(world):
    result = proc_tree.kill_tree(ROOT, created=7)  # the record said created=7, the live ROOT was created at 1000
    assert world.killed == [] and result.skipped == [ROOT] and not result.failed
    assert world.leaked_handles == 0
    assert proc_tree.kill_tree(ROOT, created=1000).killed == [GRANDKID, KID, ROOT]


def test_a_missing_root_kills_nothing(world):
    result = proc_tree.kill_tree(N + 12345)
    assert world.killed == [] and result.skipped == [N + 12345]


def test_an_unreadable_process_table_ends_only_the_root_with_its_identity(world, monkeypatch):
    monkeypatch.setattr(proc_tree, "_table", lambda: None)
    result = proc_tree.kill_tree(ROOT, created=1000)
    assert result.killed == [ROOT] and world.killed == [ROOT] and world.opened == [ROOT]


def test_a_second_table_read_that_fails_ends_only_the_root(world, monkeypatch):
    reads = iter([world.table(), None])
    monkeypatch.setattr(proc_tree, "_table", lambda: next(reads))
    result = proc_tree.kill_tree(ROOT)
    assert result.killed == [ROOT] and world.leaked_handles == 0


def test_a_descendant_that_cannot_be_opened_is_never_touched_but_is_reported_as_failed(world):
    world.unreadable.add(KID)
    result = proc_tree.kill_tree(ROOT)
    assert result.failed == {KID: "Access is denied"} and result.killed == [ROOT]  # by default the rest still ends
    assert KID in world.live and GRANDKID in world.live  # unchecked, so not touched, and nor is its subtree


def test_a_descendant_that_cannot_be_opened_spares_its_ancestor_chain_like_a_failed_kill(monkeypatch):
    """The bot's P1 on #597: an elevated child gave Access denied on open, the open failure was swallowed, the root
    was ended and `failed` stayed empty, so `down` dropped the record and the child outlived it."""
    world = FakeProcessTable(_two_branches()).install(monkeypatch)
    world.unreadable.add(GRANDKID)

    first = proc_tree.kill_tree(ROOT, spare_ancestors_on_failure=True)

    assert first.failed == {GRANDKID: "Access is denied"}
    assert first.killed == [SIBKID, SIB] and first.spared == [KID, ROOT]  # the sibling branch has no failure
    assert {ROOT, KID, GRANDKID} <= set(world.live) and world.leaked_handles == 0

    world.unreadable.clear()
    retry = proc_tree.kill_tree(ROOT, spare_ancestors_on_failure=True)

    assert retry.killed == [GRANDKID, KID, ROOT] and not retry.failed and not world.live


def test_a_descendant_that_vanishes_before_it_can_be_opened_is_not_a_failure(monkeypatch):
    world = FakeProcessTable(_two_branches()).install(monkeypatch)
    world.hooks[1] = lambda w: w.live.pop(GRANDKID)
    result = proc_tree.kill_tree(ROOT, spare_ancestors_on_failure=True)
    assert not result.failed and not result.spared and result.killed == [SIBKID, SIB, KID, ROOT]


def test_a_denied_descendant_that_is_gone_by_the_second_read_is_not_a_failure(monkeypatch):
    world = FakeProcessTable(_two_branches()).install(monkeypatch)
    world.unreadable.add(GRANDKID)
    world.hooks[1] = lambda w: w.live.pop(GRANDKID)  # it ended after the first read, so it is not in the second
    result = proc_tree.kill_tree(ROOT, spare_ancestors_on_failure=True)
    assert not result.failed and not result.spared and result.killed == [SIBKID, SIB, KID, ROOT]


def test_a_denied_process_under_something_that_is_not_ours_is_not_a_failure(world):
    world.unreadable.add(ORPHAN_CHILD)  # its parent ORPHAN is older than ROOT, so it is not in ROOT's tree
    result = proc_tree.kill_tree(ROOT, spare_ancestors_on_failure=True)
    assert not result.failed and result.killed == [GRANDKID, KID, ROOT]


def test_the_system_console_host_exits_with_its_client_instead_of_blocking_the_tree(monkeypatch):
    world = FakeProcessTable(_two_branches()).install(monkeypatch)
    world.terminate_denied.add(GRANDKID)
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    world.image_paths[GRANDKID] = str(Path(system_root) / "System32" / "conhost.exe")
    world.auto_exit_with_parent.add(GRANDKID)

    result = proc_tree.kill_tree(ROOT, spare_ancestors_on_failure=True)

    assert not result.failed and not result.spared
    assert result.killed == [SIBKID, SIB, KID, ROOT] and GRANDKID not in world.live
    assert GRANDKID in world.opened_for_query_only and world.leaked_handles == 0


def test_a_queryable_user_child_without_terminate_right_still_blocks_its_ancestors(monkeypatch):
    world = FakeProcessTable(_two_branches()).install(monkeypatch)
    world.terminate_denied.add(GRANDKID)
    world.image_paths[GRANDKID] = r"C:\tools\worker.exe"

    result = proc_tree.kill_tree(ROOT, spare_ancestors_on_failure=True)

    assert result.failed == {GRANDKID: "Access is denied"}
    assert result.killed == [SIBKID, SIB] and result.spared == [KID, ROOT]
    assert {ROOT, KID, GRANDKID} <= set(world.live) and world.leaked_handles == 0


def test_a_root_that_cannot_be_opened_is_reported_as_failed(world):
    world.unreadable.add(ROOT)
    result = proc_tree.kill_tree(ROOT)
    assert result.failed == {ROOT: "Access is denied"} and world.killed == [] and world.leaked_handles == 0


def test_by_default_one_process_that_refuses_to_die_does_not_stop_the_others(world):
    world.refuse[KID] = "Access is denied"
    result = proc_tree.kill_tree(ROOT)
    assert result.failed == {KID: "Access is denied"} and result.killed == [GRANDKID, ROOT]
    assert world.leaked_handles == 0


def test_a_failed_process_keeps_its_whole_ancestor_chain_alive_and_a_retry_finishes_the_tree(monkeypatch):
    """The bot's P1 on #597: with GRANDKID failing, only ROOT was spared, so KID died, the survivor's parent PID named
    a dead PID, and the retry ended ROOT, dropped the record and left GRANDKID running."""
    world = FakeProcessTable(_two_branches()).install(monkeypatch)
    world.refuse[GRANDKID] = "Access is denied"

    first = proc_tree.kill_tree(ROOT, spare_ancestors_on_failure=True)

    assert first.failed == {GRANDKID: "Access is denied"}
    assert first.killed == [SIBKID, SIB]  # a branch without a failure is still ended, leaves first
    assert first.spared == [KID, ROOT]
    assert {KID, GRANDKID, ROOT} <= set(world.live) and not {KID, ROOT} & set(world.killed)
    assert world.leaked_handles == 0

    world.refuse.clear()
    retry = proc_tree.kill_tree(ROOT, spare_ancestors_on_failure=True)

    assert retry.killed == [GRANDKID, KID, ROOT] and not retry.failed and not retry.spared
    assert not world.live


def test_a_failure_spares_only_its_own_ancestors(monkeypatch):
    world = FakeProcessTable(_two_branches()).install(monkeypatch)
    world.refuse[SIBKID] = "Access is denied"
    result = proc_tree.kill_tree(ROOT, spare_ancestors_on_failure=True)
    assert result.killed == [GRANDKID, KID] and result.spared == [SIB, ROOT]  # KID's branch has no failure
    assert set(world.live) == {ROOT, SIB, SIBKID}


def test_a_failing_root_has_nothing_to_spare(monkeypatch):
    world = FakeProcessTable(_two_branches()).install(monkeypatch)
    world.refuse[ROOT] = "Access is denied"
    result = proc_tree.kill_tree(ROOT, spare_ancestors_on_failure=True)
    assert result.failed == {ROOT: "Access is denied"} and not result.spared
    assert result.killed == [SIBKID, GRANDKID, SIB, KID]


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
    assert ORPHANS <= set(world.live)


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
    assert ORPHANS <= set(world.live)


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
    waited = []
    monkeypatch.setattr(cli, "_wait_for_process_end",
                        lambda pid, created, timeout: waited.append((pid, created, timeout)) or True)
    cli._terminate_tree(ROOT, created="windows:1000")
    assert world.killed == [GRANDKID, KID, ROOT]
    assert ORPHANS <= set(world.live)
    assert waited == [(ROOT, "windows:1000", cli._TERMINATE_GRACE_SECONDS)]


def test_cli_terminate_tree_leaves_a_reused_pid_alone_without_an_error(world, monkeypatch):
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli, "_wait_for_process_end", lambda pid, created, timeout: True)
    cli._terminate_tree(ROOT, created="windows:7")
    assert world.killed == []


def test_cli_terminate_tree_refuses_an_identity_it_cannot_read(world, monkeypatch):
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    with pytest.raises(OSError, match="읽을 수 없어"):
        cli._terminate_tree(ROOT, created="proc:123")
    assert world.killed == [] and world.opened == []


def test_cli_terminate_tree_raises_when_the_recorded_process_cannot_be_ended(world, monkeypatch):
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    world.refuse[ROOT] = "Access is denied"
    with pytest.raises(OSError, match="Access is denied"):
        cli._terminate_tree(ROOT, created="windows:1000")


def test_cli_terminate_tree_raises_when_a_descendant_cannot_even_be_opened(monkeypatch):
    world = FakeProcessTable(_two_branches()).install(monkeypatch)
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    world.unreadable.add(GRANDKID)
    with pytest.raises(OSError) as raised:
        cli._terminate_tree(ROOT, created="windows:1000")
    assert f"PID {GRANDKID}: Access is denied" in str(raised.value) and f"{KID}, {ROOT}" in str(raised.value)
    assert world.killed == [SIBKID, SIB] and {ROOT, KID, GRANDKID} <= set(world.live)


def test_cli_terminate_tree_raises_when_a_descendant_cannot_be_ended_and_keeps_its_ancestors_for_a_retry(
        monkeypatch):
    """The bot's P1 on #597: only a failure on the root counted, so a venv interpreter that refused to die under a
    launcher that did die let `down` drop the PID record and report success."""
    world = FakeProcessTable(_two_branches()).install(monkeypatch)
    _no_process_starts(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    world.refuse[GRANDKID] = "Access is denied"
    with pytest.raises(OSError) as raised:
        cli._terminate_tree(ROOT, created="windows:1000")
    message = str(raised.value)
    assert f"PID {GRANDKID}: Access is denied" in message and f"{KID}, {ROOT}" in message
    assert world.killed == [SIBKID, SIB] and {ROOT, KID, GRANDKID} <= set(world.live)  # `down` can retry


def test_a_process_ending_by_itself_after_terminate_refuses_is_already_gone(monkeypatch):
    """A Windows venv launcher can be terminating but not signaled yet after its interpreter child exits."""
    waits = iter((0x00000102, 0))  # running at first; signaled while the bounded wait is in progress

    class Kernel:
        def WaitForSingleObject(self, _handle, _timeout):
            return next(waits)

        def TerminateProcess(self, _handle, _code):
            return False

    monkeypatch.setattr(proc_tree, "_kernel", lambda: (Kernel(), None, None))
    import ctypes
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)

    assert proc_tree._Handle(ROOT, 1000, object()).terminate() == proc_tree.GONE


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


# --- #800 Windows CI: a reused root PID still named as parent by older system processes ------------------------

def _reused_root_with_old_system_children():
    """ROOT got a PID that a boot-time process once held. Two system processes started by that old holder still name
    ROOT's PID as their parent; they are older than ROOT, can be queried but refuse the terminate right, and are not the
    console host. KID is ROOT's real child."""
    return {ROOT: (N + 1, 1000), KID: (ROOT, 1100), ORPHAN: (ROOT, 10), ORPHAN_CHILD: (ROOT, 20)}


def test_an_older_process_that_refuses_terminate_under_a_reused_root_pid_is_not_a_failure(monkeypatch):
    """PR #800 Windows CI: the watcher's sleeper got PID 752 and two boot-time processes (PID 760, 856) still named 752
    as their parent. They were denied, recorded as failed without their creation time being checked, ROOT was spared on
    purpose and the watcher answered 2. The age rule that already leaves an opened orphan alone applies to them too."""
    world = FakeProcessTable(_reused_root_with_old_system_children()).install(monkeypatch)
    world.terminate_denied.update({ORPHAN, ORPHAN_CHILD})
    world.image_paths.update({ORPHAN: r"C:\Windows\System32\svchost.exe", ORPHAN_CHILD: r"C:\Windows\System32\lsass.exe"})

    result = proc_tree.kill_tree(ROOT, created=1000, spare_ancestors_on_failure=True)

    assert not result.failed and not result.spared and result.killed == [KID, ROOT]
    assert {ORPHAN, ORPHAN_CHILD} <= set(world.live) and world.killed == [KID, ROOT]  # never touched
    assert {ORPHAN, ORPHAN_CHILD} <= set(world.opened_for_query_only) and world.leaked_handles == 0


@pytest.mark.parametrize("created", [1000, 1100])
def test_a_denied_child_as_old_as_or_younger_than_its_parent_still_blocks_its_ancestors(monkeypatch, created):
    """Equal creation time counts as a child, as in `validated_descendants`: only a strictly older process is skipped."""
    world = FakeProcessTable({ROOT: (N + 1, 1000), KID: (ROOT, created)}).install(monkeypatch)
    world.terminate_denied.add(KID)
    world.image_paths[KID] = r"C:\tools\worker.exe"
    result = proc_tree.kill_tree(ROOT, created=1000, spare_ancestors_on_failure=True)
    assert result.failed == {KID: "Access is denied"} and result.spared == [ROOT] and result.killed == []
    assert {ROOT, KID} <= set(world.live) and world.leaked_handles == 0


def test_an_older_denied_process_under_a_real_descendant_is_skipped_against_that_parents_age(monkeypatch):
    world = FakeProcessTable({ROOT: (N + 1, 1000), KID: (ROOT, 1100), GRANDKID: (KID, 1050)}).install(monkeypatch)
    world.terminate_denied.add(GRANDKID)  # older than KID, though younger than ROOT: KID's PID was reused
    result = proc_tree.kill_tree(ROOT, created=1000, spare_ancestors_on_failure=True)
    assert not result.failed and result.killed == [KID, ROOT] and GRANDKID in world.live


def test_the_watcher_stops_a_stalled_gateway_whose_reused_pid_old_system_processes_still_name(monkeypatch, tmp_path,
                                                                                              capsys):
    """The failing CI case end to end with a fake process table and a fake clock check: 1, the record is dropped."""
    from labhq.gateway import shutdown
    from labhq.settings import Settings

    world = FakeProcessTable(_reused_root_with_old_system_children()).install(monkeypatch)
    world.terminate_denied.update({ORPHAN, ORPHAN_CHILD})
    monkeypatch.setattr(sys, "platform", "win32")
    ends = iter([False, True])  # stalled past the grace, then gone after the kill
    monkeypatch.setattr(cli, "_wait_for_process_end", lambda pid, created, timeout: next(ends))
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    record = cli._pid_file(settings, "gateway")
    record.parent.mkdir(parents=True)
    record.write_text(f'{{"pid": {ROOT}, "role": "gateway", "created": "windows:1000"}}\n', encoding="utf-8")

    assert shutdown.watch_gateway_exit(settings, ROOT, "windows:1000", grace=0.5) == 1
    assert world.killed == [KID, ROOT] and {ORPHAN, ORPHAN_CHILD} <= set(world.live) and not record.exists()
    assert "강제로 종료" in capsys.readouterr().out
