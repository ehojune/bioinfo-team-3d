"""Automatic approvals retain the same command and reason shown on their cards."""
import pytest

from tests.test_policy import request_grant_hub, _grant_card, _receive_grant_card


@pytest.mark.asyncio
async def test_auto_event_has_card_and_source_even_when_a_broader_grant_covers_it(request_grant_hub):
    hub, events, _ = request_grant_hub
    task = hub.store.get("task", "t1")
    hub.store.put("task", "t1", {**task, "step_id": "step-1"})
    await _receive_grant_card(hub, _grant_card("source", category="unresolved_path"))
    await hub.resolve_approval("source", True, grant_for_request=True)
    card = _grant_card("auto", category="workspace")
    card["detail"].update(tool_name="PowerShell", tool_input={"command": "Set-Content outputs/a.txt test"},
                          policy_reason="workspace write")
    await _receive_grant_card(hub, card)
    event = events[-1]
    data = event["data"]
    assert event["type"] == "approval.resolved"
    assert event["request_id"] == "r1" and event["task_id"] == "t1" and event["agent_id"] == "agent"
    assert data["step_id"] == "step-1"
    assert data["automatic"] is True and data["grant_source"] == "source"
    assert data["grant_category"] == "unresolved_path"
    assert data["approval"] == hub.store.get("approval_decision", "auto")["approval"]
    assert data["approval"]["detail"]["grant_category"] == "workspace"
    assert data["approval"]["detail"]["tool_input"]["command"] == "Set-Content outputs/a.txt test"
    assert data["approval"]["detail"]["policy_reason"] == "workspace write"
    assert data["approval"]["pi_reason"]

    # Replayed runner input cannot replace the already committed audit card.
    card["detail"]["tool_input"]["command"] = "forged"
    await _receive_grant_card(hub, card)
    assert events[-1]["data"]["approval"]["detail"]["tool_input"]["command"] == "Set-Content outputs/a.txt test"


@pytest.mark.asyncio
async def test_manual_decision_does_not_duplicate_card_in_resolved_event(request_grant_hub):
    hub, events, _ = request_grant_hub
    await _receive_grant_card(hub, _grant_card("manual"))
    await hub.resolve_approval("manual", True, grant_for_request=True)
    assert "approval" not in events[-1]["data"]


@pytest.mark.asyncio
async def test_automatic_audit_is_persisted_in_event_log(tmp_path, monkeypatch):
    from labhq.gateway.server import Hub
    from labhq.settings import Settings
    from labhq.store import StateStore

    cfg = Settings()
    cfg.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(cfg)
    hub.requests["r1"] = {"id": "r1", "status": "running", "mode": "orchestrate"}
    hub.store.put("request", "r1", hub.requests["r1"])
    hub.store.put("task", "t1", {"request_id": "r1", "runner_id": "runner", "step_id": "step-1",
                                "payload": {"agent_id": "agent"}})
    hub.runners["runner"] = object()
    hub.runner_processes["runner"] = "process-1"
    hub.runner_incarnations["runner"] = "inc-1"
    hub.runner_capabilities["runner"] = {"approval_grants": 1}
    async def send(*args):
        pass
    monkeypatch.setattr(hub, "send_runner", send)
    await _receive_grant_card(hub, _grant_card("source"))
    await hub.resolve_approval("source", True, grant_for_request=True)
    card = _grant_card("auto")
    card["detail"].update(tool_name="Bash", tool_input={"command": "echo test > outputs/a"},
                          policy_reason="workspace write")
    await _receive_grant_card(hub, card)
    db = tmp_path / "state" / "gateway.sqlite3"
    hub.store.close()
    reopened = StateStore(db)
    try:
        events = reopened.events_since(0)
        event = next(e for e in events if e["type"] == "approval.resolved" and e["data"]["id"] == "auto")
        assert event["data"]["approval"]["detail"]["tool_input"]["command"] == "echo test > outputs/a"
        assert event["data"]["approval"]["detail"]["policy_reason"] == "workspace write"
        assert event["data"]["step_id"] == "step-1"
        assert event["data"]["grant_source"] == "source"
    finally:
        reopened.close()
