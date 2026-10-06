"""PI-approved facilities fixes: fake subprocesses and fake runners only (#35 stage 3)."""
import asyncio
import contextlib
import errno
from pathlib import Path

import pytest

from labhq.facilities import fixes, signatures
from labhq.gateway.server import create_app
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.daemon import Runner
from labhq.runner.workspace import TaskWorkspace
from labhq.settings import Settings
from tests.test_cso import FakeHub, result


@pytest.mark.parametrize("name", ["scanpy", "pydeseq2", "my-package", "zope.interface"])
def test_python_package_accepts_bare_pypi_names(name):
    assert fixes.validate_python_package(name) == name


@pytest.mark.parametrize("name", ["scanpy==1.10", "scanpy>=1", "https://example/x.whl", "./wheel.whl",
                                       "C:\\wheel.whl", "--index-url", "-e", "pkg extra"])
def test_python_package_rejects_versions_urls_paths_and_options(name):
    with pytest.raises(fixes.FixError):
        fixes.validate_python_package(name)


@pytest.mark.parametrize("name", ["DESeq2", "data.table", "BiocManager"])
def test_r_package_accepts_bare_names(name):
    assert fixes.validate_r_package(name) == name


@pytest.mark.parametrize("name", ["DESeq2@1", "DESeq2==1", "https://cran/x", "./pkg", "C:\\pkg", "--repos",
                                       ".hidden", "bad..name"])
def test_r_package_rejects_versions_urls_paths_and_options(name):
    with pytest.raises(fixes.FixError):
        fixes.validate_r_package(name)


def test_allowlist_matches_only_the_three_approved_signatures():
    assert {(fix.id, fix.signatures, fix.execution) for fix in fixes.fixes()} == {
        ("install_python_package", ("python_module_missing",), "python_package"),
        ("install_r_package", ("r_package_missing",), "r_package"),
        ("clear_workspace_cache", ("disk_full",), "workspace_cache"),
    }


def test_every_verified_package_name_passes_the_name_rules():
    python, r = fixes.load_packages()
    assert len(python) == 38 and len(r) == 44
    assert all(name.isidentifier() and fixes.validate_python_package(distribution) == distribution
               for name, distribution in python.items())
    assert all(fixes.validate_r_package(name) == name and repository in fixes.R_REPOSITORIES
               for name, repository in r.items())


def test_python_imports_map_to_verified_pypi_distributions():
    py = {"id": "python_module_missing", "fix": "install_python_package", "cause": "missing"}
    bio = fixes.proposal(py, "ModuleNotFoundError: No module named 'Bio'")
    sklearn = fixes.proposal(py, "ModuleNotFoundError: No module named 'sklearn'")
    assert (bio["import_name"], bio["package"]) == ("Bio", "biopython")
    assert bio["command"].endswith("./.pylib biopython")
    assert (sklearn["import_name"], sklearn["package"]) == ("sklearn", "scikit-learn")
    assert fixes.proposal(py, "ModuleNotFoundError: No module named '--index-url'") is None


def test_unmapped_import_keeps_the_environment_hint_without_an_approval_proposal():
    error = "ModuleNotFoundError: No module named 'unverified_lab_package'"
    signature = signatures.match("", error)
    environment = signature.record("error")
    assert environment["hint"] and fixes.proposal(environment, error) is None


def test_r_package_proposals_split_cran_and_bioconductor():
    r = {"id": "r_package_missing", "fix": "install_r_package", "cause": "missing"}
    cran = fixes.proposal(r, "Error: there is no package called ‘data.table’")
    bioc = fixes.proposal(r, "Error: there is no package called ‘DESeq2’")
    assert cran["repository"] == "cran" and "install.packages('data.table'" in cran["command"]
    assert bioc["repository"] == "bioconductor"
    instruction = fixes.retry_instruction(bioc)
    assert "if (!requireNamespace('BiocManager', quietly=TRUE))" in instruction
    assert "install.packages('BiocManager', lib='./.rlib')" in instruction
    assert "BiocManager::install('DESeq2', lib='./.rlib', update=FALSE, ask=FALSE)" in instruction


