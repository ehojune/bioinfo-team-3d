import asyncio
import csv
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from labhq.gateway.server import Hub, create_app
from labhq.result_storage import draft_documents
from labhq.settings import Settings
from tests.test_result_storage import fake_github


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.gateway.client_token = "test-token"
    calls = []
    app = create_app(s, github_transport=fake_github(calls))
    hub = app.state.hub
    hub.requests["r"] = {"id": "r", "status": "done", "text": "Public study", "report": "Result"}
    return app, hub, calls


@pytest.mark.parametrize("status", ["done", "review_pending", "failed", "cancelled"])
async def test_late_card_is_authenticated_durable_and_reused(setup, status):
    app, hub, calls = setup
    hub.requests["r"]["status"] = status
    hub.save_request("r")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.post("/api/requests/r/storage")).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert (await client.post("/api/requests/missing/storage")).status_code == 404
        a, b = await asyncio.gather(client.post("/api/requests/r/storage"), client.post("/api/requests/r/storage"))
        assert a.status_code == b.status_code == 200
        card = a.json()
        assert card == b.json()
        assert card["kind"] == "result_storage"
        assert card["detail"]["storage"]["github_available"]
        assert len(hub.approvals) == 1
        restarted = Hub(hub.s)
        assert card["id"] in restarted.approvals
    assert not any(method in {"POST", "PUT"} for method, _, _ in calls)
    await hub.result_storage.close()


@pytest.mark.parametrize("saved", [{"mode": "local", "status": "selected"},
                                   {"mode": "new", "status": "published"}])
async def test_selected_request_cannot_reopen(setup, saved):
    app, hub, calls = setup
    hub.requests["r"]["result_storage"] = saved
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                 headers={"Authorization": "Bearer test-token"}) as client:
        response = await client.post("/api/requests/r/storage")
    assert response.status_code == 409
    assert "이미" in response.json()["detail"]
    assert not hub.approvals and not calls


@pytest.mark.parametrize("status", ["running", "completed"])
async def test_unreviewable_request_cannot_open(setup, status):
    app, hub, calls = setup
    hub.requests["r"]["status"] = status
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                 headers={"Authorization": "Bearer test-token"}) as client:
        assert (await client.post("/api/requests/r/storage")).status_code == 409
    assert not hub.approvals and not calls
    assert not hub.requests["r"].get("result_storage")


@pytest.mark.parametrize("status", ["done", "cancelled"])
@pytest.mark.parametrize("reviewed", [False, True])
async def test_late_selection_runs_editor_after_review_then_approval_and_mock_upload(setup, monkeypatch, reviewed, status):
    app, hub, calls = setup
    hub.requests["r"]["status"] = status
    if reviewed:
        hub.requests["r"]["pi_reviewed_at"] = 1
    hub.agents["editor"] = {}
    bundle = Path(hub.s.runner.workspace_root) / "requests/r"
    bundle.mkdir(parents=True)
    (bundle / "README.md").write_text("## 다시 실행\npython analysis.py\n", encoding="utf-8")
    with (bundle / "MANIFEST.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["status", "environment_records"], delimiter="\t")
        writer.writeheader()
        writer.writerow({"status": "summary", "environment_records": json.dumps({"s1": {"python": "3.12"}})})
    editor = AsyncMock(return_value=SimpleNamespace(ok=True, structured={"readme": "# Edited summary"}))
    monkeypatch.setattr(hub.orchestrator, "run_step", editor)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                 headers={"Authorization": "Bearer test-token"}) as client:
        opened = await client.post("/api/requests/r/storage")
        assert opened.status_code == 200
        aid = opened.json()["id"]
        invalid = await client.post(f"/api/approvals/{aid}", json={"approved": True,
                                    "storage": {"mode": "new", "repo": "../invalid"}})
        assert invalid.status_code == 422 and aid in hub.approvals
        assert (await client.post(f"/api/approvals/{aid}", json={"approved": True,
                                  "storage": {"mode": "new", "repo": "study"}})).status_code == 200
        if not reviewed:
            hub.result_storage.schedule("r")  # also covers startup/terminal scheduling
            assert not hub.result_storage.tasks
            editor.assert_not_awaited()
            assert (await client.post("/api/requests/r/review-done")).status_code == 200
        for _ in range(100):
            publish = next((a["approval"] for a in hub.approvals.values()
                            if a["approval"]["kind"] == "result_publish"), None)
            if publish:
                break
            await asyncio.sleep(0.01)
        assert publish
        editor.assert_awaited_once()
        docs = publish["detail"]["documents"]
        portfolio_docs = {
            "report/report.md", "run/progress.md", "run/reproduce.md", "REFERENCES.md",
        }
        assert {"README.md", "PROGRESS.md", "REPRODUCE.md", "SOURCES.md", *portfolio_docs} <= docs.keys()
        assert docs["run/progress.md"] == docs["PROGRESS.md"]
        assert "python analysis.py" in docs["run/reproduce.md"] and "3.12" in docs["run/reproduce.md"]
        for filename in portfolio_docs:
            assert filename in docs["README.md"]
        assert not any(method in {"POST", "PUT"} for method, _, _ in calls)
        assert (await client.post(f"/api/approvals/{publish['id']}", json={"approved": True})).status_code == 200
        await hub.result_storage.tasks["r"]
        assert hub.requests["r"]["result_storage"]["status"] == "published"
        assert next(body for method, url, body in calls if method == "POST")["private"] is True
        draft = Path(hub.requests["r"]["result_storage"]["draft_path"])
        assert all((draft / name).is_file() for name in docs)
    await hub.result_storage.close()


