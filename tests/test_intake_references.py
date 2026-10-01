"""#36 PR A: reference pointers (GitHub, DOI, PMID, URL, runner path) reach briefing, plan and steps.

Nothing is uploaded or cloned. A path reference is read-only and must sit inside a runner reference root.
"""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from labhq.gateway.server import RequestIn, create_app
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.daemon import Runner
from labhq.settings import DataZone, ProjectSettings, Settings


def ref(kind, value, note=None):
    from labhq.intake import Reference

    return Reference(kind=kind, value=value, note=note)


@pytest.mark.parametrize("kind,value,expected", [
    ("github", "scverse/scanpy", "https://github.com/scverse/scanpy"),
    ("github", "https://github.com/scverse/scanpy.git", "https://github.com/scverse/scanpy"),
    ("github", "https://www.github.com/scverse/scanpy/", "https://github.com/scverse/scanpy"),
    ("github", "scverse/scanpy@v1.10", "https://github.com/scverse/scanpy/tree/v1.10"),
    ("github", "https://github.com/nf-core/rnaseq/tree/dev/docs", "https://github.com/nf-core/rnaseq/tree/dev/docs"),
    ("doi", "doi:10.1038/nature12373", "10.1038/nature12373"),
    ("doi", "https://doi.org/10.1186/s13059-017-1382-0", "10.1186/s13059-017-1382-0"),
    ("pmid", "PMID: 29409532", "29409532"),
    ("url", "https://example.org/protocol?v=2#s3", "https://example.org/protocol?v=2#s3"),
    ("path", "/srv/refs/notes/", "/srv/refs/notes"),
    ("path", "C:\\refs\\notes", "C:\\refs\\notes"),
])
def test_reference_values_are_normalized_per_kind(kind, value, expected):
    assert ref(kind, value).value == expected


@pytest.mark.parametrize("kind,value", [
    ("github", "https://gitlab.com/owner/repo"), ("github", "owner"), ("github", "owner/repo/extra"),
    ("github", "owner/.."), ("github", "owner/repo@../main"), ("github", "https://github.com/o/r?tab=x"),
    ("doi", "10.12/short-prefix"), ("doi", "nature12373"), ("pmid", "0123"), ("pmid", "12a"),
    ("url", "ftp://example.org/x"), ("url", "javascript:alert(1)"), ("url", "https://user:pw@example.org/"),
    ("url", "https:///no-host"), ("url", "https://example.org/a b"),
    ("path", "relative/dir"), ("path", "/srv/refs/../etc"), ("path", "\\\\server\\share\\refs"),
    ("path", "C:refs"), ("path", "/srv/refs\nnext"), ("bogus", "x"),
])
def test_invalid_references_are_rejected(kind, value):
    with pytest.raises(ValidationError):
        ref(kind, value)


def test_reference_note_is_one_bounded_line():
    assert ref("pmid", "1", "  line one\n\tline two ").note == "line one line two"
    with pytest.raises(ValidationError):
        ref("pmid", "1", "x" * 301)
    with pytest.raises(ValidationError):
        RequestIn(text="t", references=[{"kind": "pmid", "value": str(i)} for i in range(1, 22)])
    with pytest.raises(ValidationError):
        RequestIn(text="t", references=[{"kind": "pmid", "value": "1", "extra": "x"}])


