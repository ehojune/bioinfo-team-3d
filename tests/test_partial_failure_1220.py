"""#1220: a request that finished `done` with failed, skipped or never-run steps says so. The marker is derived from the
results the request already holds: nothing new is stored and there is no new outcome or status."""
import asyncio
import copy
import json
import subprocess
from pathlib import Path

import pytest

from labhq import cli
from labhq.gateway.server import Hub
from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.request_status import failed_step_ids, request_display_label
from labhq.settings import Settings
from tests.test_branched_research import branched_plan, result_for
from tests.test_research_protocol import MiniHub

ROOT = Path(__file__).resolve().parents[1]


def request(status="done", **extra):
    return {"id": "r", "status": status, "text": "study",
            "plan": {"steps": [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}, {"id": "s4"}]},
            "results": {"s1": {"ok": True},
                        "s2": {"ok": False, "error": "internal worker failure"},
                        "s3": {"ok": False, "status": "skipped", "error": "skipped: s2 failed"}},
            **extra}


# ----- the helper and the label -----

def test_failed_step_ids_are_the_failed_and_skipped_steps_that_left_a_result():
    # s4 has no result: a step without one is not counted (it may never have been meant to run)
    assert failed_step_ids(request()) == ["s2", "s3"]


def test_a_request_that_is_not_done_has_no_partial_failure_marker():
    for status in ("running", "failed", "cancelled", "interrupted", "waiting_pi", "rejected"):
        assert failed_step_ids(request(status)) == []


def test_a_clean_done_request_and_a_direct_one_have_no_marker_unless_they_failed():
    clean = {"status": "done", "plan": {"steps": [{"id": "s1"}]}, "results": {"s1": {"ok": True}}}
    assert failed_step_ids(clean) == []
    assert failed_step_ids({"status": "done", "mode": "direct", "results": {"direct": {"ok": True}}}) == []
    assert failed_step_ids({"status": "done", "mode": "direct", "results": {"direct": {"ok": False}}}) == ["direct"]


@pytest.mark.parametrize("name,done", [
    ("plan_only", {"mode": "plan_only", "results": {}}),                                   # plans on purpose, runs nothing
    ("plan_only by outcome", {"mode": "team", "outcome": "plan_only", "results": {}}),
    ("plan approved, checkpoint off", {"mode": "team", "outcome": "plan_approved", "results": {}}),
    ("solo route after a team plan", {"mode": "team", "results": {"direct": {"ok": True}}}),
])
def test_a_request_that_left_plan_steps_unused_on_purpose_is_not_partially_failed(name, done):
    """#1222 review: plan_only, a solo route that keeps the team plan, and a plan approved without the evidence checkpoint
    all end `done` with plan steps that never ran. No step failed, so nothing is marked: the marker counts only steps that
    left a failed or skipped result, never the plan minus the results."""
    planned = {"status": "done", "plan": {"steps": [{"id": "s1"}, {"id": "s2"}]}, **done}
    assert failed_step_ids(planned) == [], name
    assert request_display_label(planned) == "완료", name


def test_a_failure_that_left_a_result_is_marked_whatever_else_the_request_did():
    team = {"status": "done", "mode": "team", "plan": {"steps": [{"id": "s1"}, {"id": "s2"}]},
            "results": {"s1": {"ok": True}, "s2": {"ok": False, "error": "x"}}}
    assert failed_step_ids(team) == ["s2"]
    assert request_display_label(team) == "완료 · 일부 실패 1"


def test_a_summary_that_already_lists_the_failed_steps_is_used_as_it_is():
    assert failed_step_ids({"status": "done", "failed_steps": ["a", "b"]}) == ["a", "b"]
    assert failed_step_ids({"status": "done", "failed_steps": []}) == []


