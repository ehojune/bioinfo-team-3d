"""Project updates on GitHub, end to end with mock agents and a fake GitHub API."""

import asyncio
import base64
import json
import shutil
import time
from pathlib import Path

import httpx
import uvicorn

from labhq.gateway.server import RequestIn, create_app
from labhq.integrations.github import codex_comment, sanitize
from labhq.runner.daemon import Runner
from labhq.settings import DataZone, PolicySettings, ProjectSettings, Settings
from labhq.util import free_port

REPO = Path(__file__).resolve().parents[1]


def fake_github(calls: list):
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        path = req.url.path
        if req.method == "POST" and path.endswith("/issues"):
            return httpx.Response(201, json={"number": 7, "html_url": "https://github.com/o/p/issues/7"})
        if req.method == "POST" and path.endswith("/comments"):
            return httpx.Response(201, json={"html_url": f"https://github.com/o/p/issues/7#c{len(calls)}"})
        if req.method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if req.method == "PUT" and "/contents/" in path:
            return httpx.Response(201, json={"content": {"html_url": f"https://github.com/o/p/blob/main/{path}"}})
        if req.method == "PATCH":
            return httpx.Response(200, json={"state": "closed"})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


async def _until(pred, timeout=30.0):
    t0 = time.time()
    while not pred():
        assert time.time() - t0 < timeout, "timed out"
        await asyncio.sleep(0.05)


async def test_request_updates_land_in_project_repo(tmp_path):
    shutil.copytree(REPO / "agents", tmp_path / "agents")
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    gport = free_port()
    s.gateway.port, s.gateway.url = gport, f"ws://127.0.0.1:{gport}"
    s.runner.broker_port, s.runner.force_engine, s.runner.job_poll_s = free_port(), "mock", 1
    s.runner.workspace_root, s.runner.talent_dir = str(tmp_path / "runs"), str(tmp_path / "talent")
    s.runner.agents_dir = str(tmp_path / "agents")
    s.hpc.scheduler = "mock"
    s.projects = [ProjectSettings(id="demo", repo="o/p", local_dir=str(tmp_path / "clone")),
                  ProjectSettings(id="open", repo="o/public", visibility="public")]
    calls: list = []
    app = create_app(s, github_transport=fake_github(calls))
    hub = app.state.hub
    publish = hub.publish

    async def tap(ev, **kwargs):
        await publish(ev, **kwargs)
        if ev.get("type") == "approval.requested":
            asyncio.get_running_loop().create_task(hub.resolve_approval(ev["data"]["id"], True, "ok"))

    hub.publish = tap
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=gport, log_level="warning"))
    tasks = [asyncio.create_task(server.serve())]
    await _until(lambda: server.started, 10)
    runner = Runner(s)
    tasks.append(asyncio.create_task(runner.run_forever()))
    try:
        await _until(lambda: "cso" in hub.agents, 15)
        rid = hub.create_request(RequestIn(text="CD276 분석 [revise]", project_id="demo"))
        assert str(tmp_path / "clone") in hub.requests[rid]["project_dirs"]
        await _until(lambda: hub.requests[rid]["status"] != "running", 60)
        await asyncio.sleep(0.2)
        await hub.reporter.drain()
        kinds = [(m, p.rsplit("/", 1)[-1] if "/contents/" not in p else "contents") for m, p, _ in calls]
        assert kinds[0] == ("POST", "issues")
        comments = [b["body"] for m, p, b in calls if p.endswith("/comments")]
        assert comments[0].startswith("📋 **CSO 계획**") and "bioinfo-agent" in comments[0]
        assert sum("과학 리뷰" in c for c in comments) == 2 and comments[-1].startswith("🏁 완료")
        put = next(b for m, p, b in calls if m == "PUT")
        assert "CD276" in base64.b64decode(put["content"]).decode() and put["branch"] == "main"
        assert calls[-1][0] == "PATCH"

        before = len(calls)
        rid2 = hub.create_request(RequestIn(text="공개 저장소 테스트", project_id="open"))
        await _until(lambda: hub.requests[rid2]["status"] != "running", 60)
        await hub.reporter.drain()
        assert len(calls) == before  # public repo stays silent without allow_public_reports
    finally:
        runner.stop()
        server.should_exit = True
        await asyncio.sleep(0.2)
        for t in tasks:
            t.cancel()


def test_publish_guard_and_codex_mention():
    pol = PolicySettings(data_zones=[DataZone(path="/data/cohort", level="restricted")])
    out = sanitize("see /data/cohort/chr1.vcf.gz token ghp_" + "a" * 36, pol, ["change-me-client"])
    assert "/data/cohort" not in out and "ghp_" not in out and "<restricted-zone>" in out
    assert codex_comment("review") == "@codex review"
    assert codex_comment("@codex 이 부분 다시 봐줘") == "@codex 이 부분 다시 봐줘"
