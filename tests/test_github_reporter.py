"""Project updates on GitHub, end to end with mock agents and a fake GitHub API."""

import asyncio
import base64
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

from labhq.gateway.server import Hub, RequestIn, create_app
from labhq.integrations.github import GitHubClient, GitHubHTTPError, codex_comment, sanitize
from labhq.runner.daemon import Runner
from labhq.settings import DataZone, PolicySettings, ProjectSettings, Settings
from labhq.util import free_port
from tests.support.serve_task import serve_task, wait_started
from tests.support.time_proxy import RuntimeProxy

REPO = Path(__file__).resolve().parents[1]


def test_github_error_preserves_rate_limit_reset_as_retry_delay(monkeypatch):
    from labhq.integrations import github

    monkeypatch.setattr(github, "time", RuntimeProxy(github.time, time=lambda: 1_000))
    response = httpx.Response(403, headers={"X-RateLimit-Remaining": "0",
                                            "X-RateLimit-Reset": "1060"}, text="limited")
    error = GitHubHTTPError("GET", "/repos/o/p", response)
    assert error.rate_limited
    assert error.retry_delay_s() == 60


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
    tasks = [serve_task(server.serve())]
    await wait_started(lambda: server.started, tasks[0], 10, "gateway")
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


def test_sanitize_stays_linear_on_long_separator_runs():
    policy = PolicySettings(data_zones=[DataZone(path="/restricted", level="restricted")])
    text = "/" * 65_536 + "\\" * 65_536 + ":" * 600_000
    # Budget synchronous scan CPU, excluding other xdist workers' scheduling.
    # A fixed-input cost guard does not prove asymptotic linearity.
    started = time.process_time()
    assert sanitize(text, policy, limit=None) == text
    assert time.process_time() - started < 2, "publish path scanning must stay linear in the text length"


@pytest.mark.parametrize("cpu_elapsed,wall_elapsed,alter_output,reject", [
    (0.125, 900, False, False),
    (2, 0.125, False, True),
    (2.125, 0.125, False, True),
    (0.125, 0.125, True, True),
    (0.125, 0.125, False, False),
])
def test_sanitize_cost_guard_keeps_cpu_budget_and_exact_untruncated_output(
        monkeypatch, cpu_elapsed, wall_elapsed, alter_output, reject):
    cpu, wall = iter((0, cpu_elapsed)), iter((0, wall_elapsed))
    # Replace this test module's binding, never pytest/product/global clocks.
    monkeypatch.setitem(globals(), "time", SimpleNamespace(
        process_time=lambda: next(cpu), perf_counter=lambda: next(wall)))
    calls = []

    def scan(text, policy, *, limit):
        calls.append((text, [(zone.path, zone.level) for zone in policy.data_zones], limit))
        return text + "changed" if alter_output else text

    monkeypatch.setitem(globals(), "sanitize", scan)
    if reject:
        with pytest.raises(AssertionError):
            test_sanitize_stays_linear_on_long_separator_runs()
    else:
        test_sanitize_stays_linear_on_long_separator_runs()
    assert calls == [("/" * 65_536 + "\\" * 65_536 + ":" * 600_000,
                      [("/restricted", "restricted")], None)]


@pytest.mark.parametrize("prefix", ["", "D:"])
def test_literal_zone_separator_runs_have_bounded_python_work(prefix):
    from labhq.integrations.github import _literal_zone_end

    class CountedText(str):
        reads = 0

        def __getitem__(self, key):
            self.reads += 1
            return super().__getitem__(key)

    # Both leading and between-component runs matter; this measures Python
    # indexing work rather than host scheduling, without changing the output.
    text = CountedText(prefix + "/\\" * 4_096 + "data" + "\\/" * 4_096 + "cohort/x")
    assert _literal_zone_end(text, 0, prefix + "/data/cohort") == len(text) - 2
    assert text.reads < 32


