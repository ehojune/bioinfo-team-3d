import asyncio
import shutil
import time
from pathlib import Path

import uvicorn

from labhq.gateway.server import RequestIn, create_app
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.util import free_port

REPO = Path(__file__).resolve().parents[1]


async def _until(pred, timeout=30.0):
    t0 = time.time()
    while not pred():
        assert time.time() - t0 < timeout, "timed out"
        await asyncio.sleep(0.05)


async def test_full_lab_flow_with_mock_agents(tmp_path):
    shutil.copytree(REPO / "agents", tmp_path / "agents")
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    gport = free_port()
    s.gateway.port, s.gateway.url = gport, f"ws://127.0.0.1:{gport}"
    s.runner.broker_port, s.runner.force_engine, s.runner.job_poll_s = free_port(), "mock", 1
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
            async def approve():
                await asyncio.sleep(0.05)
                await hub.resolve_approval(ev["data"]["id"], True, "ok")
            asyncio.get_running_loop().create_task(approve())

    hub.publish = tap
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=gport, log_level="warning"))
    tasks = [asyncio.create_task(server.serve())]
    await _until(lambda: server.started, 10)
    runner = Runner(s)
    tasks.append(asyncio.create_task(runner.run_forever()))
    try:
        await _until(lambda: "cso" in hub.agents and "recruiter" in hub.agents, 15)
        assert hub.agents["analyst"]["hpc_tools"] is True
        assert hub.agents["lit_scout"]["hpc_tools"] is False
        assert hub.agents["analyst"]["engine"] == "mock"

        rid = hub.create_request(RequestIn(text="CD276 세포유형 분석 [hpc] [needs-approval] [revise] [revision-fail] [artifact] [recruit] [question] [block]"))
        await _until(lambda: hub.requests[rid]["status"] != "running", 60)
        req = hub.requests[rid]
        assert req["status"] == "done", req.get("error")
        steps = req["plan"]["steps"]
        assert not {s["agent_id"] for s in steps} & {"cso", "chief_of_staff", "sci_reviewer"}
        assert [st["agent_id"] for st in steps] == ["biologist", "data_steward", "bioinfo-agent", "analyst", "qc_reviewer"]
        analyst_step = next(st["id"] for st in steps if st["agent_id"] == "analyst")

        types = [e["type"] for e in seen if e.get("request_id") == rid]
        assert types.count("request.review") == 2  # revise → accept
        assert "recruit.suggested" in types and "job.submitted" in types and "jobs.finished" in types
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
        assert "깨어나서" in req["results"][analyst_step]["text"]  # analyst hibernated on HPC and was resumed
        assert "revision failed" in req["results"][analyst_step]["revision_failed"]
        steward = next(st["id"] for st in steps if st["agent_id"] == "data_steward")
        assert req["results"][steward]["outputs"] == ["outputs/artifact.txt"]
        runs = req["results"][steward]["provenance"]["runs"]  # sent by the runner, not read off its disk
        assert runs and all("started_at" in r and "ended_at" in r for r in runs.values())
        assert hub.runner_capabilities and all("engine_cli_versions" in c for c in hub.runner_capabilities.values())
        assert "outputs/artifact.txt" in req["report"]

        # Resume을 지원하지 않는 엔진도 blocking 답을 같은 workdir의 새 세션에 전달한다.
        supports_resume = hub.supports_resume
        hub.supports_resume = lambda agent_id: False if agent_id == "biologist" else supports_resume(agent_id)
        rid_no_resume = hub.create_request(RequestIn(text="Resume 없는 blocking 확인 [block]"))
        await _until(lambda: hub.requests[rid_no_resume]["status"] != "running", 30)
        no_resume = hub.requests[rid_no_resume]
        assert no_resume["status"] == "done", no_resume.get("error")
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
        await _until(lambda: hub.requests[rid_partial]["status"] != "running", 30)
        partial = hub.requests[rid_partial]
        assert partial["status"] == "failed"
        biologist = next(st["id"] for st in partial["plan"]["steps"] if st["agent_id"] == "biologist")
        assert partial["results"][biologist]["partial_results"]
        assert "outputs/PARTIAL_STATUS.md" in partial["results"][biologist]["outputs"]
        assert (Path(partial["results"][biologist]["workdir"]) / "outputs" / "PARTIAL_STATUS.md").exists()

        # 파견직 채용 → 명단 등록 → 직접 업무 → 스킬이 작업공간에 설치됨
        await hub.send_runner(s.runner.id, {"type": "recruit.start", "repo": "https://github.com/scverse/scanpy",
                                            "focus": "Preprocessing and clustering", "ttl_days": 7})
        await _until(lambda: "c_scanpy" in hub.agents, 20)
        assert hub.agents["c_scanpy"]["employment"] == "contract"
        rid2 = hub.create_request(RequestIn(text="클러스터링 자문", mode="direct", agent_id="c_scanpy"))
        await _until(lambda: hub.requests[rid2]["status"] != "running", 20)
        assert hub.requests[rid2]["status"] == "done"
        wd = Path(hub.requests[rid2]["results"]["direct"]["workdir"])
        assert (wd / ".claude" / "skills" / "scanpy-paper" / "SKILL.md").exists()
        assert (wd / "manifest.json").exists() and (wd / "events.jsonl").exists()
    finally:
        runner.stop()
        server.should_exit = True
        await asyncio.sleep(0.2)
        for t in tasks:
            t.cancel()
