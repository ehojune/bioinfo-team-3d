import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from labhq.gateway.server import RequestIn, create_app
from labhq.models import ASK_MAX_WAIT_S, ASK_WAIT_SECONDS, AgentSpec, AskRequest, Engine, Task, TaskResult
from labhq.orchestrator.cso import hard_stop_kind
from labhq.runner.approvals import Broker
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.util import free_port


REPO = Path(__file__).resolve().parents[1]


def _junction(link: Path, target: Path) -> None:
    if os.name == "nt":  # a junction needs no symlink privilege
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


async def _until(predicate, timeout=20.0):
    started = time.time()
    while not predicate():
        assert time.time() - started < timeout, "timed out"
        await asyncio.sleep(0.03)


def test_ask_contract_and_hard_stops_are_code_enforced():
    assert ASK_WAIT_SECONDS == {"cso": 300, "facilities": 1200, "colleague": 900, "pi": 0}
    ask = AskRequest(task_id="task_a", agent_id="analyst", request_id="req_a", to="cso",
                     question="어느 표본군을 쓰나요?", why_blocked="계획에 표본군이 없습니다")
    assert ask.wait == "short"
    assert hard_stop_kind(ask.model_copy(update={"question": "새 package를 설치해도 되나요?"})) == "installation"
    assert hard_stop_kind(ask.model_copy(update={"question": "통제 데이터 구역 원본을 읽어도 되나요?"})) == "data_zone"
    assert hard_stop_kind(ask.model_copy(update={"question": "예산 상한을 넘겨 계속할까요?"})) == "budget_cap"
    assert hard_stop_kind(ask.model_copy(update={"question": "요청 범위 밖 분석을 추가할까요?"})) == "out_of_scope"
    assert hard_stop_kind(ask.model_copy(update={"question": "결과 폴더를 재귀 삭제할까요?"})) == "destructive"
    assert hard_stop_kind(ask.model_copy(update={"question": "May I cross the restricted data zone?"})) == "data_zone"
    assert hard_stop_kind(ask.model_copy(update={"question": "The cost cap was exceeded; continue?"})) == "budget_cap"
    assert hard_stop_kind(ask) is None
    with pytest.raises(ValueError):
        AskRequest(task_id="t", agent_id="a", to="cso", question="x" * 701, why_blocked="blocked")
    with pytest.raises(ValueError):
        AskRequest(task_id="t", agent_id="a", to="unknown", question="x", why_blocked="blocked")


@pytest.mark.parametrize(
    ("seed", "current", "message"),
    [
        ([('task_a', 'cso', 'req_a'), ('task_a', 'cso', 'req_a')],
         ('task_a', 'cso', 'req_a'), "같은 대상"),
        ([('task_b', 'cso', 'req_b'), ('task_b', 'facilities', 'req_b'),
          ('task_b', 'colleague:data_steward', 'req_b')],
         ('task_b', 'colleague:analyst', 'req_b'), "task의 질의 상한"),
        ([(f'task_{i}', 'cso', 'req_c') for i in range(12)],
         ('task_new', 'cso', 'req_c'), "요청의 질의 상한"),
    ],
)
async def test_ask_caps_are_enforced_before_consult(tmp_path, seed, current, message):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    for i, (task_id, target, request_id) in enumerate(seed):
        previous = AskRequest(id=f"ask_seed_{i}", task_id=task_id, agent_id="worker",
                              request_id=request_id, to=target, question=f"question {i}",
                              why_blocked="blocked")
        hub.store.put("ask", previous.id, {"state": "resolved",
                                           "ask": previous.model_dump(mode="json"),
                                           "answer": {"status": "answered", "answer": "ok"}})
    task_id, target, request_id = current
    ask = AskRequest(task_id=task_id, agent_id="worker", request_id=request_id,
                     to=target, question="new question", why_blocked="blocked")
    hub.store.put("ask", ask.id, {"state": "working", "origin": "runner",
                                  "ask": ask.model_dump(mode="json")})
    await hub.orchestrator.answer_ask(ask, "runner")
    answer = hub.store.get("ask", ask.id)["answer"]
    assert answer["status"] == "rejected"
    assert message in answer["reason"]
    assert "answer" not in answer


