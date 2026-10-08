"""The task ledger is decoded once per web snapshot, request list or record recovery, never once per request.

2026-10-08 trial: 40 requests and 18 MB of task rows made each web office connect hold the event loop for ~11 s
(health checks timed out), because the snapshot decoded the whole ledger twice per request.
"""

from fastapi.testclient import TestClient

from labhq.gateway.server import create_app
from labhq.settings import Settings

REQUESTS = 30


def _hub(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    for n in range(REQUESTS):
        rid = f"r{n:02d}"
        hub.requests[rid] = {"id": rid, "text": f"request {n}", "mode": "orchestrate", "status": "done",
                             "created_at": float(n), "plan": {"steps": [{"id": "s1"}, {"id": "s2"}]},
                             "results": {"s1": {"ok": True, "task_id": f"t{n}-1"}}}
        hub.store.put("task", f"t{n}-1", {"request_id": rid, "step_id": "s1", "accepted": True, "completed": True,
                                         "result": {"ok": True, "text": "x" * 200}, "dispatched_at": 1.0})
    hub.requests["r00"]["status"] = "running"
    hub.store.put("task", "t00-2", {"request_id": "r00", "step_id": "s2", "accepted": True, "completed": False,
                                    "payload": {"agent_id": "worker"}, "dispatched_at": 2.0})
    return app, hub


def _count_ledger_reads(monkeypatch, hub) -> list[str]:
    reads: list[str] = []
    original = hub.store.all

    def counting(kind):
        reads.append(kind)
        return original(kind)

    monkeypatch.setattr(hub.store, "all", counting)
    return reads


def test_snapshot_decodes_the_task_ledger_once(tmp_path, monkeypatch):
    _, hub = _hub(tmp_path)
    reads = _count_ledger_reads(monkeypatch, hub)

    data = hub.snapshot()["data"]

    assert reads.count("task") == 1
    by_id = {r["id"]: r for r in data["requests"]}
    assert by_id["r00"]["step_status"] == {"s1": "done", "s2": "running"}
    assert by_id["r00"]["step_details"]["s2"]["task_id"] == "t00-2"
    assert by_id["r05"]["step_details"]["s1"]["attempts"] == 1
    assert [task["id"] for task in data["running_tasks"]] == ["t00-2"]


def test_request_list_decodes_the_task_ledger_once(tmp_path, monkeypatch):
    app, hub = _hub(tmp_path)
    headers = {"Authorization": f"Bearer {hub.s.gateway.client_token}"}
    with TestClient(app) as client:
        reads = _count_ledger_reads(monkeypatch, hub)
        items = client.get("/api/requests?status=all&limit=200", headers=headers).json()

    assert reads.count("task") == 1
    assert len(items) == REQUESTS
    assert next(i for i in items if i["id"] == "r00")["step_progress"]["steps"] == {"s1": "done", "s2": "running"}


def test_health_skips_the_task_ledger_when_nothing_is_active(tmp_path, monkeypatch):
    app, hub = _hub(tmp_path)
    hub.requests["r00"]["status"] = "done"
    with TestClient(app) as client:
        reads = _count_ledger_reads(monkeypatch, hub)
        health = client.get("/api/health").json()

    assert health["running_tasks"] == 0 and health["active_requests"] == 0
    assert reads.count("task") == 0


def test_round_recovery_decodes_the_task_ledger_once(tmp_path, monkeypatch):
    _, hub = _hub(tmp_path)
    reads = _count_ledger_reads(monkeypatch, hub)

    hub.rounds.recover()

    assert reads.count("task") == 1
    assert len(list((tmp_path / "state" / "rounds").glob("r*.json"))) == REQUESTS - 1  # r00 is still running
