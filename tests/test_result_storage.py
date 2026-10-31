import asyncio
import base64
import json
import shutil
from pathlib import Path
from xml.etree import ElementTree

import httpx
import pytest

from labhq.settings import Settings, ProjectSettings
from tests.support.serve_task import serve_task, wait_started
from labhq.portfolio import broken_markdown_links
from labhq.result_storage import ResultStorage, clean_document, draft_documents


class Hub:
    def __init__(self, tmp_path, transport=None):
        self.s = Settings()
        self.s.runner.workspace_root = str(tmp_path / "runs")
        self.requests = {"req_test": {"id": "req_test", "text": "Compare public studies",
                                     "intake": {"work_kind": "research"}}}
        self.agents = {}
        self.transport = transport
        self.saved = []

    def save_request(self, rid):
        self.saved.append(rid)

    async def publish(self, event):
        pass


async def test_card_conditions_and_no_token(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    hub = Hub(tmp_path)
    service = ResultStorage(hub)
    card = await service.card("req_test")
    assert card["default"] == "local"
    assert "GITHUB_TOKEN" in card["warning"]
    assert not card["github_available"]
    hub.requests["req_test"]["intake"] = {"work_kind": "general"}
    assert await service.card("req_test") is None
    hub.s.github.ask_storage = True
    assert await service.card("req_test")
    await service.select("req_test", {"mode": "new", "repo": "study", "visibility": "private"})
    assert hub.requests["req_test"]["result_storage"]["mode"] == "local"
    assert await service.card("req_test") is None


async def test_project_skips_question_and_inherits_choice(tmp_path):
    hub = Hub(tmp_path)
    hub.s.projects = [ProjectSettings(id="study", repo="owner/study")]
    hub.requests["req_test"]["project_id"] = "study"
    service = ResultStorage(hub)
    assert await service.card("req_test") is None
    assert hub.requests["req_test"]["result_storage"]["repo"] == "owner/study"
    hub.requests["req_next"] = {"id": "req_next", "project_id": "study"}
    assert await service.card("req_next") is None
    assert hub.requests["req_next"]["result_storage"]["repo"] == "owner/study"


def fake_github(calls, *, forbidden=False, exists=False):
    def handle(req):
        body = json.loads(req.content) if req.content else {}
        calls.append((req.method, req.url.path, body))
        if forbidden:
            return httpx.Response(403, json={"message": "forbidden"})
        if req.url.path == "/user":
            return httpx.Response(200, json={"login": "owner"})
        if req.url.path == "/user/repos":
            if req.method == "POST" and not body:
                return httpx.Response(422, json={"message": "name must not be blank"})
            return httpx.Response(201 if req.method == "POST" else 200,
                                  json={"full_name": "owner/study", "private": True,
                                        "default_branch": "main"} if req.method == "POST" else [])
        if "/contents/" in req.url.path:
            return httpx.Response(404 if req.method == "GET" else 201,
                                  json={} if req.method == "GET" else {"content": {"html_url": "https://github.com/owner/study"}})
        return httpx.Response(200 if exists or any(c[0] == "POST" for c in calls) else 404,
                              json={"full_name": "owner/study", "private": True,
                                    "permissions": {"push": True}, "default_branch": "main"})
    return httpx.MockTransport(handle)


@pytest.mark.parametrize("exists", [False, True])
async def test_new_repo_and_upload_only_after_review(tmp_path, monkeypatch, exists):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    calls = []
    hub = Hub(tmp_path)
    service = ResultStorage(hub, fake_github(calls, exists=exists))
    await service.select("req_test", {"mode": "new", "repo": "study", "visibility": "private"})
    assert not any(method in {"POST", "PUT"} for method, _, _ in calls)
    hub.requests["req_test"].update(status="done", pi_reviewed_at=1,
                                    report="Observed difference; mechanism remains unknown.")
    async def approve(**kw):
        assert kw["kind"] == "result_publish"
        assert "README.md" in kw["detail"]["documents"]
        assert not any(c[0] == "PUT" for c in calls)
        return {"approved": True}
    hub.request_approval = approve
    await service.finish("req_test")
    posts = [c for c in calls if c[0] == "POST"]
    assert len(posts) == (0 if exists else 1)
    if posts:
        assert posts[0][2]["private"] is True
    files = {path.split("/contents/")[1]: base64.b64decode(body["content"]).decode()
             for method, path, body in calls if method == "PUT"}
    assert any(path.endswith("README.md") for path in files)
    assert not any(path.endswith("CITATION.cff") for path in files)  # the request names no author, and none is invented (#792)
    assert "인용 정보 미정(저자 미입력)" in next(body for path, body in files.items() if path.endswith("README.md"))
    messages = {path.split("/contents/")[1]: body["message"] for method, path, body in calls if method == "PUT"}
    assert messages["README.md"].startswith("연구 결과 정리: README.md")
    assert len(set(messages.values())) == len(messages)  # one message per file, not three fixed sentences
    assert hub.requests["req_test"]["result_storage"]["status"] == "published"
    await service.close()


async def test_new_portfolio_omits_raster_report_figure_without_broken_links(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    calls = []
    hub = Hub(tmp_path)
    service = ResultStorage(hub, fake_github(calls, exists=True))
    await service.select("req_test", {"mode": "new", "repo": "study", "visibility": "private"})
    bundle = Path(hub.s.runner.workspace_root) / "requests/req_test"
    output = bundle / "steps/analysis/outputs/result.png"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"synthetic-png")
    (bundle / "MANIFEST.tsv").write_text(
        "relative_path\tstatus\nsteps/analysis/outputs/result.png\tcopied\n", encoding="utf-8")
    paper = ("# 결과 보고서\n\n## 요약(Abstract)\n\n공개 자료 결과다.\n\n## 서론\n\n질문을 검토했다.\n\n"
             "## 방법\n\n공개 자료를 분석했다.\n\n## 결과\n\n![결과 그림](work-analysis/outputs/result.png)\n\n"
             "## 논의\n\n한계를 확인했다.\n\n## 결론\n\n결론 문장이다.\n")
    hub.requests["req_test"].update(
        status="done", pi_reviewed_at=1, report=paper,
        results={"analysis": {"workdir": "work-analysis", "outputs": ["outputs/result.png"]}},
    )

    async def approve(**_kw):
        return {"approved": True}

    hub.request_approval = approve
    await service.finish("req_test")
    draft = Path(hub.requests["req_test"]["result_storage"]["draft_path"])
    readme = (draft / "README.md").read_text(encoding="utf-8")
    report = (draft / "report" / "report.md").read_text(encoding="utf-8")
    assert hub.requests["req_test"]["result_storage"]["status"] == "published"
    assert "그림 생략: 결과 그림" in readme
    assert "그림 생략: 결과 그림" in report
    assert "result.png" not in readme + report
    await service.close()


async def test_new_portfolio_long_report_points_to_nested_report(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    hub = Hub(tmp_path)
    service = ResultStorage(hub, fake_github([], exists=True))
    await service.select("req_test", {"mode": "new", "repo": "study", "visibility": "private"})
    hub.requests["req_test"].update(
        status="done", pi_reviewed_at=1,
        report="# 긴 보고서\n\n" + "공개 자료를 검토했다. " * 160,
    )

    async def approve(**_kw):
        return {"approved": True}

    hub.request_approval = approve
    await service.finish("req_test")
    draft = Path(hub.requests["req_test"]["result_storage"]["draft_path"])
    readme = (draft / "README.md").read_text(encoding="utf-8")
    # The repository README opens with a link to the full report; a trailing "continues in ..." note would dangle (#792).
    assert "](report/report.md)" in readme
    assert "이어지는 내용은" not in readme
    await service.close()


async def test_denied_permissions_keep_local(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    service = ResultStorage(Hub(tmp_path), fake_github([], forbidden=True))
    card = await service.card("req_test")
    assert not card["github_available"] and "권한" in card["warning"]
    await service.select("req_test", {"mode": "existing", "repo": "owner/study"})
    assert service.hub.requests["req_test"]["result_storage"]["mode"] == "local"
    await service.close()


def test_editor_documents_remove_secrets_and_personal_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-private-token")
    hub = Hub(tmp_path)
    req = hub.requests["req_test"]
    req["report"] = "Result. synthetic-private-token C:/Users/alice/project/a.txt /home/alice/result.csv"
    docs = draft_documents(req, hub.s)
    assert {"README.md", "PROGRESS.md", "REPRODUCE.md", "SOURCES.md", "report.md"} <= docs.keys()
    joined = "\n".join(docs.values())
    assert "synthetic-private-token" not in joined
    assert "alice" not in joined
    assert "report.md" in docs["README.md"]
    table = "gene\tvalue\n" + "A\t2\n" * 20_000
    assert clean_document(table, hub.s, req) == table  # files are not GitHub comments; never truncate rows


def test_reproduce_is_a_short_guide_and_long_instructions_are_separate(tmp_path):
    hub = Hub(tmp_path)
    req = hub.requests["req_test"]
    req.update(
        plan={"steps": [
            {"id": "env", "instruction": "환경을 고정한다.",
             "outputs": ["outputs/env/requirements.lock.txt"]},
            {"id": "analysis", "depends_on": ["env"],
             "instruction": "아주 긴 작업 지시 " * 500,
             "outputs": ["outputs/result.tsv"]},
        ]},
        results={"analysis": {"text": "완료"}},
    )
    assets = {
        "steps/env/outputs/env/requirements.lock.txt": "python==3.12\npandas==2.2.2\n",
        "steps/analysis/outputs/scripts/analyze.py":
            '__import__("os").environ["LABHQ_INPUT_COHORT_TSV"]\n',
    }
    bundle_readme = ("## 단계 순서\n\n- `env` (의존: 없음)\n- `analysis` (의존: env)\n\n"
                     "## 다시 실행\n\n- `cd steps/analysis && python outputs/scripts/analyze.py`\n")

    docs = draft_documents(
        req, hub.s, assets=assets,
        environment_records={"env": {"python": "3.12", "status": "complete"}},
        bundle_readme=bundle_readme,
    )

    reproduce = docs["REPRODUCE.md"]
    for heading in ("## 고정 환경", "## 입력 설정", "## 실행 순서와 명령", "## 예상 출력", "## 재현 가능 범위"):
        assert heading in reproduce
    assert "LABHQ_INPUT_COHORT_TSV" in reproduce
    assert "requirements.lock.txt" in reproduce and "3.12" in reproduce
    assert "공개 자료로 다시 만들 수 있는 것" in reproduce
    assert "접근 제한 자료가 필요한 것" in reproduce
    assert len(reproduce) < 6000
    assert "아주 긴 작업 지시" not in reproduce
    assert "아주 긴 작업 지시" in docs["TASK_RECORD.md"]


async def test_clarify_and_storage_share_one_card(tmp_path, monkeypatch):
    from labhq.orchestrator.cso import Orchestrator
    from labhq.gateway.server import Hub as RealHub, RequestIn
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    hub = RealHub(s)
    rid = hub.create_request(RequestIn(text="research question", work_kind="research"))
    if isinstance(rid, dict):
        rid = rid["request_id"]
    hub.requests[rid]["intake"] = {"work_kind": "research"}
    calls = []
    async def answer(**kw):
        calls.append(kw)
        return {"approved": True, "note": "public studies", "storage": {"mode": "local"}}
    monkeypatch.setattr(hub, "request_approval", answer)
    monkeypatch.setattr(hub.orchestrator, "_emit", lambda *a, **k: asyncio.sleep(0))
    status, _ = await hub.orchestrator._plan_clarification(rid, {"clarifying_questions": ["Which studies?"]})
    assert status == "answered" and len(calls) == 1
    assert "storage" in calls[0]["detail"]
    assert hub.requests[rid]["result_storage"]["mode"] == "local"


async def test_mock_request_reaches_editor_and_fake_github(tmp_path, monkeypatch):
    import uvicorn
    from labhq.gateway.server import create_app, RequestIn
    from labhq.runner.daemon import Runner
    from labhq.util import free_port
    from tests.test_github_reporter import _until
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    shutil.copytree(Path(__file__).resolve().parents[1] / "agents", tmp_path / "agents")
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.gateway.port = free_port()
    s.gateway.url = f"ws://127.0.0.1:{s.gateway.port}"
    s.runner.broker_port = free_port()
    s.runner.force_engine, s.runner.job_poll_s = "mock", 1
    s.runner.workspace_root, s.runner.talent_dir = str(tmp_path / "runs"), str(tmp_path / "talent")
    s.runner.agents_dir = str(tmp_path / "agents")
    s.hpc.scheduler = "mock"
    s.github.ask_storage = True
    calls, cards = [], []
    app = create_app(s, github_transport=fake_github(calls))
    hub = app.state.hub
    publish = hub.publish
    async def tap(ev, **kwargs):
        await publish(ev, **kwargs)
        if ev.get("type") == "approval.requested":
            card = ev["data"]
            cards.append(card)
            storage = {"mode": "new", "repo": "study", "visibility": "private"} if card.get("detail", {}).get("storage") else None
            asyncio.create_task(hub.resolve_approval(card["id"], True, "ok", storage=storage))
    hub.publish = tap
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=s.gateway.port, log_level="warning"))
    jobs = [serve_task(server.serve())]
    runner = Runner(s)
    try:
        await wait_started(lambda: server.started, jobs[0], 10, "gateway")
        jobs.append(asyncio.create_task(runner.run_forever()))
        await _until(lambda: "editor" in hub.agents, 15)
        rid = hub.create_request(RequestIn(text="Compare public studies"))
        await _until(lambda: hub.requests[rid].get("status") == "done", 60)
        assert not any(c["kind"] == "result_publish" for c in cards)
        hub.review_done(rid)
        await _until(lambda: (hub.requests[rid].get("result_storage") or {}).get("status") in {"published", "failed"}, 60)
        saved = hub.requests[rid]["result_storage"]
        assert saved["status"] == "published", saved
        assert Path(saved["draft_path"], "README.md").is_file()
        assert [c["kind"] for c in cards].count("result_storage") == 2  # planning choice, then reviewed-result choice
        assert [c["kind"] for c in cards].count("result_publish") == 1
        assert any(t.get("payload", {}).get("agent_id") == "editor" and t.get("completed")
                   for t in hub.store.all("task").values())
        assert any(method == "PUT" for method, _, _ in calls)
    finally:
        await hub.result_storage.close()
        runner.stop()
        server.should_exit = True
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)