def test_ask_mcp_timeout_exceeds_longest_wait(tmp_path):
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    runner = Runner(settings)
    try:
        agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.codex,
                          builtin_mcp=[])
        servers = runner._mcp_servers(agent, {"LABHQ_TASK_ID": "task_a"})
        ask = next(server for server in servers if server.name == "labhq_ask")
        assert ask.timeout_s and ask.timeout_s > ASK_MAX_WAIT_S
        assert not any(server.name == "labhq_ask" for server in
                       runner._mcp_servers(agent, {}, allow_ask=False))
    finally:
        runner.store.close()


async def test_broker_capability_token_cannot_impersonate_another_task():
    seen = []

    async def record(body):
        seen.append(body)

    broker = Broker(0, record, record, record, record)
    token = broker.issue_task_token("task_a", "analyst", "req_a")
    transport = httpx.ASGITransport(app=broker.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
        bad = await client.post("/event", headers={"X-Labhq-Token": token},
                                json={"task_id": "task_b", "agent_id": "analyst", "type": "agent.log"})
        assert bad.status_code == 403
        good = await client.post("/event", headers={"X-Labhq-Token": token},
                                 json={"task_id": "task_a", "agent_id": "analyst", "type": "agent.log"})
        assert good.status_code == 200
    assert seen[-1]["task_id"] == "task_a" and seen[-1]["request_id"] == "req_a"


async def test_broker_attaches_the_task_workdir_to_an_ask(tmp_path):
    seen = []

    async def record(body):
        seen.append(body)

    broker = Broker(0, record, record, record, record)
    workdir = str(tmp_path / "runs" / "task_a")
    token = broker.issue_task_token("task_a", "analyst", "req_a", workdir=workdir)
    transport = httpx.ASGITransport(app=broker.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
        response = await client.post("/ask", headers={"X-Labhq-Token": token}, json={
            "to": "cso", "question": "Read the table?", "why_blocked": "Need its values",
            "refs": ["outputs/table.tsv"], "wait": "hibernate",
        })
    assert response.status_code == 200
    assert seen[0].source_workdir == workdir


async def test_consult_reads_the_ask_workspace_and_verified_refs(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    hub.agents = {"cso": {"engine": "mock"}}
    hub.agent_runner = {"cso": "runner"}
    hub.requests["r"] = {"id": "r", "text": "study", "status": "running"}
    source = tmp_path / "runs" / "source"
    (source / "outputs").mkdir(parents=True)
    (source / "outputs" / "table.tsv").write_text("n\n3\n", encoding="utf-8")
    calls = []

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="three")

    hub.dispatch = dispatch
    ask = AskRequest(task_id="source", agent_id="worker", request_id="r", to="cso",
                     question="How many?", why_blocked="Need the artifact",
                     refs=["outputs/table.tsv"], source_workdir=str(source))
    try:
        await hub.orchestrator.answer_ask(ask, "runner")
        consult = calls[0]
        assert consult.meta["source_workdir"] == str(source)
        assert consult.meta["consult_refs"] == ["outputs/table.tsv"]
        assert "outputs/table.tsv" in consult.prompt
        assert consult.meta.get("workdir") != str(source.resolve()), "consult writes only to its own workspace"
    finally:
        hub.store.close()


async def test_consult_cache_signature_includes_normalized_refs(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    hub.agents = {"cso": {"engine": "mock"}}
    hub.agent_runner = {"cso": "runner"}
    hub.requests["r"] = {"id": "r", "text": "study", "status": "running"}
    calls = []

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id="cso", ok=True,
                          text=task.meta["consult_refs"][0])

    hub.dispatch = dispatch
    common = dict(task_id="source", agent_id="worker", request_id="r", to="cso",
                  question="What does it say?", why_blocked="Need the artifact",
                  source_workdir=str(tmp_path / "runs" / "source"))
    try:
        await hub.orchestrator.answer_ask(AskRequest(**common, refs=["outputs\\a.tsv"]), "runner")
        second = AskRequest(**common, refs=["outputs/b.tsv"])
        await hub.orchestrator.answer_ask(second, "runner")
        assert len(calls) == 2
        answer = hub.store.get("ask", second.id)["answer"]
        assert answer["answer"] == "outputs/b.tsv" and not answer.get("cached")
    finally:
        hub.store.close()


async def test_gateway_does_not_resolve_a_runner_workdir_when_refs_are_empty(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    hub.agents = {"cso": {"engine": "mock"}}
    hub.agent_runner = {"cso": "runner"}
    hub.requests["r"] = {"id": "r", "text": "study", "status": "running"}
    calls = []

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="answer")

    hub.dispatch = dispatch
    ask = AskRequest(task_id="source", agent_id="worker", request_id="r", to="cso",
                     question="General question?", why_blocked="Need advice", refs=[],
                     source_workdir="Z:/runner-only/missing")
    try:
        await hub.orchestrator.answer_ask(ask, "runner")
        answer = hub.store.get("ask", ask.id)["answer"]
        assert answer["status"] == "answered"
        assert len(calls) == 1
    finally:
        hub.store.close()


async def test_consult_on_another_runner_withholds_the_source_workspace(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    hub.agents = {"cso": {"engine": "mock"}}
    hub.agent_runner = {"cso": "runner-b"}
    hub.requests["r"] = {"id": "r", "text": "study", "status": "running"}
    calls = []

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id="cso", ok=True, text="answer")

    hub.dispatch = dispatch
    ask = AskRequest(task_id="source", agent_id="worker", request_id="r", to="cso",
                     question="Read it?", why_blocked="Need the artifact", refs=["outputs/table.tsv"],
                     source_workdir="C:/runner-a/runs/source")
    try:
        await hub.orchestrator.answer_ask(ask, "runner-a")
        consult = calls[0]
        assert "source_workdir" not in consult.meta
        assert "consult_refs" not in consult.meta
        assert "참고 파일은 다른 runner에 있어 읽을 수 없다" in consult.prompt
    finally:
        hub.store.close()


async def test_runner_stages_only_declared_consult_refs(tmp_path, monkeypatch):
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    runner = Runner(settings)
    agent = AgentSpec(id="cso", name="CSO", role="test", engine=Engine.mock, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)
    source = Path(settings.runner.workspace_root) / "source"
    (source / ".claude" / "rules").mkdir(parents=True)
    ref = source / "outputs" / "table.tsv"
    ref.parent.mkdir()
    ref.write_text("n\n3\n", encoding="utf-8")
    (source / "secret.tsv").write_text("not declared", encoding="utf-8")
    (source / "CLAUDE.md").write_text("untrusted instruction", encoding="utf-8")
    (source / ".claude" / "rules" / "policy.md").write_text("untrusted rule", encoding="utf-8")
    calls = []

    class Adapter:
        async def run(self, ctx):
            calls.append(ctx)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="answer")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())
    task = Task(agent_id="cso", prompt="Read table.tsv", meta={
        "kind": "consult", "source_workdir": str(source),
        "consult_refs": ["outputs/table.tsv"]})
    try:
        result = await runner.run_task(task)
        assert result.ok
        ctx = calls[0]
        assert str(source.resolve()) not in ctx.extra_dirs
        staged = ctx.workdir / "refs" / f"consult-{task.id}"
        files = [path for path in staged.rglob("*") if path.is_file()]
        assert [path.name for path in files] == ["ref-01.tsv"]
        assert files[0].read_text(encoding="utf-8") == "n\n3\n"
        assert "refs/" in ctx.task.prompt and "ref-01.tsv" in ctx.task.prompt
        source_text = str(source.resolve())
        manifest = (ctx.workdir / "manifest.json").read_text(encoding="utf-8")
        assert source_text not in ctx.task.prompt and source_text not in manifest
        assert "source_workdir" not in ctx.task.meta and "consult_refs" not in ctx.task.meta
    finally:
        runner.store.close()