def test_literal_zone_run_search_matches_original_character_walk():
    from itertools import product
    from labhq.integrations.github import _literal_zone_end

    def original(text, start, zone):
        pos = start
        parts = tuple(part for part in zone.split("/") if part)
        if zone.startswith("/"):
            if pos >= len(text) or text[pos] not in "/\\":
                return None
            while pos < len(text) and text[pos] in "/\\":
                pos += 1
        for index, part in enumerate(parts):
            end = pos + len(part)
            if text[pos:end].casefold() != part.casefold():
                return None
            pos = end
            if index + 1 < len(parts):
                if pos >= len(text) or text[pos] not in "/\\":
                    return None
                while pos < len(text) and text[pos] in "/\\":
                    pos += 1
        if pos < len(text) and (text[pos].isalnum() or text[pos] in "_.-"):
            return None
        return pos

    separators = ("", "/", "\\", "/\\/", "\\\\", "/" * 128)
    tails = ("", "/x", "\\x", "2", "_x", ".x", "-x", ":ads", "한", " ", ")")
    for leading, middle, tail, prefix in product(separators, separators, tails, ("", "D:")):
        zone = prefix + "/data/cohort"
        text = prefix + leading + "DaTa" + middle + "COHORT" + tail
        for head in ("", "문장 "):
            assert _literal_zone_end(head + text, len(head), zone) == original(
                head + text, len(head), zone)


@pytest.mark.parametrize("name,marker,operation", [
    ("URL_USERINFO", "@", "sub"),
    ("WEBHOOK_PATH", ".", "sub"),
    ("QUERY_PARAM", "=", "finditer"),
])
def test_url_redaction_does_not_scan_without_its_required_literal(monkeypatch, name, marker, operation):
    from labhq.integrations import github

    pattern = getattr(github, name)
    calls = []

    def scan(*args, **kwargs):
        calls.append(True)
        return getattr(pattern, operation)(*args, **kwargs)

    monkeypatch.setattr(github, name, SimpleNamespace(**{operation: scan}))
    text = "/" * 4_096 + "\\" * 4_096 + ":" * 32_768
    assert github.redact_url_credentials(text) == text
    assert not calls
    # A present marker is only eligibility to scan, never a replacement for
    # the existing pattern or the URL/parameter-name classification.
    assert github.redact_url_credentials(text + marker) == text + marker
    assert calls


def test_url_scan_prefilters_preserve_unconditional_redaction():
    from itertools import product
    from labhq.integrations import github

    def original(text):
        text = github.URL_USERINFO.sub(lambda m: m.group(1) + "<redacted-secret>@" if (
            m.group(1).partition(":")[0].casefold() in github.USERINFO_SCHEMES
            or ":" in m.group(2)) else m.group(0), text)
        text = github.WEBHOOK_PATH.sub(
            lambda m: m.group(1) + (m.group(2) or "") + "<redacted-secret>", text)
        out, last = [], 0
        for match in github.QUERY_PARAM.finditer(text):
            if match.start() < last or not github.QUERY_SECRET_NAME.fullmatch(
                    github._decoded(match.group(1))):
                continue
            value = github.QUERY_VALUE.match(text, match.end())
            if value:
                out += [text[last:match.end()], "<redacted-secret>"]
                last = value.end()
        return "".join(out) + text[last:]

    samples = (
        "https://user:synthetic@host.invalid/a", "https://synthetic@host.invalid/a",
        r"https:\/\/CORP\alice:synthetic@host.invalid", "ssh://git@host.invalid/repo",
        "https://hooks.slack.com/services/synthetic/value", "hooks.slack.com/workflows/synthetic",
        "https%3A%2F%2Fdiscord.com%2Fapi%2Fwebhooks%2Fsynthetic%2Fvalue",
        "https%253A%252F%252Fapi.telegram.org%252FbotSynthetic%252FgetMe",
        "https://outlook.office.com/webhook/synthetic", "team.webhook.office.com/webhookb2/synthetic",
        "https://host.invalid/?X%2DAmz%2DSignature=synthetic&keep=yes",
        "?tok%2565n=synthetic;password=second#code=third", "?token=&public=visible",
        "?session=synthetic&next=https%3A%2F%2Fhooks.slack.com%2Fservices%2Fsynthetic",
        "?normal=visible#title=keep", "@", ".", "=", "./@=", "문장 한 줄",
    )
    for head, sample, tail in product(("", "보고 ", "{\"value\":\""), samples,
                                      ("", "\"}", "\nkeep")):
        text = head + sample + tail
        expected = original(text)
        assert github.redact_url_credentials(text) == expected
        assert sanitize(text, PolicySettings(), limit=None) == expected
    assert github.redact_url_credentials(samples[0]) == "https://<redacted-secret>@host.invalid/a"
    assert github.redact_url_credentials(samples[4]) == "https://hooks.slack.com/services/<redacted-secret>"
    assert github.redact_url_credentials(samples[10]) == (
        "https://host.invalid/?X%2DAmz%2DSignature=<redacted-secret>&keep=yes")