async def test_decline_and_restricted_request_never_upload(tmp_path, monkeypatch):
    from labhq.settings import DataZone
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    calls = []
    hub = Hub(tmp_path)
    service = ResultStorage(hub, fake_github(calls, exists=True))
    await service.select("req_test", {"mode": "existing", "repo": "owner/study"})
    hub.requests["req_test"].update(status="done", report="result", pi_reviewed_at=1)
    async def decline(**kw):
        return {"approved": False}
    hub.request_approval = decline
    await service.finish("req_test")
    assert hub.requests["req_test"]["result_storage"]["status"] == "declined"
    hub.s.policy.data_zones = [DataZone(path="/controlled", level="restricted")]
    hub.requests["req_test"]["text"] = "Analyze /controlled/cohort.tsv"
    await service.finish("req_test")
    assert hub.requests["req_test"]["result_storage"]["status"] == "local"
    assert not any(c[0] in {"POST", "PUT"} for c in calls)
    await service.close()


def test_assets_only_reported_outputs_and_clean_text(tmp_path, monkeypatch):
    from labhq.result_storage import edition_assets
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-private-token")
    hub = Hub(tmp_path)
    bundle = Path(hub.s.runner.workspace_root) / "requests/req_test"
    output = bundle / "steps/s1/outputs"
    output.mkdir(parents=True)
    (output / "table.tsv").write_text("gene\tvalue\nA\t2\n", encoding="utf-8")
    (output / "plot.png").write_bytes(b"synthetic-image")
    raw_svg = ('<svg xmlns="http://www.w3.org/2000/svg"><metadata>token=synthetic-private-token</metadata>'
               '<desc>C:/Users/alice/project/plot.svg</desc><!-- /home/bob/private -->'
               '<text>그림</text></svg>')
    (output / "plot.svg").write_text(raw_svg, encoding="utf-8")
    (output / "secret.txt").write_text("raw input", encoding="utf-8")
    (bundle / "MANIFEST.tsv").write_text("relative_path\tstatus\nsteps/s1/outputs/table.tsv\tcopied\n"
        "steps/s1/outputs/plot.png\tcopied\nsteps/s1/outputs/plot.svg\tcopied\n"
        "steps/s1/outputs/secret.txt\tcopied (unreported)\n", encoding="utf-8")
    text, images = edition_assets(hub.requests["req_test"], hub.s)
    assert list(text) == ["steps/s1/outputs/table.tsv"]
    assert list(images) == ["steps/s1/outputs/plot.png", "steps/s1/outputs/plot.svg"]
    published_svg = images["steps/s1/outputs/plot.svg"].decode("utf-8")
    assert "synthetic-private-token" not in published_svg
    assert "alice" not in published_svg and "bob" not in published_svg
    assert "&lt;redacted-secret&gt;" in published_svg and "&lt;local-path&gt;" in published_svg
    ElementTree.fromstring(published_svg)
    assert (output / "plot.svg").read_text(encoding="utf-8") == raw_svg  # the request bundle keeps the original

    from labhq.result_storage import publish_image_previews
    previews, omitted = publish_image_previews(images, tmp_path / "req-editor")
    preview = base64.b64decode(previews["steps/s1/outputs/plot.svg"].split(",", 1)[1]).decode("utf-8")
    assert preview == published_svg and not omitted


