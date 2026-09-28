"""Project updates on GitHub, end to end with mock agents and a fake GitHub API."""

import asyncio
import base64
import json
import shutil
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from labhq.gateway.server import Hub, RequestIn, create_app
from labhq.integrations.github import GitHubClient, codex_comment, sanitize
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
        if req.method == "GET" and path.endswith("/issues"):
            return httpx.Response(200, json=[])
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
        assert kinds[:2] == [("GET", "issues"), ("POST", "issues")]
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


def test_restricted_zones_match_separators_case_and_directory_boundary():
    policy = PolicySettings(data_zones=[
        DataZone(path="/data/cohort", level="restricted"),
        DataZone(path="/data/cohort/deeper", level="restricted"),
        DataZone(path="D:" + "/restricted/dua_cohort/", level="restricted"),
    ])
    for path in ("/DATA/cohort/sub/file.cram", r"\data\COHORT\sub\file.cram",
                 r"D:\RESTRICTED\dua_cohort\sample.cram", "d:/restricted/DUA_COHORT/",
                 "/data/cohort/deeper/file.cram"):
        assert sanitize(path, policy) == "<restricted-zone>"
    assert sanitize("/data/cohort2/file.cram", policy) == "/data/cohort2/file.cram"


def test_root_restricted_zone_fails_closed():
    policy = PolicySettings(data_zones=[DataZone(path="/", level="restricted")])
    out = sanitize("raw at /private/subject.cram (see https://github.com/o/p/issues/1)", policy)
    assert "/private" not in out and "subject.cram" not in out
    assert "https://github.com/o/p/issues/1" in out


@pytest.mark.asyncio
async def test_api_endpoint_is_not_rewritten_by_the_path_guard():
    policy = PolicySettings(data_zones=[DataZone(path="/data", level="restricted")])
    seen: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"number": 1})

    client = GitHubClient("test-token", "https://example.test", httpx.MockTransport(api),
                          lambda value: sanitize(value, policy))
    await client.create_issue("org/data", "t /data/x.cram", "b", [])
    assert seen[0].url.path == "/repos/org/data/issues"
    assert "/data/x.cram" not in json.loads(seen[0].content)["title"]


@pytest.mark.asyncio
async def test_full_reporter_flow_guards_every_outbound_string(tmp_path):
    zone = "D:" + "/restricted/dua_cohort"
    secret = "ghp_" + "t7ehzlZ1Jxnv4CLHUkwKNzwd7mEd8OGtjWN7"
    sent: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        path = request.url.path
        if request.method == "GET" and path.endswith("/issues"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and path.endswith("/issues"):
            return httpx.Response(201, json={"number": 7, "html_url": "https://example.test/7"})
        if request.method == "POST" and path.endswith("/comments"):
            return httpx.Response(201, json={"html_url": "https://example.test/comment"})
        if request.method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={})
        if request.method == "PUT" and "/contents/" in path:
            return httpx.Response(201, json={"content": {"html_url": "https://example.test/report"}})
        if request.method == "PATCH":
            return httpx.Response(200, json={"state": "closed"})
        return httpx.Response(404, json={})

    s = Settings(projects=[ProjectSettings(id="p", repo="o/p", labels=[zone + "/label", secret])])
    s.policy.data_zones = [DataZone(path=zone, level="restricted")]
    s.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(s, github_transport=httpx.MockTransport(api))
    rid = "r"
    hub.requests[rid] = {"text": f"{zone}/sample.cram {secret}", "project_id": "p", "status": "done"}
    events = [
        {"type": "request.created", "data": {}},
        {"type": "request.plan", "data": {"steps": [{"id": "s", "agent_id": "analyst",
            "instruction": zone + "/plan.cram " + secret}]}},
        {"type": "request.review", "data": {"revision": 0, "status": "review_unparsed",
            "reason": zone.upper().replace("/", "\\") + "\\review.cram " + secret}},
        {"type": "request.completed", "data": {"ok": True,
            "report": zone.upper().replace("/", "\\") + "\\report.cram " + secret}},
    ]
    for seq, ev in enumerate(events, 1):
        await hub.reporter.handle({**ev, "request_id": rid, "seq": seq})

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from strings(key)
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    payloads = [json.loads(req.content) for req in sent if req.content]
    assert [req.method for req in sent if req.method in ("POST", "PUT", "PATCH")].count("PUT") == 1
    report = next(p for p in payloads if "content" in p)
    outbound = [req.url.path for req in sent] + list(strings(payloads))
    outbound.append(base64.b64decode(report["content"]).decode("utf-8"))
    for value in outbound:
        assert secret not in value
        assert zone.casefold() not in value.replace("\\", "/").casefold()
    assert "<restricted-zone>" in str(payloads)
    assert "<restricted-zone>" in outbound[-1]