async def test_runner_skips_ref_when_parent_becomes_a_link_after_validation(tmp_path, monkeypatch):
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    runner = Runner(settings)
    agent = AgentSpec(id="cso", name="CSO", role="test", engine=Engine.mock, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)
    source = Path(settings.runner.workspace_root) / "source"
    parent = source / "outputs"
    parent.mkdir(parents=True)
    (parent / "table.tsv").write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "table.tsv").write_text("secret", encoding="utf-8")
    original = runner._consult_workspace_input

    def swap_after_validation(task):
        result = original(task)
        moved = source / "original-outputs"
        parent.rename(moved)
        try:
            _junction(parent, outside)
        except OSError as error:
            moved.rename(parent)
            pytest.skip(f"this OS account cannot create the directory link ({error})")
        return result

    monkeypatch.setattr(runner, "_consult_workspace_input", swap_after_validation)
    calls = []

    class Adapter:
        async def run(self, ctx):
            calls.append(ctx)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="answer")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())
    task = Task(agent_id="cso", prompt="Read the ref", meta={
        "kind": "consult", "source_workdir": str(source), "consult_refs": ["outputs/table.tsv"]})
    try:
        result = await runner.run_task(task)
        assert result.ok and len(calls) == 1
        staged = calls[0].workdir / "refs" / f"consult-{task.id}"
        assert list(staged.iterdir()) == []
        assert "참고 파일 제외" in calls[0].task.prompt
        assert "secret" not in calls[0].task.prompt
    finally:
        runner.store.close()