def test_svg_publish_copy_refuses_a_secret_left_after_cleaning(tmp_path, monkeypatch):
    from labhq.result_storage import clean_svg_for_publish
    hub = Hub(tmp_path)
    monkeypatch.setattr("labhq.result_storage.clean_document", lambda text, *_args: text)
    secret = ("ghp_" + "A" * 26).encode("ascii")
    with pytest.raises(ValueError, match="SVG.*비밀값"):
        clean_svg_for_publish(b"<svg><metadata>" + secret + b"</metadata></svg>", hub.s,
                              hub.requests["req_test"])


def test_publish_preview_limits_do_not_limit_original_images(tmp_path):
    from labhq.result_storage import (PUBLISH_PREVIEW_FILE_MAX_BYTES, PUBLISH_PREVIEW_TOTAL_MAX_BYTES,
                                      publish_image_previews)
    assert PUBLISH_PREVIEW_FILE_MAX_BYTES == 1_000_000
    assert PUBLISH_PREVIEW_TOTAL_MAX_BYTES == 5_000_000
    target = tmp_path / "req-editor"
    images = {"small.svg": b"<svg/>", "large.png": b"x" * (PUBLISH_PREVIEW_FILE_MAX_BYTES + 1)}
    previews, omitted = publish_image_previews(images, target)
    assert previews["small.svg"].startswith("data:image/svg+xml;base64,")
    assert omitted == {"large.png": {"size_bytes": 1_000_001, "path": str(target / "large.png")}}
    assert len(images["large.png"]) == 1_000_001  # the original payload remains available for draft and upload

    five = {f"f{i}.png": b"x" * PUBLISH_PREVIEW_FILE_MAX_BYTES for i in range(6)}
    previews, omitted = publish_image_previews(five, target)
    assert len(previews) == 5 and list(omitted) == ["f5.png"]