@pytest.mark.asyncio
async def test_runner_never_executes_an_interpreter_named_by_an_employee_lock(tmp_path, monkeypatch):
    workdir = tmp_path / "step"
    envdir = tmp_path / "environment"
    lock = envdir / "outputs" / "env" / "requirements.lock.txt"
    interpreter = envdir / "venv" / ("python.exe" if __import__("os").name == "nt" else "python")
    workdir.mkdir()
    lock.parent.mkdir(parents=True)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("fake", encoding="utf-8")
    lock.write_text(f"Interpreter: {interpreter} --version\n", encoding="utf-8")

    async def forbidden_exec(*_args, **_kwargs):
        raise AssertionError("employee-authored interpreter was executed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_exec)

    proposal = {"fix_id": "install_python_package", "signature_id": "python_module_missing",
                "action": "ignored sender text", "execution": "python_package",
                "import_name": "scanpy", "package": "scanpy"}
    record = await fixes.execute(proposal, workdir)
    assert not record["ok"] and "원 단계" in record["error"]
    instruction = fixes.retry_instruction(proposal)
    assert "python -m pip install --target ./.pylib scanpy" in instruction
    assert str(interpreter) not in instruction


class FixHub(FakeHub):
    def __init__(self, *, decision=None, fix_ok=True, rerun_ok=True, resume=True,
                 initial_error="ModuleNotFoundError: No module named 'scanpy'", rerun_error=None):
        self.decision = decision or {"approved": True, "status": "approved", "approval_id": "appr1"}
        self.fix_ok, self.rerun_ok, self.resume, self.initial_error = fix_ok, rerun_ok, resume, initial_error
        self.rerun_error = rerun_error or "ModuleNotFoundError: No module named 'scanpy'"
        self.fix_requests, self.fix_records = [], []
        self.ordinary_count = 0

        async def dispatch(task):
            if task.meta.get("kind") == "facilities_fix":
                record = {**task.meta["facilities_fix"], "ok": self.fix_ok,
                          "status": "succeeded" if self.fix_ok else "failed",
                          **({"error": "fake install failed"} if not self.fix_ok else {})}
                return result(task, ok=self.fix_ok, error=None if self.fix_ok else "fake install failed",
                              facilities_fix=record, workdir=task.meta.get("workdir"))
            self.ordinary_count += 1
            if self.ordinary_count == 1:
                return result(task, ok=False, error=self.initial_error,
                              session_id="session-1", workdir="C:/fake/work")
            return result(task, ok=self.rerun_ok, text="done" if self.rerun_ok else "",
                          error=None if self.rerun_ok else self.rerun_error,
                          session_id="session-1", workdir="C:/fake/work")

        super().__init__(dispatch)

    async def request_facilities_fix(self, rid, step_id, proposal):
        self.fix_requests.append((rid, step_id, proposal))
        return dict(self.decision)

    async def record_facilities_fix(self, rid, step_id, record):
        self.fix_records.append((rid, step_id, record))

    def supports_resume(self, agent_id):
        return self.resume


@pytest.mark.asyncio
async def test_approval_fix_and_one_same_workspace_rerun_succeeds():
    hub = FixHub()
    task = Task(agent_id="worker", request_id="r", prompt="analyze",
                meta={"kind": "step", "step_id": "s1", "upstream_steps": {"env": "C:/fake/env"}})
    outcome = await Orchestrator(hub).run_step(task)
    assert outcome.ok and outcome.facilities_fix["status"] == "succeeded"
    assert [call.meta.get("kind") for call in hub.calls] == ["step", "step"]
    assert hub.calls[-1].meta["workdir"] == "C:/fake/work"
    assert hub.calls[-1].resume_session_id == "session-1"
    assert hub.calls[-1].prompt.startswith("LABHQ-APPROVED TASK-LOCAL REPAIR")
    assert "python -m pip install --target ./.pylib scanpy" in hub.calls[-1].prompt
    assert len(hub.fix_requests) == 1
    assert [record[2]["status"] for record in hub.fix_records] == ["applied", "succeeded"]
    assert hub.fix_records[0][2]["ok"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [
    {"approved": False, "status": "declined", "approval_id": "a"},
    {"approved": False, "status": "timed_out", "approval_id": "a", "state": "timed_out"},
])
async def test_decline_and_timeout_keep_the_environment_failure(decision):
    hub = FixHub(decision=decision)
    outcome = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="x", meta={"kind": "step", "step_id": "s1"}))
    assert not outcome.ok and outcome.environment["id"] == "python_module_missing"
    assert outcome.facilities_fix["status"] == decision["status"]
    assert len(hub.calls) == 1


@pytest.mark.asyncio
async def test_fix_failure_keeps_environment_failure_without_rerun():
    hub = FixHub(fix_ok=False, initial_error="OSError: [Errno 28] No space left on device")
    outcome = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="x", meta={"kind": "step", "step_id": "s1"}))
    assert not outcome.ok and outcome.environment["id"] == "disk_full"
    assert outcome.facilities_fix["status"] == "failed"
    assert [call.meta.get("kind") for call in hub.calls] == ["step", "facilities_fix"]