@pytest.mark.parametrize("raw_ref", ["../outside.tsv", "folder"])
async def test_runner_rejects_non_file_consult_refs(tmp_path, monkeypatch, raw_ref):
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    runner = Runner(settings)
    agent = AgentSpec(id="cso", name="CSO", role="test", engine=Engine.mock, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)
    source = Path(settings.runner.workspace_root) / "source"
    (source / "folder").mkdir(parents=True)
    calls = []

    class Adapter:
        async def run(self, ctx):
            calls.append(ctx)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="answer")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())
    task = Task(agent_id="cso", prompt="Read the ref", meta={
        "kind": "consult", "source_workdir": str(source), "consult_refs": [raw_ref]})
    try:
        result = await runner.run_task(task)
        assert not result.ok and "consult refs" in (result.error or "")
        assert calls == []
    finally:
        runner.store.close()


async def test_runner_renames_instruction_named_consult_refs(tmp_path, monkeypatch):
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    runner = Runner(settings)
    agent = AgentSpec(id="cso", name="CSO", role="test", engine=Engine.mock, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)
    source = Path(settings.runner.workspace_root) / "source"
    (source / ".claude" / "rules").mkdir(parents=True)
    refs = ["CLAUDE.md", "AGENTS.md", "GEMINI.md", ".claude/rules/policy.md"]
    for raw_ref in refs:
        path = source / raw_ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw_ref, encoding="utf-8")
    calls = []

    class Adapter:
        async def run(self, ctx):
            calls.append(ctx)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="answer")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())
    task = Task(agent_id="cso", prompt="Read the refs", meta={
        "kind": "consult", "source_workdir": str(source), "consult_refs": refs})
    try:
        result = await runner.run_task(task)
        assert result.ok
        staged = calls[0].workdir / "refs" / f"consult-{task.id}"
        names = {path.name.casefold() for path in staged.iterdir()}
        assert names == {f"ref-{number:02d}.md" for number in range(1, 5)}
        assert not {"claude.md", "agents.md", "gemini.md"} & names
        assert ".claude" not in {part.casefold() for path in staged.rglob("*") for part in path.parts}
    finally:
        runner.store.close()


@pytest.mark.parametrize("cap", ["file", "total"])
async def test_runner_skips_consult_refs_over_size_caps(tmp_path, monkeypatch, cap):
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    runner = Runner(settings)
    agent = AgentSpec(id="cso", name="CSO", role="test", engine=Engine.mock, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda task: agent)
    monkeypatch.setattr("labhq.runner.daemon.CONSULT_REF_MAX_FILE_BYTES", 4)
    monkeypatch.setattr("labhq.runner.daemon.CONSULT_REFS_MAX_TOTAL_BYTES", 6)
    source = Path(settings.runner.workspace_root) / "source"
    source.mkdir(parents=True)
    (source / "first.txt").write_bytes(b"1234")
    (source / "second.txt").write_bytes(b"56789" if cap == "file" else b"5678")
    calls = []

    class Adapter:
        async def run(self, ctx):
            calls.append(ctx)
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="answer")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *args: Adapter())
    task = Task(agent_id="cso", prompt="Read refs", meta={
        "kind": "consult", "source_workdir": str(source),
        "consult_refs": ["first.txt", "second.txt"]})
    try:
        result = await runner.run_task(task)
        assert result.ok
        staged = calls[0].workdir / "refs" / f"consult-{task.id}"
        assert [path.name for path in staged.iterdir()] == ["ref-01.txt"]
        assert "참고 파일 제외" in calls[0].task.prompt
        assert "second.txt" in calls[0].task.prompt and "상한" in calls[0].task.prompt
    finally:
        runner.store.close()


