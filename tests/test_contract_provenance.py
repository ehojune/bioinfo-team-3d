"""파견직 채용물 고정과 외부 전송 선언 (#424): source commit, licenses, labhq's own hashes of the delivered skill and
MCP folders, external services, offer_status probation, the rehire hash warning, and keeping staff that send data
to external services off steps whose inputs touch a restricted data zone. No network and no real engine."""

import asyncio
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from labhq.models import AgentSpec, ContractInfo, Employment, Engine, Task, TaskResult
from labhq.orchestrator.cso import PlanAgentError, format_roster, validate_steps
from labhq.recruit import paper2agent, provenance
from labhq.registry import Registry
from labhq.runner.daemon import Runner
from labhq.settings import DataZone, Settings

COMMIT = "0123456789abcdef0123456789abcdef01234567"
ZONE = "/srv/cohort"


def _offer(skill_dir: Path | None, **fields) -> dict:
    offer = {"agent_name": "scanpy 파견", "role_summary": "단일세포 전처리", "kind": "consultant",
             "mcp_zip": None, "mcp_dir": None, "mcp_command": None, "mcp_args": [], "mcp_env_required": [],
             "skill_dir": str(skill_dir) if skill_dir else None, "tools": [], "verification_status": "reviewed",
             "limitations": [], "source_commit": COMMIT, "code_license": "BSD-3-Clause", "weights_license": None,
             "external_services": [{"host": "api.example.org", "what_is_sent": "gene symbols"}]}
    return {**offer, **fields}


def _skill(path: Path, text: str = "# paper skill\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(text, encoding="utf-8")
    return path


class _Runner:
    """What Recruitment uses of the runner: settings, registry, run_task, tasks (#521) and emit."""

    def __init__(self, tmp_path: Path, offer: dict):
        self.s = Settings()
        self.s.runner.force_engine = "mock"
        self.registry = Registry(tmp_path / "agents", tmp_path / "talent")
        self.offer, self.events, self.tasks = offer, [], {}

    async def run_task(self, task, workdir_override=None):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=self.offer)

    async def emit(self, event):
        self.events.append(event)


def _recruit(tmp_path: Path, offer: dict) -> tuple[AgentSpec, _Runner]:
    runner = _Runner(tmp_path, offer)
    spec = asyncio.run(paper2agent.Recruitment(runner, {"repo": "https://github.com/scverse/scanpy",
                                                        "name": "scanpy"}).run())
    assert spec is not None, [e.data for e in runner.events]
    return spec, runner


# ---------------- contract fields ----------------

def test_a_contract_written_before_424_still_loads_with_empty_provenance(tmp_path):
    old = {"id": "c_tissue", "name": "TISSUE", "role": "[파견] x", "employment": "contract",
           "contract": {"repo": "https://github.com/sunericd/TISSUE", "kind": "full", "hired_at": 1.0,
                        "expires_at": 2.0, "status": "active", "verification": {"offer_status": "reviewed"}}}
    spec = AgentSpec.model_validate(old)
    assert spec.contract.source_commit is None and spec.contract.external_services == []
    assert spec.summary()["contract_provenance"]["external_services"] == []


@pytest.mark.parametrize("bad", ["abc1234", COMMIT.upper(), COMMIT + "0", "g" * 40])
def test_source_commit_must_be_a_full_lowercase_hex_commit(bad):
    with pytest.raises(ValueError, match="40"):
        ContractInfo(hired_at=0, expires_at=1, source_commit=bad)


def test_offer_schema_asks_for_commit_licenses_and_external_services():
    props, required = paper2agent.OFFER_SCHEMA["properties"], paper2agent.OFFER_SCHEMA["required"]
    for key in ("source_commit", "code_license", "weights_license", "external_services"):
        assert key in props and key in required
    service = props["external_services"]["items"]
    assert service["required"] == ["host", "what_is_sent"] and service["additionalProperties"] is False
    prompt = paper2agent.build_prompt(None, "https://github.com/x/y", None, Path("/tmp/build"))
    assert "source_commit" in prompt and "external_services" in prompt


# ---------------- recruitment ----------------

def test_recruitment_records_commit_licenses_services_and_labhq_hashes(tmp_path):
    built = _skill(tmp_path / "build" / "dist" / "scanpy-paper")
    mcp = tmp_path / "build" / "labhq_mcp"
    (mcp / "src").mkdir(parents=True)
    (mcp / "src" / "server.py").write_text("print('mcp')\n", encoding="utf-8")
    spec, runner = _recruit(tmp_path, _offer(built, mcp_dir=str(mcp)))
    c = spec.contract
    assert (c.source_commit, c.code_license, c.weights_license) == (COMMIT, "BSD-3-Clause", None)
    assert [(s.host, s.what_is_sent) for s in c.external_services] == [("api.example.org", "gene symbols")]
    assert c.skill_sha256 == provenance.folder_sha256(c.skill_dir) and len(c.skill_sha256) == 64
    assert c.mcp_sha256 == provenance.folder_sha256(mcp) and c.mcp_sha256 != c.skill_sha256
    saved = yaml.safe_load((tmp_path / "talent" / "scanpy" / "contract.yaml").read_text(encoding="utf-8"))
    assert saved["contract"]["source_commit"] == COMMIT and saved["contract"]["skill_sha256"] == c.skill_sha256
    done = next(e for e in runner.events if e.type == "recruit.done")
    assert done.data["agent"]["contract_provenance"]["external_services"][0]["host"] == "api.example.org"
    assert "api.example.org" in spec.system_prompt


def test_an_invalid_offered_commit_is_dropped_and_noted_not_fatal(tmp_path):
    built = _skill(tmp_path / "build" / "skill")
    spec, _ = _recruit(tmp_path, _offer(built, source_commit="abc1234"))
    assert spec.contract.source_commit is None
    assert any("source_commit" in note for note in spec.contract.verification["provenance_notes"])


def test_a_malformed_external_service_is_kept_so_it_still_blocks_restricted_steps(tmp_path):
    built = _skill(tmp_path / "build" / "skill")
    spec, _ = _recruit(tmp_path, _offer(built, external_services=[{"host": "api.example.org"}]))
    assert [(s.host, s.what_is_sent) for s in spec.contract.external_services] == [("api.example.org", "not stated")]
    assert provenance.external_hosts(spec) == ["api.example.org"]


@pytest.mark.parametrize("status,passed", [
    ("reviewed", True), ("reviewed_with_limitations", True), ("tested: all tools passed", True),
    ("unreviewed", False), ("mechanical_failure", False), ("unresolved_discrepancies", False),
    ("partial: MCP blocked", False),
])
def test_probation_uses_the_offer_status_paper2agent_reports(tmp_path, status, passed):
    built = _skill(tmp_path / "build" / "skill")
    spec, runner = _recruit(tmp_path, _offer(built, verification_status=status))
    assert spec.contract.status == ("active" if passed else "probation")
    done = next(e for e in runner.events if e.type == "recruit.done")
    assert done.data["passed_probation"] is passed
    if not passed:
        assert status.split(":")[0] in spec.contract.verification["probation_reason"]


