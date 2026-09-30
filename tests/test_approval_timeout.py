import pytest

from labhq.gateway.server import Hub
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
