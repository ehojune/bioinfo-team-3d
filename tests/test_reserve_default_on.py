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


@pytest.mark.parametrize("remaining,expected", [
    (0.0199996, 0.01),       # a real $0.0199996 is not rounded up to $0.02 (PR #1210 review)
    (0.019999, 0.01),
    (0.0199999999, 0.02),    # 1e-10 under: float noise, not a missing cent
    (2.0 - 1.98, 0.02),      # 0.020000000000000018
    (5.0 - 4.99, 0.01),      # 0.009999999999999787: exactly one cent left, not under it
    (0.0099999999, 0.01),
    (1.239, 1.23),
    (1.9800004, 1.98),
])
def test_the_clip_floors_to_cents_with_an_explicit_tolerance_for_float_noise(remaining, expected):
    from labhq.orchestrator.cso import _clip_to_remaining
    assert _clip_to_remaining(5.0, remaining) == pytest.approx(expected)
    assert _clip_to_remaining(5.0, remaining) <= remaining + 1e-9  # never more than was left


@pytest.mark.parametrize("remaining", [0.0099, 0.005, 0.0, -1.0])
def test_under_a_cent_the_clip_leaves_the_cap_alone(remaining):
    from labhq.orchestrator.cso import _clip_to_remaining
    assert _clip_to_remaining(5.0, remaining) == 5.0


def test_a_cap_below_the_remaining_amount_and_a_missing_cap_pass_through():
    from labhq.orchestrator.cso import _clip_to_remaining
    assert _clip_to_remaining(0.015, 1.0) == 0.015 and _clip_to_remaining(None, 1.0) is None


# ----- a call cap that is not whole cents is floored too (PR #1210 remaining note) -----

@pytest.mark.asyncio
@pytest.mark.parametrize("per_task,held", [(0.015, 0.01), (1.239, 1.23), (2.675, 2.67), (0.10, 0.10), (1.5, 1.5), (2.5, 2.5)])
async def test_a_per_task_cap_is_floored_to_whole_cents_so_the_cli_cannot_round_it_up(per_task, held):
    """Claude's --max-budget-usd is written in cents: a hold of $0.015 sent as $0.02 would spend more than was held."""
    hub = await _one_step(100.0, per_task=per_task, cost=0.0)
    assert hub.calls[0].budget_usd == pytest.approx(held)
    assert hub.calls[0].budget_usd * 100 == pytest.approx(round(hub.calls[0].budget_usd * 100))
    assert hub.calls[0].budget_usd <= per_task + 1e-9


@pytest.mark.asyncio
@pytest.mark.parametrize("task_cap,held", [(0.019, 0.01), (3.456, 3.45)])
async def test_a_task_or_agent_cap_that_is_not_whole_cents_is_floored_as_well(task_cap, held):
    hub = await _one_step(100.0, cost=0.0, budget_usd=task_cap)
    assert hub.calls[0].budget_usd == pytest.approx(held)


@pytest.mark.asyncio
async def test_an_agents_own_cap_is_floored_too():
    async def dispatch(t):
        return result(t, text="done", cost_usd=0.0)

    hub = FakeHub(dispatch)
    hub.s.policy.budget.reserve_before_dispatch = True
    hub.requests["r"]["budget_usd"] = 100.0
    hub.agents["worker"] = {"id": "worker", "engine": "mock", "max_budget_usd": 0.0149}
    hub.s.policy.budget.per_task_usd = 5.0
    await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work"))
    assert hub.calls[0].budget_usd == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_a_call_cap_under_one_cent_is_refused_before_anything_is_sent():
    async def dispatch(t):
        return result(t, text="done", cost_usd=0.0)

    hub = FakeHub(dispatch)
    hub.s.policy.budget.reserve_before_dispatch = True
    hub.requests["r"]["budget_usd"] = 100.0
    with pytest.raises(BudgetExceeded, match="1센트|one cent"):
        await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="work", budget_usd=0.004))
    assert hub.calls == [] and hub.approvals == []


def test_the_settings_refuse_a_per_task_cap_under_one_cent_while_reservation_is_on():
    for value in (0.005, 0.0099):
        with pytest.raises(ValueError, match="per_task_usd"):
            BudgetSettings(per_task_usd=value)
    assert BudgetSettings(per_task_usd=0.01).per_task_usd == 0.01
    assert BudgetSettings(per_task_usd=0).per_task_usd == 0  # 0 means no per-task cap
    assert BudgetSettings(per_task_usd=0.005, reserve_before_dispatch=False).per_task_usd == 0.005  # the old check keeps it


def test_a_very_large_amount_is_not_floored_through_a_28_digit_decimal():
    """PR #1210 review P2: 1e26 overflowed Decimal's default precision. Cents mean nothing at that size."""
    from labhq.orchestrator.cso import _clip_to_remaining, _floor_cents
    assert _floor_cents(1e26) == 1e26
    assert _clip_to_remaining(2e26, 1e26) == 1e26
    assert _floor_cents(0.015) == 0.01 and _floor_cents(1.239) == 1.23 and _floor_cents(2.0 - 1.98) == 0.02

