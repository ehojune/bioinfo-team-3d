"""PI notes join only turns dispatched after the note was sent (#373 question 6)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labhq import cli
from labhq.gateway.server import Hub, create_app
from labhq.models import Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.settings import Settings


def _settings(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    return settings


def test_note_endpoint_persists_emits_and_rejects_finished_requests(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["run"] = {"id": "run", "text": "work", "status": "running"}
    hub.save_request("run")
    auth = {"Authorization": f"Bearer {settings.gateway.client_token}"}

    with TestClient(app) as client:
        response = client.post("/api/requests/run/notes", json={"text": "  표도 그려 주세요  "}, headers=auth)
        assert response.status_code == 200
        note = response.json()["note"]
        assert note["text"] == "표도 그려 주세요" and note["id"].startswith("note_")

    assert hub.requests["run"]["pi_notes"] == [note]
    event = next(event for event in hub.store.events_since(0) if event["type"] == "request.note")
    assert event["request_id"] == "run" and event["data"] == note

    restarted = Hub(settings)
    assert restarted.requests["run"]["pi_notes"] == [note]
    restarted.requests["run"]["status"] = "done"
    restarted.save_request("run")
    app2 = create_app(settings)
    with TestClient(app2) as client:
        response = client.post("/api/requests/run/notes", json={"text": "too late"}, headers=auth)
    assert response.status_code == 409
    assert "이어 묻기를 쓰세요" in response.json()["detail"]

    app2.state.hub.requests["direct"] = {
        "id": "direct", "text": "work", "mode": "direct", "status": "running"
    }
    with TestClient(app2) as client:
        response = client.post("/api/requests/direct/notes", json={"text": "too early"}, headers=auth)
    assert response.status_code == 409
    assert "이어 묻기를 쓰세요" in response.json()["detail"]


def test_note_limits_are_enforced(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["run"] = {"id": "run", "text": "work", "status": "running",
                           "pi_notes": [{"id": f"note_{i}", "text": "n", "at": i} for i in range(20)]}
    auth = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    with TestClient(app) as client:
        assert client.post("/api/requests/run/notes", json={"text": "x" * 2001}, headers=auth).status_code == 422
        response = client.post("/api/requests/run/notes", json={"text": "one more"}, headers=auth)
    assert response.status_code == 409 and "20" in response.json()["detail"]


class PromptHub:
    def __init__(self, replies=None, *, research=False):
        self.s = Settings()
        self.requests = {"r": {"id": "r", "status": "running", "pi_notes": [],
                                **({"research_contract": {"execution_enabled": True}} if research else {})}}
        self.agents = {"worker": {"id": "worker", "engine": "mock"}}
        self.calls = []
        self.replies = list(replies or [])

    async def dispatch(self, task):
        self.calls.append(task)
        if self.replies:
            reply = self.replies.pop(0)
            if callable(reply):
                return reply(task)
            return reply
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="done")

    async def publish(self, _event):
        return None

    def supports_resume(self, _agent_id):
        return True

    def save_request(self, _rid):
        return None


@pytest.mark.asyncio
async def test_a_note_reaches_only_turns_dispatched_after_it_was_sent():
    hub = PromptHub()
    orchestrator = Orchestrator(hub)
    await orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt="first",
                                     meta={"kind": "step", "step_id": "s1"}))
    hub.requests["r"]["pi_notes"].append({"id": "note_1", "text": "add a table", "at": 10})
    await orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt="second",
                                     meta={"kind": "step", "step_id": "s2"}))

    assert "PI notes sent during this request" not in hub.calls[0].prompt
    assert "PI notes sent during this request" in hub.calls[1].prompt
    assert "note_1" in hub.calls[1].prompt and "add a table" in hub.calls[1].prompt


@pytest.mark.asyncio
async def test_a_note_sent_during_briefing_reaches_plan_and_later_replan():
    hub = PromptHub()

    def briefing(task):
        hub.requests["r"]["pi_notes"].append(
            {"id": "note_plan", "text": "stratify by sex", "at": 10}
        )
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="brief")

    hub.replies = [briefing]
    orchestrator = Orchestrator(hub)
    await orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt="briefing",
                                     meta={"kind": "briefing"}))
    for kind in ("plan", "replan"):
        await orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt=kind,
                                         meta={"kind": kind}))

    assert "stratify by sex" not in hub.calls[0].prompt
    assert all("stratify by sex" in task.prompt for task in hub.calls[1:])
    assert all("frozen plan" not in task.prompt for task in hub.calls[1:])


@pytest.mark.asyncio
async def test_a_note_sent_during_a_turn_reaches_its_next_continuation():
    hub = PromptHub()

    def first(task):
        hub.requests["r"]["pi_notes"].append({"id": "note_2", "text": "also check sex", "at": 11})
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="max turns",
                          error_kind="error_max_turns", session_id="session-1", workdir="C:/work/s1")

    hub.replies = [first, TaskResult(task_id="done", agent_id="worker", ok=True, text="done")]
    await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work",
                                          meta={"kind": "step", "step_id": "s1", "finish_turns": 1}))
    assert "PI notes sent during this request" not in hub.calls[0].prompt
    assert "also check sex" in hub.calls[1].prompt


@pytest.mark.asyncio
async def test_review_and_synthesis_get_notes_and_research_keeps_the_frozen_plan():
    hub = PromptHub(research=True)
    hub.requests["r"]["pi_notes"] = [{"id": "note_3", "text": "compare another cohort", "at": 12}]
    orchestrator = Orchestrator(hub)
    for kind in ("plan", "review", "synthesis"):
        await orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt=kind,
                                         meta={"kind": kind}))

    frozen_plan, review, synthesis = (task.prompt for task in hub.calls)
    assert "compare another cohort" in frozen_plan
    assert "reference only" in frozen_plan and "new CP1" in frozen_plan
    assert "PI notes sent during this request" in review
    assert "reference only" in review and "new CP1" in review
    assert "one line per PI note" in synthesis
    assert "whether it was incorporated" in synthesis
    assert "what new request is needed" in synthesis
    assert "list the choice that was actually used" in synthesis  # a note that changed an assumption (PR #387)


def test_note_cli_posts_to_the_note_endpoint(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli.Settings, "load", lambda _path: Settings())
    monkeypatch.setattr(cli, "_api", lambda _s, method, path, **kwargs:
                        calls.append((method, path, kwargs)) or {"request_id": "req_1", "note": {"id": "note_1"}})
    cli.main(["note", "req_1", "메모"])
    assert calls == [("POST", "/api/requests/req_1/notes", {"json": {"text": "메모"}})]
    assert "note_1" in capsys.readouterr().out


def test_both_web_views_show_notes_and_the_composer_defaults_to_note():
    root = Path(__file__).resolve().parents[1]
    index = (root / "labhq/web/index.html").read_text(encoding="utf-8")
    live3d = (root / "labhq/web/lab3d/src/live.js").read_text(encoding="utf-8")
    assert '<option value="note">이 요청에 메모</option><option value="request">새 요청</option>' in index
    assert "mode.value = 'note'" in index and "/notes`" in index
    assert "q.mode !== 'direct'" in index
    assert "실행 중 메모" in index and "q.piNotes" in live3d and "toLocaleTimeString" in live3d
