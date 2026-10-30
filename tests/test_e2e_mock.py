import asyncio
import re
import shutil
import time
from pathlib import Path

import pytest
import uvicorn
import yaml

from labhq.gateway.server import RequestIn, create_app
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.util import free_port
from tests.support.serve_task import serve_task, wait_started

REPO = Path(__file__).resolve().parents[1]


# A hang detector, not a speed limit. The flow does about 285 durable writes (write_owned with fsync) on the event
# loop, so a slow Windows disk stretches every step. That ran past the old 60 s and 90 s bounds (#592, #604) while the
# request was still running and its next task had just been dispatched.
HANG_S = 120.0       # no progress for this long means stuck
CEILING_S = 1200.0   # even a flow that keeps moving ends here
_clock = time.monotonic


async def _until(pred, timeout=HANG_S, waiting_for="condition", state=None, progress=None):
    """Wait until pred() holds.

    Without `progress`, `timeout` is a plain deadline. With it (a cheap snapshot of what the flow has done so far),
    `timeout` counts from the last change, so a slow runner that keeps moving is never cut off, while a flow that
    stopped still fails after `timeout`. Nothing waits longer than CEILING_S.
    """
    start = _clock()
    deadline = start + timeout
    last = progress() if progress else None
    while not pred():
        now = _clock()
        if progress:
            current = progress()
            if current != last:
                last, deadline = current, now + timeout
        if now >= deadline or now - start >= CEILING_S:
            observed = state() if state else None
            raise AssertionError(f"timed out waiting for {waiting_for}; observed={observed!r}")
        await asyncio.sleep(0.05)


def _request_state(hub, rid, seen):
    request = hub.requests.get(rid) or {}
    return {
        "status": request.get("status"),
        "result_steps": list((request.get("results") or {}).keys()),
        "result_texts": {
            step_id: str((result or {}).get("text") or "")[:120]
            for step_id, result in (request.get("results") or {}).items()
        },
        "events": [event.get("type") for event in seen if event.get("request_id") == rid],
    }


def _request_progress(hub, rid, seen):
    request = hub.requests.get(rid) or {}
    return (request.get("status"), len(request.get("results") or {}),
            sum(1 for event in seen if event.get("request_id") == rid))


async def _wait_for_request_terminal(hub, rid, seen, timeout=HANG_S):
    """`timeout` is the longest the request may go without any progress (tests/test_research_cp1.py passes its own)."""
    await _until(
        lambda: (hub.requests.get(rid) or {}).get("status") in {"done", "failed", "cancelled", "interrupted"},
        timeout,
        f"request {rid} to reach a terminal state",
        lambda: _request_state(hub, rid, seen),
        progress=lambda: _request_progress(hub, rid, seen),
    )


async def test_until_timeout_reports_observed_event_order():
    with pytest.raises(AssertionError, match=r"request events.*request.started.*job.submitted"):
        await _until(
            lambda: False,
            0,
            "request events",
            lambda: {"events": ["request.started", "job.submitted"]},
        )


class _Clock:
    """Each reading moves a fake clock forward, so the wait helper's limits run in a few polls and no real time."""

    def __init__(self, step):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


async def test_until_keeps_waiting_while_the_flow_moves(monkeypatch):
    monkeypatch.setitem(globals(), "_clock", _Clock(100.0))
    polls = {"n": 0}

    def done():
        polls["n"] += 1
        return polls["n"] > 8  # 800 fake seconds, past the 250 s hang limit, under the ceiling

    await _until(done, 250.0, "a slow but moving flow", progress=lambda: polls["n"])


async def test_until_fails_when_progress_stops_and_names_what_it_saw(monkeypatch):
    monkeypatch.setitem(globals(), "_clock", _Clock(100.0))
    with pytest.raises(AssertionError, match=r"stuck flow.*'last': 'dispatched'"):
        await _until(lambda: False, 250.0, "stuck flow", lambda: {"last": "dispatched"}, progress=lambda: "same")


async def test_until_ends_at_the_ceiling_even_if_progress_never_stops(monkeypatch):
    monkeypatch.setitem(globals(), "_clock", _Clock(100.0))
    polls = {"n": 0}

    def moving():
        polls["n"] += 1
        return polls["n"]

    with pytest.raises(AssertionError, match="endless flow"):
        await _until(lambda: False, 250.0, "endless flow", progress=moving)
    assert polls["n"] * 100.0 <= CEILING_S + 400.0  # stopped near the ceiling, not run on


