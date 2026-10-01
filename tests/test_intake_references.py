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
    # Signed URLs carry credentials in the query or fragment: only scheme, host and path are kept.
    ("url", "https://example.org/protocol?v=2#s3", "https://example.org/protocol"),
    ("url", "https://lab.s3.amazonaws.com/a.bam?X-Amz-Signature=ab12&X-Amz-Credential=AKID", "https://lab.s3.amazonaws.com/a.bam"),
    ("url", "https://example.org:8443/share/x.tsv?token=t0k3n#access_token=z", "https://example.org:8443/share/x.tsv"),
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
    # Line breaks a prompt line would honour: NEL, DEL, and the Unicode line/paragraph separators.
    ("path", "/srv/refs\x85next"), ("path", "/srv/refs next"), ("path", "/srv/refs next"),
    ("path", "/srv/refs\x7fnext"), ("url", "https://example.org/a b"),
])
def test_invalid_references_are_rejected(kind, value):
    with pytest.raises(ValidationError):
        ref(kind, value)


def test_reference_note_is_one_bounded_line():
    assert ref("pmid", "1", "  line one\n\tline two ").note == "line one line two"
    assert ref("pmid", "1", "a\x1b[2Jb c\x85d").note == "a[2Jb c d"
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


def test_gateway_root_check_is_lexical_across_operating_systems():
    from labhq.intake import check_reference_path

    # A Windows gateway may serve a POSIX runner and the reverse; Path() on the gateway host would turn
    # `/srv/refs` into `C:\srv\refs` (or `C:\refs` into a relative path) and reject every reference.
    s = Settings()
    s.runner.reference_roots = ["/srv/refs", "C:\\Lab\\refs"]
    s.projects = [ProjectSettings(id="p", local_dir="/home/pi/projects/p1")]
    for value in ("/srv/refs/yuan", "C:\\Lab\\refs\\papers", "c:/lab/refs/notes", "/home/pi/projects/p1/docs"):
        assert check_reference_path(value, s) == value
    for value in ("/srv/other", "/srv/refs2", "D:\\Lab\\refs\\x", "/home/pi/projects/p2",
                  "/home/other/projects/p1/docs"):  # home-relative matching is only for `~` values (#124)
        with pytest.raises(ValueError):
            check_reference_path(value, s)


def _home(monkeypatch, path):
    for name in ("HOME", "USERPROFILE"):  # POSIX and Windows expanduser
        monkeypatch.setenv(name, str(path))


@pytest.mark.asyncio
@pytest.mark.parametrize("root", ["~/refs", "/home/pi/refs"])
async def test_tilde_reference_opens_in_the_runner_accounts_home(tmp_path, monkeypatch, root):
    # #124: a Windows gateway expanded `~` with its own account and stored C:\Users\<gateway>\refs\x; a POSIX
    # runner (WSL, HPC) then looked there and dropped the reference as missing. The gateway's roots may be
    # written for the runner (`~/refs` or `/home/pi/refs`).
    from labhq.intake import reference_dirs, render_references

    gateway_home, runner_home = tmp_path / "gateway-home", tmp_path / "runner-home"
    (runner_home / "refs" / "x").mkdir(parents=True)
    _home(monkeypatch, gateway_home)
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    s.runner.reference_roots = [root]
    s.policy.data_zones = [DataZone(path="~/refs/vault")]
    client = TestClient(create_app(s))
    auth = {"Authorization": f"Bearer {s.gateway.client_token}"}
    rid = client.post("/api/requests", json={"text": "t", "mode": "plan_only", "references": [
        {"kind": "path", "value": "~/refs/x"}]}, headers=auth).json()["request_id"]
    stored = client.app.state.hub.requests[rid]["references"]
    assert stored[0]["value"] == "~/refs/x", "stored as written, for the runner to expand"
    for bad in ("~/refs/vault/raw", "~/other/x"):
        response = client.post("/api/requests", json={"text": "t", "references": [{"kind": "path", "value": bad}]},
                               headers=auth)
        assert response.status_code == 422, bad

    _home(monkeypatch, runner_home)  # the runner, on its own account
    runner_settings = Settings()
    runner_settings.runner.reference_roots = ["~/refs"]
    runner, seen = _reference_runner(tmp_path, monkeypatch, runner_settings)
    await runner.run_task(Task(id="task-h", request_id="r1", agent_id="worker",
                               prompt="Compare" + render_references(stored), meta={"reference_dirs": reference_dirs(stored)}))
    assert seen["ctx"].read_dirs == [str((runner_home / "refs" / "x").resolve())]
    assert f"[path] {os.path.expanduser('~/refs/x')} (read-only on the runner)" in seen["ctx"].prompt
    assert not [t for t in _log_texts(runner) if t.startswith("참고 경로 제외")]


