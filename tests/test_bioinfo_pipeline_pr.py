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


def settings(tmp_path: Path, pipeline_pr: bool = False) -> Settings:
    configured = Settings()
    configured.gateway.state_dir = configured.runner.state_dir = str(tmp_path / "state")
    configured.runner.workspace_root = str(tmp_path / "runs")
    if pipeline_pr:  # left unset otherwise, so the off case exercises the shipped default
        configured.policy.bioinfo_agent.pipeline_pr = True
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


@pytest.mark.parametrize("name", ["GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "gh_token"])
def test_no_github_credential_reaches_staff_whatever_its_name(tmp_path, monkeypatch, name):
    """#301 review P1: gh reads GH_TOKEN before GITHUB_TOKEN, so staff could push to the public repo themselves."""
    configured = settings(tmp_path)
    monkeypatch.setenv(name, "pi-credential")
    context = RunContext(task=Task(agent_id="bioinfo-agent", prompt="work"),
                         agent=AgentSpec(id="bioinfo-agent", name="bio", role="pipeline", engine=Engine.mock),
                         workdir=tmp_path, settings=configured, mcp_servers=[], env={name: "from-task"},
                         emit=lambda *_args: None, prompt="work")
    env = get_adapter(Engine.mock, configured).staff_env(context)
    assert not any(key.casefold() == name.casefold() for key in env)


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


async def run_submission(tmp_path, submission, transport, pipeline_pr=True):
    hub = Hub(settings(tmp_path, pipeline_pr), github_transport=transport)
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


FIXTURE = "sample,file\nsmoke,https://raw.githubusercontent.com/nf-core/test-datasets/x/test.bam\n"


@pytest.mark.parametrize("bad", [
    {"path": "pipelines/tiny/samples.csv", "content": "sample_id,phenotype\nKOR-0012,case\n"},
    {"path": "pipelines/tiny/assets/counts.tsv", "content": "gene\tKOR-0012\nTP53\t41\n"},
    {"path": "pipelines/tiny/assets/samplesheet.csv", "content": "sample,phenotype\nKOR-0012,case\n"},
    {"path": "pipelines/tiny/assets/samplesheet.test.csv", "content": FIXTURE * 200},
    {"path": "pipelines/tiny/ref.fasta", "content": ">chr1\nACGT\n"},
    {"path": "pipelines/tiny/calls.vcf", "content": "##fileformat=VCFv4.2\n"},
    {"path": "pipelines/tiny/aln.sam", "content": "@HD\tVN:1.6\n"},
    {"path": "pipelines/tiny/cells.h5ad", "content": "HDF\n"},
    {"path": "pipelines/tiny/notes.txt", "content": "KOR-0012 case\n"},
    {"path": "pipelines/tiny/samples.json", "content": '[{"sample": "KOR-0012", "phenotype": "case"}]\n'},
    {"path": "pipelines/tiny/assets/cohort.yml", "content": "KOR-0012: case\n"},
    {"path": "pipelines/tiny/meta.yml", "content": "KOR-0012: case\n"},
    {"path": "pipelines/tiny/results.md", "content": "| sample | phenotype |\n| KOR-0012 | case |\n"},
    {"path": "pipelines/tiny/bin/samples", "content": "KOR-0012 case\n"},
    {"path": "pipelines/tiny/assets/NO_TRF", "content": "x" * 5000},
])
async def test_data_files_never_reach_the_public_pipeline_pr(tmp_path, bad):
    """#301 review P1: only pipeline source kinds pass; a table passes only as a bioinfo-agent test fixture list."""
    calls = []
    hub, delivered = await run_submission(tmp_path, pipeline(extra=[bad]), fake_pipeline_api(calls))
    try:
        saved = hub.store.get("pipeline_submission", "t")
        assert delivered is True and calls == []
        assert saved["state"] == "rejected" and saved["reason"].startswith("data file is not allowed")
    finally:
        hub.store.close()


async def test_pipeline_source_and_test_fixture_list_still_open_a_pr(tmp_path):
    extra = [
        {"path": "pipelines/tiny/assets/samplesheet.test.csv", "content": "# smoke test\n" + FIXTURE},
        {"path": "pipelines/tiny/assets/NO_TRF", "content": "# placeholder for an unset optional input\n"},
        {"path": "pipelines/tiny/bin/summarise.py", "content": "#!/usr/bin/env python3\nimport sys\n"},
        {"path": "pipelines/tiny/bin/plot.R", "content": "x <- 1\n"},
        {"path": "pipelines/tiny/nextflow_schema.json", "content": "{}\n"},
        {"path": "pipelines/tiny/assets/schema_input.json", "content": "{}\n"},
        {"path": "pipelines/tiny/docs/usage.md", "content": "# Usage\n"},
        {"path": "pipelines/tiny/modules/local/run/meta.yml", "content": "name: run\n"},
        {"path": "pipelines/tiny/environment.yml", "content": "dependencies: []\n"},
        {"path": "pipelines/tiny/bin/check_samplesheet", "content": "#!/usr/bin/env python3\nprint(1)\n"},
        {"path": "pipelines/tiny/LICENSE", "content": "MIT License\n"},
        {"path": "pipelines/tiny/conf/test.config",
         "content": "params.input = \"${projectDir}/assets/samplesheet.test.csv\"\n"},
        {"path": "pipelines/tiny/modules/run.nf",
         "content": "process RUN {\n  script:\n  \"\"\"\n  #!/usr/bin/env bash\n  tool --in x 2>/dev/null\n  \"\"\"\n}\n"},
    ]
    calls = []
    hub, delivered = await run_submission(tmp_path, pipeline(extra=extra), fake_pipeline_api(calls))
    try:
        assert delivered is True and hub.store.get("pipeline_submission", "t")["state"] == "open"
        assert len([path for method, path, _ in calls if method == "PUT"]) == 3 + len(extra)
    finally:
        await hub.reporter.client().http.aclose()
        hub.store.close()


@pytest.mark.parametrize("line", [
    "params.fasta = '/data/reference.fa'\n",
    "params.genome = [fasta:'/srv/genomes/hg38.fa']\n",
    "params.input = 'file:///shared/x.bam'\n",
    "params.home = '/home/someone/run'\n",
    "params.scratch = '/BiO/scratch/project/x'\n",
    "params.mnt = '/mnt/d/runs/x'\n",
])
async def test_every_host_specific_absolute_path_refuses_the_pipeline_pr(tmp_path, line):
    """#301 review P2: only FHS system prefixes every host or image has pass; lab server paths never reach the PR."""
    submission = pipeline()
    submission["files"][1]["content"] += line
    calls = []
    hub, delivered = await run_submission(tmp_path, submission, fake_pipeline_api(calls))
    try:
        assert delivered is True and calls == []
        assert hub.store.get("pipeline_submission", "t")["reason"] == "local absolute path is not allowed"
    finally:
        hub.store.close()


@pytest.mark.parametrize("line", [
    "params.tool = \"/opt/tool/bin/run\"\n",
    "workDir = '/tmp/work'\n",
    "params.bin = '/usr/local/bin/samtools'\n",
])
async def test_standard_system_paths_do_not_refuse_the_pipeline_pr(tmp_path, line):
    """Container and system prefixes (FHS) are the same on every host; refusing them would block real pipelines."""
    submission = pipeline()
    submission["files"][1]["content"] += line
    calls = []
    hub, delivered = await run_submission(tmp_path, submission, fake_pipeline_api(calls))
    try:
        assert delivered is True and hub.store.get("pipeline_submission", "t")["state"] == "open"
    finally:
        await hub.reporter.client().http.aclose()
        hub.store.close()


async def test_late_client_snapshot_keeps_the_pipeline_pr_status(tmp_path):
    """#301 review P2: the status lives on the stored row, not only on a one-off event a late client misses."""
    calls = []
    hub, _ = await run_submission(tmp_path, pipeline(), fake_pipeline_api(calls, status=403))
    try:
        hub.events.clear()  # the pipeline.pr event has left the 200-event replay window
        request = next(r for r in hub.snapshot()["data"]["requests"] if r["id"] == "r")
        assert request["pipeline_pr"] == {"status": "pending", "name": "tiny", "url": None, "number": None,
                                          "reason": "bioinfo-agent repository write permission required"}
    finally:
        await hub.reporter.client().http.aclose()
        hub.store.close()
    calls = []
    hub, _ = await run_submission(tmp_path / "open", pipeline(), fake_pipeline_api(calls))
    try:
        request = next(r for r in hub.snapshot()["data"]["requests"] if r["id"] == "r")
        assert request["pipeline_pr"]["status"] == "open"
        assert request["pipeline_pr"]["url"] == "https://example.test/pr/9"
    finally:
        await hub.reporter.client().http.aclose()
        hub.store.close()


def test_pipeline_pr_is_off_by_default_in_code_and_example_configs():
    root = Path(__file__).resolve().parents[1]
    assert Settings().policy.bioinfo_agent.pipeline_pr is False
    for path in (root / "config" / "labhq.example.yaml", root / "labhq" / "config" / "labhq.example.yaml"):
        assert Settings.load(str(path)).policy.bioinfo_agent.pipeline_pr is False


async def test_default_settings_attempt_no_pipeline_pr_and_send_nothing(tmp_path):
    """PI decision #300: off by default. The gateway neither stores, queues, publishes nor calls GitHub."""
    calls = []
    hub, delivered = await run_submission(tmp_path, pipeline(), fake_pipeline_api(calls), pipeline_pr=False)
    try:
        assert delivered is True and calls == []
        assert hub.store.get("pipeline_submission", "t")["state"] == "ready"
        assert hub.reporter.enabled() is False  # no project repo: nothing reaches the reporter at all
        result = TaskResult(task_id="t2", agent_id="bioinfo-agent", ok=True, text="done",
                            pipeline_submission=pipeline())
        await hub.on_runner_message("runner", {"type": "task.result", "task_id": "t2", "request_id": "r",
                                               "data": result.model_dump(mode="json")})
        assert hub.store.get("pipeline_submission", "t2") is None
        assert not [e for e in hub.events if e["type"] in {"pipeline.ready", "pipeline.pr"}]
        assert "pipelines/tiny/main.nf" not in json.dumps(list(hub.events), default=str)
        assert calls == []
    finally:
        hub.store.close()


@pytest.mark.parametrize("enabled", [False, True])
async def test_gateway_asks_the_runner_for_pipeline_files_only_when_turned_on(tmp_path, enabled):
    hub = Hub(settings(tmp_path, pipeline_pr=enabled))
    sent = []

    async def send(_runner_id, message):
        sent.append(message)
        task_id = message["task"]["id"]
        hub.futures[task_id].set_result(TaskResult(task_id=task_id, agent_id="bioinfo-agent", ok=True, text="done"))

    hub.send_runner = send
    hub.agent_runner["bioinfo-agent"] = "runner"
    try:
        await hub.dispatch(Task(agent_id="bioinfo-agent", request_id="r", prompt="build", meta={"kind": "direct"}))
        assert sent[0]["type"] == "task.dispatch"
        assert sent[0]["task"]["meta"].get("pipeline_pr") is (True if enabled else None)
    finally:
        hub.store.close()


@pytest.mark.parametrize(("enabled", "outcome"), [(False, "ok"), (True, "ok"), (True, "failed"), (True, "waiting")])
async def test_runner_attaches_pipeline_files_only_when_the_gateway_asks(tmp_path, monkeypatch, enabled, outcome):
    """Off unless the gateway asks (#300), and only from a finished successful turn (#301 review)."""
    from labhq.runner.daemon import Runner

    configured = settings(tmp_path)
    for name in ("agents_dir", "talent_dir"):
        setattr(configured.runner, name, str(tmp_path / name))
    runner = Runner(configured)
    agent = AgentSpec(id="bioinfo-agent", name="bio", role="pipeline", engine=Engine.claude_code, builtin_mcp=[])

    class Adapter:
        async def run(self, ctx):
            folder = Path(ctx.workdir) / "outputs" / "pipeline" / "tiny"
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_text(json.dumps({"schema": 1, "name": "tiny"}), encoding="utf-8")
            for name in ("main.nf", "nextflow.config", "README.md"):
                (folder / name).write_text("x\n", encoding="utf-8")
            if outcome == "waiting":  # an HPC job is still out: the bundle is not validated yet
                runner.jobs["job-1"] = {"task_id": ctx.task.id}
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=outcome != "failed", text="done")

    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)
    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    meta = {"kind": "direct", **({"pipeline_pr": True} if enabled else {})}
    result = await runner.run_task(Task(id="t", request_id="r", agent_id=agent.id, prompt="build", meta=meta))
    if enabled and outcome == "ok":
        assert result.pipeline_submission["state"] == "ready"
    else:
        assert result.pipeline_submission is None
        assert "pipeline_submission" not in result.model_dump(mode="json")


async def test_gateway_ignores_a_bundle_from_a_failed_or_unfinished_turn(tmp_path):
    hub = Hub(settings(tmp_path, pipeline_pr=True))
    hub.requests["r"] = {"id": "r", "status": "running"}
    try:
        for tid, extra in (("t1", {"ok": False}), ("t2", {"ok": True, "pending_jobs": ["job-1"]})):
            result = TaskResult(task_id=tid, agent_id="bioinfo-agent", text="", pipeline_submission=pipeline(), **extra)
            await hub.on_runner_message("runner", {"type": "task.result", "task_id": tid, "request_id": "r",
                                                   "data": result.model_dump(mode="json")})
            assert hub.store.get("pipeline_submission", tid) is None
        assert not [e for e in hub.events if e["type"] == "pipeline.ready"]
    finally:
        hub.store.close()