async def test_full_lab_flow_with_mock_agents(tmp_path):
    shutil.copytree(REPO / "agents", tmp_path / "agents")
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    gport = free_port()
    s.gateway.port, s.gateway.url = gport, f"ws://127.0.0.1:{gport}"
    s.runner.broker_port, s.runner.force_engine, s.runner.job_poll_s = free_port(), "mock", 0.05
    s.runner.workspace_root = str(tmp_path / "runs")
    s.runner.talent_dir = str(tmp_path / "talent")
    s.runner.agents_dir = str(tmp_path / "agents")
    s.hpc.scheduler = "mock"

    app = create_app(s)
    hub = app.state.hub
    seen: list[dict] = []
    publish = hub.publish

    async def tap(ev, **kwargs):
        seen.append(ev)
        await publish(ev, **kwargs)
        if ev.get("type") == "approval.requested":
            aid = ev["data"]["id"]

            async def approve():
                # Answer later, like the web or a phone: request_approval must already be waiting on
                # its future, so the pending-approval path stays under test (no fixed sleep).
                await _until(lambda: aid in hub.approvals, HANG_S, f"approval {aid} to be pending",
                             lambda: {"pending": sorted(hub.approvals)})
                await hub.resolve_approval(aid, True, "ok")

            asyncio.get_running_loop().create_task(approve())

    hub.publish = tap
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=gport, log_level="warning"))
    tasks = [serve_task(server.serve())]
    await wait_started(lambda: server.started, tasks[0], HANG_S, "gateway server startup")
    runner = Runner(s)
    tasks.append(asyncio.create_task(runner.run_forever()))
    try:
        await _until(
            lambda: "cso" in hub.agents and "recruiter" in hub.agents,
            HANG_S,
            "runner agent registration",
            lambda: {"agents": list(hub.agents)},
        )
        assert hub.agents["analyst"]["hpc_tools"] is True
        assert hub.agents["lit_scout"]["hpc_tools"] is False
        assert hub.agents["analyst"]["engine"] == "mock"

        rid = hub.create_request(RequestIn(text="CD276 세포유형 분석 [hpc] [needs-approval] [revise] [revision-fail] [artifact] [recruit] [question] [block]"))
        await _wait_for_request_terminal(hub, rid, seen)
        req = hub.requests[rid]
        assert req["status"] == "done", _request_state(hub, rid, seen)
        steps = req["plan"]["steps"]
        assert not {s["agent_id"] for s in steps} & {"cso", "chief_of_staff", "sci_reviewer"}
        assert [st["agent_id"] for st in steps] == ["biologist", "data_steward", "bioinfo-agent", "analyst", "qc_reviewer"]
        analyst_step = next(st["id"] for st in steps if st["agent_id"] == "analyst")

        required_events = {"recruit.suggested", "job.submitted", "jobs.finished"}
        await _until(
            lambda: required_events <= {
                event["type"] for event in seen if event.get("request_id") == rid
            } and "깨어나서" in (
                ((hub.requests[rid].get("results") or {}).get(analyst_step) or {}).get("text") or ""
            ),
            HANG_S,
            "recruit, HPC completion, and analyst wake result",
            lambda: _request_state(hub, rid, seen),
            progress=lambda: _request_progress(hub, rid, seen),
        )
        req = hub.requests[rid]

        types = [e["type"] for e in seen if e.get("request_id") == rid]
        assert types.count("request.review") == 2, types  # revise → accept
        assert "recruit.suggested" in types and "job.submitted" in types and "jobs.finished" in types, types
        assert sum(e["data"]["kind"] == "clarify" for e in seen if e["type"] == "approval.requested") == 1
        clarify = next(e["data"] for e in seen if e["type"] == "approval.requested" and e["data"]["kind"] == "clarify")
        assert clarify["detail"]["questions"][0]["options"] == ["cases", "controls", "both"]  # #36 buttons
        assert req["clarifications"][0]["question_details"][0]["depth"] == 60
        assert "agent.ask" in types and "agent.answer" in types
        assert any(e["type"] == "approval.resolved" for e in seen)
        biologist_step = next(st["id"] for st in steps if st["agent_id"] == "biologist")
        biologist_tasks = [v for v in hub.store.all("task").values()
                           if v.get("request_id") == rid
                           and (v.get("payload") or {}).get("meta", {}).get("step_id") == biologist_step]
        first_biologist = next(v for v in biologist_tasks
                               if not (v.get("payload") or {}).get("resume_session_id"))
        resumed_biologist = next(v for v in biologist_tasks
                                 if (v.get("payload") or {}).get("resume_session_id"))
        assert resumed_biologist["payload"]["resume_session_id"] == first_biologist["result"]["session_id"]
        assert resumed_biologist["payload"]["meta"]["workdir"] == first_biologist["result"]["workdir"]
        assert "깨어나서" in req["results"][analyst_step]["text"], _request_state(hub, rid, seen)
        assert "revision failed" in req["results"][analyst_step]["revision_failed"]
        steward = next(st["id"] for st in steps if st["agent_id"] == "data_steward")
        assert req["results"][steward]["outputs"] == ["outputs/artifact.txt"]
        runs = req["results"][steward]["provenance"]["runs"]  # sent by the runner, not read off its disk
        assert runs and all("started_at" in r and "ended_at" in r for r in runs.values())
        assert hub.runner_capabilities and all("engine_cli_versions" in c for c in hub.runner_capabilities.values())
        assert "outputs/artifact.txt" not in req["report"]
        assert "outputs/artifact.txt" in req["execution_record"]

        # Resume을 지원하지 않는 엔진도 blocking 답을 같은 workdir의 새 세션에 전달한다.
        supports_resume = hub.supports_resume
        hub.supports_resume = lambda agent_id: False if agent_id == "biologist" else supports_resume(agent_id)
        rid_no_resume = hub.create_request(RequestIn(text="Resume 없는 blocking 확인 [block]"))
        await _wait_for_request_terminal(hub, rid_no_resume, seen)
        no_resume = hub.requests[rid_no_resume]
        assert no_resume["status"] == "done", _request_state(hub, rid_no_resume, seen)
        blocked_step = next(st["id"] for st in no_resume["plan"]["steps"]
                            if st["agent_id"] == "biologist")
        blocked_tasks = sorted(
            (v for v in hub.store.all("task").values()
             if v.get("request_id") == rid_no_resume
             and (v.get("payload") or {}).get("meta", {}).get("step_id") == blocked_step),
            key=lambda v: v["dispatched_at"],
        )
        assert len(blocked_tasks) == 2
        first_blocked, continued_blocked = blocked_tasks
        assert not continued_blocked["payload"].get("resume_session_id")
        assert continued_blocked["payload"]["meta"]["workdir"] == first_blocked["result"]["workdir"]
        assert continued_blocked["result"]["session_id"] != first_blocked["result"]["session_id"]
        assert "Choose sample group (a) cases or (b) controls." in continued_blocked["payload"]["prompt"]
        assert "answer:" in continued_blocked["payload"]["prompt"]
        hub.supports_resume = supports_resume

        rid_partial = hub.create_request(RequestIn(text="Partial study [max-turns]"))
        await _wait_for_request_terminal(hub, rid_partial, seen)
        partial = hub.requests[rid_partial]
        assert partial["status"] == "failed"
        biologist = next(st["id"] for st in partial["plan"]["steps"] if st["agent_id"] == "biologist")
        assert partial["results"][biologist]["partial_results"]
        assert "outputs/PARTIAL_STATUS.md" in partial["results"][biologist]["outputs"]
        assert (Path(partial["results"][biologist]["workdir"]) / "outputs" / "PARTIAL_STATUS.md").exists()

        # 파견직 채용 → 명단 등록 → 직접 업무 → 스킬이 작업공간에 설치됨
        await hub.send_runner(s.runner.id, {"type": "recruit.start", "repo": "https://github.com/scverse/scanpy",
                                            "focus": "Preprocessing and clustering", "ttl_days": 7})
        await _until(
            lambda: "c_scanpy" in hub.agents,
            HANG_S,
            "contract agent registration",
            lambda: {"agents": list(hub.agents)},
            progress=lambda: len(seen),
        )
        assert hub.agents["c_scanpy"]["employment"] == "contract"
        # #424: the contract keeps the commit and license it was built from and labhq's hash of the skill copy
        provenance = hub.agents["c_scanpy"]["contract_provenance"]
        assert re.fullmatch(r"[0-9a-f]{40}", provenance["source_commit"]) and provenance["code_license"]
        assert re.fullmatch(r"[0-9a-f]{64}", provenance["skill_sha256"]) and provenance["external_services"] == []
        saved = yaml.safe_load((tmp_path / "talent" / "scanpy" / "contract.yaml").read_text(encoding="utf-8"))
        assert saved["contract"]["skill_sha256"] == provenance["skill_sha256"]
        assert saved["contract"]["source_commit"] == provenance["source_commit"]
        rid2 = hub.create_request(RequestIn(text="클러스터링 자문", mode="direct", agent_id="c_scanpy"))
        await _wait_for_request_terminal(hub, rid2, seen)
        assert hub.requests[rid2]["status"] == "done"
        wd = Path(hub.requests[rid2]["results"]["direct"]["workdir"])
        assert (wd / ".claude" / "skills" / "scanpy-paper" / "SKILL.md").exists()
        assert (wd / "manifest.json").exists() and (wd / "events.jsonl").exists()
    finally:
        runner.stop()
        server.should_exit = True
        _, pending = await asyncio.wait(tasks, timeout=10)
        for t in pending:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
