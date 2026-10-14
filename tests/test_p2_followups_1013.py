"""Remaining merged-review regressions; all stores and runners are synthetic."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from labhq.gateway.server import Hub
from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator, PlanAgentError, validate_steps
from labhq.result_storage import ResultStorage
from labhq.settings import Settings, DataZone
from tests.test_result_storage import Hub as StorageHub
from tests.test_followup import FakeHub, answer
from tests.test_semantics_request_report import config
from tests.test_facilities_staff import staff


def test_timeout_decision_is_counted(tmp_path):
    from labhq.store import StateStore
    from labhq.research.semantics_request_report import build_request_report, render_request_report
    s = config(tmp_path)
    store = StateStore(tmp_path / "state/gateway.sqlite3")
    store.put("request", "req_test", {"id": "req_test", "status": "done"})
    store.put("approval_decision", "a", {"approval": {"request_id": "req_test"}, "state": "timed_out"})
    store.close()
    report = build_request_report(s, "req_test")
    assert sum(report["actual"]["approvals"].values()) == 1
    assert "시간 초과 1" in render_request_report(report)


@pytest.mark.parametrize("status,label", [("waiting_pi", "PI 응답 대기"), ("waiting_quota", "사용량 한도 대기"),
    ("waiting_login", "로그인 대기"), ("waiting_facilities_fix", "환경 수정 대기"),
    ("waiting_for_runner", "runner 연결 대기")])
def test_waiting_status_names(status, label):
    from labhq.research.semantics_request_report import render_request_report
    report = {"request_id": "req_test", "shadow": None, "warnings": [], "actual": {
        "status": status, "steps": 0, "steps_done": 0, "steps_failed": 0, "tasks": 0,
        "inputs": 0, "outputs_reported": 0, "approvals": {"approved": 0, "denied": 0, "expired": 0}}}
    assert label in render_request_report(report)


async def test_all_prior_followup_workspaces_remain_inputs():
    hub = FakeHub(answer())
    hub.requests["r"]["followups"] = [dict(id=f"old{i}", status="done", text=f"question{i}",
        answer="outputs/chart.png", result={"workdir": f"/w/old{i}"}) for i in range(5)]
    hub.ask("Use the first chart")
    await Orchestrator(hub).run_followup("r", "fu_1")
    task = hub.calls[0]
    assert all(f"/w/old{i}" in task.meta["upstream_dirs"] for i in range(5))
    assert "question4" in task.prompt and "/w/old4" in task.prompt
    assert "Paths in earlier answers are relative to that workspace" not in task.prompt


async def test_followup_records_new_workspace_for_later_turns():
    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="outputs/chart.png",
                          workdir=f"/w/{task.meta['followup_id']}", session_id="session")
    hub = FakeHub(reply)
    orchestrator = Orchestrator(hub)
    for i in range(5):
        hub.ask(f"question{i}", fid=f"fu_{i}")
        await orchestrator.run_followup("r", f"fu_{i}")
    assert all(f"/w/fu_{i}" in hub.calls[-1].meta["upstream_dirs"] for i in range(4))
    assert hub.requests["r"]["followups"][0]["result"]["workdir"] == "/w/fu_0"


@pytest.mark.parametrize("failure", [RuntimeError("dispatch failed"), None])
async def test_editor_fallback_and_required_links(tmp_path, failure):
    hub = StorageHub(tmp_path)
    hub.requests["req_test"].update(status="done", result_storage={"mode": "local", "status": "selected"})
    hub.agents = {"editor": {}}
    run = AsyncMock(side_effect=failure, return_value=TaskResult(task_id="t", agent_id="editor", ok=True,
                                                               structured={"readme": "# Edited result"}))
    hub.orchestrator = SimpleNamespace(run_step=run)
    await ResultStorage(hub).finish("req_test")
    saved = hub.requests["req_test"]["result_storage"]
    assert saved["status"] == "local"
    text = Path(saved["draft_path"], "README.md").read_text(encoding="utf-8")
    for name in ("report.md", "PROGRESS.md", "REPRODUCE.md", "SOURCES.md"):
        assert f"]({name})" in text


async def test_cancelled_request_gets_local_edition(tmp_path):
    hub = StorageHub(tmp_path)
    hub.requests["req_test"].update(status="cancelled", report="Partial result", result_storage={"mode": "local", "status": "selected"})
    await ResultStorage(hub).finish("req_test")
    saved = hub.requests["req_test"]["result_storage"]
    assert saved["status"] == "local"
    assert "Partial result" in Path(saved["draft_path"], "report.md").read_text(encoding="utf-8")


async def test_no_clarification_wait_still_selects_local(tmp_path, monkeypatch):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    s.orchestrator.wait_for_clarification = False
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    hub = Hub(s)
    try:
        hub.requests["req_test"] = {"id": "req_test", "intake": {"work_kind": "research"}}
        status, _ = await hub.orchestrator._plan_clarification("req_test", {"clarifying_questions": ["Which input?"]})
        assert status == "open"
        assert hub.requests["req_test"]["result_storage"]["mode"] == "local"
    finally:
        hub.store.close()


async def test_publish_timeout_preserves_recoverable_draft(tmp_path):
    hub = StorageHub(tmp_path)
    hub.requests["req_test"].update(status="done", result_storage={"mode": "existing", "status": "selected", "repo": "owner/study"})
    hub.request_approval = AsyncMock(return_value={"approved": False, "state": "timed_out"})
    service = ResultStorage(hub)
    await service.finish("req_test")
    assert hub.requests["req_test"]["result_storage"]["status"] == "draft"
    service.schedule("req_test")
    await service.tasks["req_test"]
    assert hub.request_approval.await_count == 2


async def test_editor_task_blocks_graceful_shutdown(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(s)
    pending = asyncio.create_task(asyncio.Event().wait())
    hub.requests["req_test"] = {"status": "done"}
    hub.result_storage.tasks["req_test"] = pending
    try:
        readiness = hub.shutdown_readiness()
        assert not readiness["ready"]
        assert any(row["id"] == "req_test" and row["stage"] == "result_storage" for row in readiness["finishing"])
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        hub.store.close()


def test_staff_skill_guard_precedes_resolution(tmp_path, monkeypatch):
    from labhq.recruit import paper2agent
    s = Settings()
    s.engines.codex.env = {"CODEX_HOME": str(tmp_path / "staff")}
    def guarded(path):
        raise RuntimeError("guard first")
    monkeypatch.setattr(paper2agent, "guard_test_path", guarded)
    monkeypatch.setattr(Path, "resolve", lambda *a, **kw: pytest.fail("resolved before guard"))
    with pytest.raises(RuntimeError, match="guard first"):
        paper2agent.skill_installed("codex", s)


def test_facilities_cannot_be_a_plan_worker():
    with pytest.raises(PlanAgentError):
        validate_steps([{"id": "s1", "agent_id": "facilities", "instruction": "diagnose", "outputs": ["outputs/a.txt"]}],
                       {"worker", "facilities"}, 5)


async def test_facilities_evidence_includes_log_tail(staff, monkeypatch):
    _, runner = staff
    source = Path(runner.s.runner.workspace_root) / "source"
    source.mkdir(parents=True)
    (source / "error.txt").write_text("BUILD START\n" + "progress\n" * 1000 + "ModuleNotFoundError: missing_tail_tool", encoding="utf-8")
    captured = []
    class Adapter:
        async def run(self, ctx):
            captured.append(ctx)
            return TaskResult(task_id=ctx.task.id, agent_id="facilities", ok=True, text="diagnosis")
    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())
    from labhq.models import Task
    result = await runner.run_task(Task(agent_id="facilities", prompt="diagnose", meta={
        "kind": "consult", "source_workdir": str(source), "consult_refs": ["error.txt"]}))
    assert result.ok
    assert "BUILD START" in captured[0].prompt and "missing_tail_tool" in captured[0].prompt


def test_automatic_diagnosis_is_not_a_blocking_ask(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(s)
    try:
        hub.store.put("ask", "automatic", {"ask": {"task_id": "source"}, "facilities_diagnosis": True,
                                           "state": "resolved", "answer": {"status": "rejected"}})
        hub.store.put("ask", "ordinary", {"ask": {"task_id": "source"}, "state": "resolved",
                                          "answer": {"status": "answered", "answer": "continue"}})
        assert hub.ask_results_for_task("source") == [{"status": "answered", "answer": "continue"}]
    finally:
        hub.store.close()


async def test_recall_searches_past_filtered_candidates(tmp_path):
    from tests.test_knowledge_recall import config as recall_config, request
    hub = Hub(recall_config(tmp_path))
    try:
        request(hub, "req_aaa_safe", text="유전자 safe study")
        for i in range(101):
            request(hub, f"req_filtered{i}", text="유전자 /later study")
        await hub.knowledge.drain()
        hub.s.policy.data_zones = [DataZone(path="/later", classification="restricted")]
        assert [r["request_id"] for r in hub.knowledge.search("유전자")] == ["req_aaa_safe"]
    finally:
        hub.store.close()


def test_dispatch_messages_are_archived(tmp_path):
    from labhq.store import StateStore
    store = StateStore(tmp_path / "store.sqlite3")
    try:
        store.append_event({"type": "task.dispatched", "request_id": "r", "agent_id": "worker",
                            "data": {"kind": "step", "title": "Analyze"}}, 100)
        assert store.db.execute("SELECT COUNT(*) FROM messenger_events").fetchone()[0] == 1
    finally:
        store.close()


def test_purge_removes_archived_messages(tmp_path):
    from labhq.store import StateStore
    from scripts.purge_test_requests import purge
    store = StateStore(tmp_path / "store.sqlite3")
    for rid, text in [("test", "synthetic"), ("keep", "real")]:
        store.put("request", rid, {"text": text, "agent_id": "worker", "mode": "direct"})
        store.append_event({"type": "request.created", "request_id": rid}, 100)
    store.close()
    purge(tmp_path / "store.sqlite3", tmp_path / "backup.sqlite3", ["synthetic"], "worker", "direct")
    store = StateStore(tmp_path / "store.sqlite3")
    try:
        assert store.db.execute("SELECT request_id FROM messenger_events").fetchall() == [("keep",)]
    finally:
        store.close()