def test_citation_cff_is_written_only_from_authors_the_request_gives():
    from labhq.portfolio import _citation_cff

    settings = Settings()
    assert _citation_cff({"id": "r", "text": "질문이다."}, settings) == ""
    assert _citation_cff({"id": "r", "text": "질문이다.", "authors": ["", {"name": " "}]}, settings) == ""
    cff = _citation_cff({"id": "r", "text": "질문이다.", "authors": ["Kim A", {"name": "Lee B"}]}, settings)
    assert cff.startswith("cff-version: 1.2.0") and '  - name: "Kim A"' in cff and '  - name: "Lee B"' in cff
    assert "authors: []" not in cff and "doi" not in cff.lower()


def test_citation_authors_come_from_the_setting_when_the_request_names_none():
    from labhq.portfolio import _citation_cff, _request_authors

    settings = Settings()
    assert Settings().result_storage.authors == []  # empty by default: nothing is invented
    settings.result_storage.authors = ["Kim A", " ", "Lee B"]
    req = {"id": "r", "text": "질문이다."}
    assert _request_authors(req, settings) == ["Kim A", "Lee B"]
    cff = _citation_cff(req, settings)
    assert '  - name: "Kim A"' in cff and '  - name: "Lee B"' in cff and cff.startswith("cff-version: 1.2.0")
    own = _citation_cff({**req, "authors": ["Park C"]}, settings)  # the request's own names win
    assert '"Park C"' in own and "Kim A" not in own
    assert _citation_cff(req, Settings()) == ""