def settings_with_roots(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    s.runner.reference_roots = [str(tmp_path / "refs")]
    s.projects = [ProjectSettings(id="p", local_dir=str(tmp_path / "project"))]
    s.policy.data_zones = [DataZone(path=str(tmp_path / "refs" / "restricted"))]
    return s


def test_gateway_stores_references_with_pi_defaults_and_checks_runner_roots(tmp_path):
    s = settings_with_roots(tmp_path)
    s.pi_profile.references = [ref("github", "lab/protocols", "lab rules"), ref("pmid", "29409532")]
    client = TestClient(create_app(s))
    hub = client.app.state.hub
    auth = {"Authorization": f"Bearer {s.gateway.client_token}"}
    good = [{"kind": "path", "value": str(tmp_path / "refs" / "yuan")}, {"kind": "pmid", "value": "29409532"},
            {"kind": "path", "value": str(tmp_path / "project" / "docs"), "note": "spec"}]
    rid = client.post("/api/requests", json={"text": "t", "mode": "plan_only", "references": good},
                      headers=auth).json()["request_id"]
    refs = hub.requests[rid]["references"]
    assert [(r["kind"], r["source"]) for r in refs] == [
        ("path", "request"), ("pmid", "request"), ("path", "request"), ("github", "pi_profile")]
    assert refs[2]["note"] == "spec" and refs[3]["note"] == "lab rules"  # the duplicate PMID is kept once
    off = client.post("/api/requests", json={"text": "t", "mode": "plan_only", "default_references": False},
                      headers=auth).json()["request_id"]
    assert hub.requests[off]["references"] == []
    for bad, reason in ((tmp_path / "elsewhere", "outside"), (tmp_path / "refs" / "restricted" / "x", "restricted"),
                        (tmp_path / "refs", "contains"), (tmp_path / "refs2", "outside")):
        response = client.post("/api/requests", json={"text": "t", "references": [{"kind": "path", "value": str(bad)}]},
                               headers=auth)
        assert response.status_code == 422, (reason, response.text)
    assert client.post("/api/requests", json={"text": "t", "references": [{"kind": "doi", "value": "nope"}]},
                       headers=auth).status_code == 422


def test_pi_profile_path_outside_roots_blocks_requests_loudly(tmp_path):
    s = settings_with_roots(tmp_path)
    s.pi_profile.references = [ref("path", str(tmp_path / "private"))]
    client = TestClient(create_app(s))
    response = client.post("/api/requests", json={"text": "t"},
                           headers={"Authorization": f"Bearer {s.gateway.client_token}"})
    assert response.status_code == 422 and "pi_profile" in response.text


class FakeHub:
    def __init__(self, references, mode="orchestrate"):
        self.s = Settings()
        self.s.orchestrator.reviewer_agent = None
        self.requests = {"r": {"text": "Compare cohorts", "mode": mode, "agent_id": "worker",
                               "references": references}}
        self.agents = {name: {"id": name, "name": name, "role": "test", "engine": "mock"}
                       for name in ("cso", "chief_of_staff", "worker")}
        self.calls, self.events = [], []

    async def dispatch(self, task):
        self.calls.append(task)
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured={
                "clarifying_questions": [], "recruit": [], "notes": "",
                "steps": [{"id": "s1", "agent_id": "worker", "instruction": "work", "depends_on": []}]})
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="done")

    async def publish(self, event):
        self.events.append(event)

    async def request_approval(self, **kwargs):
        return {"approved": True, "note": "ok"}

    def supports_resume(self, agent_id):
        return False

    def result_map(self, rid):
        return {}

    def save_request(self, rid):
        pass


STORED = [{"kind": "github", "value": "https://github.com/lab/protocols", "note": "lab rules", "source": "pi_profile"},
          {"kind": "doi", "value": "10.1038/nature12373", "note": None, "source": "request"},
          {"kind": "pmid", "value": "29409532", "note": None, "source": "request"},
          {"kind": "url", "value": "https://example.org/protocol", "note": None, "source": "request"},
          {"kind": "path", "value": "/srv/refs/yuan", "note": "Yuan lessons", "source": "request"}]


@pytest.mark.asyncio
async def test_briefing_plan_and_step_prompts_carry_pointers_and_read_only_paths():
    hub = FakeHub(STORED)
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "done", hub.requests["r"]
    kinds = {t.meta["kind"]: t for t in hub.calls}
    assert set(kinds) >= {"briefing", "plan", "step", "synthesis"}
    for kind in ("briefing", "plan", "step"):
        prompt = kinds[kind].prompt
        assert "Reference pointers from the PI" in prompt, kind
        assert "https://github.com/lab/protocols (branch: repository default; not cloned) — lab rules (PI default)" in prompt
        assert "[doi] 10.1038/nature12373 — https://doi.org/10.1038/nature12373" in prompt
        assert "[pmid] 29409532 — https://pubmed.ncbi.nlm.nih.gov/29409532/" in prompt
        assert "[url] https://example.org/protocol" in prompt
        assert "[path] /srv/refs/yuan (read-only on the runner) — Yuan lessons" in prompt
        assert kinds[kind].meta["reference_dirs"] == ["/srv/refs/yuan"], kind
        assert "Never write, move or delete anything under a [path] reference" in prompt
    assert "Reference pointers" not in hub.requests["r"]["text"]


