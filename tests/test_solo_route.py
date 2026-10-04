from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from labhq import cli
from labhq.gateway.server import RequestIn
from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator, PLAN_PROMPT, PLAN_SCHEMA, route_decision
from labhq.settings import Settings


def plan(route="solo"):
    value = {
        "scope": {"verdict": "in", "reason": "bioinformatics"},
        "clarifying_questions": [],
        "assumptions": ["공개 자료만 사용 — 접근 승인이 필요 없다"],
        "steps": [{"id": "A", "agent_id": "worker", "instruction": "Make the table",
                   "outputs": ["outputs/team.tsv"], "depends_on": []}],
        "recruit": [],
        "notes": "fallback",
    }
    if route is not None:
        value["route"] = route
    return value


class SoloHub:
    def __init__(self, *, planned=None, solo=None, route="auto", solo_agent="solo"):
        self.s = Settings()
        self.s.orchestrator.chief_of_staff_agent = None
        self.s.orchestrator.reviewer_agent = None
        self.s.orchestrator.solo_agent = solo_agent
        self.s.orchestrator.step_retry_backoff_s = 0
        self.requests = {"r": {"id": "r", "text": "원 요청", "mode": "orchestrate", "route": route,
                               "status": "running", "budget_usd": None, "project_dirs": [], "references": []}}
        self.agents = {name: {"id": name, "name": name, "role": "test", "engine": "mock"}
                       for name in ("cso", "worker", "solo")}
        self.planned = planned or plan()
        self.solo = solo or TaskResult(task_id="solo-task", agent_id="solo", ok=True, text="짧은 결론",
                                       outputs=["answer.md"], workdir_id="task_solo", cost_usd=0.4)
        self.calls = []
        self.events = []
        self.terminal = []

    async def dispatch(self, task):
        self.calls.append(task)
        kind = task.meta.get("kind")
        if kind == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=self.planned)
        if kind == "direct":
            return self.solo.model_copy(update={"task_id": task.id, "agent_id": task.agent_id})
        if kind == "step":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="team result",
                              outputs=["outputs/team.tsv"], workdir_id="task_team")
        if kind == "synthesis":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="team report")
        raise AssertionError(f"unexpected task kind {kind}")

    async def publish(self, event):
        self.events.append(event)

    async def request_approval(self, **_kwargs):
        return {"approved": True, "note": "approved"}

    def supports_resume(self, _agent_id):
        return False

    def result_map(self, rid):
        return {}

    def save_request(self, _rid):
        return None

    def commit_terminal(self, rid, typ, data):
        self.terminal.append((rid, typ, data))


