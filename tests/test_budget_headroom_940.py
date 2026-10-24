"""Budget card: an approved raise leaves room for the next task, and an unknown PI translation holds only its own
ceiling (#940, trial 2026-10-24 attempts 8 and 9: two cards in a row, then a cancel)."""

import pytest

from labhq.orchestrator.cso import Orchestrator
from tests.test_cso import FakeHub

SPENT, LIMIT, PER_TASK = 2.88, 11.44, 10.0  # attempt 9: spent $2.88, per_task $10; two unknown tasks bound $22.88


def _orch(unknown, *, translations=False):
    hub = FakeHub(None)
    hub.s.policy.budget.reserve_before_dispatch = True
    hub.s.policy.budget.per_task_usd = PER_TASK
    hub.requests["r"]["budget_usd"] = LIMIT
    hub.approve_budget = True
    if translations:
        rows = {tid: {"reservation_id": tid, "task_id": tid, "request_id": "r", "agent_id": "chief_of_staff",
                      "attempt": 1, "ceiling_usd": 0.05, "state": "reserved", "legacy_reserved_usd": 0.0,
                      "source": "pi_translation"} for tid in unknown}

        class Store:
            def get(self, kind, key):
                return rows if kind == "pi_translation_reservations" else None

        hub.store = Store()
    orch = Orchestrator(hub)
    orch.cost["r"] = SPENT
    orch.cost_unknown["r"] = set(unknown)
    orch.cost_tasks["r"] = set(unknown)
    return hub, orch


@pytest.mark.asyncio
async def test_an_approved_raise_leaves_room_for_the_next_task_reservation():
    hub, orch = _orch({"u1", "u2"})
    await orch._check_budget("r", block=False)  # no next task yet: needed = 0, as at attempt 9's first card
    assert len(hub.approvals) == 1
    bound = SPENT + 2 * PER_TASK
    assert hub.approvals[0]["detail"]["requested_budget_usd"] == pytest.approx(bound + PER_TASK)
    assert f"${bound + PER_TASK:.2f}" in hub.approvals[0]["summary"]
    assert hub.requests["r"]["budget_usd"] == pytest.approx(bound + PER_TASK)

    await orch._check_budget("r", reserve=PER_TASK)  # the next task's reservation fits: no second card
    assert len(hub.approvals) == 1 and "r" not in orch.budget_denials


@pytest.mark.asyncio
async def test_a_card_that_already_names_the_next_task_keeps_its_cap():
    hub, orch = _orch({"u1", "u2"})
    await orch._check_budget("r", reserve=PER_TASK)
    assert hub.approvals[0]["detail"]["requested_budget_usd"] == pytest.approx(SPENT + 2 * PER_TASK + PER_TASK)


@pytest.mark.asyncio
async def test_unknown_translations_hold_only_their_own_ceiling():
    hub, orch = _orch({"pi_text_20", "pi_text_34"}, translations=True)
    spent, unknown, bound = orch._budget_bound("r", LIMIT)
    assert unknown == 2 and bound == pytest.approx(SPENT + 2 * 0.05)  # not SPENT + 2 x $10
    await orch._check_budget("r", block=False)
    assert hub.approvals == []  # attempt 9 opened a card here: bound was $22.88 on a $11.44 cap


@pytest.mark.asyncio
async def test_an_unknown_task_that_is_not_a_translation_still_holds_the_task_reserve():
    hub, orch = _orch({"pi_text_20", "worker_task"}, translations=True)
    rows = hub.store.get("pi_translation_reservations", "r")
    rows.pop("worker_task")
    _spent, unknown, bound = orch._budget_bound("r", LIMIT)
    assert unknown == 2 and bound == pytest.approx(SPENT + PER_TASK + 0.05)


@pytest.mark.asyncio
async def test_without_reservations_the_raised_cap_is_unchanged():
    hub, orch = _orch({"u1", "u2"})
    hub.s.policy.budget.reserve_before_dispatch = False
    await orch._check_budget("r", block=False)
    assert hub.approvals[0]["detail"]["requested_budget_usd"] == pytest.approx(SPENT + 2 * PER_TASK)  # no extra room