@pytest.mark.asyncio
async def test_direct_mode_and_requests_without_references_are_unchanged():
    hub = FakeHub(STORED[4:], mode="direct")
    await Orchestrator(hub).run_request("r")
    direct = hub.calls[0]
    assert direct.meta["kind"] == "direct" and direct.meta["reference_dirs"] == ["/srv/refs/yuan"]
    assert "[path] /srv/refs/yuan" in direct.prompt
    plain = FakeHub([], mode="direct")
    await Orchestrator(plain).run_request("r")
    assert plain.calls[0].prompt == "Compare cohorts" and "reference_dirs" not in plain.calls[0].meta


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", [Engine.claude_code, Engine.codex])
async def test_runner_adds_reference_paths_read_only_and_drops_unsafe_ones(tmp_path, monkeypatch, engine):
    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.runner.agents_dir = str(tmp_path / "agents")
    settings.runner.talent_dir = str(tmp_path / "talent")
    settings.runner.reference_roots = [str(tmp_path / "refs")]
    settings.projects = [ProjectSettings(id="p", local_dir=str(tmp_path / "project"))]
    for name in ("refs/yuan", "refs/papers", "project/docs", "outside"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "refs" / "papers" / "a.pdf").write_text("pdf", encoding="utf-8")
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=engine, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)
    seen = {}

    class Adapter:
        async def run(self, ctx):
            seen["ctx"] = ctx
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    refs = [str(tmp_path / "refs" / "yuan"), str(tmp_path / "refs" / "papers" / "a.pdf"),
            str(tmp_path / "outside"), str(tmp_path / "refs" / "missing"), str(tmp_path / "project" / "docs")]
    task = Task(id="task-1", request_id="r1", agent_id=agent.id, prompt="test",
                meta={"reference_dirs": refs, "project_dirs": [str(tmp_path / "project")]})
    await runner.run_task(task)
    ctx = seen["ctx"]
    expected = [str((tmp_path / "refs" / "yuan").resolve()), str((tmp_path / "refs" / "papers").resolve())]
    assert ctx.read_dirs == expected  # project/docs is already reachable through the writable project dir
    assert not set(expected) & set(ctx.env["LABHQ_EXTRA_ROOTS"].split(os.pathsep))
    deny = ctx.claude_settings["permissions"]["deny"]
    for directory in expected:
        from labhq.policy import claude_rule_path
        assert f"Edit(/{claude_rule_path(directory)}/**)" in deny and f"Write(/{claude_rule_path(directory)}/**)" in deny
    texts = [e["data"].get("text", "") for e in runner.store.pending() if e["type"] == "agent.log"]
    assert len([t for t in texts if t.startswith("참고 경로 제외")]) == 2  # outside the roots, and missing
    # Pre-approved shell commands are not sandboxed: a writable reference gets one honest warning per directory.
    writable = [t for t in texts if "쓰기 가능" in t]
    assert len(writable) == 2 and all("OS 권한" in t for t in writable)
    manifest = json.loads((runner.workspaces[task.id].dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["runs"][task.id]["reference_dirs"] == expected
    await runner.run_task(Task(id="task-2", request_id="r1", agent_id=agent.id, prompt="again",
                               meta={"reference_dirs": refs[:1]}))
    texts = [e["data"].get("text", "") for e in runner.store.pending() if e["type"] == "agent.log"]
    assert len([t for t in texts if "쓰기 가능" in t]) == 2, "warned once per directory"


def test_shell_writes_into_a_reference_dir_go_to_the_pi_when_the_gate_sees_them(tmp_path):
    from labhq.policy import evaluate_tool

    workdir, reference = str(tmp_path / "ws"), str(tmp_path / "refs" / "yuan")
    decision = evaluate_tool("Bash", {"command": f"python make.py > {reference}/cache.tsv"}, Settings().policy,
                             allowed_roots=[workdir], workdir=workdir)
    assert decision.action == "ask"  # reference dirs are never write roots (LABHQ_EXTRA_ROOTS)
    assert evaluate_tool("Write", {"file_path": f"{reference}/x.md"}, Settings().policy,
                         allowed_roots=[workdir], workdir=workdir).action == "ask"


def test_claude_adds_reference_dirs_but_codex_gets_no_write_access(tmp_path):
    from labhq.adapters import get_adapter
    from labhq.adapters.base import RunContext

    settings = Settings()
    read_dir = str(tmp_path / "refs")
    for engine in (Engine.claude_code, Engine.codex):
        agent = AgentSpec(id="worker", name="Worker", role="test", engine=engine, builtin_mcp=[])
        task = Task(agent_id="worker", prompt="p")
        ctx = RunContext(task=task, agent=agent, workdir=tmp_path, settings=settings, mcp_servers=[], env={},
                         emit=None, prompt="p", extra_dirs=[], read_dirs=[read_dir])
        command = get_adapter(engine, settings).build_command(ctx)
        pairs = list(zip(command, command[1:]))
        assert (("--add-dir", read_dir) in pairs) is (engine == Engine.claude_code)


def test_cli_send_parses_repeated_refs(monkeypatch, capsys):
    from labhq.cli import main

    sent = {}

    def api(_settings, method, path, **kwargs):
        sent.update(method=method, path=path, **kwargs)
        return {"request_id": "req_1"}

    monkeypatch.setattr("labhq.cli._api", api)
    main(["send", "Compare cohorts", "--no-wait", "--ref", "scverse/scanpy", "--ref", "doi:10.1038/nature12373",
          "--ref", "PMID:29409532", "--ref", "https://example.org/p", "--ref", "path:C:\\refs\\yuan",
          "--ref", "/srv/refs/papers", "--ref", "10.1186/s13059-017-1382-0", "--no-default-refs"])
    body = sent["json"]
    assert body["references"] == [
        {"kind": "github", "value": "scverse/scanpy"}, {"kind": "doi", "value": "10.1038/nature12373"},
        {"kind": "pmid", "value": "29409532"}, {"kind": "url", "value": "https://example.org/p"},
        {"kind": "path", "value": "C:\\refs\\yuan"}, {"kind": "path", "value": "/srv/refs/papers"},
        {"kind": "doi", "value": "10.1186/s13059-017-1382-0"}]
    assert body["default_references"] is False
    with pytest.raises(SystemExit):
        main(["send", "x", "--no-wait", "--ref", "???"])


def test_example_configs_ship_empty_pi_profile_and_reference_roots():
    import yaml

    root = Path(__file__).resolve().parents[1]
    for path in (root / "config" / "labhq.example.yaml", root / "labhq" / "config" / "labhq.example.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["pi_profile"] == {"references": []}
        assert data["runner"]["reference_roots"] == []
        Settings.model_validate(data)


def test_cli_and_web_share_reference_kind_inference():
    from labhq.intake import infer_reference

    cases = json.loads((Path(__file__).parent / "fixtures" / "reference_inference.json").read_text(encoding="utf-8"))
    for text, expected in cases:
        assert infer_reference(text) == expected, text


def test_project_reports_never_carry_reference_paths(tmp_path):
    s = settings_with_roots(tmp_path)
    private = tmp_path / "refs" / "yuan"
    s.pi_profile.references = [ref("path", str(private), "private lessons")]
    hub = create_app(s).state.hub
    hub.requests["r"] = {"references": [{"kind": "path", "value": str(tmp_path / "project" / "spec"),
                                         "source": "request"}]}
    text = (f"Read {private}/notes.md and {(tmp_path / 'project' / 'spec').as_posix()}/a.md; "
            "keep https://github.com/lab/protocols")
    cleaned = hub.reporter._clean(text)
    assert str(private) not in cleaned and private.as_posix() not in cleaned
    assert "spec/a.md" not in cleaned and cleaned.count("<reference-path>") == 2
    assert "https://github.com/lab/protocols" in cleaned