async def test_fake_mcp_client_gets_one_terminal_answer(tmp_path):
    port = free_port()
    holder = {}

    async def noop(_):
        return None

    async def answer(req):
        await asyncio.sleep(0.02)
        holder["broker"].resolve_ask(req.id, {"answer": "cases를 사용하세요", "from": "cso"})

    broker = Broker(port, noop, noop, noop, answer)
    holder["broker"] = broker
    token = broker.issue_task_token("task_a", "analyst", "req_a")
    server_task = asyncio.create_task(broker.serve())
    try:
        await _until(lambda: broker.server is not None and broker.server.started)
        env = {**os.environ, "PYTHONPATH": str(REPO), "LABHQ_BROKER_URL": broker.url,
               "LABHQ_BROKER_TOKEN": token, "LABHQ_TASK_ID": "task_a",
               "LABHQ_AGENT_ID": "analyst", "LABHQ_WORKDIR": str(tmp_path)}
        params = StdioServerParameters(command=sys.executable, args=["-m", "labhq.tools.ask_mcp"], env=env)
        async with stdio_client(params) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = await session.call_tool("ask", {"to": "cso", "question": "어느 군인가요?",
                                                          "why_blocked": "표본군이 없습니다"})
        payload = json.loads(result.content[0].text)
        assert payload["answer"] == "cases를 사용하세요"
        assert payload["from"] == "cso" and payload["status"] == "answered"
    finally:
        broker.stop()
        await asyncio.sleep(0.05)
        server_task.cancel()


async def test_mock_engine_ask_routes_without_deadlock_and_pi_hibernates(tmp_path):
    shutil.copytree(REPO / "agents", tmp_path / "agents")
    settings = Settings()
    settings.gateway.state_dir = settings.runner.state_dir = str(tmp_path / "state")
    gateway_port = free_port()
    settings.gateway.port = gateway_port
    settings.gateway.url = f"ws://127.0.0.1:{gateway_port}"
    settings.runner.broker_port = free_port()
    settings.runner.force_engine = "mock"
    settings.runner.max_parallel = 1
    settings.runner.consult_parallel = 1
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.runner.talent_dir = str(tmp_path / "talent")
    settings.runner.agents_dir = str(tmp_path / "agents")
    settings.hpc.scheduler = "none"

    app = create_app(settings)
    hub = app.state.hub
    seen = []
    publish = hub.publish

    async def tap(event, **kwargs):
        seen.append(event)
        await publish(event, **kwargs)
        if event.get("type") == "approval.requested" and event.get("data", {}).get("kind") == "question":
            async def reply():
                await asyncio.sleep(0.05)
                await hub.resolve_approval(event["data"]["id"], True, "PI가 설치를 승인했습니다")
            asyncio.create_task(reply())

    hub.publish = tap
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=gateway_port, log_level="warning"))
    runner = Runner(settings)
    tasks = [asyncio.create_task(server.serve())]
    try:
        await _until(lambda: server.started)
        tasks.append(asyncio.create_task(runner.run_forever()))
        await _until(lambda: "analyst" in hub.agents and "cso" in hub.agents)

        cso_request = hub.create_request(RequestIn(text="[ask:cso] 표본군 결정", mode="direct", agent_id="analyst"))
        await _until(lambda: hub.requests[cso_request]["status"] != "running")
        assert hub.requests[cso_request]["status"] == "done"
        assert "CSO 답변" in hub.requests[cso_request]["results"]["direct"]["text"]

        facilities_request = hub.create_request(RequestIn(
            text="[ask:facilities] 도구 경로 확인", mode="direct", agent_id="analyst"))
        await _until(lambda: hub.requests[facilities_request]["status"] != "running")
        facilities_answer = next(e for e in seen if e.get("type") == "agent.answer" and
                                 e.get("request_id") == facilities_request)
        assert facilities_answer["data"]["from"] == "cso"
        assert facilities_answer["data"]["routed_to"] == "cso"

        colleague_request = hub.create_request(RequestIn(
            text="[ask:colleague:data_steward] manifest 위치", mode="direct", agent_id="analyst"))
        await _until(lambda: hub.requests[colleague_request]["status"] != "running")
        colleague_answer = next(e for e in seen if e.get("type") == "agent.answer" and
                                e.get("request_id") == colleague_request)
        assert colleague_answer["data"]["from"] == "data_steward"
        consults = [v for v in hub.store.all("task").values()
                    if (v.get("payload") or {}).get("meta", {}).get("ask_id") == colleague_answer["data"]["ask_id"]]
        assert consults and consults[0]["payload"]["meta"]["agent_overrides"]["sandbox"] == "read-only"

        pi_request = hub.create_request(RequestIn(
            text="[ask:pi-install] 새 package 설치", mode="direct", agent_id="analyst"))
        await _until(lambda: hub.requests[pi_request]["status"] not in {"running", "waiting_pi"})  # a PI card parks it (R5)
        assert hub.requests[pi_request]["status"] == "done"
        pi_tasks = [v for v in hub.store.all("task").values()
                    if v.get("request_id") == pi_request and v.get("kind") == "direct"]
        initial = next(v for v in pi_tasks if not (v.get("payload") or {}).get("resume_session_id"))
        resumed = next(v for v in pi_tasks if (v.get("payload") or {}).get("resume_session_id"))
        assert resumed["payload"]["resume_session_id"] == initial["result"]["session_id"]
        assert resumed["payload"]["meta"]["workdir"] == initial["result"]["workdir"]
        assert any(e.get("type") == "agent.ask" and e.get("request_id") == pi_request for e in seen)
        assert any(e.get("type") == "agent.answer" and e.get("request_id") == pi_request for e in seen)
    finally:
        runner.stop()
        server.should_exit = True
        await asyncio.sleep(0.15)
        for task in tasks:
            task.cancel()


