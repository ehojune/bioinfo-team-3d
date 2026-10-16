import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from labhq.gateway.server import Hub, SavedResults, create_app
from labhq.models import ApprovalRequest
from labhq.orchestrator.cso import Orchestrator
from labhq.settings import Settings
from tests.test_cso import FakeHub, result


@pytest.mark.parametrize("stopping", [True, False])
@pytest.mark.parametrize("code", [129, -15, 137, 143, 3221225786, -1073741510, 1])
async def test_plan_shutdown_and_restart(tmp_path, stopping, code):
    async def dispatch(task):
        return result(task, ok=False, error=f"exit {code}: terminated")

    hub = FakeHub(dispatch)
    hub.s.gateway.state_dir = str(tmp_path / "state")
    hub.stopping = stopping
    hub.requests["r"].update(id="r", status="running",
        shutdown_evidence={"exit_code": 129, "stopping": True})
    await Orchestrator(hub).run_request("r")
    assert any(t.meta.get("kind") == "plan" for t in hub.calls)
    assert hub.requests["r"]["status"] == ("interrupted" if stopping else "failed")
    store_hub = Hub(hub.s)
    store_hub.requests["r"] = hub.requests["r"]
    store_hub.save_request("r")
    restarted = Hub(hub.s)
    cards = [a for a in restarted.approvals.values() if a["approval"]["kind"] == "resume"]
    assert bool(cards) is stopping


@pytest.mark.parametrize("stage", ["step", "synthesis", "revise", "clarify"])
async def test_shutdown_in_each_orchestration_path(tmp_path, stage):
    plans = 0

    async def dispatch(task):
        nonlocal plans
        kind = task.meta["kind"]
        if kind == "plan":
            plans += 1
        if kind == stage or (kind == "plan" and (stage == "revise" or (stage == "clarify" and plans == 2))):
            hub.stopping = True
            return result(task, ok=False, error="exit 129: terminated")
        if kind == "plan":
            return result(task, structured={"clarifying_questions": ["Which public study?"] if stage == "clarify" else [],
                "steps": [{"id": "A", "agent_id": "worker", "instruction": "read", "depends_on": []}]})
        if kind == "review":
            return result(task, structured={"verdict": "accept", "scores": {
                "addresses_question": 4, "evidence": 4, "thoroughness": 4}, "issues": []})
        return result(task, text="completed")

    hub = FakeHub(dispatch)
    hub.s.gateway.state_dir = str(tmp_path / "state")
    hub.approve_budget = True
    hub.s.orchestrator.wait_for_clarification = True
    hub.request_approval = AsyncMock(return_value={"approved": True, "note": "Use study A"})
    hub.result_map = lambda rid: SavedResults(hub, rid)
    hub.clear_step_jobs = lambda *args: None
    if stage == "revise":
        hub.requests["r"]["revision_of"] = {"request_id": "old", "note": "revise", "plan": {"steps": []}}
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "interrupted"
    assert hub.requests["r"]["shutdown_evidence"]["kind"] == ("plan" if stage in {"revise", "clarify"} else stage)
    if stage == "synthesis":
        assert hub.requests["r"]["results"]["A"]["ok"]


def make_hub(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    hub = Hub(settings)
    hub.requests["r"] = {"id": "r", "text": "public question", "mode": "orchestrate", "status": "done"}
    hub.save_request("r")
    return hub


@pytest.mark.parametrize("status", ["done", "failed"])
async def test_review_done_schedules_publish_card(tmp_path, monkeypatch, status):
    hub = make_hub(tmp_path)
    hub.requests["r"]["status"] = status
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "README.md").write_text("reviewed edition", encoding="utf-8")
    (draft / "report.md").write_text("reviewed report", encoding="utf-8")
    hub.requests["r"]["result_storage"] = {"mode": "existing", "repo": "owner/study",
        "status": "draft", "draft_path": str(draft)}
    approval = AsyncMock(return_value={"approved": False})
    monkeypatch.setattr(hub, "request_approval", approval)
    await hub.result_storage.finish("r")
    approval.assert_not_awaited()
    assert not (draft / "report.md").exists()
    assert (draft / "report" / "report.md").read_text(encoding="utf-8") == "reviewed report"
    assert hub.requests["r"]["result_storage"]["status"] == "draft"
    hub.review_done("r")
    await hub.result_storage.tasks["r"]
    assert approval.await_args.kwargs["kind"] == "result_publish"
    assert "report/report.md" in approval.await_args.kwargs["detail"]["documents"]


@pytest.mark.parametrize("ending", ["failed", "interrupted", "revise"])
async def test_obsolete_publish_cards_close(tmp_path, ending):
    hub = make_hub(tmp_path)
    card = ApprovalRequest(kind="result_publish", request_id="r", summary="publish?")
    hub.approvals[card.id] = {"approval": card.model_dump(mode="json"), "origin": None,
                              "future": asyncio.get_running_loop().create_future()}
    hub.save_approval(card.id)
    if ending == "revise":
        hub.create_revised_request("r", "change plan")
    elif ending == "interrupted":
        await hub._set_status("r", ending)
    else:
        hub.requests["r"]["status"] = ending
        hub.commit_terminal("r", "request.failed", {})
    await asyncio.sleep(0)
    assert card.id not in hub.approvals
    assert hub.store.get("approval_decision", card.id)["state"] == "expired"