def test_long_runs_do_not_hide_restricted_paths_or_credentials():
    policy = PolicySettings(data_zones=[DataZone(path="/data/cohort", level="restricted")])
    separators = "/\\" * 4_096
    text = ("keep\n" + separators + "DATA" + separators + "cohort/sample.cram\n"
            "https://user:synthetic@host.invalid/?token=synthetic\n"
            "https%3A%2F%2Fhooks.slack.com%2Fservices%2Fsynthetic\nkeep")
    assert sanitize(text, policy, limit=None) == (
        "keep\n<restricted-zone>\n"
        "https://<redacted-secret>@host.invalid/?token=<redacted-secret>\n"
        "https%3A%2F%2Fhooks.slack.com%2Fservices%2F<redacted-secret>\nkeep")


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


@pytest.mark.parametrize("root", ["/", "E:/", "e:\\"])
def test_filesystem_root_zone_publishes_nothing(root):
    policy = PolicySettings(data_zones=[DataZone(path=root, level="restricted")])
    for text in ("raw at /private/subject 123.cram", "경로/private/subject.cram", "uri file:///private/x.cram",
                 "https://example.org/guide?next=/docs/start", "passed: 12"):
        assert sanitize(text, policy) == "<restricted-zone>"
    assert sanitize("", policy) == ""


def test_candidate_paths_are_normalized_like_the_access_policy():
    policy = PolicySettings(data_zones=[DataZone(path="/data/cohort", level="restricted")])
    for line in ("see /data/./cohort/sample.cram", "see /data/tmp/../cohort/sample.cram",
                 "uri file:///data/./cohort/sample.cram", 'cmd --in="/data/cohort/a b.cram"'):
        out = sanitize(line + "\nkeep", policy)
        assert out == "<restricted-zone>\nkeep", out


def test_network_urls_that_share_a_zone_path_stay_intact():
    policy = PolicySettings(data_zones=[DataZone(path="/data/cohort", level="restricted")])
    text = "guide https://example.org/data/cohort/guide and (https://x.org/docs/data/cohort)"
    assert sanitize(text, policy) == text
    assert sanitize("https://x.org/a then /data/cohort/s.cram", policy) == "<restricted-zone>"


def test_zone_path_fails_closed_to_the_end_of_the_line():
    policy = PolicySettings(data_zones=[DataZone(path="/data/cohort", level="restricted")])
    for line in ("| s1 | /data/cohort/subject 123 (v2), part/sample.cram | pass |",
                 "/data/cohort/O'Brien/sample.cram", "/data/cohort/a`b`/s.cram",
                 '{"path": "/data/cohort/a b.cram", "n": 1}'):
        out = sanitize(line + "\nnext line", policy)
        assert out.endswith("<restricted-zone>\nnext line"), out
        assert not any(t in out for t in ("123", "Brien", "sample", "s.cram", "a b"))
    assert sanitize("see /data/cohort2/y", policy) == "see /data/cohort2/y"
    assert sanitize("경로/data/cohort/s.cram", policy) == "경로<restricted-zone>"


