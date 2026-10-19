"""PI #732: standard workflows first, and one durable consent per contribution."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from labhq.gateway.server import Hub
from labhq.models import AskRequest, TaskResult
from labhq.orchestrator.cso import PLAN_PROMPT, REPLAN_PROMPT, RESEARCH_PLAN_PROMPT
from tests.test_bioinfo_pipeline_pr import fake_pipeline_api, pipeline, settings


READY = {"type": "pipeline.ready", "request_id": "r", "task_id": "t"}


def test_manual_matches_assignment_and_per_candidate_contribution_policy():
    manual = (Path(__file__).parents[1] / "docs" / "manual.md").read_text(encoding="utf-8")
    for current in (
        "지원하는 nf-core pipeline을 우선",
        "지원 밖의 복잡한 신규 구현은 engineer나 analyst",
        "후보마다 PI가 별도로 `yes`",
        "후보별 승인 경로만 활성화",
    ):
        assert current in manual
    for stale in (
        "공개 기여에 동의하면 `policy.bioinfo_agent.pipeline_pr: true`",
        "신규 구현(bioinfo-agent)",
        "bioinfo-agent의 workflow 구현",
        "새 pipeline 생성 여부는 CSO가 답합니다",
    ):
        assert stale not in manual


async def close(hub):
    if hub.reporter.worker:
        hub.reporter.worker.cancel()
        await asyncio.gather(hub.reporter.worker, return_exceptions=True)
    if hub.reporter._client:
        await hub.reporter._client.http.aclose()
    hub.store.close()


async def candidate(tmp_path, calls, agent="engineer"):
    hub = Hub(settings(tmp_path, pipeline_pr=True), github_transport=fake_pipeline_api(calls))
    hub.requests["r"] = {"id": "r", "status": "done"}
    hub.save_request("r")
    hub.store.put("pipeline_submission", "t", {"request_id": "r", "task_id": "t",
        "agent_id": agent, "state": "ready", "submission": pipeline()})
    await hub.publish(READY)
    await hub.reporter.drain()
    return hub


def card(hub):
    return next(e["approval"] for e in hub.approvals.values()
                if e["approval"]["kind"] == "pipeline_contribute")


@pytest.mark.parametrize("prompt", [PLAN_PROMPT, RESEARCH_PLAN_PROMPT, REPLAN_PROMPT],
                         ids=["general", "research", "replan"])
def test_every_planning_route_carries_the_three_way_assignment(prompt):
    for boundary in ("nf-core", "bioinfo-agent", "simple collection", "engineer or analyst",
                     "per-candidate PI yes", "reason"):
        assert boundary in prompt


async def test_missing_pipeline_consult_assigns_a_builder_and_keeps_contribution_separate(tmp_path):
    hub = Hub(settings(tmp_path))
    hub.agents = {"cso": {"engine": "mock"}}
    hub.requests["r"] = {"id": "r", "text": "analysis", "status": "running"}
    prompts = []

    async def dispatch(task):
        prompts.append(task.prompt)
        return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="engineer에게 배정합니다")

    hub.dispatch = dispatch
    try:
        await hub.orchestrator.answer_ask(AskRequest(task_id="t", agent_id="bioinfo-agent",
            request_id="r", to="cso", question="nf-core에 없는 새 pipeline을 만들까요?",
            why_blocked="지원 pipeline이 없습니다"), "runner")
        assert "engineer or analyst" in prompts[0]
        assert "per-candidate PI yes" in prompts[0]
        assert "answer that it should build it" not in prompts[0]
    finally:
        await close(hub)


async def test_candidate_waits_without_any_github_request_and_duplicates_reuse_the_card(tmp_path):
    calls = []
    hub = await candidate(tmp_path, calls)
    try:
        first = card(hub)
        assert calls == []
        assert "거절" in first["pi_outcome"] and "PR" in first["pi_outcome"]
        assert "timeout_s" not in first  # optional contribution stays answerable after the analysis ends
        assert hub.requests["r"]["status"] == "done"
        assert hub.snapshot()["data"]["requests"][0]["pipeline_pr"]["status"] == "awaiting_approval"
        await hub.publish(READY)
        await hub.reporter.drain()
        assert card(hub)["id"] == first["id"] and len(hub.approvals) == 1
        assert calls == []
    finally:
        await close(hub)


async def test_contribution_card_contains_the_exact_candidate_source_before_yes(tmp_path):
    calls = []
    submission = pipeline()
    hub = await candidate(tmp_path, calls)
    try:
        detail = card(hub)["detail"]
        assert detail["files"] == submission["files"]
        assert calls == []
    finally:
        await close(hub)


@pytest.mark.parametrize("approved", [False, True])
async def test_one_decision_controls_one_contribution_and_survives_duplicate_results(tmp_path, approved):
    calls = []
    hub = await candidate(tmp_path, calls)
    try:
        await hub.resolve_approval(card(hub)["id"], approved)
        await hub.reporter.drain()
        row = hub.store.get("pipeline_submission", "t")
        assert row["state"] == ("open" if approved else "declined")
        assert hub.snapshot()["data"]["requests"][0]["pipeline_pr"]["status"] == row["state"]
        await hub.on_runner_message("runner", {"type": "task.result", "request_id": "r", "task_id": "t",
            "data": TaskResult(task_id="t", agent_id="engineer", ok=True,
                               pipeline_submission=pipeline()).model_dump(mode="json")})
        await hub.publish(READY)
        await hub.reporter.drain()
        assert hub.store.get("pipeline_submission", "t")["state"] == row["state"]
        assert len([c for c in calls if c[0] == "POST" and c[1].endswith("/pulls")]) == int(approved)
        assert not hub.approvals
        assert not hub.store.all("reporter_delivery")
        if not approved:
            assert calls == []
    finally:
        await close(hub)


@pytest.mark.parametrize("approved", [None, False, True])
async def test_restart_keeps_the_card_or_saved_decision_without_duplicate_prs(tmp_path, approved):
    calls = []
    hub = await candidate(tmp_path, calls)
    aid = card(hub)["id"]
    if approved is not None:
        await hub.resolve_approval(aid, approved)
        await hub.reporter.drain()
    await close(hub)
    restored = Hub(settings(tmp_path, pipeline_pr=True), github_transport=fake_pipeline_api(calls))
    try:
        restored.recover_terminal_deliveries()
        await restored.reporter.drain()
        if approved is None:
            assert card(restored)["id"] == aid
            assert calls == []
            await restored.resolve_approval(aid, True)
            await restored.reporter.drain()
        else:
            assert not restored.approvals
        assert len([c for c in calls if c[0] == "POST" and c[1].endswith("/pulls")]) == int(approved is not False)
    finally:
        await close(restored)


async def test_restart_recovers_yes_committed_before_the_ready_event(tmp_path):
    calls = []
    hub = await candidate(tmp_path, calls)
    publish = hub.publish

    async def crash(ev, *args, **kwargs):
        if ev["type"] == "pipeline.ready":
            raise RuntimeError("crash after saved decision")
        await publish(ev, *args, **kwargs)

    hub.publish = crash
    with pytest.raises(RuntimeError, match="saved decision"):
        await hub.resolve_approval(card(hub)["id"], True)
    await close(hub)
    restored = Hub(settings(tmp_path, pipeline_pr=True), github_transport=fake_pipeline_api(calls))
    try:
        restored.recover_terminal_deliveries()
        await restored.reporter.drain()
        assert restored.store.get("pipeline_submission", "t")["state"] == "open"
        assert len([c for c in calls if c[0] == "POST" and c[1].endswith("/pulls")]) == 1
    finally:
        await close(restored)


@pytest.mark.parametrize("change", ["content", "target"])
async def test_yes_cannot_authorize_changed_content_or_a_different_target(tmp_path, change):
    calls = []
    hub = await candidate(tmp_path, calls)
    aid = card(hub)["id"]
    try:
        if change == "content":
            row = hub.store.get("pipeline_submission", "t")
            row["submission"]["files"][0]["content"] += "workflow {}\n"
            hub.store.put("pipeline_submission", "t", row)
        else:
            hub.s.policy.bioinfo_agent.pipeline_repo = "example/another"
        await hub.resolve_approval(aid, True)
        await hub.reporter.drain()
        assert calls == []
        assert card(hub)["id"] != aid
    finally:
        await close(hub)


async def test_restart_finds_a_pr_created_before_the_open_state_was_saved(tmp_path):
    calls, opened = [], []
    hub = await candidate(tmp_path, calls)
    base_api = fake_pipeline_api(calls).handler

    def api(request):
        if request.method == "GET" and request.url.path.endswith("/pulls") and opened:
            calls.append((request.method, request.url.path, None))
            return httpx.Response(200, json=opened)
        response = base_api(request)
        if request.method == "POST" and request.url.path.endswith("/pulls"):
            body = json.loads(request.content)
            opened.append({**response.json(), "body": body["body"], "head": {"ref": body["head"]}})
        return response

    hub.reporter.transport = httpx.MockTransport(api)
    save_status = hub.reporter._pipeline_status

    async def crash(entry, status, *args, **kwargs):
        if status == "open":
            raise RuntimeError("crash before saved open state")
        await save_status(entry, status, *args, **kwargs)

    hub.reporter._pipeline_status = crash
    await hub.resolve_approval(card(hub)["id"], True)
    await hub.reporter.drain()
    assert len(opened) == 1
    await close(hub)
    restored = Hub(settings(tmp_path, pipeline_pr=True), github_transport=httpx.MockTransport(api))
    try:
        restored.recover_terminal_deliveries()
        await restored.reporter.drain()
        assert restored.store.get("pipeline_submission", "t")["state"] == "open"
        assert len(opened) == 1
    finally:
        await close(restored)
