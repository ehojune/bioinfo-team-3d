import httpx
import pytest

from labhq.gateway.server import create_app
from labhq.settings import Settings
from labhq.store import StateStore


@pytest.mark.asyncio
async def test_automatic_approval_list_is_request_scoped_and_durable(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.gateway.client_token = "test-token"
    app = create_app(settings)
    hub = app.state.hub
    for request_id in ("r1", "r2", "empty"):
        hub.requests[request_id] = {"status": "done"}
        hub.store.put("request", request_id, hub.requests[request_id])

    hub.store.put("task", "t1", {"request_id": "r1", "step_id": "prepare"})
    hub.store.put("task", "t2", {"request_id": "r2", "step_id": "other-step"})
    auto = {
        "approval": {
            "request_id": "r1", "task_id": "t1", "agent_id": "analyst",
            "detail": {
                "grant_category": "workspace",
                "tool_input": {"command": "printf '<test>\\n' > outputs/a.txt", "unused": "exclude this"},
            },
        },
        "approved": True, "automatic": True, "grant_category": "unresolved_path", "decided_at": 10,
    }
    for aid, decision in {
        "auto": auto,
        "legacy": {"approval": {"request_id": "r1", "task_id": "t2"}, "approved": True, "automatic": True},
        "manual": {**auto, "automatic": False},
        "rejected": {**auto, "approved": False},
        "pending": {**auto, "grant_pending": True},
        "other": {**auto, "approval": {**auto["approval"], "request_id": "r2", "task_id": "t2"}},
    }.items():
        hub.store.put("approval_decision", aid, decision)
    hub.store.put("approval_decision", "auto", auto)  # Replay keeps a single row per decision ID.
    path = hub.store.path
    hub.store.close()
    hub.store = StateStore(path)

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            url = "/api/requests/r1/automatic-approvals"
            assert (await client.get(url)).status_code == 401
            client.headers["Authorization"] = "Bearer test-token"
            assert (await client.get("/api/requests/missing/automatic-approvals")).status_code == 404
            assert (await client.get("/api/requests/empty/automatic-approvals")).json() == {
                "request_id": "empty", "approvals": [],
            }
            response = await client.get(url)
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert response.json() == {"request_id": "r1", "approvals": [
                {
                    "approval_id": "auto", "command": "printf '<test>\\n' > outputs/a.txt",
                    "agent_id": "analyst", "step_id": "prepare", "category": "workspace",
                },
                {"approval_id": "legacy", "command": None, "agent_id": None, "step_id": None, "category": None},
            ]}
            assert (await client.get("/api/requests/r1/approval-summary")).json()["automatic_approved_count"] == 2
    finally:
        hub.store.close()