def test_a_reviewers_quoted_image_link_is_not_a_link_of_the_progress_record(tmp_path):
    """#1193: the reviewer quotes the report's figure link as evidence. PROGRESS.md holds the review as JSON text, so that
    quote used to read as a real link of `run/progress.md` and the edition check refused the whole save (② 7th run)."""
    hub = Hub(tmp_path)
    req = hub.requests["req_test"]
    req["report"] = "# 결과\n\n![그림 1](graphical_abstract.svg)\n"
    req["review"] = {"verdict": "accept", "issues": [{
        "step_id": "S5", "priority": "P2", "evidence_quote": "![그래픽 초록](../graphical_abstract.svg)",
        "problem": "S2 그림이 보고서에 없습니다. 인용: [표](../tables/missing.tsv)"}]}
    docs = draft_documents(req, hub.s)
    assert broken_markdown_links({"run/progress.md": docs["PROGRESS.md"]}) == []
    assert "../graphical_abstract.svg" in docs["PROGRESS.md"], "the quote is kept, only not as a link"
    assert "../tables/missing.tsv" in docs["PROGRESS.md"]


def test_a_real_broken_link_in_a_document_is_still_found():
    files = {"run/progress.md": "# 진행\n\n[없는 파일](../nowhere.svg)\n", "figures/a.svg": "<svg/>"}
    assert broken_markdown_links(files) == [{"document": "run/progress.md", "target": "../nowhere.svg"}]


@pytest.mark.parametrize("name,text,expected", [
    ("a longer backtick fence holds a shorter one", "````json\n\"q\": \"```\"\n![a](../x.svg)\n````\n", []),
    ("a longer tilde fence holds a shorter one", "~~~~\n~~~\n![a](../x.svg)\n~~~~\n", []),
    ("a three-backtick fence still hides its link", "```json\n![a](../x.svg)\n```\n", []),
    ("a link after a closed fence is still read", "```\ncode\n```\n![a](../x.svg)\n", ["../x.svg"]),
    ("a fence that never closes does not hide the link", "```json\n![a](../x.svg)\n", ["../x.svg"]),
    ("a closing fence may be longer than the opening one", "```json\n![a](../x.svg)\n`````\n![b](../y.svg)\n", ["../y.svg"]),
])
def test_a_fence_hides_links_by_its_own_length(name, text, expected):
    """#1193 (Codex P2): `_json_block` makes its fence longer than any backtick run inside the JSON, so the link check
    has to know fences of any length. A fence is closed only by a run of the same character at least as long."""
    assert [row["target"] for row in broken_markdown_links({"run/p.md": text})] == expected, name