@pytest.mark.asyncio
async def test_one_fix_limit_when_the_rerun_has_the_same_failure():
    hub = FixHub(rerun_ok=False)
    outcome = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="x", meta={"kind": "step", "step_id": "s1"}))
    assert not outcome.ok and outcome.environment["id"] == "python_module_missing"
    assert outcome.facilities_fix["status"] == "failed"
    assert [record[2]["status"] for record in hub.fix_records] == ["applied", "failed"]
    assert len(hub.fix_requests) == 1 and len(hub.calls) == 2


@pytest.mark.asyncio
async def test_different_rerun_failure_does_not_claim_the_package_fix_succeeded():
    hub = FixHub(rerun_ok=False, rerun_error="ValueError: input table is empty")
    outcome = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="x", meta={"kind": "step", "step_id": "s1"}))
    assert not outcome.ok and outcome.facilities_fix["status"] == "applied"
    assert [record[2]["status"] for record in hub.fix_records] == ["applied"]


@pytest.mark.asyncio
async def test_failed_fix_record_cannot_be_approved_or_run_again_after_restart(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    hub.requests["r"] = {"id": "r", "status": "running", "mode": "team", "text": "x", "results": {},
                         "facilities_fixes": {"s1": {"status": "failed", "approved": True,
                                                        "approval_id": "a", "execution": {"ok": False}}}}
    decision = await hub.request_facilities_fix("r", "s1", {
        "fix_id": "clear_workspace_cache", "signature_id": "disk_full",
        "action": "작업 폴더 캐시 비우기", "execution": "workspace_cache"})
    assert decision["status"] == "failed" and decision["approved"] is False


@pytest.mark.asyncio
async def test_research_plan_hash_is_unchanged_by_the_fix_and_rerun():
    hub = FixHub()
    hub.requests["r"].update(plan={"steps": []}, research_contract={"plan_sha256": "a" * 64})
    before = hub.requests["r"]["research_contract"]["plan_sha256"]
    outcome = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="x", meta={"kind": "step", "step_id": "s1"}))
    assert outcome.ok and hub.requests["r"]["research_contract"]["plan_sha256"] == before


