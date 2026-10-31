"""#1139: `policy.budget.reserve_before_dispatch` is on by default, so parallel steps cannot pass the request cap."""
import asyncio
from pathlib import Path

import pytest
import yaml

from labhq.orchestrator.cso import BudgetExceeded, Orchestrator
from labhq.settings import BudgetSettings, Settings
from labhq.models import Task
from tests.test_cso import FakeHub, result

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = [ROOT / "config" / "labhq.example.yaml", ROOT / "labhq" / "config" / "labhq.example.yaml"]


def test_the_default_reserves_before_dispatch():
    assert BudgetSettings().reserve_before_dispatch is True
    assert Settings().policy.budget.reserve_before_dispatch is True


@pytest.mark.parametrize("path", EXAMPLES, ids=["config", "package"])
def test_both_example_configs_say_what_the_default_is(path):
    budget = yaml.safe_load(path.read_text(encoding="utf-8"))["policy"]["budget"]
    assert budget["reserve_before_dispatch"] is True
    assert Settings.model_validate({"policy": {"budget": budget}}).policy.budget == Settings().policy.budget


def test_it_can_still_be_turned_off():
    assert BudgetSettings(reserve_before_dispatch=False).reserve_before_dispatch is False
    assert Settings.model_validate({"policy": {"budget": {"reserve_before_dispatch": False}}}
                                   ).policy.budget.reserve_before_dispatch is False


def test_a_request_cap_that_is_not_an_amount_is_refused_now_that_it_is_on_by_default():
    for field in ("per_task_usd", "per_request_usd"):
        with pytest.raises(ValueError, match="finite amount"):
            BudgetSettings(**{field: float("inf")})


async def _two_parallel_steps(configure=None):
    started, release = asyncio.Event(), asyncio.Event()

    async def dispatch(task):
        started.set()
        await release.wait()
        return result(task, text="done", cost_usd=1.0)

    hub = FakeHub(dispatch)
    # FakeHub pins the older check for the tests that are about it; this file is about what the product ships.
    hub.s.policy.budget.reserve_before_dispatch = BudgetSettings().reserve_before_dispatch
    hub.s.policy.budget.per_task_usd = 1.5
    hub.requests["r"]["budget_usd"] = 2.0
    if configure:
        configure(hub)
    orchestrator = Orchestrator(hub)
    first = asyncio.create_task(orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt="first")))
    await started.wait()
    return hub, orchestrator, first, release


@pytest.mark.asyncio
async def test_with_the_default_parallel_steps_share_the_request_cap_and_a_third_is_refused():
    """Each turn may cost up to 1.5 and the request has 2.0. The second holds only the 0.5 that is left (the hold is cut to
    what the request still has, #1139), so the holds never pass the request cap; nothing is left for a third."""
    hub, orchestrator, first, release = await _two_parallel_steps()
    try:
        second = asyncio.create_task(orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt="second")))
        await asyncio.sleep(0.05)
        assert len(hub.calls) == 2 and hub.approvals == []  # both are sent, without a card
        assert sorted(task.budget_usd for task in hub.calls) == [0.5, 1.5]  # what each is capped at
        assert sum(hub.requests["r"]["budget_holds"].values()) == pytest.approx(2.0)
        with pytest.raises(BudgetExceeded, match="budget reservation"):
            await orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt="third"))
        assert len(hub.calls) == 2  # the third turn was never sent
    finally:
        release.set()
        await asyncio.gather(first, second)


async def _one_step(budget, per_task=5.0, cost=0.1, **task):
    async def dispatch(t):
        return result(t, text="done", cost_usd=cost)

    hub = FakeHub(dispatch)
    hub.s.policy.budget.reserve_before_dispatch = BudgetSettings().reserve_before_dispatch
    hub.s.policy.budget.per_task_usd = per_task
    hub.requests["r"]["budget_usd"] = budget
    await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work", **task))
    return hub


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [0.5, 1.0, 4.99])
async def test_a_request_budget_below_the_per_turn_cap_is_not_asked_about_at_its_first_turn(budget):
    hub = await _one_step(budget)
    assert hub.approvals == [] and len(hub.calls) == 1
    assert hub.calls[0].budget_usd == pytest.approx(budget)  # the turn is capped at the whole request budget


