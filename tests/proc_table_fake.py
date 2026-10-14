"""A fake Windows process table behind labhq.proc_tree's primitives (#589).

Real PIDs are never involved, so a regression in a kill path cannot touch a real process. The fake keeps the one
property the real API has and the fix relies on: a handle pins the process it was opened on. A PID that changes hands
after `open` still names the pinned process through the handle, and `terminate` ends that process, not the newcomer.
"""
from __future__ import annotations

from labhq import proc_tree
from labhq.proc_tree import Proc


class FakeHandle:
    def __init__(self, world: "FakeProcessTable", proc: Proc) -> None:
        self.world, self.proc = world, proc
        self.pid, self.created = proc.pid, proc.created
        self.closed = False

    def terminate(self) -> str:
        world = self.world
        if self.pid in world.refuse:
            raise OSError(world.refuse[self.pid])
        if id(self.proc) in world.dead:
            return proc_tree.GONE
        world.dead.add(id(self.proc))
        world.killed.append(self.pid)
        world.killed_procs.append(self.proc)
        if world.live.get(self.pid) is self.proc:
            del world.live[self.pid]
        return proc_tree.KILLED

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.world.closed += 1


class FakeProcessTable:
    """`rows` is {pid: (ppid, created)}. `hooks[n]` runs right after the n-th process-table read (1-based) and may
    change `live`, to put a PID in the hands of another process at a chosen moment."""

    def __init__(self, rows: dict[int, tuple[int, int | None]]) -> None:
        self.live = {pid: Proc(pid, ppid, created) for pid, (ppid, created) in rows.items()}
        self.killed: list[int] = []  # PIDs in kill order
        self.killed_procs: list[Proc] = []
        self.dead: set[int] = set()
        self.refuse: dict[int, str] = {}  # pid -> why terminate raises
        self.unreadable: set[int] = set()  # PIDs OpenProcess is denied for
        self.opened: list[int] = []
        self.opened_handles: list[FakeHandle] = []
        self.closed = 0
        self.reads = 0
        self.hooks: dict[int, object] = {}

    def table(self):
        rows = [(p.pid, p.ppid) for p in self.live.values()]
        self.reads += 1
        hook = self.hooks.get(self.reads)
        if hook:
            hook(self)
        return rows

    def snapshot(self):
        return list(self.live.values())

    def open(self, pid: int):
        self.opened.append(pid)
        if pid in self.unreadable:
            raise OSError("Access is denied")
        proc = self.live.get(pid)
        if proc is None:
            return None
        handle = FakeHandle(self, proc)
        self.opened_handles.append(handle)
        return handle

    def install(self, monkeypatch) -> "FakeProcessTable":
        monkeypatch.setattr(proc_tree, "_table", self.table)
        monkeypatch.setattr(proc_tree, "_snapshot", self.snapshot)
        monkeypatch.setattr(proc_tree, "_open", self.open)
        return self

    @property
    def leaked_handles(self) -> int:
        return len(self.opened_handles) - self.closed