@pytest.mark.asyncio
async def test_gateway_restart_keeps_the_fix_card_and_wait_state(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.policy.approvals.timeout_s = 60
    proposal = {"fix_id": "clear_workspace_cache", "signature_id": "disk_full",
                "action": "작업 폴더 캐시 비우기", "execution": "workspace_cache",
                "reason": "디스크 공간 부족", "command": "작업 폴더 안 cache 비우기"}
    hub1 = create_app(settings).state.hub
    hub1.requests["r"] = {"id": "r", "status": "running", "mode": "team", "text": "x", "results": {}}
    hub1.save_request("r")
    waiting = asyncio.create_task(hub1.request_facilities_fix("r", "s1", proposal))
    while not hub1.approvals:
        await asyncio.sleep(0)
    approval_id = next(iter(hub1.approvals))
    waiting.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await waiting

    hub2 = create_app(settings).state.hub
    assert hub2.requests["r"]["status"] == "waiting_facilities_fix"
    assert approval_id in hub2.approvals
    card = hub2.approvals[approval_id]["approval"]
    assert card["kind"] == "facilities_fix" and card["detail"]["signature_id"] == "disk_full"
    await hub2.resume_held_request("r")
    assert hub2.requests["r"]["status"] == "waiting_facilities_fix"
    resumed = asyncio.create_task(hub2.request_facilities_fix("r", "s1", proposal))
    await asyncio.sleep(0)
    await hub2.resolve_approval(approval_id, True)
    decision = await resumed
    assert decision["approved"] and decision["status"] == "approved"


async def _park_fix_for_restart(settings, proposal):
    hub = create_app(settings).state.hub
    hub.requests["r"] = {"id": "r", "status": "running", "mode": "team", "text": "x", "results": {}}
    hub.save_request("r")
    waiting = asyncio.create_task(hub.request_facilities_fix("r", "s1", proposal))
    while not hub.approvals:
        await asyncio.sleep(0)
    approval_id = next(iter(hub.approvals))
    waiting.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await waiting
    return hub, approval_id


@pytest.mark.asyncio
async def test_restart_decline_resumes_and_finishes_as_the_original_environment_failure(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    proposal = {"fix_id": "install_python_package", "signature_id": "python_module_missing",
                "action": "이 단계 전용 폴더에 scanpy 설치", "execution": "python_package",
                "import_name": "scanpy", "package": "scanpy", "reason": "missing",
                "command": "<이 단계 interpreter> -m pip install --target ./.pylib scanpy"}
    _old, approval_id = await _park_fix_for_restart(settings, proposal)
    restored = create_app(settings).state.hub
    assert restored.approvals[approval_id]["approval"]["summary"] == (
        "이 단계 전용 폴더에 scanpy 설치 후 한 번 다시 실행할까요?")
    resumed = asyncio.Event()

    async def resume(_rid):
        resumed.set()

    restored.resume_when_ready = resume
    await restored.resolve_approval(approval_id, False, "하지 않음")
    await asyncio.wait_for(resumed.wait(), 1)
    decision = await restored.request_facilities_fix("r", "s1", proposal)
    outcome = await Orchestrator(FixHub(decision=decision)).run_step(
        Task(agent_id="worker", request_id="r", prompt="x", meta={"kind": "step", "step_id": "s1"}))
    assert decision["status"] == "declined"
    assert not outcome.ok and outcome.environment["id"] == "python_module_missing"


@pytest.mark.asyncio
async def test_restart_expired_timeout_is_consumed_immediately_as_environment_failure(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.policy.approvals.timeout_s = 60
    proposal = {"fix_id": "install_python_package", "signature_id": "python_module_missing",
                "action": "이 단계 전용 폴더에 scanpy 설치", "execution": "python_package",
                "import_name": "scanpy", "package": "scanpy", "reason": "missing",
                "command": "<이 단계 interpreter> -m pip install --target ./.pylib scanpy"}
    old, approval_id = await _park_fix_for_restart(settings, proposal)
    old.approvals[approval_id]["approval"]["created_at"] -= 120
    old.save_approval(approval_id)
    restored = create_app(settings).state.hub
    resumed = asyncio.Event()

    async def resume(_rid):
        resumed.set()

    restored.resume_when_ready = resume
    await restored.resume_held_request("r")
    await asyncio.wait_for(resumed.wait(), 1)
    decision = await restored.request_facilities_fix("r", "s1", proposal)
    outcome = await Orchestrator(FixHub(decision=decision)).run_step(
        Task(agent_id="worker", request_id="r", prompt="x", meta={"kind": "step", "step_id": "s1"}))
    assert approval_id not in restored.approvals and decision["status"] == "timed_out"
    assert not outcome.ok and outcome.environment["id"] == "python_module_missing"


@pytest.mark.asyncio
async def test_gateway_timeout_keeps_the_environment_fix_unapproved(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.policy.approvals.timeout_s = 1
    hub = create_app(settings).state.hub
    hub.requests["r"] = {"id": "r", "status": "running", "mode": "team", "text": "x", "results": {}}
    proposal = {"fix_id": "clear_workspace_cache", "signature_id": "disk_full",
                "action": "작업 폴더 캐시 비우기", "execution": "workspace_cache",
                "reason": "디스크 공간 부족", "command": "작업 폴더 안 cache 비우기"}
    decision = await hub.request_facilities_fix("r", "s1", proposal)
    assert not decision["approved"] and decision["status"] == "timed_out"
    assert hub.requests["r"]["facilities_fixes"]["s1"]["status"] == "timed_out"


@pytest.mark.asyncio
async def test_direct_request_uses_a_restart_stable_one_fix_key():
    hub = FixHub(rerun_ok=False)
    await Orchestrator(hub).run_step(Task(agent_id="worker", request_id="r", prompt="x",
                                         meta={"kind": "direct"}))
    assert hub.fix_requests[0][1] == "direct"


@pytest.mark.asyncio
async def test_disk_full_cache_is_cleared_before_any_reused_workspace_write(tmp_path, monkeypatch):
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.codex)
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)
    workdir = tmp_path / "work"
    cache = workdir / ".cache"
    cache.mkdir(parents=True)
    (cache / "large.tmp").write_bytes(b"x")

    def no_space(_self, _event):
        assert not cache.exists(), "cache cleanup must precede the first event write"
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(TaskWorkspace, "append_event", no_space)
    proposal = {"fix_id": "clear_workspace_cache", "signature_id": "disk_full",
                "action": "작업 폴더 캐시 비우기", "execution": "workspace_cache"}
    task = Task(agent_id="worker", request_id="r", prompt="", meta={
        "kind": "facilities_fix", "workdir": str(workdir), "facilities_fix": proposal})
    with pytest.raises(OSError, match="No space left"):
        await runner.run_task(task)
    assert not cache.exists()