def test_project_reports_mask_a_tilde_reference_wherever_the_runner_expanded_it(tmp_path):
    s = settings_with_roots(tmp_path)
    hub = create_app(s).state.hub
    hub.requests["r"] = {"references": [{"kind": "path", "value": "~/refs/llm-wiki", "source": "request"}]}
    text = ("a ~/refs/llm-wiki/a.md b /home/pi/refs/llm-wiki/b.md c /BiO/home/u01/refs/llm-wiki d "
            r"C:\Users\pi\refs\llm-wiki\d.md e /Users/pi/refs/llm-wiki f $HOME/refs/llm-wiki keep /srv/refs/llm-wiki")
    cleaned = hub.reporter._clean(text)
    assert cleaned.count("<reference-path>") == 6 and "keep /srv/refs/llm-wiki" in cleaned


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


def _link_dir(link: Path, target: Path) -> None:
    if os.name == "nt":  # a junction needs no symlink privilege
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _reference_runner(tmp_path, monkeypatch, settings):
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.claude_code, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)
    seen = {}

    class Adapter:
        async def run(self, ctx):
            seen["ctx"] = ctx
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    return runner, seen


@pytest.mark.asyncio
async def test_runner_blocks_a_restricted_zone_written_through_a_link(tmp_path, monkeypatch):
    # The zone is configured by an alias path; the reference names the real directory, so only the
    # resolved comparison on the runner sees that they are the same place.
    (tmp_path / "refs" / "vault").mkdir(parents=True)
    (tmp_path / "refs" / "notes").mkdir()
    _link_dir(tmp_path / "zone_alias", tmp_path / "refs" / "vault")
    settings = Settings()
    settings.runner.reference_roots = [str(tmp_path / "refs")]
    settings.policy.data_zones = [DataZone(path=str(tmp_path / "zone_alias"))]
    runner, seen = _reference_runner(tmp_path, monkeypatch, settings)
    refs = [str(tmp_path / "refs" / "vault"), str(tmp_path / "refs"), str(tmp_path / "refs" / "notes")]
    await runner.run_task(Task(id="task-z", request_id="r1", agent_id="worker", prompt="p",
                               meta={"reference_dirs": refs}))
    assert seen["ctx"].read_dirs == [str((tmp_path / "refs" / "notes").resolve())]
    texts = [e["data"].get("text", "") for e in runner.store.pending() if e["type"] == "agent.log"]
    assert len([t for t in texts if "통제 데이터 구역" in t]) == 2


@pytest.mark.asyncio
async def test_runner_skips_a_reference_that_holds_the_tasks_writable_folders(tmp_path, monkeypatch):
    # Edit/Write deny rules on a folder that contains the workspace or project would block the task's own work.
    settings = Settings()
    settings.runner.reference_roots = [str(tmp_path)]
    (tmp_path / "lab" / "project").mkdir(parents=True)
    (tmp_path / "notes").mkdir()
    runner, seen = _reference_runner(tmp_path, monkeypatch, settings)
    refs = [str(tmp_path), str(tmp_path / "lab"), str(tmp_path / "notes")]
    await runner.run_task(Task(id="task-w", request_id="r1", agent_id="worker", prompt="p",
                               meta={"reference_dirs": refs, "project_dirs": [str(tmp_path / "lab" / "project")]}))
    ctx = seen["ctx"]
    assert ctx.read_dirs == [str((tmp_path / "notes").resolve())]
    deny = ctx.claude_settings.get("permissions", {}).get("deny", [])
    from labhq.policy import claude_rule_path
    assert not any(claude_rule_path(str(tmp_path.resolve())) + "/**)" in rule for rule in deny)
    texts = [e["data"].get("text", "") for e in runner.store.pending() if e["type"] == "agent.log"]
    assert len([t for t in texts if "품고 있어" in t]) == 2