def test_zones_are_normalized_like_the_access_policy():
    policy = PolicySettings(data_zones=[DataZone(path="/data/./cohort", level="restricted"),
                                        DataZone(path="/data/cohort/../secret", level="restricted")])
    assert sanitize("/data/cohort/sample.cram", policy) == "<restricted-zone>"
    assert sanitize("/data/secret/sample.cram", policy) == "<restricted-zone>"
    assert sanitize("passed: 12 · ID: 5", policy) == "passed: 12 · ID: 5"


@pytest.mark.parametrize("text", [
    "/" * 4_096, "\\" * 4_096, ":" * 4_096,
    "/" * 65_536 + "\\" * 65_536 + ":" * 600_000,
], ids=["slashes", "backslashes", "colons", "exact-ci-input"])
def test_separator_only_publish_lines_avoid_python_path_walk(monkeypatch, text):
    from labhq.integrations import github

    original, calls = github._scan_path_text, []

    def scan(line):
        calls.append(len(line))
        return original(line)

    monkeypatch.setattr(github, "_scan_path_text", scan)
    assert github._sanitize_path_line(text, ("/restricted", "D:/Private", "/민감")) == text
    assert calls == []  # Whole separator-only text needs no Python candidate walk.


@pytest.mark.parametrize("tail,private", [
    ("/restricted/sample.cram", True),
    (r"D:\Private\sample.cram", True),
    (r"\\server\private\sample.cram", True),
    ("/민감/결과.txt", True),
    ("file:///%72estricted/sample.cram", True),
    ("https://public.invalid/restricted/guide", False),
    ("/restricted2/public.txt", False),
    ("한 public text", False),
])
def test_separator_prefix_never_hides_a_late_path_or_public_tail(monkeypatch, tail, private):
    from labhq.integrations import github

    original, calls = github._scan_path_text, []

    def scan(line):
        calls.append(len(line))
        return original(line)

    monkeypatch.setattr(github, "_scan_path_text", scan)
    prefix = "/" * 65_536 + "\\" * 65_536 + ":" * 600_000
    text = prefix + tail
    policy = PolicySettings(data_zones=[DataZone(path=path, level="restricted") for path in
                                       ("/restricted", "D:/Private", "//server/private", "/민감")])
    assert sanitize(text, policy, limit=None) == ("<restricted-zone>" if private else text)
    assert calls == [len(text)]  # The entire untruncated line reaches the existing classifier.


@pytest.mark.parametrize("path", ["/:", "/::"])
def test_separator_only_zone_names_still_fail_closed(monkeypatch, path):
    from labhq.integrations import github

    original, calls = github._scan_path_text, []

    def scan(line):
        calls.append(len(line))
        return original(line)

    monkeypatch.setattr(github, "_scan_path_text", scan)
    text = "/\\" * 1_024 + ":" * 64
    policy = PolicySettings(data_zones=[DataZone(path=path, level="restricted")])
    assert sanitize(text, policy, limit=None) == "<restricted-zone>"
    assert calls == [len(text)]


def test_separator_eligibility_matches_unconditional_path_classifier():
    from itertools import product
    from labhq.integrations import github

    def original(line, zones):
        scan = github._scan_path_text(line)
        if github._scan_mentions_zone(line, zones, scan):
            return "<restricted-zone>"
        start = github._literal_zone_start(line, zones, scan)
        return line if start is None else line[:start] + "<restricted-zone>"

    zone_sets = ((), ("/restricted",), ("D:/Private",), ("//server/private",),
                 ("/민감",), ("/:", "/::"), ("/",), ("",))
    lines = ["".join(chars) for n in range(5) for chars in product("/\\:a한", repeat=n)]
    lines += ["/restricted/x", r"D:\PRIVATE\x", r"\\server\private\x", "/민감/x",
              "file:///%72estricted/x", "https://public.invalid/restricted/x",
              "경로/restricted/x", "'/restricted/a b'", "/tmp/../restricted/x"]
    for line, zones in product(lines, zone_sets):
        assert github._sanitize_path_line(line, zones) == original(line, zones), (line, zones)


