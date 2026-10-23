"""#669: a plan that says "no new packages" stopped a step that only needed xlrd to read a file it had received.

Installing into the step's own workspace is allowed by default; the plan prompt no longer lets the CSO forbid it, the
step prompt tells staff to install and read, and a read given up for lack of any tool reaches the CP2 card as
"도구 없음". The shared environment stays closed."""

from __future__ import annotations

from labhq.adapters.base import WORKSPACE_WRITE_RULES
from labhq.orchestrator.cso import RESEARCH_PLAN_PROMPT, RESEARCH_STEP_PROMPT, tool_missing_lines
from tests.test_environment_install import denial


def test_the_plan_prompt_does_not_let_the_cso_forbid_task_local_installs():
    assert "./.pylib" in RESEARCH_PLAN_PROMPT and "allowed by default" in RESEARCH_PLAN_PROMPT
    assert "Do not write \"no new packages\"" in RESEARCH_PLAN_PROMPT
    assert "system or a\n  shared environment" in RESEARCH_PLAN_PROMPT  # only that stays forbidden


def test_the_step_prompt_and_workspace_rules_install_then_read():
    assert "install it\ninto your own workspace" in RESEARCH_STEP_PROMPT and "도구 없음" in RESEARCH_STEP_PROMPT
    assert "without asking" in WORKSPACE_WRITE_RULES and "PI approved" not in WORKSPACE_WRITE_RULES
    assert "never" in WORKSPACE_WRITE_RULES and "shared environment" in WORKSPACE_WRITE_RULES


def test_the_policy_allows_the_workspace_install_and_refuses_the_shared_one(tmp_path):
    assert denial(tmp_path, "pip install --target ./.pylib xlrd", environment_step=False) is None
    assert denial(tmp_path, "pip install xlrd", environment_step=False)  # the shared environment
    assert denial(tmp_path, "cd ..; pip install --target ./.pylib xlrd", environment_step=False)  # outside the folder


def test_a_read_given_up_for_lack_of_a_tool_becomes_a_cp2_warning():
    ledger = {"evidence": [
        {"id": "e1", "status": "unavailable", "slots": ["data"],
         "status_detail": "도구 없음: ons_2013.xls · xlrd"},
        {"id": "e2", "status": "unavailable", "slots": ["other"], "status_detail": "서버 응답 없음"},
        {"id": "e3", "status": "observed", "slots": ["data"], "status_detail": "도구 없음: not unavailable"},
    ]}
    assert tool_missing_lines("s2", ledger) == ["s2/data: 도구 없음 — ons_2013.xls · xlrd"]
    assert tool_missing_lines("s2", {}) == [] and tool_missing_lines("s2", None) == []
