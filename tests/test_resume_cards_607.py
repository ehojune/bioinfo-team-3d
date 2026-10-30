"""A resume card never outlives its request (#607): finishing, the PI's review and a restart all remove it."""
import time

import pytest

from labhq.gateway.server import Hub as RealHub
from labhq.settings import Settings


def lifecycle_hub(tmp_path, statuses):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = RealHub(settings)
    for rid, status in statuses.items():
        hub.requests[rid] = {"id": rid, "text": f"{rid} 요청", "mode": "orchestrate", "status": status,
                             "created_at": time.time()}
        hub.save_request(rid)
    return settings, hub


def expired_events(hub, aid):
    return [e for e in hub.store.events_since(0) if e["type"] == "approval.expired" and e["data"]["id"] == aid]


async def test_a_resume_card_goes_when_its_request_completes(tmp_path):
    _, hub = lifecycle_hub(tmp_path, {"r": "interrupted", "other": "interrupted"})
    card, other = hub.new_resume_approval("r"), hub.new_resume_approval("other")
    assert card.id in hub.approvals and card.id in hub.resume_timers
    hub.requests["r"].update(status="done", report="결과")
    hub.commit_terminal("r", "request.completed", {})
    assert card.id not in hub.approvals and card.id not in hub.resume_timers
    assert hub.store.get("approval", card.id) is None
    record = hub.store.get("approval_decision", card.id)
    assert record["state"] == "expired" and record["approved"] is False and "끝나" in record["note"]
    assert len(expired_events(hub, card.id)) == 1  # in the event log, so an open page drops the card
    assert other.id in hub.approvals  # another request's card stays
    assert hub.requests["r"]["status"] == "done"


async def test_a_resume_card_goes_when_the_pi_marks_the_request_reviewed(tmp_path):
    _, hub = lifecycle_hub(tmp_path, {"r": "interrupted"})
    card = hub.new_resume_approval("r")
    hub.requests["r"]["status"] = "done"  # finished while the card stayed (the trial request's 10-13 card)
    assert card.id in hub.approvals
    hub.review_done("r")
    assert card.id not in hub.approvals
    assert hub.store.get("approval_decision", card.id)["state"] == "expired" and expired_events(hub, card.id)
    assert hub.requests["r"]["status"] == "done" and hub.requests["r"]["pi_reviewed_at"]


async def test_startup_drops_resume_cards_of_finished_requests(tmp_path):
    settings, hub = lifecycle_hub(tmp_path, {"done": "interrupted", "failed": "interrupted", "live": "interrupted"})
    cards = {rid: hub.new_resume_approval(rid) for rid in ("done", "failed", "live")}
    for rid in ("done", "failed"):
        hub.requests[rid]["status"] = rid
        hub.save_request(rid)  # the request ended, but its stored resume card was never removed
    restarted = RealHub(settings)
    assert cards["done"].id not in restarted.approvals and cards["failed"].id not in restarted.approvals
    assert restarted.store.get("approval", cards["done"].id) is None
    assert restarted.store.get("approval_decision", cards["done"].id)["state"] == "expired"
    assert expired_events(restarted, cards["done"].id) and expired_events(restarted, cards["failed"].id)
    assert [e["approval"]["request_id"] for e in restarted.approvals.values() if e["approval"]["kind"] == "resume"] == ["live"]
    assert restarted.requests["done"]["status"] == "done" and restarted.requests["failed"]["status"] == "failed"


@pytest.mark.parametrize("approved", [True, False])
async def test_answering_a_stale_resume_card_never_revives_or_fails_a_finished_request(tmp_path, approved):
    _, hub = lifecycle_hub(tmp_path, {"r": "interrupted"})
    card = hub.new_resume_approval("r")
    hub.requests["r"]["status"] = "done"
    await hub.resolve_approval(card.id, approved)
    assert card.id not in hub.approvals
    assert hub.requests["r"]["status"] == "done" and "resume declined" not in str(hub.requests["r"].get("error"))
    assert "r" not in hub.request_tasks


def _step_task(rid, sid, revision):
    from labhq.models import Task
    return Task(agent_id="worker", request_id=rid, prompt="p",
                meta={"kind": "step", "step_id": sid, "revision": revision})


async def _closed_step_hub(tmp_path):
    _, hub = lifecycle_hub(tmp_path, {"r": "running"})
    await hub._close_step_controls("r", "s1")  # round 1 finished the step: request.step_done closes its controls
    return hub, hub.step_key("r", "s1", "step", "t")


async def test_a_continuation_round_at_revision_zero_stays_closed_out_of_the_gateway(tmp_path):
    """#1053 repro: the same step id comes back at revision 0, so the closed-step check refuses it at dispatch."""
    hub, key = await _closed_step_hub(tmp_path)
    assert hub._closed_steps[key] == 0
    await hub._attach_steers(_step_task("r", "s1", 0))
    assert key in hub._closed_steps  # still closed: dispatch answers "step ended before dispatch"


async def test_the_next_round_dispatches_one_revision_above_the_closed_one_and_stragglers_stay_out(tmp_path):
    hub, key = await _closed_step_hub(tmp_path)
    assert hub.closed_step_revision("r", "s1") == 0 and hub.closed_step_revision("r", "other") == -1
    await hub._attach_steers(_step_task("r", "s1", hub.closed_step_revision("r", "s1") + 1))
    assert key not in hub._closed_steps  # reopened for the new round
    assert hub._step(key)["revision"] == 1
    assert int(_step_task("r", "s1", 0).meta["revision"]) < hub._step(key)["revision"]  # an old-round turn is refused


async def test_the_orchestrator_asks_the_gateway_for_the_revision_a_reopened_step_needs(tmp_path):
    from labhq.orchestrator.cso import Orchestrator
    from tests.test_cso import FakeHub

    hub, _ = await _closed_step_hub(tmp_path)
    cso = Orchestrator(hub)
    assert cso._step_revision("r", "s1") == 1  # closed at 0 in round 1
    assert cso._step_revision("r", "s2") == 0  # never ran: unchanged
    hub.requests["r"]["pending_revisions"] = {"s1": {"revision": 3}, "s2": {"revision": 2}}
    assert cso._step_revision("r", "s1") == 3 and cso._step_revision("r", "s2") == 2  # a review revise keeps its own
    assert Orchestrator(FakeHub(None))._step_revision("r", "s1") == 0  # a hub without the closed-step record
