"""A process holds its ancestors open so their PIDs cannot be handed out again (#589).

Fake process tables only, with PIDs no machine allocates: nothing here opens or ends a real process. The real API
behaviour (a handle on an exited process keeps its PID reserved) is checked with real processes in
tests/test_supervisor_process.py.
"""
from __future__ import annotations

from labhq import proc_tree
from tests.proc_table_fake import FakeProcessTable

N = 920_000_000
TOP, GRAND, PARENT, ME = N + 1, N + 2, N + 3, N + 4
GONE = N + 77  # a parent PID that no live process owns any more


def _chain() -> dict[int, tuple[int, int | None]]:
    """TOP -> GRAND -> PARENT -> ME, each created after its parent."""
    return {TOP: (0, 100), GRAND: (TOP, 200), PARENT: (GRAND, 300), ME: (PARENT, 400)}


def test_it_holds_each_ancestor_without_the_right_to_end_it(monkeypatch):
    world = FakeProcessTable(_chain()).install(monkeypatch)

    pins = proc_tree.pin_ancestors(pid=ME)

    assert pins.pinned == [PARENT, GRAND, TOP] and pins.unpinned == {}
    assert set(world.opened_for_query_only) == {ME, PARENT, GRAND, TOP}
    assert world.killed == [] and world.leaked_handles == 3  # only the process's own handle was closed
    pins.close()
    assert world.leaked_handles == 0 and pins.handles == []


def test_the_walk_stops_after_depth_ancestors(monkeypatch):
    world = FakeProcessTable(_chain()).install(monkeypatch)

    pins = proc_tree.pin_ancestors(pid=ME, depth=2)

    assert pins.pinned == [PARENT, GRAND]
    assert TOP not in world.opened
    pins.close()


def test_a_parent_that_is_already_gone_cannot_be_held_and_ends_the_walk(monkeypatch):
    world = FakeProcessTable({GRAND: (0, 200), ME: (GONE, 400)}).install(monkeypatch)

    pins = proc_tree.pin_ancestors(pid=ME)

    assert pins.pinned == [] and set(pins.unpinned) == {GONE}
    assert "종료" in pins.unpinned[GONE]
    assert world.leaked_handles == 0


def test_a_parent_younger_than_its_child_is_a_reused_pid_and_is_not_held(monkeypatch):
    rows = _chain()
    rows[PARENT] = (GRAND, 500)  # created after ME: the PID now belongs to a process that is not ME's parent
    world = FakeProcessTable(rows).install(monkeypatch)

    pins = proc_tree.pin_ancestors(pid=ME)

    assert pins.pinned == [] and set(pins.unpinned) == {PARENT}
    assert "재사용" in pins.unpinned[PARENT]
    assert GRAND not in world.opened  # nothing above a stranger is ours to hold
    assert world.leaked_handles == 0  # the stranger's handle was closed again


def test_an_ancestor_that_cannot_be_opened_is_reported_and_ends_the_walk(monkeypatch):
    world = FakeProcessTable(_chain()).install(monkeypatch)
    world.unreadable.add(GRAND)

    pins = proc_tree.pin_ancestors(pid=ME)

    assert pins.pinned == [PARENT]
    assert pins.unpinned == {GRAND: "Access is denied"}
    assert TOP not in world.opened
    pins.close()
    assert world.leaked_handles == 0


def test_a_process_that_cannot_open_itself_pins_nothing(monkeypatch):
    world = FakeProcessTable(_chain()).install(monkeypatch)
    world.unreadable.add(ME)

    pins = proc_tree.pin_ancestors(pid=ME)

    assert pins.pinned == [] and set(pins.unpinned) == {ME}
    assert world.opened == [ME]


def test_without_a_process_table_it_pins_nothing(monkeypatch):
    world = FakeProcessTable(_chain()).install(monkeypatch)
    monkeypatch.setattr(proc_tree, "_table", lambda: None)

    pins = proc_tree.pin_ancestors(pid=ME)

    assert pins.pinned == [] and pins.unpinned == {} and world.opened == []
