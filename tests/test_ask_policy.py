import json

import pytest

from labhq.cli import render
from labhq.intake import QUESTION_RULE
from labhq.orchestrator import cso
from labhq.orchestrator.cso import Orchestrator
from tests.test_cso import FakeHub, result


ASSUMPTIONS = [
    "GSE10072를 주 데이터셋으로 선택 — 짝지은 종양·정상 시료가 요청에 맞음",
    "환자 짝을 고정효과로 반영 — 세포가 아니라 환자가 분석 단위임",
]


def plan(*, assumptions=None, questions=None):
    value = {
        "scope": {"verdict": "in", "reason": "bioinformatics"},
        "clarifying_questions": questions or [],
        "steps": [{"id": "analysis", "agent_id": "worker", "instruction": "analyze",
                   "outputs": [], "depends_on": []}],
        "recruit": [],
        "notes": "fixture",
    }
    if assumptions is not None:
        value["assumptions"] = assumptions
    return value


def test_question_rule_is_shared_by_general_replan_and_research_planning():
    for phrase in (
        "authority, cost, or data access",
        "disease, cohort, or specimen scope",
        "recommended option first",
        "(권장)",
        "approximate time and cost",
        "Do not ask about scientific design choices",
        "public dataset selection, statistical models, filters or thresholds, comparators, or methods",
    ):
        assert phrase in QUESTION_RULE

    common = dict(request="REQ", roster="ROSTER", capabilities="CAPS", briefing="BRIEF", max_steps=3,
                  question_rule=QUESTION_RULE, output_types_rule="", topics_rule="", lab_scope="LAB")
    rendered = [
        cso.PLAN_PROMPT.format(**common),
        cso.RESEARCH_PLAN_PROMPT.format(**common, intake="INTAKE", packs="PACKS"),
        cso.REPLAN_PROMPT.format(roster="ROSTER", capabilities="CAPS", trigger="WHY", retired="none",
                                 drop_rule="DROP", used="A", max_steps=3, output_types_rule="", topics_rule="",
                                 empty_rule="EMPTY", request="REQ", plan="[]", results="",
                                 question_rule=QUESTION_RULE),
    ]
    assert all(QUESTION_RULE in prompt for prompt in rendered)
    assert all(cso.ASSUMPTIONS_RULE in prompt for prompt in (rendered[0], rendered[2]))
    assert "Record the scientific design choices you make in `protocol`" in rendered[1]


def test_general_plan_assumptions_schema_and_normalization_are_bounded():
    field = cso.PLAN_SCHEMA["properties"]["assumptions"]
    assert field["type"] == "array" and field["maxItems"] == 8
    assert field["items"]["type"] == "string"
    assert "assumptions" not in cso.PLAN_SCHEMA["required"]

    nine = [f"choice {i} — reason" for i in range(9)]
    assert cso.normalize_assumptions(nine) == nine[:8]
    assert cso.normalize_assumptions([" kept ", 7, None, "", "also kept"]) == ["kept", "also kept"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_assumptions", [ASSUMPTIONS, None])
async def test_saved_general_plan_survives_restart_and_reaches_synthesis(stored_assumptions):
    async def dispatch(task):
        assert task.meta["kind"] != "plan", "a saved plan must not be planned again after restart"
        if task.meta["kind"] == "synthesis":
            if stored_assumptions:
                assert all(value in task.prompt for value in stored_assumptions)
            else:
                assert "Scientific design assumptions: (none recorded)" in task.prompt
            return result(task, text="report")
        return result(task, text="done")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.reviewer_agent = None
    # JSON round-trip stands in for the gateway store read performed on restart.
    hub.requests["r"]["plan"] = json.loads(json.dumps(plan(assumptions=stored_assumptions)))
    await Orchestrator(hub).run_request("r", resume=True)

    assert hub.requests["r"]["status"] == "done"
    if stored_assumptions:
        assert hub.requests["r"]["plan"]["assumptions"] == stored_assumptions
    else:
        assert "assumptions" not in hub.requests["r"]["plan"]


@pytest.mark.asyncio
async def test_clarify_card_carries_plan_assumptions_after_its_questions():
    calls = 0

    async def dispatch(task):
        nonlocal calls
        if task.meta["kind"] == "plan":
            calls += 1
            return result(task, structured=plan(
                assumptions=ASSUMPTIONS,
                questions=[] if calls == 2 else [{"question": "분석 깊이는?",
                                                   "options": ["표준 (권장) · 약 1시간 · 약 $5",
                                                               "심화 · 약 3시간 · 약 $15"],
                                                   "allow_free_text": False, "depth": 60}],
            ))
        return result(task, text="done")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.reviewer_agent = None

    async def approve(**kwargs):
        assert kwargs["kind"] == "clarify"
        assert kwargs["detail"]["assumptions"] == ASSUMPTIONS
        return {"approved": True, "note": "Q1. a) 표준 (권장) · 약 1시간 · 약 $5"}

    hub.request_approval = approve
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "done"


CHANGED = "GSE19804로 주 데이터셋을 바꿈 — GSE10072 내려받기가 실패함"


@pytest.mark.asyncio
@pytest.mark.parametrize("returned, expected", [
    ([CHANGED, ASSUMPTIONS[1]], [CHANGED, ASSUMPTIONS[1]]),  # a stated list replaces: the changed choice is gone
    (None, ASSUMPTIONS),  # an omitted field keeps the earlier list
])
async def test_replan_assumptions_replace_the_earlier_list_when_stated(returned, expected):
    initial = plan(assumptions=ASSUMPTIONS)
    replanned = {
        "clarifying_questions": [],
        "steps": [{"id": "fallback", "agent_id": "worker", "instruction": "fallback",
                   "outputs": [], "depends_on": []}],
        "recruit": [], "notes": "replace failed analysis", "drop": [],
    }
    if returned is not None:
        replanned["assumptions"] = returned
    prompts = []

    async def dispatch(task):
        if task.meta["kind"] == "plan":
            return result(task, structured=initial)
        if task.meta["kind"] == "replan":
            prompts.append(task.prompt)
            return result(task, structured=replanned)
        if task.meta["kind"] == "step" and task.meta["step_id"] == "analysis":
            return result(task, ok=False, error="fixture failure")
        return result(task, text="done")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.reviewer_agent = None
    await Orchestrator(hub).run_request("r")

    assert hub.requests["r"]["status"] == "done"
    assert hub.requests["r"]["plan"]["assumptions"] == expected
    assert "Return the complete updated list" in prompts[0]
    assert ASSUMPTIONS[0] in prompts[0]


def test_carry_assumptions_replaces_a_stated_list_and_keeps_an_omitted_one():
    old = {"assumptions": [ASSUMPTIONS[0]]}
    assert cso._carry_assumptions(old, {"assumptions": [CHANGED]})["assumptions"] == [CHANGED]
    assert cso._carry_assumptions(old, {"assumptions": []})["assumptions"] == []
    assert cso._carry_assumptions(old, {"steps": []})["assumptions"] == [ASSUMPTIONS[0]]
    assert "assumptions" not in cso._carry_assumptions(None, {"steps": []})


def test_cli_plan_prints_assumptions_and_midrun_note_guidance(capsys):
    render({"type": "request.plan", "request_id": "req_1", "ts": 1,
            "data": {"steps": [], "assumptions": ASSUMPTIONS}})
    shown = capsys.readouterr().out
    assert "가정" in shown and all(value in shown for value in ASSUMPTIONS)
    assert 'labhq note req_1 "바꿀 내용"' in shown
