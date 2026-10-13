"""Messenger history is durable, PI-only, and paged independently of replay."""
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from labhq.gateway.server import create_app
from labhq.settings import Settings
from labhq.store import StateStore


@pytest.fixture
def history(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.gateway.client_token = "history-pi"
    settings.gateway.runner_token = "history-runner"
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, app.state.hub.store
    app.state.hub.store.close()


def get(client, query=""):
    return client.get("/api/messenger" + query, headers={"Authorization": "Bearer history-pi"})


def append(store, typ="agent.log", rid="one", **data):
    return store.append_event({"type": typ, "request_id": rid, "agent_id": "worker",
                               "ts": 10, "data": {"text": "message", **data}}, 3)


def test_history_pages_survive_replay_retention_and_restart(history):
    client, store = history
    ids = [append(store)["seq"] for _ in range(7)]
    assert len(store.events_since(0)) == 3
    page = get(client, "?limit=3").json()
    assert [e["seq"] for e in page["events"]] == ids[-3:]
    assert page["has_more"] and page["next_before"] == ids[-3]
    append(store)  # new arrivals cannot shift the exclusive cursor
    page = get(client, f'?limit=3&before={page["next_before"]}').json()
    assert [e["seq"] for e in page["events"]] == ids[1:4]
    page = get(client, f'?limit=3&before={page["next_before"]}').json()
    assert [e["seq"] for e in page["events"]] == ids[:1]
    assert page["has_more"] is False and page["next_before"] is None
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    reopened = StateStore(Path(path))
    try:
        assert len(reopened.messenger_history()["events"]) == 8
    finally:
        reopened.close()


def test_history_auth_filter_types_and_cap(history):
    client, store = history
    assert client.get("/api/messenger").status_code == 401
    assert client.get("/api/messenger", headers={"Authorization": "Bearer history-runner"}).status_code == 401
    for typ in ("agent.ask", "agent.answer", "request.created", "request.followup",
                "request.followup_done", "request.note", "request.completed", "request.failed",
                "request.questions", "approval.requested", "approval.resolved"):
        append(store, typ, "two")
    for typ in ("agent.output", "agent.thinking", "task.result", "agent.tool"):
        append(store, typ, "two")
    append(store, level="debug")
    append(store, level="thinking")
    for _ in range(205):
        append(store)
    page = get(client, "?request_id=two&limit=200").json()
    assert len(page["events"]) == 11 and not page["has_more"]
    assert all(e["request_id"] == "two" for e in page["events"])
    assert len(get(client, "?limit=200").json()["events"]) == 200
    assert get(client, "?request_id=missing").json()["events"] == []
    for query in ("?limit=201", "?limit=0", "?limit=-1", "?before=0", "?before=-1",
                  "?before=9223372036854775808"):
        assert get(client, query).status_code == 422


def test_existing_retained_events_are_backfilled_once(tmp_path):
    path = tmp_path / "old.sqlite"
    store = StateStore(path)
    event = append(store, "agent.ask", question="old question")
    # Simulate the pre-feature schema without the new archive.
    store.db.execute("DROP TABLE IF EXISTS messenger_events")
    store.db.commit()
    store.close()
    for _ in range(2):
        store = StateStore(path)
        assert [e["seq"] for e in store.messenger_history()["events"]] == [event["seq"]]
        store.close()


def test_terminal_transaction_and_followup_actor_are_archived(history):
    client, store = history
    store.put("request", "one", {"followups": [{"id": "fu", "agent_id": "analyst"}]})
    append(store, "request.followup_done", id="fu", answer="full answer", ok=True)
    store.commit_terminal("one", {"status": "done"}, {
        "type": "request.completed", "request_id": "one", "data": {"report": "large report"}}, 1)
    events = get(client).json()["events"]
    assert events[0]["data"]["agent_id"] == "analyst"
    assert events[0]["data"]["answer"] == "full answer"
    assert events[1]["type"] == "request.completed"
    assert "report" not in events[1]["data"]  # notification, not a duplicate report archive
