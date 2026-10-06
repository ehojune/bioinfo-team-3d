"""PI-approved facilities fixes: fake subprocesses and fake runners only (#35 stage 3)."""
import asyncio
import contextlib
from pathlib import Path

import pytest

from labhq.facilities import fixes
from labhq.gateway.server import create_app
from labhq.models import Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
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
def test_r_package_accepts_bare_cran_names(name):
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


def test_proposal_extracts_names_from_the_error_and_refuses_unsafe_names():
    py = {"id": "python_module_missing", "fix": "install_python_package", "cause": "missing"}
    assert fixes.proposal(py, "ModuleNotFoundError: No module named 'scanpy'")["package"] == "scanpy"
    assert fixes.proposal(py, "ModuleNotFoundError: No module named '--index-url'") is None
    r = {"id": "r_package_missing", "fix": "install_r_package", "cause": "missing"}
    assert fixes.proposal(r, "Error: there is no package called ‘DESeq2’")["package"] == "DESeq2"


@pytest.mark.asyncio
async def test_executor_uses_fixed_argv_and_never_installs_in_the_test(tmp_path):
    workdir = tmp_path / "step"
    envdir = tmp_path / "environment"
    lock = envdir / "outputs" / "env" / "requirements.lock.txt"
    interpreter = envdir / "venv" / ("python.exe" if __import__("os").name == "nt" else "python")
    workdir.mkdir()
    lock.parent.mkdir(parents=True)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("fake", encoding="utf-8")
    lock.write_text(f"Interpreter: {interpreter} --version\n", encoding="utf-8")
    calls = []

    async def fake_run(argv, cwd):
        calls.append((argv, cwd))
        return 0, "fake installed", ""

    proposal = {"fix_id": "install_python_package", "signature_id": "python_module_missing",
                "action": "ignored sender text", "execution": "python_package", "package": "scanpy"}
    record = await fixes.execute(proposal, workdir, {"env": str(envdir)}, tmp_path, run=fake_run)
    assert record["ok"] and calls == [([str(interpreter), "-m", "pip", "install", "scanpy"], workdir)]


class FixHub(FakeHub):
    def __init__(self, *, decision=None, fix_ok=True, rerun_ok=True, resume=True):
        self.decision = decision or {"approved": True, "status": "approved", "approval_id": "appr1"}
        self.fix_ok, self.rerun_ok, self.resume = fix_ok, rerun_ok, resume
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
                return result(task, ok=False, error="ModuleNotFoundError: No module named 'scanpy'",
                              session_id="session-1", workdir="C:/fake/work")
            return result(task, ok=self.rerun_ok, text="done" if self.rerun_ok else "",
                          error=None if self.rerun_ok else "ModuleNotFoundError: No module named 'scanpy'",
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
    assert [call.meta.get("kind") for call in hub.calls] == ["step", "facilities_fix", "step"]
    assert hub.calls[-1].meta["workdir"] == "C:/fake/work"
    assert hub.calls[-1].resume_session_id == "session-1"
    assert len(hub.fix_requests) == len(hub.fix_records) == 1


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
    hub = FixHub(fix_ok=False)
    outcome = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="x", meta={"kind": "step", "step_id": "s1"}))
    assert not outcome.ok and outcome.environment["id"] == "python_module_missing"
    assert outcome.facilities_fix["status"] == "failed"
    assert [call.meta.get("kind") for call in hub.calls] == ["step", "facilities_fix"]


@pytest.mark.asyncio
async def test_one_fix_limit_when_the_rerun_has_the_same_failure():
    hub = FixHub(rerun_ok=False)
    outcome = await Orchestrator(hub).run_step(
        Task(agent_id="worker", request_id="r", prompt="x", meta={"kind": "step", "step_id": "s1"}))
    assert not outcome.ok and outcome.environment["id"] == "python_module_missing"
    assert len(hub.fix_requests) == 1 and len(hub.calls) == 3


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