# ---------------- hashes and rehire ----------------

def test_folder_hash_follows_content_and_skips_caches(tmp_path):
    folder = _skill(tmp_path / "skill")
    first = provenance.folder_sha256(folder)
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (folder / ".venv" / "lib").mkdir(parents=True)
    (folder / ".venv" / "lib" / "site.py").write_text("x\n", encoding="utf-8")
    assert provenance.folder_sha256(folder) == first
    (folder / "SKILL.md").write_text("# changed\n", encoding="utf-8")
    assert provenance.folder_sha256(folder) != first
    assert provenance.folder_sha256(tmp_path / "missing") is None and provenance.folder_sha256(None) is None


def test_folder_hash_in_a_git_checkout_skips_an_unignored_venv(tmp_path):
    folder = _skill(tmp_path / "mcp")
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
    first = provenance.folder_sha256(folder)
    (folder / ".venv").mkdir()
    (folder / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    assert provenance.folder_sha256(folder) == first
    (folder / "server.py").write_text("print()\n", encoding="utf-8")  # untracked code still counts
    assert provenance.folder_sha256(folder) != first


def _hired(tmp_path: Path) -> tuple[Registry, Path]:
    skill = _skill(tmp_path / "talent" / "tissue" / "skill" / "tissue-paper")
    now = time.time()
    spec = AgentSpec(id="c_tissue", name="TISSUE", role="[파견] x", employment=Employment.contract,
                     contract=ContractInfo(hired_at=now, expires_at=now + 3600, status="active",
                                           skill_dir=str(skill), talent_dir=str(tmp_path / "talent" / "tissue"),
                                           skill_sha256=provenance.folder_sha256(skill)))
    (tmp_path / "agents" / "core").mkdir(parents=True)
    reg = Registry(tmp_path / "agents", tmp_path / "talent")
    reg.save_contract(spec)
    reg.release("c_tissue")
    return reg, skill


def test_rehire_with_unchanged_hashes_has_no_warning(tmp_path):
    reg, _ = _hired(tmp_path)
    spec = reg.rehire("tissue", days=7)
    assert "rehire_changed" not in spec.contract.verification


def test_rehire_warns_when_the_skill_changed_since_hiring(tmp_path, caplog):
    reg, skill = _hired(tmp_path)
    (skill / "SKILL.md").write_text("# edited after hiring\n", encoding="utf-8")
    spec = reg.rehire("tissue", days=7)
    assert spec.contract.verification["rehire_changed"] == ["skill"]
    assert spec.summary()["contract_provenance"]["rehire_changed"] == ["skill"]
    assert "c_tissue" in caplog.text and "skill" in caplog.text
    (skill / "SKILL.md").write_text("# paper skill\n", encoding="utf-8")  # restored: the warning goes
    reg.release("c_tissue")
    assert "rehire_changed" not in reg.rehire("tissue", days=7).contract.verification


# ---------------- restricted steps ----------------

def _settings() -> Settings:
    s = Settings()
    s.policy.data_zones = [DataZone(path=ZONE, level="restricted")]
    return s


def _roster(external: bool, status: str = "active") -> list[dict]:
    now = time.time()
    services = [{"host": "api.example.org", "what_is_sent": "sequences"}] if external else []
    contract = AgentSpec(id="c_tool", name="Tool", role="[파견] x", employment=Employment.contract,
                         contract=ContractInfo(hired_at=now, expires_at=now + 60, external_services=services,
                                               status=status))
    core = AgentSpec(id="analyst", name="Analyst", role="analysis")
    return [contract.summary(), core.summary()]


def _guard(settings, roster, text="공개 데이터 분석", dirs=()):
    return lambda steps: provenance.external_contract_conflicts(steps, roster, [text], list(dirs), settings)


def _steps(first_instruction: str, second_agent: str = "c_tool") -> list[dict]:
    return [{"id": "s1", "agent_id": "analyst", "instruction": first_instruction, "depends_on": []},
            {"id": "s2", "agent_id": second_agent, "instruction": "run the paper tool on s1 results",
             "depends_on": ["s1"]}]


def test_plan_refuses_external_contract_staff_downstream_of_a_restricted_input():
    with pytest.raises(PlanAgentError, match=r"s2 \(c_tool\)"):
        validate_steps(_steps(f"filter {ZONE}/cohort.vcf"), {"analyst", "c_tool"}, 10,
                       guard=_guard(_settings(), _roster(True)))


@pytest.mark.parametrize("case", ["request", "project_dir"])
def test_plan_refuses_external_contract_staff_when_the_request_names_a_zone(case):
    guard = (_guard(_settings(), _roster(True), text=f"{ZONE}/raw.tsv 분석") if case == "request" else
             _guard(_settings(), _roster(True), dirs=[ZONE + "/project"]))
    with pytest.raises(PlanAgentError, match="restricted"):
        validate_steps(_steps("count public genes"), {"analyst", "c_tool"}, 10, guard=guard)


@pytest.mark.parametrize("external,second,instruction", [
    (False, "c_tool", f"filter {ZONE}/cohort.vcf"),  # contract staff without external services
    (True, "analyst", f"filter {ZONE}/cohort.vcf"),  # restricted work stays with core staff
    (True, "c_tool", "fetch public GEO counts"),  # no restricted input anywhere
])
def test_plan_keeps_steps_that_do_not_mix_external_services_and_restricted_inputs(external, second, instruction):
    steps, _ = validate_steps(_steps(instruction, second), {"analyst", "c_tool"}, 10,
                              guard=_guard(_settings(), _roster(external)))
    assert [s["id"] for s in steps] == ["s1", "s2"]


def test_the_cso_roster_names_where_contract_staff_send_data():
    line = format_roster(_roster(True))
    assert "api.example.org" in line and "restricted" in line


@pytest.fixture
def spawned(monkeypatch):
    seen = []

    class _Proc:
        returncode, stdin = 0, None

        def __init__(self):
            async def lines(items):
                for item in items:
                    yield item
            self.stdout = lines([b'{"type":"thread.started","thread_id":"t1"}\n',
                                 b'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n',
                                 b'{"type":"turn.completed","usage":{}}\n'])
            self.stderr = lines([])

        async def wait(self):
            return 0

    async def fake_exec(*cmd, env=None, cwd=None, **kwargs):
        seen.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("labhq.adapters.base._resolve_command", lambda cmd, env, engine: cmd)
    return seen


def _contract_runner(tmp_path, monkeypatch, external: bool, status: str = "active"):
    settings = _settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    services = [{"host": "api.example.org", "what_is_sent": "sequences"}] if external else []
    staff = AgentSpec(id="c_tool", name="Tool", role="[파견] x", engine=Engine.codex, builtin_mcp=[],
                      employment=Employment.contract,
                      contract=ContractInfo(hired_at=0, expires_at=time.time() + 60, external_services=services,
                                            status=status))
    runner = Runner(settings)  # run_forever is not called: Windows refuses restricted zones only there
    monkeypatch.setattr(runner.registry, "get", lambda _id: staff)
    return runner


@pytest.mark.asyncio
@pytest.mark.parametrize("meta,prompt", [
    ({"kind": "step"}, f"read {ZONE}/cohort.vcf"),
    ({"kind": "step", "restricted_inputs": True}, "use the upstream table"),
    ({"kind": "direct", "project_dirs": [ZONE]}, "summarize"),
])
async def test_the_runner_refuses_external_contract_staff_on_restricted_inputs(
        tmp_path, monkeypatch, spawned, meta, prompt):
    runner = _contract_runner(tmp_path, monkeypatch, external=True)
    result = await runner.run_task(Task(agent_id="c_tool", request_id="r", prompt=prompt, meta=meta))
    assert not result.ok and "외부 전송" in result.error and "api.example.org" in result.error, result.error
    assert ZONE not in result.error, "errors reach reports: no zone path"
    assert spawned == []


@pytest.mark.asyncio
@pytest.mark.parametrize("external,prompt", [(True, "fetch public GEO counts"), (False, f"read {ZONE}/x.vcf")])
async def test_the_runner_still_runs_contract_staff_that_do_not_mix_the_two(
        tmp_path, monkeypatch, spawned, external, prompt):
    runner = _contract_runner(tmp_path, monkeypatch, external=external)
    result = await runner.run_task(Task(agent_id="c_tool", request_id="r", prompt=prompt, meta={"kind": "step"}))
    assert "외부 전송" not in (result.error or "")
    assert len(spawned) == 1


# ---------------- setup-paper2agent pins the converter ----------------

SOURCE = "https://example.org/Paper2Agent.git"


@pytest.fixture
def install(tmp_path, monkeypatch):
    repo = tmp_path / "cache" / "Paper2Agent"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {SOURCE}\n', encoding="utf-8")
    _skill(repo / "skills" / "paper2agent", "new skill\n")
    destinations = {e: tmp_path / e / "skills" / "paper2agent" for e in ("claude_code", "codex")}
    monkeypatch.setattr(paper2agent, "skill_path", lambda engine: destinations[engine])
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(paper2agent.subprocess, "run", run)
    return SimpleNamespace(repo=repo, cache=tmp_path / "cache", destinations=destinations, run=run)


def _marker(dst: Path) -> dict:
    return json.loads((dst / paper2agent.SOURCE_MARKER).read_text(encoding="utf-8"))


def test_a_pinned_install_fetches_and_checks_out_that_commit(install):
    def checkout(cmd, check):
        if cmd[3:5] == ["checkout", "--detach"]:
            (install.repo / ".git" / "HEAD").write_text(COMMIT + "\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    install.run.side_effect = checkout
    paper2agent.install_skill(SOURCE, install.cache, commit=COMMIT)
    repo = str(install.repo)
    assert [c.args[0] for c in install.run.call_args_list] == [
        ["git", "-C", repo, "fetch", "--depth", "1", "origin", COMMIT],
        ["git", "-C", repo, "checkout", "--detach", "FETCH_HEAD"]]
    for dst in install.destinations.values():
        assert {k: v for k, v in _marker(dst).items() if k != "patches"} == {"source": SOURCE, "commit": COMMIT}
        assert (dst / "SKILL.md").read_text(encoding="utf-8") == "new skill\n"
    assert paper2agent.installed_commit("codex") == COMMIT


def test_a_pinned_install_that_lands_elsewhere_changes_nothing(install):
    (install.repo / ".git" / "HEAD").write_text("f" * 40 + "\n", encoding="utf-8")
    for dst in install.destinations.values():
        _skill(dst, "old\n")
    with pytest.raises(RuntimeError, match="commit"):
        paper2agent.install_skill(SOURCE, install.cache, commit=COMMIT)
    for dst in install.destinations.values():
        assert (dst / "SKILL.md").read_text(encoding="utf-8") == "old\n"


@pytest.mark.parametrize("bad", ["main", "abc1234", "-c core.sshCommand=x"])
def test_a_pin_that_is_not_a_full_commit_never_runs_git(install, bad):
    with pytest.raises(ValueError, match="40"):
        paper2agent.install_skill(SOURCE, install.cache, commit=bad)
    install.run.assert_not_called()


@pytest.mark.parametrize("head", ["ref", "packed"])
def test_an_unpinned_install_records_the_commit_it_installed(install, head):
    git = install.repo / ".git"
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    if head == "ref":
        (git / "refs" / "heads").mkdir(parents=True)
        (git / "refs" / "heads" / "main").write_text(COMMIT + "\n", encoding="utf-8")
    else:
        (git / "packed-refs").write_text(f"# pack-refs with: peeled\n{COMMIT} refs/heads/main\n", encoding="utf-8")
    paper2agent.install_skill(SOURCE, install.cache)
    install.run.assert_called_once_with(["git", "-C", str(install.repo), "pull", "--ff-only"], check=True)
    assert all(_marker(dst)["commit"] == COMMIT for dst in install.destinations.values())


def test_an_unpinned_install_after_a_pin_moves_the_detached_checkout_to_the_latest_head(install):
    (install.repo / ".git" / "HEAD").write_text(COMMIT + "\n", encoding="utf-8")
    paper2agent.install_skill(SOURCE, install.cache)
    repo = str(install.repo)
    assert [c.args[0] for c in install.run.call_args_list] == [
        ["git", "-C", repo, "fetch", "--depth", "1", "origin", "HEAD"],
        ["git", "-C", repo, "checkout", "--detach", "FETCH_HEAD"]]


def test_a_git_failure_while_pinning_changes_nothing(install):
    for dst in install.destinations.values():
        _skill(dst, "old\n")
    install.run.side_effect = subprocess.CalledProcessError(128, "git")
    with pytest.raises(subprocess.CalledProcessError):
        paper2agent.install_skill(SOURCE, install.cache, commit=COMMIT)
    assert all((d / "SKILL.md").read_text(encoding="utf-8") == "old\n" for d in install.destinations.values())


# ---------------- PR #522 review: asks, a changed skill source, a ZIP-only MCP ----------------

def _ask_hub(tmp_path):
    from labhq.gateway.server import create_app

    settings = _settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    external = _roster(True)[0]
    plain = {**_roster(False)[0], "id": "c_plain"}
    hub.agents = {"c_tool": external, "c_plain": plain}
    hub.agent_runner = {"c_tool": "runner", "c_plain": "runner"}
    hub.requests["r"] = {"id": "r", "text": "공개 데이터 분석", "status": "running"}
    calls = []

    async def dispatch(task):
        calls.append(task)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="answer")

    hub.dispatch = dispatch
    return hub, calls


def _ask(to: str, restricted: bool, tmp_path):
    from labhq.models import AskRequest

    return AskRequest(task_id="source", agent_id="analyst", request_id="r", to=to, question="이 표를 해석해 줄래요?",
                      why_blocked="방법 확인", refs=["outputs/table.tsv"], source_workdir=str(tmp_path / "runs" / "src"),
                      source_restricted=restricted)


@pytest.mark.asyncio
async def test_a_restricted_task_cannot_consult_an_external_service_contract(tmp_path):
    hub, calls = _ask_hub(tmp_path)
    ask = _ask("colleague:c_tool", True, tmp_path)
    try:
        await hub.orchestrator.answer_ask(ask, "runner")
        answer = hub.store.get("ask", ask.id)["answer"]
        assert calls == [], "no consult task, so no ref is staged into the contract worker's workspace"
        assert "외부 전송" in answer["reason"] and "api.example.org" in answer["reason"]
        assert ZONE not in answer["reason"]
    finally:
        hub.store.close()


@pytest.mark.asyncio
async def test_a_request_that_names_a_zone_cannot_consult_an_external_service_contract(tmp_path):
    hub, calls = _ask_hub(tmp_path)
    hub.requests["r"]["text"] = f"{ZONE}/cohort.vcf 분석"
    ask = _ask("colleague:c_tool", False, tmp_path)
    try:
        await hub.orchestrator.answer_ask(ask, "runner")
        assert calls == [] and "외부 전송" in hub.store.get("ask", ask.id)["answer"]["reason"]
    finally:
        hub.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("to,restricted", [("colleague:c_plain", True), ("colleague:c_tool", False)])
async def test_other_consults_run_as_before_and_carry_the_verdict(tmp_path, to, restricted):
    hub, calls = _ask_hub(tmp_path)
    ask = _ask(to, restricted, tmp_path)
    try:
        await hub.orchestrator.answer_ask(ask, "runner")
        [consult] = calls
        assert consult.meta["consult_refs"] == ["outputs/table.tsv"]
        assert consult.meta.get("restricted_inputs", False) is restricted, "the runner rechecks with this verdict"
    finally:
        hub.store.close()


@pytest.mark.asyncio
async def test_the_broker_token_sets_source_restricted_and_a_caller_cannot_clear_it(tmp_path):
    import httpx

    from labhq.runner.approvals import Broker

    seen = []

    async def record(body):
        seen.append(body)

    broker = Broker(0, record, record, record, record)
    token = broker.issue_task_token("t", "analyst", "r", workdir=str(tmp_path), restricted=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=broker.app), base_url="http://broker") as client:
        response = await client.post("/ask", headers={"X-Labhq-Token": token}, json={
            "to": "colleague:c_tool", "question": "q?", "why_blocked": "b", "wait": "hibernate",
            "source_restricted": False})
    assert response.status_code == 200 and seen[0].source_restricted is True