def test_the_label_says_partial_failure_only_for_a_done_request_with_failed_steps():
    assert request_display_label(request()) == "완료 · 일부 실패 2"
    assert request_display_label({**request(), "results": {"s1": {"ok": True}}, "plan": {"steps": [{"id": "s1"}]}}) == "완료"
    assert request_display_label(request("failed")) == "실패"  # a failed request keeps its own word
    # a review wait keeps its own word first and still says what did not finish (the PI reads that label)
    assert request_display_label(request(pi_review_pending=True)) == "보고서 검토 대기 · 일부 실패 2"
    assert request_display_label(request("review_pending", pi_review_pending=True)) == "보고서 검토 대기 · 일부 실패 2"
    clean_review = {"status": "done", "pi_review_pending": True, "plan": {"steps": [{"id": "s1"}]},
                    "results": {"s1": {"ok": True}}}
    assert request_display_label(clean_review) == "보고서 검토 대기"


# ----- the gateway summary -----

def settings(tmp_path):
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    return s


def test_request_summary_lists_the_failed_steps_without_changing_the_step_progress(tmp_path):
    hub = Hub(settings(tmp_path))
    try:
        req = request()
        summary = hub.request_summary(req, running=[])
        assert summary["failed_steps"] == ["s2", "s3"]
        assert summary["status"] == "done"
        assert summary["step_progress"]["steps"] == {"s1": "done", "s2": "failed", "s3": "skipped", "s4": "pending"}
        assert hub.request_summary(request("running"), running=[])["failed_steps"] == []
        assert hub.request_summary(request("failed"), running=[])["failed_steps"] == []
        assert "failed_steps" not in req  # derived: nothing is written back to the request
    finally:
        hub.store.close()


# ----- the CLI -----

def test_watch_says_which_steps_failed_when_a_request_completes(capsys):
    cli.render({"type": "request.completed", "request_id": "r", "ts": 0,
                "data": {"ok": True, "status": "done", "failed_steps": ["B1", "B2"], "cost_usd": 0.0}})
    assert "일부 실패 B1, B2" in capsys.readouterr().out
    cli.render({"type": "request.completed", "request_id": "r", "ts": 0, "data": {"ok": True, "cost_usd": 0.0}})
    assert "일부 실패" not in capsys.readouterr().out


def test_status_lists_a_partially_failed_finished_request_even_without_a_bundle():
    lines = cli._finished_request_lines({"id": "r", "status": "done", "failed_steps": ["s2", "s3"]})
    assert lines[0] == "  r 완료 · 일부 실패 2"
    review = cli._finished_request_lines({"id": "r4", "status": "review_pending", "failed_steps": ["s2"]})
    assert "일부 실패한 단계: s2" in "\n".join(review)  # a CP4 review wait names the steps too
    assert "일부 실패한 단계: s2, s3" in "\n".join(lines)
    plain = cli._finished_request_lines({"id": "r2", "status": "done", "bundle_path": "x"})
    assert plain[0] == "  r2 완료" and "일부 실패" not in "\n".join(plain)
    assert cli._finished_request_lines({"id": "r3", "status": "done"}) == []  # nothing to say about it


# ----- the report and the finished event, through a request that really loses a branch -----