@pytest.mark.parametrize("path,body,field", [
    ("/ask", {"to": "cso", "question": "Which cohort?", "why_blocked": "No groups", "wait": "brief"}, "wait"),
    ("/ask", {"to": "nobody", "question": "Which cohort?", "why_blocked": "No groups"}, "to must be"),
    ("/approval", {"summary": "submit"}, "kind"),
])
async def test_broker_answers_invalid_bodies_with_400_and_the_fix(path, body, field):
    # #331: wait="brief" raised an unhandled ValidationError (HTTP 500), read by staff as a broken environment.
    async def record(_):
        return None

    broker = Broker(0, record, record, record, record)
    token = broker.issue_task_token("task_a", "analyst", "req_a")
    transport = httpx.ASGITransport(app=broker.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
        response = await client.post(path, headers={"X-Labhq-Token": token}, json=body)
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert field in detail
    if body.get("wait") == "brief":
        assert "'short'" in detail and "'hibernate'" in detail
    assert broker.pending_asks == {} and broker.pending == {}


async def test_ask_tool_schema_limits_wait_and_relays_a_400(tmp_path):
    async def record(_):
        return None

    port = free_port()
    broker = Broker(port, record, record, record, record)
    token = broker.issue_task_token("task_a", "analyst", "req_a")
    server_task = asyncio.create_task(broker.serve())
    try:
        await _until(lambda: broker.server is not None and broker.server.started)
        env = {**os.environ, "PYTHONPATH": str(REPO), "LABHQ_BROKER_URL": broker.url,
               "LABHQ_BROKER_TOKEN": token, "LABHQ_TASK_ID": "task_a", "LABHQ_AGENT_ID": "analyst"}
        params = StdioServerParameters(command=sys.executable, args=["-m", "labhq.tools.ask_mcp"], env=env)
        async with stdio_client(params) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                tool = next(t for t in tools if t.name == "ask")
                schema = getattr(tool, "input_schema", None) or tool.inputSchema  # mcp>=2 renamed it
                wait = schema["properties"]["wait"]
                result = await session.call_tool("ask", {"to": "nobody", "question": "q?", "why_blocked": "b"})
        assert wait.get("enum") == ["short", "hibernate"] and wait.get("default") == "short"
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "rejected"
        assert "to must be" in payload["reason"] and "broker에 연결하지 못했습니다" not in payload["reason"]
    finally:
        broker.stop()
        await asyncio.sleep(0.05)
        server_task.cancel()