@pytest.mark.asyncio
@pytest.mark.parametrize("meta,restricted", [
    ({"kind": "step", "project_dirs": [ZONE + "/project"]}, True),
    ({"kind": "step", "restricted_inputs": True}, True),
    ({"kind": "step"}, False),
])
async def test_the_runner_puts_the_task_verdict_on_its_ask_token(tmp_path, monkeypatch, spawned, meta, restricted):
    runner = _contract_runner(tmp_path, monkeypatch, external=False)
    issued = []
    original = runner.broker.issue_task_token

    def capture(*args, **kwargs):
        issued.append(kwargs.get("restricted"))
        return original(*args, **kwargs)

    monkeypatch.setattr(runner.broker, "issue_task_token", capture)
    await runner.run_task(Task(agent_id="c_tool", request_id="r", prompt="summarize the table", meta=meta))
    assert issued == [restricted]


@pytest.mark.asyncio
async def test_the_runner_refuses_a_restricted_consult_before_staging_refs(tmp_path, monkeypatch, spawned):
    runner = _contract_runner(tmp_path, monkeypatch, external=True)
    staged = []
    monkeypatch.setattr(runner, "_stage_consult_refs", lambda task, ws: staged.append(task) or (task, None))
    task = Task(agent_id="c_tool", request_id="r", prompt="표를 해석해 주세요",
                meta={"kind": "consult", "restricted_inputs": True, "source_workdir": str(tmp_path / "src"),
                      "consult_refs": ["outputs/table.tsv"]})
    result = await runner.run_task(task)
    assert not result.ok and "질문할 수 없습니다" in result.error
    assert staged == [] and spawned == []


