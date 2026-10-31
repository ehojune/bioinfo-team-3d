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
async def test_with_the_default_two_parallel_steps_cannot_pass_the_request_cap():
    """Each turn may cost up to 1.5 and the request has 2.0: the second must not start while the first holds its 1.5."""
    hub, orchestrator, first, release = await _two_parallel_steps()
    try:
        with pytest.raises(BudgetExceeded, match="budget reservation"):
            await orchestrator.run_step(Task(agent_id="worker", request_id="r", prompt="second"))
        assert len(hub.calls) == 1  # the second turn was never sent
    finally:
        release.set()
        await first


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
