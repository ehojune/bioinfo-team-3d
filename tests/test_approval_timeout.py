import pytest

from labhq.gateway.server import Hub
from labhq.models import ApprovalRequest
from labhq.runner.approvals import Broker
from labhq.settings import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["clarify", "budget"])
async def test_timeout_decision_survives_in_round_and_event(tmp_path, kind):
    cfg = Settings()
    cfg.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(cfg)
    hub.requests["req-timeout"] = {"id": "req-timeout", "text": "timeout test",
                                   "mode": "orchestrate", "status": "running"}
    decision = await hub.request_approval(kind, "PI input", "req-timeout", timeout_s=1)
    hub.requests["req-timeout"]["status"] = "failed"
    record = hub.rounds.write("req-timeout")
    assert record["pi_decisions"] == [{"kind": kind, "approved": False,
                                        "note": "timed out", "state": "timed_out"}]
    assert not hub.approvals and not hub.store.all("approval")
    stored = next(iter(hub.store.all("approval_decision").values()))
    assert stored["approved"] is False and stored["state"] == "timed_out"
    assert decision["state"] == "timed_out"
    event = next(e for e in hub.events if e["type"] == "approval.resolved")
    assert event["data"]["approved"] is False and event["data"]["state"] == "timed_out"


@pytest.mark.asyncio
async def test_runner_timeout_removes_gateway_approval_and_resolves_clients(tmp_path):
    cfg = Settings()
    cfg.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(cfg)

    async def requested(req):
        await hub.on_runner_message("runner", {"type": "approval.requested", "data": req.model_dump(mode="json")})

    async def timed_out(req):
        await hub.on_runner_message("runner", {"type": "approval.timed_out", "data": {"id": req.id}})

    async def noop(_body):
        return None

    broker = Broker(0, requested, noop, noop, on_approval_timeout=timed_out)
    req = ApprovalRequest(id="appr_runner_timeout", kind="tool_permission", summary="tool", timeout_s=0)

    decision = await broker.request_approval(req)

    assert decision == {"approved": False, "note": "approval timed out", "state": "timed_out"}
    assert req.id not in hub.approvals
    assert hub.store.get("approval", req.id) is None
    stored = hub.store.get("approval_decision", req.id)
    assert stored["approved"] is False and stored["state"] == "timed_out"
    resolved = [event for event in hub.events if event["type"] == "approval.resolved"]
    assert resolved[-1]["data"] == {"id": req.id, "approved": False,
                                     "note": "approval timed out", "state": "timed_out"}


@pytest.mark.asyncio
async def test_runner_timeout_supersedes_a_racing_client_decision(tmp_path):
    cfg = Settings()
    cfg.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(cfg)
    req = ApprovalRequest(id="appr_race", kind="tool_permission", summary="tool")
    await hub.on_runner_message("runner", {"type": "approval.requested", "data": req.model_dump(mode="json")})

    await hub.resolve_approval(req.id, True)
    await hub.on_runner_message("runner", {"type": "approval.timed_out", "data": {"id": req.id}})

    assert hub.store.get("decision", req.id) is None
    stored = hub.store.get("approval_decision", req.id)
    assert stored["approved"] is False and stored["state"] == "timed_out"
    assert hub.events[-1]["type"] == "approval.resolved"
    assert hub.events[-1]["data"]["state"] == "timed_out"