def _git_config(repo: Path, url: str) -> None:
    (repo / ".git" / "config").write_text(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n'
                                          '\tfetch = +refs/heads/*:refs/remotes/origin/*\n', encoding="utf-8")


def test_a_changed_skill_source_is_cloned_fresh_not_pulled_from_the_old_origin(install):
    _git_config(install.repo, "https://example.org/Old.git")
    (install.repo / "old-only.txt").write_text("old checkout\n", encoding="utf-8")

    def clone(cmd, check):
        assert cmd[:4] == ["git", "clone", "--depth", "1"] and cmd[4] == SOURCE
        new = Path(cmd[5])
        (new / ".git").mkdir(parents=True)
        _git_config(new, SOURCE)
        (new / ".git" / "HEAD").write_text(COMMIT + "\n", encoding="utf-8")
        _skill(new / "skills" / "paper2agent", "fresh skill\n")
        return SimpleNamespace(returncode=0)

    install.run.side_effect = clone
    paper2agent.install_skill(SOURCE, install.cache)
    assert install.run.call_count == 1, "no pull from the old origin"
    assert paper2agent.origin_url(install.repo) == SOURCE and not (install.repo / "old-only.txt").exists()
    assert not list(install.cache.glob(".Paper2Agent-*")), "the swap folder is cleaned up"
    for dst in install.destinations.values():
        assert (dst / "SKILL.md").read_text(encoding="utf-8") == "fresh skill\n"
        assert {k: v for k, v in _marker(dst).items() if k != "patches"} == {"source": SOURCE, "commit": COMMIT}


def test_a_failed_clone_of_a_changed_source_keeps_the_old_checkout_and_installs(install):
    _git_config(install.repo, "https://example.org/Old.git")
    for dst in install.destinations.values():
        _skill(dst, "old\n")
    install.run.side_effect = subprocess.CalledProcessError(128, "git")
    with pytest.raises(subprocess.CalledProcessError):
        paper2agent.install_skill(SOURCE, install.cache)
    assert paper2agent.origin_url(install.repo) == "https://example.org/Old.git"
    assert (install.repo / "skills" / "paper2agent" / "SKILL.md").read_text(encoding="utf-8") == "new skill\n"
    assert all((d / "SKILL.md").read_text(encoding="utf-8") == "old\n" for d in install.destinations.values())
    assert not list(install.cache.glob(".Paper2Agent-*"))


