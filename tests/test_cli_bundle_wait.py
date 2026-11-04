"""`labhq send` and `labhq watch <id>` print the request bundle path and grade (trial-ready 2026-10-12).

The gateway commits request.completed before it builds the bundle, so those fields come in a later request.bundle
event. These tests drive the real `Hub.schedule_terminal` and feed its events to the CLI in the order it commits them."""

from __future__ import annotations

import asyncio
import copy
import json
import threading

import pytest

from labhq import cli
from labhq.gateway.server import Hub
from labhq.settings import Settings
import labhq.request_bundle as request_bundle_module


class FakeSocket:
    """What `websockets.connect` returns: an async context manager that yields queued frames."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def recv(self) -> str:
        return await self.queue.get()

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        return await self.recv()

    def put(self, event: dict) -> None:
        self.queue.put_nowait(json.dumps(event))


def _hub(tmp_path, rid: str, *, with_workdir: bool = True) -> Hub:
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    hub = Hub(settings)
    results = {"direct": {"workdir_id": "task_" + rid}} if with_workdir else {}
    hub.requests[rid] = {"id": rid, "text": "표 정리", "mode": "direct", "status": "done", "results": results,
                         "plan": {"steps": []}, "report": "본문", "report_appendix": ""}
    return hub


def _slow_bundle(monkeypatch, tmp_path, release: threading.Event) -> None:
    def build(*_args):
        assert release.wait(10)
        return {"path": str(tmp_path / "bundle"), "status": "complete", "grade": "replayable", "grade_reasons": []}

    monkeypatch.setattr(request_bundle_module, "build_request_bundle", build)


def _wire(monkeypatch, hub: Hub, socket: FakeSocket, rid: str) -> None:
    import websockets

    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: socket)

    def api(_settings, method, path, **kwargs):
        if method == "POST":
            return {"request_id": rid}
        assert method == "GET" and path == f"/api/requests/{rid}"
        return copy.deepcopy(hub.requests[rid])

    monkeypatch.setattr(cli, "_api", api)


def _start(mode: str, rid: str):
    settings = Settings()
    if mode == "send":
        return asyncio.create_task(cli._send_and_wait(settings, {"text": "표 정리"}))
    return asyncio.create_task(cli._watch(settings, rid))


def _push_new_events(hub: Hub, socket: FakeSocket, seen: int) -> int:
    events = list(hub.events)
    for event in events[seen:]:
        socket.put(event)
    return len(events)


@pytest.mark.parametrize("mode", ["send", "watch"])
async def test_send_and_watch_wait_for_the_bundle_event_after_the_terminal_event(tmp_path, monkeypatch, capsys, mode):
    rid = "req_bundle"
    hub = _hub(tmp_path, rid)
    release = threading.Event()
    _slow_bundle(monkeypatch, tmp_path, release)
    socket = FakeSocket()
    _wire(monkeypatch, hub, socket, rid)
    socket.put({"type": "snapshot", "data": {}})  # `send` reads the snapshot before it posts
    cli_task = _start(mode, rid)
    await asyncio.sleep(0.05)

    bundle_task = hub.schedule_terminal(rid, "request.completed", {"ok": True, "report": "본문"})
    seen = _push_new_events(hub, socket, 0)
    assert [event["type"] for event in hub.events][-1] == "request.completed"  # the bundle is still being built
    await asyncio.sleep(0.2)
    assert not cli_task.done(), "returned at the terminal event, before the bundle existed"

    release.set()
    await asyncio.wait_for(bundle_task, 5)
    _push_new_events(hub, socket, seen)
    await asyncio.wait_for(cli_task, 5)

    out = capsys.readouterr().out
    assert "본문" in out
    assert f"요청 묶음: {tmp_path / 'bundle'}" in out and "재현 등급: replayable" in out
    hub.store.close()


@pytest.mark.parametrize("mode", ["send", "watch"])
async def test_a_request_with_no_bundle_returns_at_once(tmp_path, monkeypatch, capsys, mode):
    """A request cancelled before any step has nothing pending: waiting 60 seconds for it would be a hang."""
    rid = "req_none"
    hub = _hub(tmp_path, rid, with_workdir=False)
    socket = FakeSocket()
    _wire(monkeypatch, hub, socket, rid)
    monkeypatch.setattr(cli, "_BUNDLE_WAIT_SECONDS", 30.0)
    socket.put({"type": "snapshot", "data": {}})
    cli_task = _start(mode, rid)
    await asyncio.sleep(0.05)
    assert hub.schedule_terminal(rid, "request.completed", {"ok": True, "report": "본문"}) is None
    _push_new_events(hub, socket, 0)
    await asyncio.wait_for(cli_task, 5)
    assert "요청 묶음" not in capsys.readouterr().out
    hub.store.close()


async def test_a_bundle_that_never_finishes_is_left_to_status_after_the_bounded_wait(tmp_path, monkeypatch, capsys):
    rid = "req_slow"
    hub = _hub(tmp_path, rid)
    release = threading.Event()
    _slow_bundle(monkeypatch, tmp_path, release)
    socket = FakeSocket()
    _wire(monkeypatch, hub, socket, rid)
    monkeypatch.setattr(cli, "_BUNDLE_WAIT_SECONDS", 0.4)
    monkeypatch.setattr(cli, "_BUNDLE_POLL_SECONDS", 0.1)
    socket.put({"type": "snapshot", "data": {}})
    cli_task = _start("watch", rid)
    bundle_task = hub.schedule_terminal(rid, "request.completed", {"ok": True, "report": "본문"})
    _push_new_events(hub, socket, 0)
    await asyncio.wait_for(cli_task, 5)
    assert "요청 묶음을 아직 만드는 중입니다" in capsys.readouterr().out
    release.set()
    await asyncio.wait_for(bundle_task, 5)
    hub.store.close()


async def test_a_missed_bundle_event_is_found_by_polling_the_request_record(tmp_path, monkeypatch, capsys):
    rid = "req_missed"
    hub = _hub(tmp_path, rid)
    release = threading.Event()
    _slow_bundle(monkeypatch, tmp_path, release)
    socket = FakeSocket()
    _wire(monkeypatch, hub, socket, rid)
    monkeypatch.setattr(cli, "_BUNDLE_POLL_SECONDS", 0.1)
    socket.put({"type": "snapshot", "data": {}})
    cli_task = _start("watch", rid)
    bundle_task = hub.schedule_terminal(rid, "request.completed", {"ok": True, "report": "본문"})
    _push_new_events(hub, socket, 0)  # the request.bundle event is never delivered to this socket
    await asyncio.sleep(0.2)
    release.set()
    await asyncio.wait_for(bundle_task, 5)
    await asyncio.wait_for(cli_task, 5)
    out = capsys.readouterr().out
    assert f"요청 묶음: {tmp_path / 'bundle'}" in out and "재현 등급: replayable" in out
    hub.store.close()