@pytest.mark.asyncio
async def test_file_upload_guards_content_before_base64_without_truncating_payload():
    zone = "D:" + "/restricted/dua_cohort"
    policy = PolicySettings(data_zones=[DataZone(path=zone, level="restricted")])
    uploads = []

    def api(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={})
        uploads.append(json.loads(request.content))
        return httpx.Response(201, json={"content": {"html_url": "https://example.test/report"}})

    client = GitHubClient("test-token", "https://example.test", httpx.MockTransport(api),
                          lambda value: sanitize(value, policy))
    await client.put_file("o/p", "report.md", "한" * 20_000 + zone + "/file.cram",
                          zone + "/commit", "main")
    body = uploads[0]
    decoded = base64.b64decode(body["content"]).decode("utf-8")
    assert len(body["content"]) > 60_000
    assert decoded.endswith("<restricted-zone>")
    assert body["message"] == "<restricted-zone>"


@pytest.mark.asyncio
async def test_title_and_report_heading_clean_before_shortening(tmp_path):
    zone = "D:" + "/restricted/dua_cohort"
    calls = []
    s = Settings(projects=[ProjectSettings(id="p", repo="o/p")])
    s.policy.data_zones = [DataZone(path=zone, level="restricted")]
    s.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(s, github_transport=fake_github(calls))
    hub.requests["r"] = {"text": "x" * 62 + zone + "/sample.cram", "project_id": "p"}
    await hub.reporter.handle({"type": "request.created", "request_id": "r", "data": {}})
    title = next(body["title"] for method, _, body in calls if method == "POST")
    assert "D:/res" not in title
    heading = hub.reporter._report_md("r", {"text": "x" * 110 + zone + "/sample.cram"}, "")
    assert "D:/res" not in heading


@pytest.mark.asyncio
async def test_issue_creation_replay_finds_remote_issue_before_reposting(tmp_path):
    remote = {"issues": [], "posts": 0}

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues") and request.method == "GET":
            return httpx.Response(200, json=remote["issues"])
        if request.url.path.endswith("/issues") and request.method == "POST":
            remote["posts"] += 1
            issue = {"number": 7, "body": json.loads(request.content)["body"],
                     "html_url": "https://example.test/7"}
            remote["issues"].append(issue)
            return httpx.Response(201, json=issue)
        return httpx.Response(404, json={"message": "Not Found"})

    s = Settings(projects=[ProjectSettings(id="p", repo="o/p")])
    s.gateway.state_dir = str(tmp_path / "state")
    transport = httpx.MockTransport(api)
    first = Hub(s, github_transport=transport)
    first.requests["r"] = {"id": "r", "text": "study", "project_id": "p", "status": "done"}
    first.save_request("r")
    event = {"type": "request.created", "request_id": "r", "data": {}}
    original_put = first.store.put

    def crash_before_issue_checkpoint(kind, key, body):
        if kind == "github_issue":
            raise RuntimeError("simulated restart before checkpoint")
        original_put(kind, key, body)

    first.store.put = crash_before_issue_checkpoint
    with pytest.raises(RuntimeError, match="simulated restart"):
        await first.reporter.handle(event)
    assert "<!-- labhq request r -->" in remote["issues"][0]["body"]
    first.store.close()

    restored = Hub(s, github_transport=transport)
    await restored.reporter.handle(event)
    assert remote["posts"] == 1
    assert restored.store.get("github_issue", "r") == {"number": 7}


def reporter_comments_transport(remote):
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/7/comments") and request.method == "GET":
            return httpx.Response(200, json=remote)
        if request.url.path.endswith("/issues/7/comments") and request.method == "POST":
            comment = {"body": json.loads(request.content)["body"],
                       "html_url": f"https://example.test/comment/{len(remote) + 1}"}
            remote.append(comment)
            return httpx.Response(201, json=comment)
        return httpx.Response(404, json={"message": "Not Found"})
    return httpx.MockTransport(api)