def _link_file(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as error:  # Windows without Developer Mode or admin rights
        pytest.skip(f"this OS account cannot create file symlinks ({error}); directory junction cases still run")


def _log_texts(runner):
    return [e["data"].get("text", "") for e in runner.store.pending() if e["type"] == "agent.log"]


async def _expose(tmp_path, monkeypatch, settings, reference):
    from labhq.intake import render_references

    runner, seen = _reference_runner(tmp_path, monkeypatch, settings)
    prompt = "Compare cohorts" + render_references([{"kind": "path", "value": str(reference)}])
    prompt += f"\nStep: read {reference}/summary.md first."
    await runner.run_task(Task(id="task-l", request_id="r1", agent_id="worker", prompt=prompt,
                               meta={"reference_dirs": [str(reference)]}))
    return runner, seen["ctx"]


def _vault_settings(tmp_path):
    (tmp_path / "refs" / "notes" / "sub").mkdir(parents=True)
    (tmp_path / "refs" / "notes" / "summary.md").write_text("ok", encoding="utf-8")
    (tmp_path / "vault").mkdir()
    (tmp_path / "vault" / "raw.tsv").write_text("donor\tgenotype", encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()
    settings = Settings()
    settings.runner.reference_roots = [str(tmp_path / "refs")]
    settings.policy.data_zones = [DataZone(path=str(tmp_path / "vault"))]
    return settings


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["vault", "elsewhere"])
async def test_runner_refuses_a_reference_whose_subfolder_links_out(tmp_path, monkeypatch, target):
    # reference/link/raw.tsv would read the restricted zone (or any folder the PI did not point at)
    # through a path no lexical rule recognizes. A junction needs no privilege on Windows.
    settings = _vault_settings(tmp_path)
    notes = tmp_path / "refs" / "notes"
    _link_dir(notes / "sub" / "link", tmp_path / target)
    runner, ctx = await _expose(tmp_path, monkeypatch, settings, notes)
    assert ctx.read_dirs == []
    refused = [t for t in _log_texts(runner) if t.startswith("참고 경로 제외")]
    assert len(refused) == 1 and "sub/link" in refused[0].replace("\\", "/") and "링크" in refused[0]
    assert str(tmp_path / target) not in refused[0], "the link target is not echoed into logs"
    # The pointer is withheld from the prompt too: every engine, not only Claude's --add-dir, follows it.
    assert str(notes) not in ctx.prompt and notes.as_posix() not in ctx.prompt
    assert "withheld by the runner" in ctx.prompt


@pytest.mark.asyncio
async def test_runner_refuses_a_reference_holding_a_file_link_into_a_restricted_zone(tmp_path, monkeypatch):
    settings = _vault_settings(tmp_path)
    notes = tmp_path / "refs" / "notes"
    _link_file(notes / "raw.tsv", tmp_path / "vault" / "raw.tsv")
    runner, ctx = await _expose(tmp_path, monkeypatch, settings, notes)
    assert ctx.read_dirs == []
    assert any("raw.tsv" in t and "통제" in t for t in _log_texts(runner))


@pytest.mark.asyncio
async def test_runner_keeps_a_reference_whose_links_stay_inside_it(tmp_path, monkeypatch):
    settings = _vault_settings(tmp_path)
    notes = tmp_path / "refs" / "notes"
    _link_dir(notes / "alias", notes / "sub")
    runner, ctx = await _expose(tmp_path, monkeypatch, settings, notes)
    assert ctx.read_dirs == [str(notes.resolve())]
    assert not [t for t in _log_texts(runner) if t.startswith("참고 경로 제외")]
    assert f"[path] {notes} (read-only on the runner)" in ctx.prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["entries", "depth"])
async def test_runner_fails_closed_when_a_reference_is_too_big_to_check(tmp_path, monkeypatch, limit):
    settings = _vault_settings(tmp_path)
    notes = tmp_path / "refs" / "notes"
    if limit == "entries":
        settings.runner.reference_scan_max_entries = 3
        for i in range(5):
            (notes / f"f{i}.txt").write_text("x", encoding="utf-8")
    else:
        settings.runner.reference_scan_max_depth = 2
        (notes / "a" / "b" / "c").mkdir(parents=True)
    runner, ctx = await _expose(tmp_path, monkeypatch, settings, notes)
    assert ctx.read_dirs == [] and "withheld by the runner" in ctx.prompt
    assert any("상한" in t for t in _log_texts(runner) if t.startswith("참고 경로 제외"))


def _upstream(runner, name):
    directory = runner.ws_root / "r1" / name
    (directory / "outputs").mkdir(parents=True)
    (directory / "outputs" / "summary.tsv").write_text("gene\tlog2fc", encoding="utf-8")
    return directory


@pytest.mark.asyncio
async def test_an_earlier_steps_folder_linking_into_a_zone_is_not_opened_to_the_next_step(tmp_path, monkeypatch):
    # #132: `outputs/link -> zone` made by an earlier agent reached the next step through --add-dir, where
    # Claude reads without asking the gate. Links elsewhere (a shared genome) are fine; two hops are found.
    settings = _vault_settings(tmp_path)
    runner, seen = _reference_runner(tmp_path, monkeypatch, settings)
    direct, chained, clean, genome = (_upstream(runner, n) for n in ("task-a", "task-b", "task-c", "task-d"))
    _link_dir(direct / "outputs" / "link", tmp_path / "vault")
    (tmp_path / "elsewhere" / "hop").mkdir()
    _link_dir(tmp_path / "elsewhere" / "hop" / "raw", tmp_path / "vault")
    _link_dir(chained / "outputs" / "cache", tmp_path / "elsewhere")
    (tmp_path / "genome").mkdir()
    _link_dir(genome / "outputs" / "hg38", tmp_path / "genome")
    await runner.run_task(Task(id="task-n", request_id="r1", agent_id="worker", prompt="next step",
                               meta={"upstream_dirs": [str(d) for d in (direct, chained, clean, genome)]}))
    ctx = seen["ctx"]
    opened = [str(clean.resolve()), str(genome.resolve())]
    assert ctx.extra_dirs == opened and ctx.env["LABHQ_EXTRA_ROOTS"].split(os.pathsep) == opened
    refused = [t for t in _log_texts(runner) if t.startswith("이전 단계 폴더 제외")]
    assert len(refused) == 2 and all("통제 데이터 구역" in t for t in refused)
    assert "task-a" in refused[0] and "outputs/link" in refused[0] and "cache" in refused[1]
    assert str(tmp_path / "vault") not in " ".join(refused), "the zone path is not echoed into logs"