def test_the_same_source_spelled_differently_still_pulls(install):
    _git_config(install.repo, SOURCE.removesuffix(".git") + "/")
    paper2agent.install_skill(SOURCE, install.cache)
    install.run.assert_called_once_with(["git", "-C", str(install.repo), "pull", "--ff-only"], check=True)


def test_a_zip_only_mcp_is_hashed_recorded_and_rechecked_on_rehire(tmp_path):
    built = _skill(tmp_path / "build" / "skill")
    archive = tmp_path / "build" / "dist" / "scanpy-mcp.zip"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"PK\x03\x04 first build")
    spec, runner = _recruit(tmp_path, _offer(built, mcp_zip=str(archive)))
    c = spec.contract
    assert c.mcp_path == str(archive) and c.mcp_sha256 == provenance.folder_sha256(archive)
    runner.registry.release(spec.id)
    assert "rehire_changed" not in runner.registry.rehire("scanpy", days=7).contract.verification
    archive.write_bytes(b"PK\x03\x04 rebuilt")
    runner.registry.release(spec.id)
    assert runner.registry.rehire("scanpy", days=7).contract.verification["rehire_changed"] == ["mcp"]


# ---------------- PR #522 review round 2: probation gets no work, hosts are bare hostnames ----------------

@pytest.mark.parametrize("host", ["api.example.org", "API.Example.org", "api.example.org:8443", "10.0.0.5:9000",
                                  "[2001:db8::1]:443"])
def test_a_bare_host_or_host_port_is_kept(host):
    from labhq.models import ExternalService

    assert ExternalService(host=host, what_is_sent="x").host == host.lower()


@pytest.mark.parametrize("host", ["https://api.example.org?key=SECRET", "user:pass@api.example.org",
                                  "api.example.org/v1/token-abc", "api.example.org#frag", "api.example.org:99999",
                                  "api example.org", ""])
def test_a_url_or_credential_shaped_host_is_rejected_by_the_contract(host):
    from labhq.models import ExternalService

    with pytest.raises(ValueError, match="bare hostname"):
        ExternalService(host=host, what_is_sent="x")
    with pytest.raises(ValueError):
        ContractInfo(hired_at=0, expires_at=1, external_services=[{"host": host, "what_is_sent": "x"}])


@pytest.mark.parametrize("raw,stored", [
    ("https://user:SECRET@api.example.org:8443/v1?key=SECRET#x", "api.example.org:8443"),
    ("https://api.example.org?key=SECRET", "api.example.org"),
    ("not a host at all ?key=SECRET", "unknown.invalid"),
])
def test_recruitment_keeps_only_the_hostname_and_never_the_secret(tmp_path, raw, stored):
    built = _skill(tmp_path / "build" / "skill")
    spec, runner = _recruit(tmp_path, _offer(built, external_services=[{"host": raw, "what_is_sent": "ids"}]))
    assert [s.host for s in spec.contract.external_services] == [stored]
    saved = (tmp_path / "talent" / "scanpy" / "contract.yaml").read_text(encoding="utf-8")
    events = json.dumps([e.data for e in runner.events], ensure_ascii=False)
    assert "SECRET" not in saved and "SECRET" not in events and "SECRET" not in spec.system_prompt
    assert any("bare hostname" in note for note in spec.contract.verification["provenance_notes"])


def test_a_contract_saved_without_external_services_still_loads(tmp_path):
    (tmp_path / "agents" / "contract").mkdir(parents=True)
    (tmp_path / "agents" / "core").mkdir(parents=True)
    (tmp_path / "agents" / "contract" / "c_old.yaml").write_text(yaml.safe_dump({
        "id": "c_old", "name": "Old", "role": "[파견] x", "employment": "contract",
        "contract": {"kind": "consultant", "hired_at": time.time(), "expires_at": time.time() + 3600,
                     "status": "active"}}), encoding="utf-8")
    reg = Registry(tmp_path / "agents", tmp_path / "talent")
    reg.load()
    assert reg.get("c_old").contract.external_services == []


@pytest.mark.asyncio
async def test_the_runner_gives_a_probation_contract_no_work(tmp_path, monkeypatch, spawned):
    runner = _contract_runner(tmp_path, monkeypatch, external=False, status="probation")
    result = await runner.run_task(Task(agent_id="c_tool", request_id="r", prompt="fetch public GEO counts",
                                        meta={"kind": "direct"}))
    assert not result.ok and "수습" in result.error and "contract activate" in result.error
    assert spawned == []


@pytest.mark.asyncio
async def test_a_probation_contract_answers_no_consult(tmp_path):
    hub, calls = _ask_hub(tmp_path)
    hub.agents["c_plain"] = {**_roster(False, status="probation")[0], "id": "c_plain"}
    ask = _ask("colleague:c_plain", False, tmp_path)
    try:
        await hub.orchestrator.answer_ask(ask, "runner")
        assert calls == [] and "수습" in hub.store.get("ask", ask.id)["answer"]["reason"]
    finally:
        hub.store.close()


@pytest.mark.asyncio
async def test_a_direct_request_to_a_probation_contract_is_refused_but_it_stays_on_the_roster(tmp_path):
    import httpx

    from labhq.gateway.server import create_app

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    hub.agents = {"c_tool": _roster(False, status="probation")[0], "c_ok": {**_roster(False)[0], "id": "c_ok"}}
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
            refused = await client.post("/api/requests", headers=headers,
                                        json={"text": "자문", "mode": "direct", "agent_id": "c_tool"})
            roster = await client.get("/api/agents", headers=headers)
        assert refused.status_code == 409 and "수습" in refused.json()["detail"]
        assert {a["id"]: a["contract_status"] for a in roster.json()}["c_tool"] == "probation"
    finally:
        hub.store.close()


# ---------------- PR #522 review round 3: unhashable parts, the server that runs, an unverified origin ----------------

def test_a_delivered_part_labhq_cannot_hash_keeps_the_hire_on_probation(tmp_path, monkeypatch):
    built = _skill(tmp_path / "build" / "skill")
    real = provenance.folder_sha256
    monkeypatch.setattr(provenance, "folder_sha256",
                        lambda path: None if path and Path(path).name == "skill" else real(path))
    spec, runner = _recruit(tmp_path, _offer(built))
    assert spec.contract.skill_sha256 is None and spec.contract.status == "probation"
    assert spec.contract.verification["probation_reason"] == "provenance hash failed: skill"
    assert next(e for e in runner.events if e.type == "recruit.done").data["passed_probation"] is False