def test_separator_only_output_still_redacts_extra_secrets_and_clips_last():
    policy = PolicySettings(data_zones=[DataZone(path="/restricted", level="restricted")])
    text = "/" * 32 + ":" * 32
    assert sanitize(text, policy, ["/" * 8], limit=None) == "<redacted-secret>" * 4 + ":" * 32
    assert sanitize(text, policy, [":" * 8], limit=32) == sanitize(
        "/" * 32 + "<redacted-secret>" * 4, PolicySettings(), limit=32)


@pytest.mark.parametrize("tail,clean", [
    ("ghp_" + "a" * 36, "<redacted-secret>"),
    ("Authorization: Bearer synthetic-long-credential", "Authorization: <redacted-secret>"),
    ("https://user:synthetic@host.invalid/?token=synthetic",
     "https://<redacted-secret>@host.invalid/?token=<redacted-secret>"),
    ("https%3A%2F%2Fhooks.slack.com%2Fservices%2Fsynthetic",
     "https%3A%2F%2Fhooks.slack.com%2Fservices%2F<redacted-secret>"),
])
def test_exact_separator_prefix_never_hides_a_late_credential(tail, clean):
    prefix = "/" * 65_536 + "\\" * 65_536 + ":" * 600_000
    policy = PolicySettings(data_zones=[DataZone(path="/restricted", level="restricted")])
    assert sanitize(prefix + " " + tail, policy, limit=None) == prefix + " " + clean


@pytest.mark.asyncio
async def test_branch_and_sha_are_control_values_not_published_text():
    policy = PolicySettings(data_zones=[DataZone(path="/data", level="restricted")])
    seen: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(404, json={})
        return httpx.Response(201, json={"content": {"html_url": "https://example.test/r"}})

    client = GitHubClient("test-token", "https://example.test", httpx.MockTransport(api),
                          lambda value: sanitize(value, policy))
    await client.put_file("o/p", "r.md", "raw /data/x.cram", "msg /data/y", "feature/data")
    assert seen[0].url.params["ref"] == "feature/data"
    body = json.loads(seen[1].content)
    assert body["branch"] == "feature/data"
    assert "/data/" not in base64.b64decode(body["content"]).decode("utf-8") and "/data" not in body["message"]


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
    assert [req.method for req in sent if req.method in ("POST", "PUT", "PATCH")].count("PUT") == 2
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
             "data": {"ok": True, "report": "final report", "report_appendix": "execution record",
                      "cost_usd": 1.0}}
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
    assert (remote["puts"], remote["posts"], remote["patches"]) == (2, 1, 1)
    assert "<!-- labhq terminal r 9 -->" in remote["comments"][0]["body"]
    assert all(restored.store.get("github_action", f"r:9:{action}")["done"]
               for action in ("report", "comment", "close"))
    saved_report = restored.store.get("github_action", "r:9:report")
    assert saved_report["path"].endswith("-r.md")
    assert saved_report["appendix_path"].endswith("-r_appendix.md")


@pytest.mark.asyncio
async def test_committed_github_appendix_uses_the_stored_request_not_the_truncated_event(tmp_path):
    calls = []
    s = Settings(projects=[ProjectSettings(id="p", repo="o/p", commit_reports=True)])
    s.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(s, github_transport=fake_github(calls))
    full = "긴 실행 기록\n" + "x" * 21_000 + "\n전문 끝"
    hub.requests["r"] = {"id": "r", "text": "study", "project_id": "p", "status": "done",
                         "finished_at": 1_700_000_000, "report": "full PI report",
                         "report_appendix": full}
    hub.reporter.issues["r"] = 7

    await hub.reporter.handle({"type": "request.completed", "request_id": "r", "seq": 9,
                               "data": {"ok": True, "report": "event report",
                                        "report_appendix": full[:20_000],
                                        "report_appendix_truncated": True,
                                        "report_appendix_chars": len(full),
                                        "report_appendix_api": "/api/requests/r"}})

    uploads = {path: base64.b64decode(body["content"]).decode("utf-8")
               for method, path, body in calls if method == "PUT" and "/contents/" in path}
    appendix = next(text for path, text in uploads.items() if "_appendix.md" in path)
    report = next(text for path, text in uploads.items() if "_appendix.md" not in path)
    assert "전문 끝" in appendix and full in appendix
    assert "full PI report" in report and "event report" not in report


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