@pytest.mark.asyncio
async def test_a_lost_branch_is_named_in_the_limits_and_in_the_completed_event():
    plan = branched_plan((1, 2, 1, 1), synthesis=1)
    s = Settings()
    s.research.enabled = True
    s.research.evidence_checkpoint = True
    s.orchestrator.chief_of_staff_agent = None
    s.orchestrator.reviewer_agent = None
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=copy.deepcopy(plan))
        if task.meta["step_id"] == "B1":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="source refused",
                              workdir="failed-b1-workdir", workdir_id="wd-failed-b1")
        result = result_for(hub, task)
        path = next(step["outputs"][0] for step in hub.requests["r"]["plan"]["steps"] if step["id"] == task.meta["step_id"])
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result, outputs=[path],
                          output_sha256={path: "a" * 64})

    hub = holder["hub"] = MiniHub(s, reply, mode="orchestrate", work_kind="research", text="compare literature")
    hub.requests["r"]["budget_usd"] = 100
    decisions = [{"approved": True, "note": "approved"}, {"approved": True, "choice": "approve", "note": ""}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")
    await asyncio.sleep(0.05)  # MiniHub emits the terminal event from a task

    card = hub.approvals[1]["detail"]
    assert any(w.startswith("B1 (갈래 B): 단계 실패") for w in card["warnings"]), card["warnings"]
    assert any(w.startswith("B2 (갈래 B): 단계 실패") for w in card["warnings"]), card["warnings"]
    done = next(e for e in hub.events if e["type"] == "request.completed")
    assert done["data"]["failed_steps"] == ["B1", "B2"], done["data"]  # B2 left a "skipped: upstream B1" result
    assert "failed_steps" not in hub.requests["r"]  # derived at the moment of the event, never stored


@pytest.mark.asyncio
async def test_a_clean_request_completes_without_a_failed_steps_marker():
    plan = branched_plan((1, 1), synthesis=1)
    s = Settings()
    s.research.enabled = True
    s.research.evidence_checkpoint = True
    s.orchestrator.chief_of_staff_agent = None
    s.orchestrator.reviewer_agent = None
    holder = {}

    async def reply(task):
        hub = holder["hub"]
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=copy.deepcopy(plan))
        result = result_for(hub, task)
        path = next(step["outputs"][0] for step in hub.requests["r"]["plan"]["steps"] if step["id"] == task.meta["step_id"])
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=result, outputs=[path],
                          output_sha256={path: "a" * 64})

    hub = holder["hub"] = MiniHub(s, reply, mode="orchestrate", work_kind="research", text="compare")
    hub.requests["r"]["budget_usd"] = 100
    decisions = [{"approved": True, "note": "approved"}, {"approved": True, "choice": "approve", "note": ""}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")
    await asyncio.sleep(0.05)  # MiniHub emits the terminal event from a task
    done = [e for e in hub.events if e["type"] == "request.completed"]
    assert done and all(not e["data"].get("failed_steps") for e in done)


# ----- the web -----

def node_state(expression):
    script = (
        "const fs=require('fs'),vm=require('vm');const ctx=vm.createContext({});"
        f"vm.runInContext(fs.readFileSync({json.dumps(str(ROOT / 'labhq/web/state.js'))},'utf8'),ctx);"
        f"console.log(JSON.stringify((function(S){{return {expression};}})(ctx.LabHQState)));")
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_web_status_text_says_partial_failure_for_a_done_request():
    assert node_state("S.requestStatusText({status:'done', failed_steps:['s2','s3']}, null)") == "완료 · 일부 실패 2"
    # a live request has no snapshot list: the steps it already shows are enough
    assert node_state("S.requestStatusText({status:'done', steps:{s1:'done', s2:'error', s3:'skipped'}}, null)") == \
        "완료 · 일부 실패 2"
    assert node_state("S.requestStatusText({status:'done', steps:{s1:'done'}}, null)") == "완료"
    assert node_state("S.requestStatusText({status:'failed', failed_steps:['s2']}, null)") == "실패"


def test_the_web_keeps_the_review_wait_word_and_adds_the_partial_failure():
    assert node_state("S.requestStatusText({status:'done', pi_review_pending:true, failed_steps:['s2']}, null)") ==         "보고서 검토 대기 · 일부 실패 1"
    assert node_state("S.requestStatusText({status:'review_pending', pi_review_pending:true, failed_steps:['s2']}, null)") ==         "보고서 검토 대기 · 일부 실패 1"


def test_the_web_marks_a_partially_failed_request_with_a_warning_class():
    assert node_state("S.partialFailureSteps({status:'done', failed_steps:['s2']})") == ["s2"]
    assert node_state("S.partialFailureSteps({status:'running', failed_steps:['s2']})") == []
    assert node_state("S.partialFailureSteps({status:'done', steps:{s1:'done'}})") == []


def test_the_pdf_header_and_its_cache_key_carry_the_partial_failure_label():
    from labhq.report_pdf import _cache_key, build_report_html
    partial = {**request(), "report": "# Title\n\nbody", "created_at": 1.0}
    clean = {**partial, "results": {"s1": {"ok": True}}, "plan": {"steps": [{"id": "s1"}]}}
    page, _ = build_report_html(partial, None)
    assert "완료 · 일부 실패 2" in page
    clean_page, _ = build_report_html(clean, None)
    assert "일부 실패" not in clean_page
    assert _cache_key(partial, None) != _cache_key({**partial, "results": clean["results"], "plan": clean["plan"]}, None)