def test_a_rehire_flags_a_delivered_part_without_a_recorded_hash(tmp_path):
    reg, skill = _hired(tmp_path)
    spec = reg.rehire("tissue", days=1)
    spec.contract.skill_sha256 = None  # e.g. hired before #424, or hashing failed at hiring
    reg.save_contract(spec)
    reg.release(spec.id)
    assert reg.rehire("tissue", days=7).contract.verification["rehire_changed"] == ["skill(hash 없음)"]


def test_the_extracted_server_mcp_args_runs_is_hashed_and_rechecked_on_rehire(tmp_path, monkeypatch):
    async def tools(server):
        return ["cluster"]

    monkeypatch.setattr(paper2agent, "list_tools", tools)
    built = _skill(tmp_path / "build" / "skill")
    archive = tmp_path / "build" / "dist" / "scanpy-mcp.zip"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"PK\x03\x04 build")
    server = tmp_path / "extracted" / "scanpy_mcp"
    server.mkdir(parents=True)
    (server / "server.py").write_text("from tools import run\n", encoding="utf-8")
    (server / "tools.py").write_text("def run(): return 1\n", encoding="utf-8")
    spec, runner = _recruit(tmp_path, _offer(built, kind="full", mcp_zip=str(archive), mcp_command="python",
                                             mcp_args=[str(server / "server.py")]))
    c = spec.contract
    assert c.status == "active" and c.mcp_entry_path == str(server.resolve())
    assert c.mcp_entry_sha256 == provenance.folder_sha256(server)
    (server / "tools.py").write_text("def run(): return 2\n", encoding="utf-8")  # the ZIP is unchanged
    runner.registry.release(spec.id)
    assert runner.registry.rehire("scanpy", days=7).contract.verification["rehire_changed"] == ["mcp"]


def test_an_entry_inside_mcp_dir_is_covered_by_the_folder_hash(tmp_path):
    mcp = tmp_path / "labhq_mcp"
    (mcp / "src").mkdir(parents=True)
    (mcp / "src" / "server.py").write_text("print()\n", encoding="utf-8")
    assert provenance.mcp_entry_tree({"mcp_dir": str(mcp), "mcp_args": [str(mcp / "src" / "server.py")]}) is None
    assert provenance.mcp_entry_tree({"mcp_args": ["-m", "relative.module"]}) is None
    data = tmp_path / "reference_data"
    data.mkdir()
    assert provenance.mcp_entry_tree({"mcp_args": ["--data", str(data)]}) is None, "a data folder is not hashed"
    assert provenance.mcp_entry_tree({"mcp_args": ["run", "--directory", str(data), "server"]}) == str(data.resolve())


def test_a_cached_checkout_whose_origin_cannot_be_read_is_cloned_fresh(install):
    (install.repo / ".git" / "config").unlink()

    def clone(cmd, check):
        new = Path(cmd[5])
        (new / ".git").mkdir(parents=True)
        _git_config(new, SOURCE)
        _skill(new / "skills" / "paper2agent", "verified skill\n")
        return SimpleNamespace(returncode=0)

    install.run.side_effect = clone
    paper2agent.install_skill(SOURCE, install.cache)
    assert [c.args[0][:5] for c in install.run.call_args_list] == [["git", "clone", "--depth", "1", SOURCE]]
    assert paper2agent.origin_url(install.repo) == SOURCE
    for dst in install.destinations.values():
        assert _marker(dst)["source"] == SOURCE and (dst / "SKILL.md").read_text(encoding="utf-8") == "verified skill\n"


# ---------------- labhq talent lists built paper skills (#660) ----------------

def _paper_skill_receipt(talent: Path, pid: str, **fields) -> Path:
    import hashlib
    from labhq.recruit.paper_skills import paper_id
    folder = talent / ("paper-skill-" + hashlib.sha256(paper_id(pid).encode()).hexdigest()[:24])
    folder.mkdir(parents=True)
    row = {"paper_id": paper_id(pid), "status": "ready", "skill_dir": str(folder / "skill" / ("b" * 64)),
           "skill_sha256": "b" * 64, "runner_id": "local", "paper2agent_commit": "c" * 40,
           "fulltext_receipt": {"text_file": "/private/paper.md"}, "fulltext_receipt_sha256": "d" * 64, **fields}
    (folder / "paper-skill.json").write_text(json.dumps(row), encoding="utf-8")
    return folder


def test_talent_lists_contracts_and_built_paper_skills_without_claiming_use(tmp_path):
    from labhq.cli import talent_lines
    reg, _ = _hired(tmp_path)
    folder = _paper_skill_receipt(tmp_path / "talent", "PMID:12345678")
    lines = talent_lines(reg)
    assert lines[0].startswith("🐥 tissue")  # the existing contract line is unchanged and first
    paper, = [line for line in lines if line.startswith("📄")]
    assert folder.name in paper and "pmid:12345678" in paper and "생성됨" in paper
    assert "skill=" + "b" * 12 in paper and "commit=ccccccc" in paper and "요청별 기록 참조" in paper
    assert "호출됨" not in paper and "called" not in paper  # built is not used, read or useful
    assert "/private/paper.md" not in paper and "skill/" not in paper and "local" not in paper  # no paths/receipt


def test_talent_marks_broken_or_foreign_paper_skill_receipts_without_echoing_them(tmp_path):
    from labhq.cli import talent_lines
    talent = tmp_path / "talent"
    _paper_skill_receipt(talent, "PMID:1", status="failed")
    _paper_skill_receipt(talent, "PMID:2", skill_sha256="not-a-hash")
    broken = _paper_skill_receipt(talent, "PMID:3")
    (broken / "paper-skill.json").write_text("{\x1b[31m not json", encoding="utf-8")
    foreign = _paper_skill_receipt(talent, "PMID:4")
    (foreign / "paper-skill.json").write_text(json.dumps({"paper_id": "pmid:99", "status": "ready",
                                                          "skill_sha256": "e" * 64}), encoding="utf-8")
    huge = _paper_skill_receipt(talent, "PMID:5")
    (huge / "paper-skill.json").write_text(" " * (256 * 1024 + 1), encoding="utf-8")
    (talent / "paper-skill-unfinished").mkdir()  # a failed build writes no receipt: counted, not listed as a skill
    lines = talent_lines(SimpleNamespace(talent_pool=lambda: [], talent_dir=talent))
    assert len(lines) == 6 and all(line.startswith("📄") for line in lines)
    assert all("표시 생략" in line for line in lines[:5])
    assert lines[5].startswith("📄 빌드 미완료 폴더 1개") and "표시 생략" not in lines[5]
    assert sum("다른 논문" in line for line in lines) == 1
    assert not any(token in "".join(lines) for token in ("\x1b", "pmid:99", "not-a-hash", "failed", "e" * 12))


