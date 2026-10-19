import asyncio
import copy
from unittest.mock import AsyncMock

import httpx
import pytest

from labhq.cli import main
from labhq.gateway.server import Hub, create_app
from labhq.intake import Reference, effective_references
from labhq.model_refusal import REASON, is_model_refusal
from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator, failure_kind
from labhq.settings import Settings
from test_plan_only_cso_model import ModelHub

REFUSAL = "API Error: Example's safeguards flagged this session. Details: [bio] <script>private</script>"


@pytest.mark.parametrize("ok,error,text,expected", [
    (False, None, REFUSAL, True), (False, REFUSAL, "", True), (True, None, REFUSAL, True),
    (True, None, "We studied refusals: " + REFUSAL, False),
    (False, "policy approval denied", "", False),
    (False, "API Error: overloaded", "", False),
])
def test_recognises_only_explicit_provider_refusal(ok, error, text, expected):
    result = TaskResult(task_id="t", agent_id="cso", ok=ok, error=error, text=text)
    assert is_model_refusal(result) is expected
    if expected:
        assert failure_kind(result) == "terminal"


async def test_failed_plan_records_safe_reason_and_offers_choice_without_retry():
    hub = ModelHub()
    hub.s.orchestrator.step_max_attempts = 3
    hub.offer_model_refusal = AsyncMock()

    async def dispatch(task):
        hub.calls.append(task)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, text=REFUSAL)

    hub.dispatch = dispatch
    await Orchestrator(hub).run_request("r")
    await asyncio.sleep(0)  # flush the lightweight Hub's terminal event
    req = hub.requests["r"]
    assert req["status"] == "failed" and req["outcome"] == "model_refused"
    assert req["error"] == REASON
    assert req["results"]["plan"]["error_kind"] == "model_refused"
    assert req["results"]["plan"]["text"] == REFUSAL  # original evidence remains in its task record
    assert len(hub.calls) == 1 and hub.calls[0].meta["kind"] == "plan"
    hub.offer_model_refusal.assert_awaited_once_with("r")
    terminal = next(e for e in hub.events if e["type"] == "request.completed")
    assert terminal["data"]["error"] == REASON
    assert "<script>" not in terminal["data"]["error"]


async def test_refusal_during_plan_correction_uses_the_same_terminal_choice():
    hub = ModelHub(mode="orchestrate")
    hub.s.research.enabled = True
    hub.s.research.evidence_checkpoint = True
    hub.requests["r"]["work_kind"] = "research"
    hub.offer_model_refusal = AsyncMock()

    async def dispatch(task):
        hub.calls.append(task)
        if len(hub.calls) == 1:
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="{}", structured={})
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, text=REFUSAL)

    hub.dispatch = dispatch
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["outcome"] == "model_refused"
    assert hub.requests["r"]["error"] == REASON
    assert len(hub.calls) == 2
    hub.offer_model_refusal.assert_awaited_once_with("r")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr("labhq.integrations.rounds._git_commit", lambda: "0" * 40)
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.gateway.client_token = "fixture-client"
    settings.orchestrator.cso_models = ["opus", "gpt-6-astra"]
    settings.research.enabled = True
    settings.research.evidence_checkpoint = True
    settings.research.final_checkpoint = True
    app = create_app(settings)
    hub = app.state.hub
    hub.agents["cso"] = {"model": "opus"}
    hub.requests["r"] = {"id": "r", "text": "Public fixture request", "mode": "orchestrate",
                         "work_kind": "research", "status": "failed", "outcome": "model_refused",
                         "error": REASON, "report": "Original failure", "created_at": 1,
                         "requested_budget_usd": 2, "budget_usd": 3,
                         "research_contract": {"approval": {"state": "approved"}},
                         "result_storage": {"status": "selected", "mode": "local"}}
    launches = []

    def track(rid, coroutine):
        launches.append(rid)
        coroutine.close()  # no operational request, runner or process is ever executed

    monkeypatch.setattr(hub, "track_request", track)
    return app, hub, launches