@pytest.mark.asyncio
async def test_saved_plan_and_review_replay_in_order_after_restart(tmp_path):
    remote = []
    s = Settings(projects=[ProjectSettings(id="p", repo="o/p")])
    s.gateway.state_dir = str(tmp_path / "state")
    transport = reporter_comments_transport(remote)
    first = Hub(s, github_transport=transport)
    first.requests["r"] = {"id": "r", "text": "study", "project_id": "p", "status": "done"}
    first.save_request("r")
    first.store.put("github_issue", "r", {"number": 7})
    first.reporter.issues["r"] = 7
    first.reporter.submit = lambda event: None  # process exits after durable append, before enqueue
    await first.publish({"type": "request.plan", "request_id": "r", "data": {"steps": []}})
    await first.publish({"type": "request.review", "request_id": "r",
                         "data": {"revision": 0, "status": "review_unparsed", "reason": "invalid verdict"}})
    assert [event["type"] for event in first.store.all("reporter_delivery").values()] == [
        "request.plan", "request.review"]
    first.store.close()

    restored = Hub(s, github_transport=transport)
    restored.recover_terminal_deliveries()
    await restored.reporter.drain()
    assert len(remote) == 2
    assert remote[0]["body"].startswith("📋 **CSO 계획**")
    assert remote[1]["body"].startswith("🐢 **과학 리뷰")
    assert restored.store.all("reporter_delivery") == {}