def test_documents_include_revision_and_public_sources(setup):
    _, hub, _ = setup
    req = hub.requests["r"]
    req.update(replan_history=[{"round": 2, "reason": "change method"}],
               review_progress={"next_revision": 3},
               references=[{"kind": "doi", "value": "10.1234/example"},
                           {"kind": "pmid", "value": "123456"}],
               report="Study https://example.org/paper\n\n## References\nSmith (2020). Example study.")
    docs = draft_documents(req, hub.s)
    assert "change method" in docs["PROGRESS.md"] and "3" in docs["PROGRESS.md"]
    assert "10.1234/example" in docs["SOURCES.md"]
    assert "Smith (2020)" in docs["SOURCES.md"]
    assert "123456" in docs["REPRODUCE.md"]


async def test_cp4_late_choice_waits_for_review_and_refreshed_bundle(setup, monkeypatch):
    from tests.test_research_cp4 import draft_hub, target
    _, hub, _ = setup
    hub.requests["r"] = (await draft_hub()).requests["r"]
    card = await hub.result_storage.open_card("r")
    await hub.resolve_approval(card["id"], True, storage={"mode": "new", "repo": "study"})
    assert not hub.result_storage.tasks
    hub.requests["r"]["bundle_pending"] = True
    result = hub.review_done("r", **target(hub.requests["r"]))
    assert result["status"] == "done" and not hub.result_storage.tasks
    finish = AsyncMock()
    monkeypatch.setattr(hub.result_storage, "finish", finish)
    hub.requests["r"]["bundle_pending"] = False
    hub.result_storage.schedule("r")
    await hub.result_storage.tasks["r"]
    finish.assert_awaited_once_with("r")
    await hub.result_storage.close()


def test_cli_opens_storage_card(monkeypatch, capsys):
    from labhq import cli
    calls = []
    monkeypatch.setattr(Settings, "load", lambda *a, **kw: Settings())
    monkeypatch.setattr(cli, "_api", lambda s, method, url, **kw:
                        calls.append((method, url, kw)) or {"id": "appr_test"})
    cli.main(["storage", "r"])
    assert calls == [("POST", "/api/requests/r/storage", {"json": {}})]
    assert "appr_test" in capsys.readouterr().out


@pytest.mark.parametrize("report", [None, "", "   "])
async def test_cancelled_without_report_explains_why_storage_is_unavailable(setup, report):
    app, hub, calls = setup
    hub.requests["r"].update(status="cancelled", report=report)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                 headers={"Authorization": "Bearer test-token"}) as client:
        response = await client.post("/api/requests/r/storage")
    assert response.status_code == 409
    assert "보고서가 없" in response.json()["detail"]
    assert not hub.approvals and not calls


