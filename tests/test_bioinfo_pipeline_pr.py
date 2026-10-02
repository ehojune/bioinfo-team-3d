import json
from pathlib import Path

import httpx
import pytest

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.gateway.server import Hub
from labhq.models import AgentSpec, AskRequest, Engine, Task, TaskResult
from labhq.pipeline_pr import collect_pipeline_submission
from labhq.settings import Settings


def settings(tmp_path: Path) -> Settings:
    configured = Settings()
    configured.gateway.state_dir = configured.runner.state_dir = str(tmp_path / "state")
    configured.runner.workspace_root = str(tmp_path / "runs")
    return configured


def test_gateway_github_token_never_reaches_staff_environment(tmp_path, monkeypatch):
    configured = settings(tmp_path)
    monkeypatch.setenv(configured.github.token_env, "gateway-only")
    context = RunContext(task=Task(agent_id="bioinfo-agent", prompt="work"),
                         agent=AgentSpec(id="bioinfo-agent", name="bio", role="pipeline",
                                         engine=Engine.mock),
                         workdir=tmp_path, settings=configured, mcp_servers=[],
                         env={configured.github.token_env: "forged"}, emit=lambda *_args: None, prompt="work")
    assert configured.github.token_env not in get_adapter(Engine.mock, configured).staff_env(context)


@pytest.mark.parametrize(
    ("question", "pi"),
    [
        ("어느 표본군을 쓰나요?", False),
        ("nf-core에 없습니다. 새 파이프라인을 만들까요?", False),
        ("요청 범위 밖 분석을 추가할까요?", False),
        ("결과 폴더를 재귀 삭제할까요?", False),
        ("통제 데이터 구역 원본을 읽어도 되나요?", True),
        ("예산 상한을 넘겨 계속할까요?", True),
        ("새 프로그램을 설치해도 되나요?", True),
    ],
)
async def test_bioinfo_questions_go_to_cso_except_three_hard_stops(tmp_path, question, pi):
    hub = Hub(settings(tmp_path))
    hub.agents = {"cso": {"engine": "mock"}}
    hub.requests["r"] = {"id": "r", "text": "analysis", "status": "running"}
    routed = []

    async def dispatch(task):
        routed.append("cso")
        return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="진행하세요")

    async def approval(**_kwargs):
        routed.append("pi")
        return {"approved": True, "note": "승인"}

    hub.dispatch, hub.request_approval = dispatch, approval
    ask = AskRequest(task_id="t", agent_id="bioinfo-agent", request_id="r", to="cso",
                     question=question, why_blocked="정책 결정이 필요합니다")
    try:
        await hub.orchestrator.answer_ask(ask, "runner")
        assert routed == (["pi"] if pi else ["cso"])
        assert hub.store.get("ask", ask.id)["answer"]["routed_to"] == ("pi" if pi else "cso")
    finally:
        hub.store.close()


def pipeline(name="tiny", extra=None):
    files = [
        {"path": f"pipelines/{name}/main.nf", "content": "nextflow.enable.dsl=2\n"},
        {"path": f"pipelines/{name}/nextflow.config", "content": "manifest { version = '1.0.0' }\n"},
        {"path": f"pipelines/{name}/README.md", "content": "# Tiny\n"},
    ]
    files.extend(extra or [])
    return {"state": "ready", "schema": 1, "name": name, "title": "Tiny pipeline",
            "summary": "A generated test pipeline.", "files": files}


def test_pipeline_bundle_uses_manifest_and_plain_text_only(tmp_path):
    root = tmp_path / "work"
    folder = root / "outputs" / "pipeline" / "tiny"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"schema": 1, "name": "tiny",
                                                       "title": "Tiny", "summary": "test"}), encoding="utf-8")
    for name, text in (("main.nf", "workflow {}\n"), ("nextflow.config", "manifest {}\n"),
                       ("README.md", "# Tiny\n")):
        (folder / name).write_text(text, encoding="utf-8")
    outputs = [f"outputs/pipeline/tiny/{name}" for name in
               ("manifest.json", "main.nf", "nextflow.config", "README.md")]
    bundle = collect_pipeline_submission(root, outputs)
    assert bundle and bundle["state"] == "ready"
    assert {item["path"] for item in bundle["files"]} == {
        "pipelines/tiny/main.nf", "pipelines/tiny/nextflow.config", "pipelines/tiny/README.md"}


def fake_pipeline_api(calls, status=200):
    def api(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/repos/ehojune/bioinfo-agent":
            return httpx.Response(status, json={"private": False, "visibility": "public"})
        if path.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if "/contents/" in path and request.method == "GET":
            return httpx.Response(404, json={"message": "Not Found"})
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "abc123"}})
        if path.endswith("/git/refs"):
            return httpx.Response(201, json={"ref": body["ref"]})
        if "/contents/" in path and request.method == "PUT":
            return httpx.Response(201, json={"content": {"html_url": "https://example.test/file"}})
        if path.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 9, "html_url": "https://example.test/pr/9"})
        return httpx.Response(404, json={"message": "Not Found"})
    return httpx.MockTransport(api)


async def run_submission(tmp_path, submission, transport):
    hub = Hub(settings(tmp_path), github_transport=transport)
    hub.requests["r"] = {"id": "r", "status": "done"}
    hub.store.put("pipeline_submission", "t", {"request_id": "r", "task_id": "t",
                                                "agent_id": "bioinfo-agent", "state": "ready",
                                                "submission": submission})
    delivered = await hub.reporter.handle({"type": "pipeline.ready", "request_id": "r", "task_id": "t"})
    return hub, delivered


async def test_gateway_opens_pipeline_pr_with_fake_github(tmp_path):
    calls = []
    hub, delivered = await run_submission(tmp_path, pipeline(), fake_pipeline_api(calls))
    try:
        assert delivered is True
        saved = hub.store.get("pipeline_submission", "t")
        assert saved["state"] == "open" and saved["number"] == 9
        puts = [path for method, path, _ in calls if method == "PUT"]
        assert len(puts) == 3 and all("/contents/pipelines/tiny/" in path for path in puts)
        assert hub.events[-1]["type"] == "pipeline.pr" and hub.events[-1]["data"]["status"] == "open"
    finally:
        await hub.reporter.client().http.aclose()
        hub.store.close()


@pytest.mark.parametrize("bad", [
    {"path": "pipelines/tiny/raw.fastq", "content": "@sample\nACGT\n"},
    {"path": "pipelines/tiny/run.sh", "content": "DATA=C:\\Users\\pi\\cohort\\sample.bam\n"},
])
async def test_data_or_local_paths_refuse_pipeline_pr_without_github_call(tmp_path, bad):
    calls = []
    hub, delivered = await run_submission(tmp_path, pipeline(extra=[bad]), fake_pipeline_api(calls))
    try:
        assert delivered is True and calls == []
        assert hub.store.get("pipeline_submission", "t")["state"] == "rejected"
        assert hub.events[-1]["data"]["status"] == "rejected"
    finally:
        hub.store.close()


async def test_missing_repo_permission_leaves_pipeline_pr_pending(tmp_path):
    calls = []
    hub, delivered = await run_submission(tmp_path, pipeline(), fake_pipeline_api(calls, status=403))
    try:
        assert delivered is False
        saved = hub.store.get("pipeline_submission", "t")
        assert saved["state"] == "pending" and "permission" in saved["reason"]
        assert hub.requests["r"]["status"] == "done"
        assert hub.events[-1]["type"] == "pipeline.pr" and hub.events[-1]["data"]["status"] == "pending"
    finally:
        await hub.reporter.client().http.aclose()
        hub.store.close()