@pytest.mark.asyncio
async def test_solo_plan_dispatches_one_direct_turn_and_uses_its_report_and_outputs():
    hub = SoloHub()
    await Orchestrator(hub).run_request("r")

    kinds = [task.meta.get("kind") for task in hub.calls]
    assert kinds == ["plan", "direct"]
    direct = hub.calls[-1]
    assert direct.agent_id == "solo" and direct.meta["kind"] == "direct"
    assert "원 요청" in direct.prompt and "공개 자료만 사용" in direct.prompt
    assert "2,000" in direct.prompt and "outputs/scripts/" in direct.prompt
    req = hub.requests["r"]
    assert req["report"] == "짧은 결론"
    assert "처리 방식: 단독 (solo)" in req["report_appendix"]
    assert "task_solo/answer.md" in req["report_appendix"] and "$0.40" in req["report_appendix"]
    assert req["route_decision"]["mode"] == "solo" and req["solo_result"]["ok"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("planned,solo_agent", [(plan(), None), (plan(None), "solo"), (plan("unknown"), "solo")])
async def test_disabled_missing_or_unknown_solo_route_uses_team(planned, solo_agent):
    hub = SoloHub(planned=planned, solo_agent=solo_agent)
    await Orchestrator(hub).run_request("r")

    kinds = [task.meta.get("kind") for task in hub.calls]
    assert "direct" not in kinds and "step" in kinds and "synthesis" in kinds
    assert hub.requests["r"]["route_decision"]["mode"] == "team"


@pytest.mark.asyncio
async def test_api_team_override_runs_the_fallback_plan_without_solo_dispatch():
    body = RequestIn(text="small task", route="team")
    hub = SoloHub(route=body.route)
    await Orchestrator(hub).run_request("r")
    kinds = [task.meta.get("kind") for task in hub.calls]
    assert kinds == ["plan", "step", "synthesis"]
    assert hub.requests["r"]["route_decision"]["requested"] == "team"


def test_research_and_forced_team_route_decisions_never_select_solo():
    roster = [{"id": "solo"}]
    assert route_decision(plan(), "solo", roster, requested="team", research=False)["mode"] == "team"
    assert route_decision(plan(), "solo", roster, requested="auto", research=True)["mode"] == "team"


@pytest.mark.asyncio
@pytest.mark.parametrize("solo", [
    TaskResult(task_id="x", agent_id="solo", ok=False, error="crashed"),
    TaskResult(task_id="x", agent_id="solo", ok=True, text="", outputs=["answer.md"]),
    TaskResult(task_id="x", agent_id="solo", ok=True, text="answer", outputs=[]),
])
async def test_solo_failure_falls_back_to_saved_team_plan(solo):
    hub = SoloHub(solo=solo)
    await Orchestrator(hub).run_request("r")

    kinds = [task.meta.get("kind") for task in hub.calls]
    assert kinds == ["plan", "direct", "step", "synthesis"]
    assert hub.requests["r"]["report"] == "team report"
    assert "단독 실패 → 팀" in hub.requests["r"]["report_appendix"]
    assert any(event["type"] == "request.route" and event["data"].get("fallback") for event in hub.events)


@pytest.mark.asyncio
async def test_note_during_solo_is_listed_if_solo_finishes_and_reaches_team_after_fallback():
    hub = SoloHub()

    async def solo_with_note(task):
        hub.requests["r"].setdefault("pi_notes", []).append(
            {"id": "note_1", "text": "표를 하나 더", "at": hub.requests["r"]["solo_started_at"] + 1})
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="answer",
                          outputs=["answer.md"], workdir_id="task_solo")

    original = hub.dispatch

    async def dispatch(task):
        return await solo_with_note(task) if task.meta.get("kind") == "direct" else await original(task)

    hub.dispatch = dispatch
    await Orchestrator(hub).run_request("r")
    assert "단독 턴이 시작된 뒤 온 메모는 반영되지 않았다" in hub.requests["r"]["report"]
    assert "note_1" in hub.requests["r"]["report"] and "표를 하나 더" in hub.requests["r"]["report"]

    failed = SoloHub(solo=TaskResult(task_id="x", agent_id="solo", ok=False, error="crashed"))

    async def failed_dispatch(task):
        if task.meta.get("kind") == "direct":
            failed.requests["r"].setdefault("pi_notes", []).append(
                {"id": "note_2", "text": "성별도 확인", "at": failed.requests["r"]["solo_started_at"] + 1})
        return await SoloHub.dispatch(failed, task)

    failed.dispatch = failed_dispatch
    await Orchestrator(failed).run_request("r")
    team = next(task for task in failed.calls if task.meta.get("kind") == "step")
    assert "note_2" in team.prompt and "성별도 확인" in team.prompt


@pytest.mark.asyncio
async def test_saved_solo_result_is_reused_after_restart():
    hub = SoloHub()
    hub.requests["r"].update(plan=plan(), route_decision={"mode": "solo", "agent_id": "solo"},
                             solo_started_at=1,
                             solo_result=hub.solo.model_dump(mode="json"))
    await Orchestrator(hub).run_request("r", resume=True)
    assert [task.meta.get("kind") for task in hub.calls] == []
    assert hub.requests["r"]["status"] == "done" and hub.requests["r"]["report"] == "짧은 결론"


def test_plan_schema_prompt_settings_and_api_route_contract():
    assert PLAN_SCHEMA["properties"]["route"]["enum"] == ["team", "solo"]
    assert "route" not in PLAN_SCHEMA["required"]
    assert "30 minutes or less" in PLAN_PROMPT and "When in doubt, use team" in PLAN_PROMPT
    assert Settings().orchestrator.solo_agent is None
    assert Settings().orchestrator.solo_review is False
    assert RequestIn(text="x").route == "auto"
    assert RequestIn(text="x", route="team").route == "team"
    with pytest.raises(ValidationError):
        RequestIn(text="x", route="solo")


def test_cli_team_flag_and_agent_conflict(monkeypatch):
    calls = []

    async def send(_settings, body):
        calls.append(body)

    monkeypatch.setattr(cli.Settings, "load", lambda _path: Settings())
    monkeypatch.setattr(cli, "_send_and_wait", send)
    cli.main(["send", "small task", "--team"])
    assert calls[0]["route"] == "team" and calls[0]["mode"] == "orchestrate"
    with pytest.raises(SystemExit):
        cli.main(["send", "small task", "--team", "--agent", "worker"])