async def test_cancelled_finish_edits_partial_report(setup):
    _, hub, calls = setup
    hub.requests["r"].update(status="cancelled", pi_reviewed_at=1,
                            result_storage={"mode": "local", "status": "selected", "after_review": True})
    await hub.result_storage.finish("r")
    saved = hub.requests["r"]["result_storage"]
    assert saved["status"] == "local"
    edition = Path(saved["draft_path"])
    # #608: the edition opens with the graphical abstract labhq draws when the report has none; the report text is as it was
    assert (edition / "report.md").read_text(encoding="utf-8") == (
        "![Graphical abstract — 질문 → 데이터 → 방법 → 핵심 결과](graphical_abstract.svg)\n\nResult")
    assert (edition / "graphical_abstract.svg").read_text(encoding="utf-8").startswith("<svg ")
    assert not calls


async def test_late_card_has_no_deadline(setup):
    _, hub, _ = setup
    card = await hub.result_storage.open_card("r")
    assert "timeout_s" not in card
    assert "timeout_s" not in hub.store.all("approval")[card["id"]]["approval"]


@pytest.mark.parametrize("mode", ["local", "new"])
async def test_restart_resolves_late_card_after_selection_commit_crash(setup, monkeypatch, mode):
    _, hub, calls = setup
    hub.save_request("r")
    card = await hub.result_storage.open_card("r")
    cursor = hub.store.event_bounds()[1]
    monkeypatch.setattr(hub, "publish", AsyncMock(side_effect=RuntimeError("crash after selection")))
    with pytest.raises(RuntimeError, match="crash after selection"):
        await hub.resolve_approval(card["id"], True, storage={"mode": mode, "repo": "study"})
    saved = hub.store.get("request", "r")["result_storage"]
    assert saved["mode"] == mode
    assert card["id"] in hub.store.all("approval")

    restarted = Hub(hub.s, github_transport=fake_github(calls))
    assert card["id"] not in restarted.approvals
    assert card["id"] not in restarted.store.all("approval")
    assert restarted.store.get("approval_decision", card["id"])["storage"] == saved
    assert restarted.store.get("approval_decision", card["id"])["state"] == "reconciled"
    replay = restarted.store.events_since(cursor)
    assert any(e["type"] == "request.storage" and e["data"]["result_storage"] == saved for e in replay)
    assert any(e["type"] == "approval.resolved" and e["data"]["id"] == card["id"] for e in replay)
    assert restarted.requests["r"]["result_storage"] == saved
    cursor = restarted.store.event_bounds()[1]
    again = Hub(hub.s, github_transport=fake_github(calls))
    assert card["id"] not in again.approvals
    assert not [e for e in again.store.events_since(cursor) if e["type"] == "approval.resolved"]
    assert not any(method in {"POST", "PUT"} for method, _, _ in calls)
    for instance in (hub, restarted, again):
        await instance.result_storage.close()
        instance.store.close()


@pytest.mark.parametrize("legacy_timeout", [False, True])
async def test_late_card_stays_answerable_after_old_deadline_and_restart(setup, legacy_timeout):
    _, hub, calls = setup
    hub.requests["r"]["pi_reviewed_at"] = 1
    hub.save_request("r")
    card = await hub.result_storage.open_card("r")
    card["created_at"] = 1
    if legacy_timeout:
        card["timeout_s"] = 60
    hub.save_approval(card["id"])
    restarted = Hub(hub.s, github_transport=fake_github(calls))
    restored = restarted.approvals[card["id"]]["approval"]
    assert "timeout_s" not in restored
    assert "timeout_s" not in restarted.store.all("approval")[card["id"]]["approval"]
    replayed = [e["data"] for e in restarted.store.events_since(0)
                if e["type"] == "approval.requested" and e["data"]["id"] == card["id"]]
    assert "timeout_s" not in replayed[-1]
    assert (await restarted.result_storage.open_card("r"))["id"] == card["id"]
    await restarted.resolve_approval(card["id"], True, storage={"mode": "local"})
    await restarted.result_storage.tasks["r"]
    assert card["id"] not in restarted.approvals
    assert restarted.requests["r"]["result_storage"]["status"] == "local"
    assert not any(method in {"POST", "PUT"} for method, _, _ in calls)
    await restarted.result_storage.close()