def test_talent_lists_a_paper_skill_once_when_a_contract_already_holds_it(tmp_path):
    from labhq.cli import talent_lines
    talent = tmp_path / "talent"
    folder = _paper_skill_receipt(talent, "PMID:7", skill_sha256="f" * 64)
    contract = SimpleNamespace(id="c_p", name="P", contract=SimpleNamespace(
        talent_dir=str(talent / "p"), kind="consultant", paper="PMID: 7", source_commit=None, external_services=[],
        skill_sha256="f" * 64))
    lines = talent_lines(SimpleNamespace(talent_pool=lambda: [contract], talent_dir=talent))
    assert len(lines) == 1 and lines[0].startswith("🐥 p")
    (folder / "contract.yaml").write_text("", encoding="utf-8")  # a contract in the same folder is the contract line
    assert talent_lines(SimpleNamespace(talent_pool=lambda: [], talent_dir=talent)) == []


def test_talent_command_prints_the_same_lines(tmp_path, capsys):
    from labhq import cli
    _paper_skill_receipt(tmp_path / "talent", "doi:10.1000/xyz")
    (tmp_path / "agents" / "core").mkdir(parents=True)
    config = tmp_path / "labhq.yaml"
    config.write_text(yaml.safe_dump({"runner": {"agents_dir": str(tmp_path / "agents"),
                                                 "talent_dir": str(tmp_path / "talent")}}), encoding="utf-8")
    cli.main(["-c", str(config), "talent"])
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and "doi:10.1000/xyz" in out[0] and "생성됨" in out[0]


# ---------------- #660: the fox's paper list, from the stored request only ----------------

def _literature_request(*, referenced=True):
    from labhq.recruit.paper_skills import reference_token
    use = {"paper_id": "doi:10.1000/fit", "step_id": "s1", "selection_sha256": "sel", "input_ref": "inputs/paper_skills/x.md",
           "skill_id": "paper-" + "c" * 24, "skill_sha256": "a" * 64, "document_sha256": "d" * 64,
           "paper2agent_commit": "e" * 40, "status": "ready", "called": referenced,
           "call_status": "referenced" if referenced else "unverified",
           "reason": "result_reference" if referenced else "no_result_reference"}
    if referenced:
        use["evidence"] = reference_token(use)
    papers = [{"title": "Fit method", "citations": ["https://doi.org/10.1000/FIT"],
               "fulltext_receipt": {"paper_id": "doi:10.1000/fit", "text_file": "/private/fit.txt"}},
              {"title": "Reproduced study", "citations": ["PMID: 4242"]},
              {"title": "Context only", "citations": ["no identifier"]}]
    plan = {"steps": [{"id": "s1", "agent_id": "analyst"}],
            "paper_skill_results": [{"paper_id": "doi:10.1000/fit", "title": "Fit method", "reason": "같은 QC 방법",
                                     "step_ids": ["s1"], "agent_ids": ["analyst"], "status": "ready",
                                     "selection_sha256": "sel", "skill_dir": "/private/talent/x"}],
            "recruit_results": [{"paper": "PMID:4242", "step_ids": ["s1"], "status": "active", "agent_id": "c_repro"}]}
    return {"id": "r", "status": "done", "analysis_precedents": {"status": "ok", "papers": papers},
            "plan": plan, "results": {"s1": {"ok": True, "provenance": {"runs": {"t1": {"paper_skills": [use]}}}}}}


def test_paper_literature_lists_every_precedent_and_the_stored_skill_receipts():
    from labhq.recruit.paper_skills import literature_view
    view = literature_view(_literature_request())
    fit, hire, context = view["papers"]
    assert view["status"] == "ok" and [row["title"] for row in view["papers"]] == ["Fit method", "Reproduced study", "Context only"]
    assert fit["uses"] == ["planning", "paper_skill"] and fit["ids"] == ["doi:10.1000/fit"]
    assert fit["skill"] == {"status": "ready", "built": True, "reason": None, "selection_reason": "같은 QC 방법",
                            "agents": ["analyst"], "skill_sha256": "a" * 64, "paper2agent_commit": "e" * 40,
                            "selected_steps": ["s1"], "attached_steps": ["s1"], "reference": "referenced",
                            "reference_reasons": ["result_reference"]}
    assert hire["uses"] == ["planning", "recruit"] and hire["recruit"] == {"status": "active", "agent_id": "c_repro", "step_ids": ["s1"]}
    assert context["uses"] == ["planning"] and context["ids"] == [] and context["skill"] is None
    assert "/private" not in json.dumps(view)  # no receipt path or talent folder
    unverified = literature_view(_literature_request(referenced=False))["papers"][0]["skill"]
    assert unverified["reference"] == "unverified" and unverified["reference_reasons"] == ["no_result_reference"]


def test_paper_literature_bounds_damaged_records_and_says_when_there_is_none():
    from labhq.recruit.paper_skills import literature_view
    assert literature_view({"id": "r"}) == {"status": "absent", "papers": []}
    req = _literature_request()
    req["analysis_precedents"]["papers"] += ["bad", {"title": "<b>x</b>\x00\n" + "y" * 900, "citations": "PMID: 1"}]
    req["plan"]["paper_skill_results"].append("bad")
    req["results"]["s2"] = {"provenance": "bad"}
    view = literature_view(req)
    assert view["damaged"] is True and all(row["skill"] is None for row in view["papers"])
    long = view["papers"][-1]
    assert len(long["title"]) == 300 and "\x00" not in long["title"] and long["ids"] == []
    # #849 review: a non-list hire record or an unhashable receipt field is damaged data, never a raised error.
    hostile = _literature_request()
    hostile["plan"]["recruit_results"] = 5
    hostile["results"]["s1"]["provenance"]["runs"]["t1"]["paper_skills"][0].update(status=["ready"], reason=["x"])
    shown = literature_view(hostile)
    assert shown["damaged"] is True and [row["recruit"] for row in shown["papers"]] == [None, None, None]
    assert shown["papers"][0]["skill"]["reference_reasons"] == []
    failed = literature_view({"analysis_precedents": {"status": "warning", "warning": "analysis precedents unavailable (no_papers)"}})
    assert failed == {"status": "unavailable", "papers": [], "warning": "analysis precedents unavailable (no_papers)"}


@pytest.mark.asyncio
async def test_request_detail_carries_the_paper_list_without_changing_the_record(tmp_path):
    import httpx

    from labhq.gateway.server import create_app

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["r"] = _literature_request()
    hub.requests["bad"] = {**_literature_request(), "id": "bad", "plan": {"recruit_results": 5}}
    stored = json.dumps(hub.requests["bad"], sort_keys=True)
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
            detail = await client.get("/api/requests/r", headers=headers)
            anonymous = await client.get("/api/requests/r")
            missing = await client.get("/api/requests/none", headers=headers)
            malformed = await client.get("/api/requests/bad", headers=headers)
        assert detail.status_code == 200 and detail.json()["paper_literature"]["papers"][0]["skill"]["reference"] == "referenced"
        assert anonymous.status_code == 401 and missing.status_code == 404
        assert "paper_literature" not in hub.requests["r"]
        assert malformed.status_code == 200 and malformed.json()["paper_literature"]["damaged"] is True
        assert json.dumps(hub.requests["bad"], sort_keys=True) == stored  # the detail never rewrites a damaged record
    finally:
        hub.store.close()