@pytest.mark.parametrize("stopping,code,accepted", [(True, 129, True), (False, 129, False), (True, 7, False)])
def test_correction_requires_recorded_shutdown_evidence(tmp_path, stopping, code, accepted):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.gateway.client_token = "test-token"
    app = create_app(settings)
    with TestClient(app) as client:
        hub = app.state.hub
        hub.requests["r"] = {"id": "r", "status": "failed", "text": "question", "mode": "team",
            "shutdown_evidence": {"exit_code": code, "stopping": stopping}, "error": f"exit {code}"}
        hub.save_request("r")
        response = client.post("/api/requests/r/mark-interrupted", headers={"Authorization": "Bearer test-token"})
        assert response.status_code == (200 if accepted else 409)
        assert hub.requests["r"]["status"] == ("interrupted" if accepted else "failed")
        if accepted:
            event = next(e for e in hub.store.events_since(0) if e["type"] == "request.corrected")
            assert event["data"]["previous"]["status"] == "failed"
            assert event["data"]["previous"]["error"] == f"exit {code}"


@pytest.mark.parametrize("storage_status", ["draft", "local"])
async def test_correction_resets_generated_storage_but_keeps_location(tmp_path, storage_status):
    hub = make_hub(tmp_path)
    old_draft = tmp_path / "old-draft"
    old_draft.mkdir()
    (old_draft / "README.md").write_text("old failure report", encoding="utf-8")
    hub.requests["r"].update(status="failed", error="exit 129: terminated",
        shutdown_evidence={"exit_code": 129, "stopping": True},
        result_storage={"mode": "existing", "repo": "owner/study", "visibility": "public",
            "allow_public": True, "branch": "reports", "status": storage_status,
            "draft_path": str(old_draft), "report_url": "https://example.test/old",
            "pdf": {"included": False}, "warning": "old warning"})
    hub.save_request("r")

    await hub.mark_interrupted("r")

    assert hub.requests["r"]["result_storage"] == {
        "mode": "existing", "repo": "owner/study", "visibility": "public",
        "allow_public": True, "branch": "reports", "status": "selected",
    }


async def test_corrected_request_builds_new_draft_from_resumed_report(tmp_path):
    hub = make_hub(tmp_path)
    old_draft = tmp_path / "runs" / "requests" / "r-editor"
    old_draft.mkdir(parents=True)
    (old_draft / "README.md").write_text("old failure report", encoding="utf-8")
    hub.requests["r"].update(status="failed", report="old failure report", error="exit 129: terminated",
        shutdown_evidence={"exit_code": 129, "stopping": True},
        result_storage={"mode": "existing", "repo": "owner/study", "visibility": "private",
            "allow_public": False, "branch": "main", "status": "draft", "draft_path": str(old_draft)})
    hub.save_request("r")

    await hub.mark_interrupted("r")
    hub.requests["r"].update(status="done", report="new resumed result")
    await hub.result_storage.finish("r")

    saved = hub.requests["r"]["result_storage"]
    assert saved["status"] == "draft"
    readme = (old_draft / "README.md").read_text(encoding="utf-8")
    assert "new resumed result" in readme
    assert "old failure report" not in readme


async def test_correction_refuses_published_storage(tmp_path):
    hub = make_hub(tmp_path)
    hub.requests["r"].update(status="failed", error="exit 129: terminated",
        shutdown_evidence={"exit_code": 129, "stopping": True},
        result_storage={"mode": "existing", "repo": "owner/study", "visibility": "private",
            "allow_public": False, "status": "published", "report_url": "https://example.test/report"})
    hub.save_request("r")

    with pytest.raises(ValueError, match="이미 게시"):
        await hub.mark_interrupted("r")
    assert hub.requests["r"]["status"] == "failed"
    assert hub.requests["r"]["result_storage"]["status"] == "published"


@pytest.mark.parametrize("age", [1, 60, -1])
async def test_legacy_correction_uses_contemporaneous_gateway_signal(tmp_path, age):
    hub = make_hub(tmp_path)
    finished = 1700000100
    hub.requests["r"].update(status="failed", error="답변 반영 계획 실패: exit 129: terminated",
                              created_at=1700000000, finished_at=finished)
    logs = hub.s.path(hub.s.gateway.state_dir) / "logs"
    logs.mkdir()
    (logs / "gateway-diagnostics.jsonl").write_text(json.dumps({"event": "signal", "role": "gateway",
        "pid": 123, "time": datetime.fromtimestamp(finished - age, timezone.utc).isoformat(), "code": 143}),
        encoding="utf-8")
    if age == 1:
        await hub.mark_interrupted("r")
        assert hub.requests["r"]["status"] == "interrupted"
    else:
        with pytest.raises(ValueError):
            await hub.mark_interrupted("r")
        assert hub.requests["r"]["status"] == "failed"


def test_cli_correction_uses_authenticated_gateway(monkeypatch, capsys):
    from labhq import cli
    api = []
    monkeypatch.setattr(cli, "_api", lambda s, method, path: api.append((method, path)) or {"status": "interrupted"})
    cli.main(["request", "mark-interrupted", "r"])
    assert api == [("POST", "/api/requests/r/mark-interrupted")]
    assert "interrupted" in capsys.readouterr().out
