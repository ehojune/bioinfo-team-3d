import pytest

from labhq.orchestrator.cso import Orchestrator, RECRUIT_DISABLED_RULE
from test_cso import kinds, replan_hub, replan_plan, result


@pytest.mark.asyncio
async def test_replan_respects_recruit_setting():
    captured = []
    for enabled in (False, True):
        hub = replan_hub(
            [{"id": "A", "agent_id": "worker", "instruction": "work", "depends_on": []}],
            lambda task: result(task, ok=False, error="unavailable"),
            lambda task: replan_plan([], notes="cannot continue"),
        )
        hub.s.recruit.enabled = enabled
        await Orchestrator(hub).run_request("r")
        captured.append(kinds(hub, "replan")[0])

    off, on = captured
    assert off.prompt.count(RECRUIT_DISABLED_RULE) == 1
    assert off.prompt.replace("\n\n" + RECRUIT_DISABLED_RULE, "") == on.prompt
    assert off.output_schema == on.output_schema
