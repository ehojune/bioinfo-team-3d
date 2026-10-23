"""#890: the plan's selection record (alternatives weighed, why each was set aside, what would change the call) is
written at planning time, frozen in the CP1 hash, and named on the CP1 card; a plan without it keeps its old hash."""
import copy

import pytest
from pydantic import ValidationError

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator, selection_record_line
from labhq.research.contract import (RESEARCH_PLAN_SCHEMA, canonical_plan_json, plan_sha256,
                                     validate_research_plan)
from labhq.settings import Settings
from tests.test_research_protocol import MiniHub, valid_plan

RECORD = {
    "question": "Does condition change expression?",
    "premises": ["public counts are enough"],
    "success_criteria": ["donor-level effect with interval"],
    "alternatives": [{"option": "cell-level test", "rejected_because": "pseudo-replication across donors"},
                     {"option": "bulk deconvolution", "rejected_because": "no matched bulk data"}],
    "change_signals": ["fewer than 3 donors per group after QC"],
}


def _validated(plan):
    return validate_research_plan(plan, max_steps=8, active_packs={})


def _with_record(**changes):
    plan = valid_plan()
    plan["selection_record"] = {**copy.deepcopy(RECORD), **changes}
    return plan


def test_a_plan_without_a_record_keeps_its_canonical_json_and_hash():
    plain = valid_plan()
    assert "selection_record" not in canonical_plan_json(_validated(plain))
    with_record = _with_record()
    assert "selection_record" in canonical_plan_json(_validated(with_record))
    assert plan_sha256(_validated(plain)) != plan_sha256(_validated(with_record))


def test_the_record_is_part_of_the_cp1_hash_so_a_later_addition_needs_a_new_approval():
    base = plan_sha256(_validated(_with_record()))
    added = _with_record(alternatives=RECORD["alternatives"] + [{"option": "x", "rejected_because": "y"}])
    changed = _with_record(change_signals=["a different observation"])
    assert plan_sha256(_validated(added)) != base
    assert plan_sha256(_validated(changed)) != base
    assert plan_sha256(_validated(_with_record())) == base  # unchanged content, unchanged hash


def test_the_field_is_optional_in_the_schema_and_strict_when_present():
    assert "selection_record" in RESEARCH_PLAN_SCHEMA["properties"]
    assert "selection_record" not in RESEARCH_PLAN_SCHEMA["required"]
    for bad in ({"alternatives": [{"option": "a", "rejected_because": ""}]},
                {"alternatives": [{"option": "a", "rejected_because": "b", "extra": 1}]},
                {"question": ""}, {"premises": ["p"] * 9},
                {"change_signals": [""]}, {"change_signals": ["  "]}, {"premises": [" "]}, {"success_criteria": [""]}):
        with pytest.raises((ValidationError, ValueError)):
            _validated(_with_record(**bad))


def test_the_card_line_counts_the_record_or_says_there_is_none():
    assert "선택 기록 없음" in selection_record_line(valid_plan())
    assert "선택 기록 없음" in selection_record_line({"selection_record": {}})
    line = selection_record_line(_with_record())
    assert "검토한 대안 2개" in line and "판단을 바꿀 관측 1개" in line


@pytest.mark.asyncio
@pytest.mark.parametrize("record, expected", [(True, "선택 기록: 검토한 대안 2개"), (False, "선택 기록 없음")])
async def test_the_cp1_card_shows_the_record_line_and_still_asks(record, expected):
    settings = Settings()
    settings.research.enabled = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    plan = _with_record() if record else valid_plan()

    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=copy.deepcopy(plan))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")
    await Orchestrator(hub).run_request("r")
    card = next(row for row in hub.approvals if row["kind"] == "research_plan")
    assert expected in card["summary"]
    assert ("selection_record" in card["detail"]["plan_canonical"]) is record


@pytest.mark.asyncio
async def test_the_record_line_survives_the_699_char_cut_of_a_long_card():
    settings = Settings()
    settings.research.enabled = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    plan = _with_record()
    plan["warnings"] = ["재배정 " + "긴 사유 " * 300]  # a long hiring line goes in front of the card text

    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=copy.deepcopy(plan))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")
    await Orchestrator(hub).run_request("r")
    card = next(row for row in hub.approvals if row["kind"] == "research_plan")
    assert len(card["summary"]) <= 700
    assert "선택 기록: 검토한 대안 2개" in card["summary"]