@pytest.mark.asyncio
async def test_plan_delivery_waits_for_missing_request_issue(tmp_path):
    remote = []
    s = Settings(projects=[ProjectSettings(id="p", repo="o/p")])
    s.gateway.state_dir = str(tmp_path / "state")
    transport = reporter_comments_transport(remote)
    first = Hub(s, github_transport=transport)
    first.requests["r"] = {"id": "r", "text": "study", "project_id": "p", "status": "done"}
    first.save_request("r")
    await first.publish({"type": "request.plan", "request_id": "r", "data": {"steps": []}})
    await first.reporter.drain()
    assert len(first.store.all("reporter_delivery")) == 1 and remote == []
    first.store.put("github_issue", "r", {"number": 7})
    first.store.close()

    restored = Hub(s, github_transport=transport)
    restored.recover_terminal_deliveries()
    await restored.reporter.drain()
    assert len(remote) == 1 and remote[0]["body"].startswith("📋 **CSO 계획**")
    assert restored.store.all("reporter_delivery") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,data", [
    ("request.plan", {"steps": []}),
    ("request.review", {"revision": 0, "status": "review_unparsed", "reason": "invalid verdict"}),
    ("recruit.done", {"agent": {"id": "new", "name": "new"}, "passed_probation": True}),
])
async def test_reporter_comment_replay_finds_posted_marker(tmp_path, kind, data):
    remote = []
    s = Settings(projects=[ProjectSettings(id="p", repo="o/p")])
    s.gateway.state_dir = str(tmp_path / "state")
    transport = reporter_comments_transport(remote)
    first = Hub(s, github_transport=transport)
    first.requests["r"] = {"id": "r", "text": "study", "project_id": "p", "status": "done"}
    first.save_request("r")
    first.store.put("github_issue", "r", {"number": 7})
    first.reporter.issues["r"] = 7
    first.reporter.submit = lambda event: None
    await first.publish({"type": kind, "request_id": "r", "data": data})
    event = next(iter(first.store.all("reporter_delivery").values()))
    original_put = first.store.put

    def crash_before_comment_checkpoint(state_kind, key, body):
        if state_kind == "github_action" and key.endswith(":comment") and body.get("done"):
            raise RuntimeError("simulated restart before checkpoint")
        original_put(state_kind, key, body)

    first.store.put = crash_before_comment_checkpoint
    with pytest.raises(RuntimeError, match="simulated restart"):
        await first.reporter.handle(event)
    assert len(remote) == 1
    first.store.close()

    restored = Hub(s, github_transport=transport)
    restored.recover_terminal_deliveries()
    await restored.reporter.drain()
    assert len(remote) == 1
    assert restored.store.all("reporter_delivery") == {}
    assert restored.store.get("github_action", f"r:{event['seq']}:comment")["done"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_action", ["report", "comment", "close"])
async def test_terminal_report_replay_skips_completed_external_actions(tmp_path, crash_action):
    remote = {"content": None, "comments": [], "state": "open", "puts": 0, "posts": 0, "patches": 0}

    def api(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if "/contents/" in path and method == "GET":
            if remote["content"] is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"encoding": "base64", "sha": "file-sha",
                                              "content": base64.b64encode(remote["content"].encode()).decode(),
                                              "html_url": "https://example.test/report"})
        if "/contents/" in path and method == "PUT":
            remote["puts"] += 1
            remote["content"] = base64.b64decode(json.loads(request.content)["content"]).decode()
            return httpx.Response(201, json={"content": {"html_url": "https://example.test/report"}})
        if path.endswith("/comments") and method == "GET":
            return httpx.Response(200, json=remote["comments"])
        if path.endswith("/comments") and method == "POST":
            remote["posts"] += 1
            comment = {"body": json.loads(request.content)["body"],
                       "html_url": "https://example.test/comment"}
            remote["comments"].append(comment)
            return httpx.Response(201, json=comment)
        if path.endswith("/issues/7") and method == "GET":
            return httpx.Response(200, json={"state": remote["state"]})
        if path.endswith("/issues/7") and method == "PATCH":
            remote["patches"] += 1
            remote["state"] = "closed"
            return httpx.Response(200, json={"state": "closed"})
        return httpx.Response(404, json={"message": "Not Found"})

    s = Settings(projects=[ProjectSettings(id="p", repo="o/p", commit_reports=True)])
    s.gateway.state_dir = str(tmp_path / "state")
    transport = httpx.MockTransport(api)
    first = Hub(s, github_transport=transport)
    first.requests["r"] = {"id": "r", "text": "study", "project_id": "p", "status": "done",
                           "finished_at": 1_700_000_000, "cost_usd": 1.0}
    first.save_request("r")
    first.reporter.issues["r"] = 7
    first.store.put("github_issue", "r", {"number": 7})
    event = {"type": "request.completed", "request_id": "r", "seq": 9,
             "data": {"ok": True, "report": "final report", "cost_usd": 1.0}}
    original_put = first.store.put

    def crash_after_external_call(kind, key, body):
        if kind == "github_action" and key.endswith(f":{crash_action}") and body.get("done"):
            raise RuntimeError("simulated restart before checkpoint")
        original_put(kind, key, body)

    first.store.put = crash_after_external_call
    with pytest.raises(RuntimeError, match="simulated restart"):
        await first.reporter.handle(event)
    first.store.close()

    restored = Hub(s, github_transport=transport)
    await restored.reporter.handle(event)
    assert (remote["puts"], remote["posts"], remote["patches"]) == (1, 1, 1)
    assert "<!-- labhq terminal r 9 -->" in remote["comments"][0]["body"]
    assert all(restored.store.get("github_action", f"r:9:{action}")["done"]
               for action in ("report", "comment", "close"))


async def test_unparsed_review_posts_failure_comment(tmp_path):
    calls = []
    s = Settings(projects=[ProjectSettings(id="demo", repo="o/p")])
    s.gateway.state_dir = str(tmp_path)
    hub = Hub(s, github_transport=fake_github(calls))
    hub.requests["r"] = {"text": "example", "project_id": "demo"}
    await hub.reporter.handle({"type": "request.created", "request_id": "r", "data": {}})
    await hub.reporter.handle({"type": "request.review", "request_id": "r",
                               "data": {"revision": 0, "status": "review_unparsed",
                                        "reason": "missing or invalid verdict"}})
    comments = [body["body"] for method, path, body in calls if path.endswith("/comments")]
    assert len(comments) == 1
    assert "리뷰 판정 실패" in comments[0] and "missing or invalid verdict" in comments[0]
    assert "None/5" not in comments[0] and ": None" not in comments[0]


def test_staff_prompt_asks_for_one_codex_review_per_push():
    """Each @codex comment starts its own Codex session; staff must not mention it in inline replies."""
    from labhq.adapters.base import ROLE_FOOTER

    assert "Codex review findings without @codex" in ROLE_FOOTER
    assert "`@codex review` once" in ROLE_FOOTER
    assert "always mention @codex" not in ROLE_FOOTER