async def test_card_is_durable_idempotent_and_decline_never_retries(setup):
    _, hub, launches = setup
    await hub.offer_model_refusal("r")
    await hub.offer_model_refusal("r")
    assert len(hub.approvals) == 1 and not launches
    aid = hub.requests["r"]["model_refusal_approval_id"]
    card = hub.approvals[aid]["approval"]
    assert card["detail"]["models"] == ["gpt-6-astra"]
    restarted = Hub(hub.s)
    assert aid in restarted.approvals
    assert restarted.requests["r"]["error"] == REASON
    await hub.resolve_approval(aid, False, "keep ended", model_refusal={"action": "model", "cso_model": "gpt-6-astra"})
    assert len(hub.requests) == 1 and not launches
    assert hub.requests["r"]["status"] == "failed"
    await hub.result_storage.close()
    await restarted.result_storage.close()


@pytest.mark.parametrize("payload", [None, {}, {"action": "model", "cso_model": "unknown"},
    {"action": "model", "cso_model": "opus"}, {"action": "edit", "text": ""},
    {"action": "edit", "text": "Public fixture request"}, {"action": "edit", "text": "x" * 4001},
    {"action": "model", "cso_model": "gpt-6-astra", "allow_public": True}])
async def test_approval_requires_valid_explicit_choice_and_keeps_card_on_error(setup, payload):
    app, hub, launches = setup
    await hub.offer_model_refusal("r")
    aid = hub.requests["r"]["model_refusal_approval_id"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        data = {"approved": True, "note": "retry with another model", "model_refusal": payload}
        assert (await client.post(f"/api/approvals/{aid}", json=data)).status_code == 401
        response = await client.post(f"/api/approvals/{aid}", json=data,
                                     headers={"Authorization": "Bearer fixture-client"})
    assert response.status_code == 422
    assert aid in hub.approvals and len(hub.requests) == 1 and not launches
    await hub.result_storage.close()


@pytest.mark.parametrize("decision,expected_text,expected_model", [
    ({"action": "model", "cso_model": "gpt-6-astra"}, "Public fixture request", "gpt-6-astra"),
    ({"action": "edit", "text": "Revised public fixture"}, "Revised public fixture", None),
])
async def test_explicit_choice_creates_one_fresh_request_with_existing_gates(setup, decision, expected_text, expected_model):
    app, hub, launches = setup
    await hub.offer_model_refusal("r")
    aid = hub.requests["r"]["model_refusal_approval_id"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.post(f"/api/approvals/{aid}", json={"approved": True, "model_refusal": decision},
                                     headers={"Authorization": "Bearer fixture-client"})
    assert response.status_code == 200
    assert len(launches) == 1
    child = hub.requests[launches[0]]
    assert child["text"] == expected_text and child["cso_model"] == expected_model
    assert child["mode"] == "orchestrate" and child["work_kind"] == "research"
    assert child["budget_usd"] == 2 and child["model_refusal_from"] == "r"
    assert child["research_options"]["final_checkpoint"]
    assert "research_contract" not in child and "result_storage" not in child
    assert hub.requests["r"]["status"] == "failed"
    recorded = hub.store.get("approval_decision", aid)
    assert recorded["approved"] and recorded["model_refusal"] == decision
    assert recorded["new_request_id"] == child["id"]
    with pytest.raises(KeyError):
        await hub.resolve_approval(aid, True, model_refusal=decision)
    assert hub._model_refusal_request("r", decision) == child["id"]
    assert len(launches) == 1
    await hub.result_storage.close()


@pytest.mark.parametrize("changed", ["cancelled", "model_removed"])
async def test_stale_choice_cannot_bypass_current_state_or_model_allowlist(setup, changed):
    _, hub, launches = setup
    await hub.offer_model_refusal("r")
    aid = hub.requests["r"]["model_refusal_approval_id"]
    if changed == "cancelled":
        hub.requests["r"]["status"] = "cancelled"
    else:
        hub.s.orchestrator.cso_models = ["opus"]
    with pytest.raises(ValueError):
        await hub.resolve_approval(aid, True, model_refusal={"action": "model", "cso_model": "gpt-6-astra"})
    assert aid in hub.approvals and len(hub.requests) == 1 and not launches
    await hub.result_storage.close()


def test_status_prints_refusal_without_bundle_or_private_provider_text(setup, monkeypatch, capsys):
    _, hub, _ = setup
    summary = hub.request_summary(hub.requests["r"])
    assert summary["outcome"] == "model_refused" and summary["error"] == REASON
    monkeypatch.setattr(Settings, "load", lambda *args: hub.s)
    monkeypatch.setattr("labhq.cli._local_process_diagnostics", lambda *args: [])
    monkeypatch.setattr("labhq.research.semantics_shadow.status_line", lambda *args: None)

    def api(_settings, method, path):
        if path == "/api/health": return {"runners": []}
        if "status=running" in path: return []
        if "status=all" in path: return [summary]
        if path == "/api/requests/summary": return {"request_count": 1, "past_count": 0, "total": {}, "session": {}}
        if path == "/api/approvals": return []
        raise AssertionError(path)

    monkeypatch.setattr("labhq.cli._api", api)
    main(["status"])
    output = capsys.readouterr().out
    assert "[model_refused]" in output and REASON in output and "private" not in output


@pytest.mark.parametrize("origin", ["request", "pi_profile", "query_removed"])
@pytest.mark.parametrize("decision", [
    {"action": "model", "cso_model": "gpt-6-astra"},
    {"action": "edit", "text": "Revised public reference fixture"},
])
async def test_replan_normalises_stored_references_before_input_validation(setup, origin, decision):
    """P1 4317267131: persisted provenance fields are not RequestIn fields."""
    app, hub, launches = setup
    reference = Reference(kind="doi", value="10.1234/fixture", note="Public fixture")
    if origin == "pi_profile":
        hub.s.pi_profile.references = [reference]
        stored = effective_references([], True, hub.s)
    else:
        if origin == "query_removed":
            reference = Reference(kind="url", value="https://example.org/fixture?view=fixture", note="Public URL")
        stored = effective_references([reference], False, hub.s)
    before = copy.deepcopy(stored)
    hub.requests["r"].update(references=stored, default_references=True)
    assert all("source" in item for item in stored)
    if origin == "query_removed":
        assert stored[0]["query_removed"] is True
    await hub.offer_model_refusal("r")
    aid = hub.requests["r"]["model_refusal_approval_id"]
    assert not launches
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.post(f"/api/approvals/{aid}", json={"approved": True, "model_refusal": decision},
                                     headers={"Authorization": "Bearer fixture-client"})
    assert response.status_code == 200, response.text
    assert len(launches) == 1
    child = hub.requests[launches[0]]
    assert [{key: item.get(key) for key in ("kind", "value", "note")} for item in child["references"]] == [
        {key: item.get(key) for key in ("kind", "value", "note")} for item in before]
    assert child["default_references"] is False  # the effective snapshot is not added twice
    assert "?" not in child["references"][0]["value"]
    assert hub.requests["r"]["references"] == before
    assert hub.requests["r"]["status"] == "failed"
    assert child["research_options"]["final_checkpoint"] and "research_contract" not in child
    await hub.result_storage.close()


async def test_replan_still_rejects_an_invalid_saved_reference(setup):
    app, hub, launches = setup
    hub.requests["r"]["references"] = [{"kind": "url", "value": "javascript:alert(1)", "source": "request"}]
    await hub.offer_model_refusal("r")
    aid = hub.requests["r"]["model_refusal_approval_id"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.post(f"/api/approvals/{aid}", json={"approved": True,
            "model_refusal": {"action": "model", "cso_model": "gpt-6-astra"}},
            headers={"Authorization": "Bearer fixture-client"})
    assert response.status_code == 422
    assert len(hub.requests) == 1 and not launches and aid in hub.approvals
    await hub.result_storage.close()