@pytest.mark.asyncio
async def test_an_earlier_steps_folder_too_big_to_check_is_not_opened(tmp_path, monkeypatch):
    settings = _vault_settings(tmp_path)
    settings.runner.reference_scan_max_entries = 3
    runner, seen = _reference_runner(tmp_path, monkeypatch, settings)
    upstream = _upstream(runner, "task-a")
    for i in range(5):
        (upstream / "outputs" / f"f{i}.tsv").write_text("x", encoding="utf-8")
    await runner.run_task(Task(id="task-n", request_id="r1", agent_id="worker", prompt="next",
                               meta={"upstream_dirs": [str(upstream)]}))
    assert seen["ctx"].extra_dirs == []
    assert any("상한" in t for t in _log_texts(runner) if t.startswith("이전 단계 폴더 제외"))


@pytest.mark.asyncio
async def test_a_project_folder_stays_open_but_claude_is_denied_its_links_into_a_zone(tmp_path, monkeypatch):
    # #132: refusing a project clone would stop every step of the project, so its zone links get deny rules.
    from labhq.policy import claude_rule_path

    settings = _vault_settings(tmp_path)
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    _link_dir(project / "data", tmp_path / "vault")
    runner, seen = _reference_runner(tmp_path, monkeypatch, settings)
    for task_id in ("task-p1", "task-p2"):
        await runner.run_task(Task(id=task_id, request_id="r1", agent_id="worker", prompt="work",
                                   meta={"project_dirs": [str(project)]}))
    ctx = seen["ctx"]
    assert ctx.extra_dirs == [str(project)]
    deny = ctx.claude_settings["permissions"]["deny"]
    link = claude_rule_path(str(project / "data"))
    for tool in ("Read", "Edit", "Write"):
        assert f"{tool}(/{link})" in deny and f"{tool}(/{link}/**)" in deny
    warned = [t for t in _log_texts(runner) if t.startswith("프로젝트 폴더")]
    assert len(warned) == 1 and "data" in warned[0], "said once, not on every task"
    # Without restricted zones nothing is listed: a large clone costs nothing.
    plain = Settings()
    runner2, seen2 = _reference_runner(tmp_path / "plain", monkeypatch, plain)
    await runner2.run_task(Task(id="task-p3", request_id="r1", agent_id="worker", prompt="work",
                                meta={"project_dirs": [str(project)]}))
    assert "permissions" not in seen2["ctx"].claude_settings


@pytest.mark.asyncio
async def test_every_alias_of_a_project_link_into_a_zone_is_denied_to_claude(tmp_path, monkeypatch):
    # Claude matches deny rules against the path as written, so `b` naming the same folder as `a`, `c`
    # naming a subfolder that holds the zone link, and `loop` naming the project itself each need a rule.
    # A link to an unrelated folder (a genome) stays open.
    from labhq.policy import claude_rule_path

    settings = _vault_settings(tmp_path)
    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    _link_dir(project / "sub" / "data", tmp_path / "vault")
    _link_dir(tmp_path / "elsewhere" / "raw", tmp_path / "vault")
    (tmp_path / "genome").mkdir()
    for name, target in (("a", tmp_path / "elsewhere"), ("b", tmp_path / "elsewhere"), ("c", project / "sub"),
                         ("loop", project), ("hg38", tmp_path / "genome")):
        _link_dir(project / name, target)
    runner, seen = _reference_runner(tmp_path, monkeypatch, settings)
    await runner.run_task(Task(id="task-p", request_id="r1", agent_id="worker", prompt="work",
                               meta={"project_dirs": [str(project)]}))
    deny = seen["ctx"].claude_settings["permissions"]["deny"]

    def denied(*parts):
        rule = claude_rule_path(str(project.joinpath(*parts)))
        return any(f"Read(/{rule})" == d or (d.startswith("Read(/") and d.endswith("/**)")
                                              and rule.startswith(d[len("Read(/"):-len("/**)")] + "/"))
                   for d in deny)

    for route in (("sub", "data"), ("a", "raw"), ("b", "raw"), ("c", "data"), ("loop", "sub", "data"),
                  ("loop", "loop", "c", "data"), ("loop", "b", "raw")):
        assert denied(*route), route
    assert not denied("hg38", "chr1.fa") and not denied("sub", "notes.md")