def test_encoded_file_uris_are_withheld():
    policy = PolicySettings(data_zones=[DataZone(path="/data/cohort", level="restricted")])
    for uri in ("file:///data/%2e/cohort/sample.cram", "file://localhost/data/./cohort/sample.cram"):
        assert sanitize(f"see {uri}\nkeep", policy) == "<restricted-zone>\nkeep"


@pytest.mark.asyncio
async def test_root_zone_turns_github_reporting_off(tmp_path):
    calls = []
    s = Settings(projects=[ProjectSettings(id="p", repo="o/p")])
    s.policy.data_zones = [DataZone(path="/", level="restricted")]
    s.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(s, github_transport=fake_github(calls))
    hub.requests["r"] = {"id": "r", "text": "study", "project_id": "p", "status": "done"}
    assert not hub.reporter.enabled()
    for typ in ("request.created", "request.completed"):
        await hub.reporter.handle({"type": typ, "request_id": "r", "data": {"report": "x"}})
    assert calls == []


def _outbound_strings(calls):
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    out = []
    for _method, path, body in calls:
        out.append(path)
        out += list(strings(body))
        if isinstance(body, dict) and "content" in body:
            out.append(base64.b64decode(body["content"]).decode("utf-8"))
    return out


@pytest.mark.asyncio
async def test_public_project_reports_hide_the_pis_default_github_and_url_references(tmp_path):
    # #123: only path references were masked, so a private repository or a personal wiki the PI set as a
    # default reference went to a public project repository once the CSO quoted it.
    from labhq.intake import Reference

    calls = []
    s = Settings(projects=[ProjectSettings(id="p", repo="o/p", visibility="public", allow_public_reports=True)])
    s.gateway.state_dir = str(tmp_path / "state")
    s.pi_profile.references = [Reference(kind="github", value="owner/Yuan"),
                               Reference(kind="url", value="https://wiki.example.org/pi/notes"),
                               Reference(kind="doi", value="10.1038/nature12373")]
    hub = Hub(s, github_transport=fake_github(calls))
    quoted = ("owner/Yuan, https://github.com/owner/Yuan, https://github.com/owner/Yuan/tree/main/docs, "
              "git@github.com:owner/Yuan.git, HTTPS://WWW.GitHub.com/Owner/yuan, https://wiki.example.org/pi/notes, "
              "https://wiki.example.org:443/pi/notes/page2?rev=3, wiki.example.org/pi/notes, "
              "https:\\/\\/wiki.example.org\\/pi\\/notes and see owner/Yuan.")
    keep = "scverse/scanpy https://github.com/scverse/scanpy 10.1038/nature12373 https://wiki.example.org/public"
    hub.requests["r"] = {"id": "r", "text": f"study with {quoted}", "project_id": "p", "status": "done",
                         "references": [{"kind": "github", "value": "https://github.com/scverse/scanpy",
                                         "source": "request"},
                                        {"kind": "github", "value": "https://github.com/owner/Yuan",
                                         "source": "pi_profile"}]}
    events = [("request.created", {}),
              ("request.plan", {"steps": [{"id": "s", "agent_id": "a", "instruction": f"Read {quoted}"}]}),
              ("request.completed", {"ok": True, "report": f"Used {quoted}. Kept {keep}"})]
    for seq, (typ, data) in enumerate(events, 1):
        await hub.reporter.handle({"type": typ, "request_id": "r", "seq": seq, "data": data})
    outbound = _outbound_strings(calls)
    assert any("<private-reference>" in value for value in outbound)
    for value in outbound:
        assert "yuan" not in value.casefold() and "wiki.example.org/pi" not in value.replace("\\/", "/"), value
    report = outbound[-1] if "Kept" in outbound[-1] else next(v for v in outbound if "Kept" in v)
    assert keep in report, "a request's own pointers and literature IDs stay"