@pytest.mark.asyncio
async def test_a_turn_cap_below_the_request_budget_is_held_as_it_is():
    hub = await _one_step(10.0, per_task=2.0)
    assert hub.calls[0].budget_usd == 2.0 and hub.approvals == []


@pytest.mark.asyncio
async def test_less_than_a_cent_left_is_not_clipped_to_a_hold_nothing_can_enforce():
    """A hold below one cent cannot be enforced (the CLI renders cents), so such a request asks like any other."""
    async def dispatch(t):
        return result(t, text="done", cost_usd=0.0)

    hub = FakeHub(dispatch)
    hub.s.policy.budget.reserve_before_dispatch = True
    hub.s.policy.budget.per_task_usd = 5.0
    hub.requests["r"]["budget_usd"] = 0.005
    with pytest.raises(BudgetExceeded):
        await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work"))
    assert [card["kind"] for card in hub.approvals] == ["budget"] and hub.calls == []


@pytest.mark.asyncio
async def test_the_card_for_an_exhausted_request_still_asks_for_the_whole_turn_cap():
    """Clipping only applies while something is left: once the holds use it all, a raise must make room for a full turn."""
    hub, orchestrator, first, release = await _two_parallel_steps()
    try:
        second = asyncio.create_task(orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt="second")))
        await asyncio.sleep(0.05)
        with pytest.raises(BudgetExceeded):
            await orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt="third"))
        assert hub.approvals[-1]["detail"]["reserve_usd"] == 1.5  # the turn cap, not the zero that was left
    finally:
        release.set()
        await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_with_it_off_the_older_check_lets_both_start_on_the_same_unspent_budget():
    """What the default prevents: with nothing spent yet, the older check sees room for both and both are sent."""
    def off(hub):
        hub.s.policy.budget.reserve_before_dispatch = False

    hub, orchestrator, first, release = await _two_parallel_steps(off)
    try:
        second = asyncio.create_task(orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt="second")))
        await asyncio.sleep(0.05)
        assert len(hub.calls) == 2  # both turns are in flight on the 2.0 budget, each allowed up to 1.5
    finally:
        release.set()
        await asyncio.gather(first, second)


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,held", [(0.019, 0.01), (0.0199, 0.01), (1.239, 1.23), (0.01, 0.01), (2.0 - 1.98, 0.02),
                                         (3.0 - 2.98, 0.02)])
async def test_a_clipped_hold_is_rounded_down_to_whole_cents(budget, held):
    """The Claude adapter renders --max-budget-usd in cents, so a hold of $0.019 would be sent as $0.02: more than was held
    and more than the request has left. The clipped hold is floored to the cent (PR #1192 review)."""
    hub = await _one_step(budget, cost=0.0)  # a free turn: no overrun card after it
    assert hub.approvals == [] and len(hub.calls) == 1
    assert hub.calls[0].budget_usd == pytest.approx(held) and hub.calls[0].request_budget_remaining_usd == pytest.approx(held)
    assert hub.calls[0].budget_usd * 100 == pytest.approx(round(hub.calls[0].budget_usd * 100))  # whole cents
    assert hub.calls[0].budget_usd <= budget + 1e-9  # never more than the request had


@pytest.mark.asyncio
async def test_a_hold_that_is_already_below_the_remaining_amount_is_not_rounded():
    """Only the hold cut to the remaining amount is floored: a task's own cap is held as it is."""
    hub = await _one_step(10.0, per_task=2.5)
    assert hub.calls[0].budget_usd == 2.5