@pytest.mark.asyncio
async def test_a_link_into_a_zone_is_not_listed_through(tmp_path, monkeypatch):
    # Listing the zone through `data -> vault` read its entries on the runner, logged the names of links
    # inside it (`cohort-EGA123`) and spent the entry cap there, so a later link went unseen.
    import labhq.intake as intake

    settings = _vault_settings(tmp_path)
    settings.runner.reference_scan_max_entries = 40
    (tmp_path / "vault" / "sub").mkdir()
    for i in range(60):
        (tmp_path / "vault" / "sub" / f"donor{i}.tsv").write_text("x", encoding="utf-8")
    _link_dir(tmp_path / "vault" / "cohort-EGA123", tmp_path / "vault" / "sub")
    project = tmp_path / "project"
    project.mkdir()
    _link_dir(project / "data", tmp_path / "vault")
    _link_dir(project / "zz", tmp_path / "elsewhere")
    _link_dir(tmp_path / "elsewhere" / "raw", tmp_path / "vault")
    # A zone inside the project folder itself is not listed either.
    (project / "controlled" / "x").mkdir(parents=True)
    _link_dir(project / "controlled" / "cohort-EGA123", project / "controlled" / "x")
    settings.policy.data_zones.append(DataZone(path=str(project / "controlled")))
    listed = []
    real_scandir = os.scandir
    monkeypatch.setattr(intake.os, "scandir", lambda p: listed.append(Path(p)) or real_scandir(p))
    runner, seen = _reference_runner(tmp_path, monkeypatch, settings)
    await runner.run_task(Task(id="task-z", request_id="r1", agent_id="worker", prompt="work",
                               meta={"project_dirs": [str(project)]}))
    for zone in (tmp_path / "vault", project / "controlled"):
        assert not [p for p in listed if p.resolve().is_relative_to(zone.resolve())], zone
    deny = " ".join(seen["ctx"].claude_settings["permissions"]["deny"])
    logs = " ".join(_log_texts(runner))
    assert "cohort-EGA123" not in deny and "cohort-EGA123" not in logs and "donor" not in logs
    assert "zz/raw" in deny and "다 확인하지 못했습니다" not in logs


def test_withholding_a_refused_path_leaves_kept_paths_that_share_its_prefix_intact():
    from labhq.intake import render_references, withhold_reference_paths

    refs = [{"kind": "path", "value": v} for v in ("/srv/refs/a", "/srv/refs/atlas", "/srv/refs/a b", "/srv/refs/a/sub")]
    prompt = render_references(refs) + "\nRead /srv/refs/atlas/summary.md, /srv/refs/a b/x.md and /srv/refs/a/y.md."
    out = withhold_reference_paths(prompt, ["/srv/refs/a", "/srv/refs/a/sub"], kept=["/srv/refs/atlas", "/srv/refs/a b"])
    assert "[path] /srv/refs/atlas (read-only on the runner)" in out and "/srv/refs/atlas/summary.md" in out
    assert "[path] /srv/refs/a b (read-only on the runner)" in out and "/srv/refs/a b/x.md" in out
    assert "/srv/refs/a/" not in out and "/srv/refs/a (" not in out and out.count("withheld by the runner") == 2