def _history_request(created_at, *, steps=("s1",), digest="a" * 64, referenced=True):
    req = _literature_request(referenced=referenced)
    req["created_at"] = created_at
    req["plan"]["paper_skill_results"][0]["step_ids"] = list(steps)
    req["results"] = {step: {"ok": True, "provenance": {"runs": {"t": {"paper_skills": [
        {**req["results"]["s1"]["provenance"]["runs"]["t1"]["paper_skills"][0], "step_id": step, "skill_sha256": digest}]}}}}
        for step in steps}
    return req


def test_skill_history_lists_the_requests_that_selected_each_built_skill_newest_first():
    from labhq.recruit.paper_skills import history_rows, skill_history

    requests = {"old": _history_request(100.0), "new": _history_request(300.0, steps=("s1", "s2"), referenced=False),
                "mid": _history_request(200.0, digest="b" * 64), "no_time": {**_history_request(1.0), "created_at": None},
                "bad": {"created_at": 50.0, "plan": {"paper_skill_results": ["x", 5]}, "results": "oops"}}
    rows, truncated = history_rows(requests)
    assert truncated is False and [row[0] for row in rows] == ["new", "mid", "old", "bad"]  # no_time is not ordered, so skipped
    history = skill_history(rows)
    fit = history[("doi:10.1000/fit", "a" * 64)]
    assert [use["request_id"] for use in fit] == ["new", "old"] and fit[0]["created_at"] == 300.0
    assert fit[0]["attached_steps"] == ["s1", "s2"] and fit[0]["reference"] == "unverified"  # a reference is not proof of a call
    assert fit[1]["reference"] == "referenced" and fit[1]["agents"] == ["analyst"]
    assert [use["request_id"] for use in history[("doi:10.1000/fit", "b" * 64)]] == ["mid"]
    many = skill_history([(f"r{n}", float(n), requests["old"]["plan"]["paper_skill_results"], requests["old"]["results"])
                          for n in range(1, 61)])
    assert len(many[("doi:10.1000/fit", "a" * 64)]) == 60  # the count is never cut to a display length
    assert "bad" not in {use["request_id"] for uses in history.values() for use in uses}  # a damaged record is skipped, not guessed


def test_history_reads_only_the_newest_requests_and_says_when_older_ones_were_left_out(monkeypatch):
    from labhq.recruit import paper_skills

    monkeypatch.setattr(paper_skills, "HISTORY_REQUEST_LIMIT", 3)
    requests = {f"r{n}": _history_request(float(n)) for n in range(1, 6)}
    rows, truncated = paper_skills.history_rows(requests)
    assert truncated is True and [row[0] for row in rows] == ["r5", "r4", "r3"]
    view = paper_skills.literature_view(_literature_request())
    shown = paper_skills.with_previous_uses(view, paper_skills.skill_history(rows), "r5", len(rows), truncated)
    assert shown["history"] == {"limit": 3, "scanned": 3, "truncated": True}
    skill = shown["papers"][0]["skill"]
    assert [use["request_id"] for use in skill["previous_uses"]] == ["r4", "r3"] and skill["previous_use_count"] == 2
    assert shown["papers"][2]["skill"] is None  # a paper without a skill gets no history


@pytest.mark.asyncio
async def test_the_paper_skills_api_and_request_detail_carry_the_history(tmp_path, monkeypatch):
    import httpx

    from labhq.recruit import paper_skills

    from labhq.gateway.server import create_app

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["r_old"] = {**_history_request(100.0), "id": "r_old"}
    hub.requests["r"] = {**_history_request(200.0), "id": "r"}
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
            listing = await client.get("/api/paper-skills", headers=headers)
            anonymous = await client.get("/api/paper-skills")
            detail = await client.get("/api/requests/r", headers=headers)
        assert anonymous.status_code == 401 and listing.status_code == 200
        body = listing.json()
        assert body["basis"] == {"limit": 500, "scanned": 2, "truncated": False}
        skill, = body["skills"]
        assert skill["paper_id"] == "doi:10.1000/fit" and [use["request_id"] for use in skill["uses"]] == ["r", "r_old"]
        assert skill["use_count"] == 2
        assert skill["first_built"] == {"request_id": "r_old", "created_at": 100.0}
        monkeypatch.setattr(paper_skills, "HISTORY_REQUEST_LIMIT", 1)  # r_old is now outside the history, but still the origin
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as again:
            cut = (await again.get("/api/paper-skills", headers=headers)).json()
        assert cut["basis"]["truncated"] is True and cut["skills"][0]["first_built"]["request_id"] == "r_old"
        card = detail.json()["paper_literature"]["papers"][0]["skill"]
        assert [use["request_id"] for use in card["previous_uses"]] == ["r_old"]  # the request is left out of its own list
        assert detail.json()["paper_literature"]["history"]["scanned"] == 2
        assert "previous_uses" not in json.dumps(hub.requests["r"])  # the stored record is unchanged
    finally:
        hub.store.close()


def test_talent_history_reads_the_store_read_only_and_works_without_a_gateway(tmp_path):
    import sqlite3

    from labhq import cli

    state = tmp_path / "state"
    state.mkdir()
    path = state / "gateway.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE state (kind TEXT NOT NULL, key TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(kind, key))")
    for rid, body in {"req_a": _history_request(100.0), "req_b": _history_request(200.0, steps=("s1", "s2"))}.items():
        db.execute("INSERT INTO state VALUES ('request', ?, ?)", (rid, json.dumps(body)))
    db.commit()
    db.close()
    before = (path.stat().st_mtime_ns, path.stat().st_size)
    settings = Settings.model_validate({"gateway": {"state_dir": str(state)}})
    rows, error = cli._paper_skill_store_rows(settings)
    assert error is None and (path.stat().st_mtime_ns, path.stat().st_size) == before  # nothing was written
    lines = cli.talent_history_lines(rows)
    assert "최근 요청 2건 기준" in lines[0] and "증거가 아닙니다" in lines[0]
    assert any("doi:10.1000/fit" in line and "고른 요청 2건" in line for line in lines)
    assert any("req_b" in line and "붙은 단계=s1,s2" in line and "결과 참조=referenced" in line for line in lines)
    none = cli.talent_history_lines([("r", 1.0, None, None)])
    assert "논문 스킬을 고른 요청이 없습니다(최근 요청 1건 기준)" in none[0]
    unread = cli.talent_history_lines(None, "gateway 기록 파일(gateway.sqlite3)이 없습니다")
    assert "읽지 못했습니다" in unread[0] and "gateway.sqlite3" in unread[0]