def test_reference_masks_stay_linear_on_long_unbroken_tokens(tmp_path):
    # A report or a published file may hold a long run with no space (a comma-joined path list, a `+`
    # chain, a separator run). Unbounded prefixes in the masks rescanned it from every position: 40 KB
    # took seconds per pattern on the gateway's event loop, and published files can be far larger.
    import time

    from labhq.intake import withhold_reference_paths

    s = settings_with_roots(tmp_path)
    s.pi_profile.references = [ref("url", "https://wiki.example.org/pi/notes"), ref("github", "owner/Yuan")]
    hub = create_app(s).state.hub
    hub.requests["r"] = {"references": [{"kind": "path", "value": "~/refs/llm-wiki", "source": "request"},
                                        {"kind": "path", "value": "C:\\Lab\\refs", "source": "request"},
                                        {"kind": "url", "value": "https://share.example.org/f/a.tsv?dl=x",
                                         "source": "request"}]}
    n = 60_000
    tokens = ["/a" * (n // 2), "a," * (n // 2), "a+" * (n // 2), "a:" * (n // 2), "/" * n, "\\" * n]
    started = time.perf_counter()
    for token in tokens:
        hub.reporter._clean(f"x {token} ~/refs/llm-wiki/a.md")
        withhold_reference_paths(f"x {token}", ["/srv/refs/x", "~/refs/llm-wiki"], [])
    assert time.perf_counter() - started < 5, "every mask must be linear in the text length"
    assert hub.reporter._clean("see ~/refs/llm-wiki/a.md") == "see <reference-path>/a.md"


@pytest.mark.asyncio
async def test_refused_references_leave_task_md_in_json_escaped_forms_too(tmp_path, monkeypatch):
    # #133: a plan quoted with json.dumps doubles backslashes and escapes non-ASCII (`\uc5f0`); another
    # encoder may escape slashes (`\/`). The refused name must not survive in any of those forms.
    from labhq.intake import render_references

    (tmp_path / "refs" / "notes").mkdir(parents=True)
    settings = Settings()
    settings.runner.reference_roots = [str(tmp_path / "refs")]
    runner, seen = _reference_runner(tmp_path, monkeypatch, settings)
    windows, posix, kept = "C:\\Lab\\refs\\연구노트", "/srv/연구/private notes", str(tmp_path / "refs" / "notes")
    plan = {"steps": [{"id": "s1", "instruction": f"Read {windows}\\summary.md, {posix}/a.md and {kept}"}]}
    prompt = ("Compare" + render_references([{"kind": "path", "value": v} for v in (windows, posix, kept)])
              + "\nPlan: " + json.dumps(plan) + "\nEscaped: " + json.dumps(plan, ensure_ascii=False).replace("/", "\\/"))
    await runner.run_task(Task(id="task-j", request_id="r1", agent_id="worker", prompt=prompt,
                               context="Upstream plan: " + json.dumps(plan),
                               meta={"reference_dirs": [windows, posix, kept]}))
    md = (runner.workspaces["task-j"].dir / "TASK.md").read_text(encoding="utf-8")
    for form in (windows, json.dumps(windows)[1:-1], posix, json.dumps(posix)[1:-1], posix.replace("/", "\\/")):
        assert form not in md, form
    for leftover in ("연구", "\\uc5f0", "private notes", "Lab\\\\refs"):
        assert leftover not in md, leftover
    assert seen["ctx"].read_dirs == [str((tmp_path / "refs" / "notes").resolve())]
    assert f"[path] {kept} (read-only on the runner)" in md and json.dumps(kept)[1:-1] in md


def test_reads_through_a_link_into_a_restricted_zone_are_denied_by_the_real_path(tmp_path):
    from labhq.policy import evaluate_tool

    (tmp_path / "vault").mkdir()
    (tmp_path / "vault" / "raw.tsv").write_text("donor", encoding="utf-8")
    workdir = tmp_path / "ws"
    (workdir / "reference").mkdir(parents=True)
    _link_dir(workdir / "reference" / "link", tmp_path / "vault")
    policy = Settings().policy
    policy.data_zones = [DataZone(path=str(tmp_path / "vault"))]
    ws = str(workdir)
    absolute = str(workdir / "reference" / "link" / "raw.tsv")
    for tool, tool_input in [("Read", {"file_path": absolute}), ("Read", {"file_path": "reference/link/raw.tsv"}),
                             ("Grep", {"pattern": "donor", "path": "reference/link"}),
                             ("Glob", {"pattern": "reference/link/*.tsv"})]:
        decision = evaluate_tool(tool, tool_input, policy, allowed_roots=[ws], workdir=ws)
        assert decision.action == "deny", (tool, tool_input)
    shell = evaluate_tool("Bash", {"command": "head reference/link/raw.tsv"}, policy, allowed_roots=[ws], workdir=ws)
    assert shell.action == "ask"
    assert evaluate_tool("Read", {"file_path": "reference/other.md"}, policy,
                         allowed_roots=[ws], workdir=ws).action == "allow"


def test_too_many_path_candidates_ask_instead_of_passing_a_link_into_a_zone(tmp_path):
    # #131: past 256 candidates the real-path check returned "no hit", so 257 decoys let the link through.
    from labhq.policy import MAX_RESOLVED_CANDIDATES, evaluate_tool

    (tmp_path / "vault").mkdir()
    (tmp_path / "vault" / "raw.tsv").write_text("donor", encoding="utf-8")
    workdir = tmp_path / "ws"
    (workdir / "ref").mkdir(parents=True)
    _link_dir(workdir / "ref" / "link", tmp_path / "vault")
    policy = Settings().policy
    policy.data_zones = [DataZone(path=str(tmp_path / "vault"))]
    ws = str(workdir)
    decoys = " ".join(f"d{i}.txt" for i in range(MAX_RESOLVED_CANDIDATES + 1))
    for tool, tool_input in [("Bash", {"command": f"cat {decoys} ref/link/raw.tsv"}),
                             ("PowerShell", {"command": f"Get-Content {decoys} ref/link/raw.tsv"}),
                             ("mcp__x__read", {"files": f"{decoys} ref/link/raw.tsv"}),
                             ("Glob", {"pattern": f"{decoys} ref/link/*.tsv"})]:
        decision = evaluate_tool(tool, tool_input, policy, allowed_roots=[ws], workdir=ws)
        assert decision.action == "ask" and "not all resolved" in decision.reason, (tool, decision)
    # Repeats are one candidate, and a file's content is not a path it opens: these still pass.
    repeated = " ".join(["d0.txt"] * (MAX_RESOLVED_CANDIDATES + 5))
    assert evaluate_tool("Bash", {"command": f"cat {repeated}"}, policy, allowed_roots=[ws], workdir=ws).action == "allow"
    write = {"file_path": str(workdir / "notes.md"), "content": decoys}
    assert evaluate_tool("Write", write, policy, allowed_roots=[ws], workdir=ws).action == "allow"
    for tool, tool_input in [("Task", {"prompt": decoys}), ("WebFetch", {"url": "https://example.org", "prompt": decoys})]:
        assert evaluate_tool(tool, tool_input, policy, allowed_roots=[ws], workdir=ws).action == "allow", tool
    many_paths = {"paths": [f"d{i}.txt" for i in range(MAX_RESOLVED_CANDIDATES + 1)]}
    assert evaluate_tool("Task", many_paths, policy, allowed_roots=[ws], workdir=ws).action == "ask"
    # Without restricted zones nothing can be reached through a link, so the cap never asks.
    assert evaluate_tool("Bash", {"command": f"cat {decoys}"}, Settings().policy,
                         allowed_roots=[ws], workdir=ws).action == "allow"


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


def test_project_reports_mask_reference_paths_in_any_case_or_separator(tmp_path):
    s = settings_with_roots(tmp_path)
    s.pi_profile.references = [ref("path", r"C:\Users\pi\Yuan", "private"), ref("path", "/home/pi/llm-wiki")]
    hub = create_app(s).state.hub
    text = (r"see c:\users\pi\yuan\a.md, C:/Users/pi/Yuan/b.md, /c/Users/pi/Yuan/c.md, "
            r"C:\Users/pi\Yuan/d.md and /HOME/pi//llm-wiki/e.md")
    cleaned = hub.reporter._clean(text)
    assert "yuan" not in cleaned.casefold() and "llm-wiki" not in cleaned
    assert cleaned.count("<reference-path>") == 5
    assert hub.reporter._clean(r"C:\Users\pi\Yuanist and /home/pi/llm-wikis").count("<reference-path>") == 2


SIGNED = "https://data.example.org/share/cohort.tsv?token=SECRETtoken123&sig=Zm9vYmFy#part"


def test_url_references_are_stored_shown_and_prompted_without_query_or_fragment(tmp_path):
    from labhq.intake import render_references

    s = settings_with_roots(tmp_path)
    client = TestClient(create_app(s))
    hub = client.app.state.hub
    auth = {"Authorization": f"Bearer {s.gateway.client_token}"}
    rid = client.post("/api/requests", json={"text": "t", "mode": "plan_only", "references": [
        {"kind": "url", "value": SIGNED, "note": "shared cohort"}]}, headers=auth).json()["request_id"]
    stored = hub.requests[rid]["references"][0]
    assert stored["value"] == "https://data.example.org/share/cohort.tsv" and stored["query_removed"] is True
    for shown in (json.dumps(hub.requests[rid]), json.dumps(hub.snapshot()),
                  client.get(f"/api/requests/{rid}", headers=auth).text, render_references([stored])):
        assert "SECRETtoken123" not in shown and "Zm9vYmFy" not in shown and "#part" not in shown
    assert "query removed" in render_references([stored])
    # The original stays only in the gateway's internal store, never in the request, snapshot or prompts.
    assert hub.store.get("reference_original", rid) == {"references": [
        {"value": "https://data.example.org/share/cohort.tsv", "original": SIGNED}]}
    # Requests saved before this fix still hold the raw URL; prompts render it without the query.
    legacy = render_references([{"kind": "url", "value": SIGNED, "source": "request"}])
    assert "SECRETtoken123" not in legacy and "https://data.example.org/share/cohort.tsv" in legacy


def test_publish_guard_redacts_query_credentials_but_keeps_plain_parameters():
    from labhq.integrations.github import sanitize

    text = ("see https://h.example/x?v=2&token=abc123XYZ and "
            "https://lab.s3.amazonaws.com/a.bam?X-Amz-Algorithm=AWS4&X-Amz-Credential=AKIDEXAMPLE%2F2026&"
            "X-Amz-Signature=deadbeef01&X-Amz-Security-Token=FwoGZX; "
            "https://acct.blob.core.windows.net/c/f?sv=2024&sig=SASsig%3D&se=2026 "
            "https://app.example/cb#access_token=ya29.secret&state=s1 "
            "https://maps.example/api?key=AIzaFAKEkey&api_key=k2&Signature=cfSig&Key-Pair-Id=APKA1&Policy=eyJ")
    out = sanitize(text, Settings().policy)
    for secret in ("abc123XYZ", "AKIDEXAMPLE", "deadbeef01", "FwoGZX", "SASsig", "ya29.secret", "AIzaFAKEkey",
                   "=k2", "cfSig", "APKA1", "eyJ"):
        assert secret not in out, secret
    assert "?v=2&token=<redacted-secret>" in out and "sv=2024" in out and "state=s1" in out
    assert "https://lab.s3.amazonaws.com/a.bam?" in out


def test_publish_guard_reads_encoded_parameter_names_and_url_userinfo():
    from labhq.integrations.github import sanitize

    # A URL pasted into the request body never went through intake, so the guard is the last check.
    text = ("a https://h.example/x?X%2DAmz%2DSignature=encSIG1&%74oken=encTOK2 "
            "b https://h.example/x?v=1;token=semiTOK3&x=1 "
            "c https://alice:pa55WORD4@h.example/x d https://ghTOKEN5@github.com/o/r.git "
            "e postgres://u:dbPASS6@db.example/lab f ssh://git@github.com/o/r.git")
    out = sanitize(text, Settings().policy)
    for secret in ("encSIG1", "encTOK2", "semiTOK3", "pa55WORD4", "alice", "ghTOKEN5", "dbPASS6"):
        assert secret not in out, secret
    assert "?X%2DAmz%2DSignature=<redacted-secret>&%74oken=<redacted-secret>" in out
    assert "?v=1;token=<redacted-secret>&x=1" in out and "https://<redacted-secret>@h.example/x" in out
    assert "ssh://git@github.com/o/r.git" in out, "an account name alone is not a credential"
    # A Windows domain account (`CORP\alice`) keeps its userinfo hidden, raw or JSON-escaped.
    domain = sanitize(r'g https://CORP\alice:d0mainPW7@proxy.example/x {"u": "https://CORP\\alice:d0mainPW8@p.example"}',
                      Settings().policy)
    assert "d0mainPW7" not in domain and "d0mainPW8" not in domain and "alice" not in domain, domain


def test_publish_guard_hides_webhook_paths_session_ids_and_codes():
    # #134: these secrets sit in a URL path or under a name that is not credential-like.
    from labhq.integrations.github import sanitize

    text = ("a https://hooks.slack.com/services/T0AAA/B0BBB/sl4ckSECRET1 "
            "b https://discord.com/api/webhooks/123456/d1scordSECRET2 "
            r"c https:\/\/hooks.slack.com\/services\/T0AAA\/B0BBB\/sl4ckSECRET3 "
            "d https://lab.webhook.office.com/webhookb2/abc@def/IncomingWebhook/t3amsSECRET4/xyz "
            "e https://api.telegram.org/bot123456:t3legramSECRET5/sendMessage "
            "f https://h.example/app;jsessionid=JSESS6 g https://app.example/cb?code=0authCODE7&state=s1 "
            "h https://h.example/x?session=SESS8&PHPSESSID=php9 "
            r"i https:\/\/alice:pa55WORD10@h.example\/x")
    out = sanitize(text, Settings().policy)
    for secret in ("sl4ckSECRET1", "d1scordSECRET2", "sl4ckSECRET3", "t3amsSECRET4", "t3legramSECRET5", "JSESS6",
                   "0authCODE7", "SESS8", "php9", "pa55WORD10", "T0AAA"):
        assert secret not in out, secret
    assert "https://hooks.slack.com/services/<redacted-secret> b" in out
    assert "https://api.telegram.org/bot<redacted-secret> f" in out and "state=s1" in out
    assert "https://h.example/app;jsessionid=<redacted-secret> g" in out


def test_reference_url_queries_are_dropped_with_a_default_port_or_json_escapes(tmp_path):
    # #134: the query of a legacy URL reference survived when the text wrote `host:443` or escaped slashes.
    s = settings_with_roots(tmp_path)
    hub = create_app(s).state.hub
    hub.requests["r"] = {"references": [{"kind": "url", "value": "https://share.example.org/f/cohort.tsv?dl=x",
                                         "source": "request"}]}
    text = ("a https://share.example.org:443/f/cohort.tsv?dl=opaque1 "
            r"b https:\/\/share.example.org\/f\/cohort.tsv?dl=opaque2 "
            "c HTTPS://Share.Example.org/f/cohort.tsv#opaque3 d https://share.example.org/f/cohort.tsv;jsession=opaque4 "
            "e https://share.example.org/f/cohort.tsv; then")
    cleaned = hub.reporter._clean(text)
    for secret in ("opaque1", "opaque2", "opaque3", "opaque4"):
        assert secret not in cleaned, secret
    assert "https://share.example.org:443/f/cohort.tsv b" in cleaned and "cohort.tsv; then" in cleaned


def test_project_reports_drop_the_query_of_legacy_url_references(tmp_path):
    s = settings_with_roots(tmp_path)
    hub = create_app(s).state.hub
    legacy = "https://share.example.org/f/cohort.tsv?dl=opaqueSHAREcode99"  # no credential-like name
    hub.requests["r"] = {"references": [{"kind": "url", "value": legacy, "source": "request"}]}
    cleaned = hub.reporter._clean(f"Downloaded {legacy} for the summary")
    assert "opaqueSHAREcode99" not in cleaned
    assert "https://share.example.org/f/cohort.tsv" in cleaned
