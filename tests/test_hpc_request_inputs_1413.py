import os

import pytest

from labhq.runner import request_inputs
from tests.test_research_cp2 import _settings
from tests.test_hpc_transfer_1308 import cluster  # noqa: F401


@pytest.fixture
def source(tmp_path):
    settings = _settings()
    folder = tmp_path / "refs"
    folder.mkdir()
    settings.runner.reference_roots = [str(folder)]
    (folder / "wanted.txt").write_text("wanted", encoding="utf-8")
    (folder / "sibling.txt").write_text("sibling", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    return settings, folder, work


def test_only_the_selected_file_is_copied(source):
    settings, folder, work = source
    result = request_inputs.stage(work, [str(folder / "wanted.txt")], [], settings)
    assert [r["path"] for r in result["files"]] == ["ref-1/wanted.txt"]
    assert (work / result["root"] / "ref-1" / "wanted.txt").read_text() == "wanted"
    request_inputs.verify(work, result)


def test_preflight_refuses_more_files_than_stage_accepts(source):
    settings, folder, work = source
    for index in range(198):  # fixture already contains two files
        (folder / f"file-{index}.txt").write_text("x")
    assert request_inputs.preflight([str(folder)], [], settings)
    request_inputs.stage(work, [str(folder)], [], settings)
    (folder / "extra.txt").write_text("x")
    assert request_inputs.preflight([str(folder)], [], settings) is None
    # A file reference copies only that file, even when its parent contains many siblings.
    assert request_inputs.preflight([str(folder / "wanted.txt")], [], settings)


@pytest.mark.parametrize("change", ["alter", "remove", "add", "add_empty_dir"])
def test_snapshot_changes_are_refused(source, change):
    settings, folder, work = source
    result = request_inputs.stage(work, [str(folder)], [], settings)
    root = work / result["root"] / "ref-1"
    if change == "alter":
        (root / "wanted.txt").write_text("changed")
    elif change == "remove":
        (root / "wanted.txt").unlink()
    elif change == "add":
        (root / "extra.txt").write_text("extra")
    else:
        (root / "extra").mkdir()
    with pytest.raises((OSError, ValueError)):
        request_inputs.verify(work, result)


def test_unapproved_path_refused(source, tmp_path):
    settings, _, work = source
    other = tmp_path / "other.txt"
    other.write_text("no")
    with pytest.raises(ValueError, match="admissible"):
        request_inputs.stage(work, [str(other)], [], settings)


def test_source_change_since_approval_refuses_staging(source):
    from labhq.research.input_proof import capture

    settings, folder, work = source
    descriptor = {"reference_dirs": [str(folder)], "project_dirs": []}
    proof = capture(descriptor, settings)
    (folder / "wanted.txt").write_text("changed")
    with pytest.raises(ValueError, match="approved preflight"):
        request_inputs.stage(work, descriptor["reference_dirs"], [], settings, proof["sha256"])


def test_preflight_is_never_adopted_as_a_recovered_task():
    from labhq.gateway.server import Hub
    from labhq.models import Task

    task = Task(agent_id="worker", request_id="r", prompt="", meta={"kind": "hpc_input_preflight"})
    entry = {"request_id": "r", "kind": "hpc_input_preflight", "payload": {"meta": dict(task.meta)}}
    assert not Hub._matches_recovery(task, entry)
    task.meta["kind"] = entry["kind"] = "step"
    entry["payload"]["meta"] = dict(task.meta)
    assert Hub._matches_recovery(task, entry)


async def test_runner_preflight_has_no_adapter_and_binds_the_runner(source, monkeypatch):
    from labhq.models import Task
    from labhq.runner.daemon import Runner

    settings, folder, _ = source
    settings.hpc.scheduler = "mock"
    runner = Runner(settings)
    async def never(*args, **kwargs):
        pytest.fail("preflight must not run an adapter")
    monkeypatch.setattr(runner, "_run_task", never)
    task = Task(agent_id="worker", request_id="r", prompt="", meta={
        "kind": "hpc_input_preflight", "reference_dirs": [str(folder)],
        "execution_location": {"mode": "hpc", "runner_id": settings.runner.id}})
    result = await runner.run_task(task)
    assert result.ok and result.structured["proof"]["sha256"]
    task.meta["execution_location"]["runner_id"] = "other"
    assert not (await runner.run_task(task)).ok


async def test_oversized_preflight_never_opens_approval(source):
    from labhq.orchestrator.cso import Orchestrator
    from labhq.runner.daemon import Runner
    from tests.test_hpc_whole_request import hub_for

    settings, folder, _ = source
    settings.hpc.scheduler = "mock"
    for index in range(199):
        (folder / f"file-{index}.txt").write_text("x")
    runner = Runner(settings)
    hub = hub_for(runner.run_task)
    hub.agent_runner["worker"] = settings.runner.id
    hub.agents["worker"]["runner_id"] = settings.runner.id
    hub.runners = {settings.runner.id: object()}
    hub.runner_capabilities = {settings.runner.id: {"scheduler": "mock", "hpc_input_preflight": True}}
    hub.requests["r"].update(project_dirs=[str(folder)],
                             execution_location={"mode": "hpc", "runner_id": settings.runner.id})
    async def approve(**card):
        pytest.fail("oversized inputs must be refused before approval")
    hub.request_approval = approve
    assert not await Orchestrator(hub)._hpc_input_gate("r")
    assert "hpc_input_approval" not in hub.requests["r"]


async def test_remote_transfer_size_limit_precedes_approval(source, monkeypatch):
    from types import SimpleNamespace
    from labhq.hpc_transfer import Limits
    from labhq.models import Task
    from labhq.runner.daemon import Runner

    settings, folder, _ = source
    settings.hpc.scheduler = "mock"
    path = folder / "wanted.txt"
    runner = Runner(settings)
    monkeypatch.setattr(runner.scheduler, "transfer", lambda: SimpleNamespace(limits=Limits()))
    task = Task(agent_id="worker", request_id="r", prompt="", meta={
        "kind": "hpc_input_preflight", "reference_dirs": [str(path)],
        "execution_location": {"mode": "hpc", "runner_id": settings.runner.id}})
    path.write_bytes(b"x" * (512 * 1024))
    assert (await runner.run_task(task)).ok
    path.write_bytes(b"x" * (512 * 1024 + 1))
    assert not (await runner.run_task(task)).ok
    monkeypatch.setattr(runner.scheduler, "transfer", lambda: None)
    assert (await runner.run_task(task)).ok  # the transport's per-file limit does not apply to a shared filesystem


async def test_approval_is_bound_to_fresh_runner_input(source):
    from labhq.orchestrator.cso import Orchestrator
    from labhq.runner.daemon import Runner
    from tests.test_hpc_whole_request import hub_for

    settings, folder, _ = source
    settings.hpc.scheduler = "mock"
    runner = Runner(settings)
    hub = hub_for(runner.run_task)
    hub.agent_runner["worker"] = settings.runner.id
    hub.agents["worker"]["runner_id"] = settings.runner.id
    hub.runners = {settings.runner.id: object()}
    hub.runner_capabilities = {settings.runner.id: {"scheduler": "mock", "hpc_input_preflight": True}}
    req = hub.requests["r"]
    req.update(project_dirs=[str(folder)], execution_location={"mode": "hpc", "runner_id": settings.runner.id})
    async def approve(**card):
        assert card["detail"]["input_sha256"]
        return {"approved": True, "approval_id": "approval-1"}
    hub.request_approval = approve
    orchestrator = Orchestrator(hub)
    assert await orchestrator._hpc_input_gate("r")
    assert req["hpc_input_approval"]["approval_id"] == "approval-1"
    assert await orchestrator._hpc_input_gate("r")
    req["project_dirs"].clear()
    assert not await orchestrator._hpc_input_gate("r")
    req["project_dirs"].append(str(folder))
    (folder / "wanted.txt").write_text("changed")
    assert not await orchestrator._hpc_input_gate("r")


async def test_preflight_requires_online_runner_and_unchanged_approval(source):
    from labhq.orchestrator.cso import Orchestrator
    from labhq.runner.daemon import Runner
    from tests.test_hpc_whole_request import hub_for

    settings, folder, _ = source
    settings.hpc.scheduler = "mock"
    runner = Runner(settings)
    hub = hub_for(runner.run_task)
    hub.agent_runner["worker"] = settings.runner.id
    hub.agents["worker"]["runner_id"] = settings.runner.id
    hub.runners = {}
    hub.runner_capabilities = {settings.runner.id: {"scheduler": "mock", "hpc_input_preflight": True}}
    req = hub.requests["r"]
    req.update(project_dirs=[str(folder)], execution_location={"mode": "hpc", "runner_id": settings.runner.id})
    orchestrator = Orchestrator(hub)
    assert not await orchestrator._hpc_input_gate("r")
    assert not hub.calls
    hub.runners = {settings.runner.id: object()}
    async def approve(**card):
        (folder / "wanted.txt").write_text("changed during approval")
        return {"approved": True, "approval_id": "approval-1"}
    hub.request_approval = approve
    assert not await orchestrator._hpc_input_gate("r")
    assert "hpc_input_approval" not in req


async def test_preflight_send_refuses_old_or_replaced_runner():
    import asyncio
    from labhq.gateway.server import Hub
    from labhq.models import RunnerNotSent

    class Socket:
        def __init__(self):
            self.sent = []
        async def send_text(self, payload):
            self.sent.append(payload)
    hub = object.__new__(Hub)
    socket = Socket()
    lock = asyncio.Lock()
    hub.runners = {"r": socket}
    hub.runner_locks = {"r": lock}
    hub.runner_capabilities = {"r": {"hpc_input_preflight": True}}
    frame = {"type": "task.dispatch", "task": {"meta": {"kind": "hpc_input_preflight",
             "execution_location": {"mode": "hpc", "runner_id": "r"}}}}
    await lock.acquire()
    pending = asyncio.create_task(hub.send_runner("r", frame))
    await asyncio.sleep(0)
    hub.runner_capabilities["r"] = {}  # an older runner takes over while this send waits
    lock.release()
    with pytest.raises(RunnerNotSent):
        await pending
    assert socket.sent == []
    hub.runner_capabilities["r"] = {"hpc_input_preflight": True}
    await hub.send_runner("r", frame)
    assert len(socket.sent) == 1


@pytest.mark.parametrize("change", ["none", "source", "snapshot"])
async def test_submission_rechecks_approved_source_and_snapshot(source, change):
    from labhq.research.input_proof import capture
    from labhq.runner.hpc_jobs import submit_job

    settings, folder, work = source
    settings.hpc.scheduler = "mock"
    descriptor = {"reference_dirs": [str(folder)], "project_dirs": []}
    digest = capture(descriptor, settings)["sha256"]
    snapshot = request_inputs.stage(work, descriptor["reference_dirs"], [], settings, digest)
    approval = {"descriptor": descriptor, "sha256": digest, "approval_id": "a", "runner_id": settings.runner.id}
    jobs = work / "jobs"
    jobs.mkdir()
    script = jobs / "test.sh"
    script.write_text("#!/bin/sh\necho ok\n")
    if change == "source":
        (folder / "wanted.txt").write_text("changed")
    elif change == "snapshot":
        (work / snapshot["root"] / "ref-1" / "wanted.txt").write_text("changed")
    calls = []
    class Scheduler:
        def submit(self, *args):
            calls.append(args)
            return "mock-1"
    async def approve(_):
        return {"approved": True}
    result = await submit_job(settings, Scheduler(), work, {"script": str(script)}, approve,
                              remote_data={"request_id": "r", "request_snapshot": snapshot,
                                           "request_input_approval": approval})
    assert bool(calls) is (change == "none")
    assert bool(result.get("submitted")) is (change == "none")


async def test_remote_submission_refuses_missing_snapshot_manifest(source):
    from labhq.runner.hpc_jobs import submit_job
    settings, folder, work = source
    settings.hpc.scheduler = "mock"
    snapshot = request_inputs.stage(work, [str(folder)], [], settings)
    (work / "jobs").mkdir()
    script = work / "jobs" / "test.sh"
    script.write_text("#!/bin/sh\ncd /tmp\n")
    class Scheduler:
        def transfer(self):
            return object()
        def submit(self, *args):
            pytest.fail("missing remote manifest must not submit")
    async def approve(_):
        pytest.fail("missing remote manifest must not ask for approval")
    result = await submit_job(settings, Scheduler(), work, {"script": str(script)}, approve,
                              remote_data={"request_id": "r", "request_snapshot": snapshot, "inputs": {}})
    assert "remote request-input snapshot is missing" in result["error"]


def test_request_snapshot_upload_failure_is_not_admitted(source):
    from labhq.hpc_transfer import HashMismatch, upload_request_snapshot
    settings, folder, work = source
    snapshot = request_inputs.stage(work, [str(folder)], [], settings)
    class Transfer:
        def make_fresh_dir(self, _):
            pass
        def ensure_dir(self, _):
            pass
        def upload_file(self, *args, **kwargs):
            raise HashMismatch("fixture transfer failure")
    with pytest.raises(HashMismatch, match="fixture transfer failure"):
        upload_request_snapshot(Transfer(), work / snapshot["root"], snapshot, "r")


async def test_remote_snapshot_drift_after_job_upload_refuses_submit(source, monkeypatch):
    import labhq.hpc_transfer as transfer_module
    import labhq.runner.hpc_jobs as jobs_module
    from labhq.research.input_proof import capture
    settings, folder, work = source
    settings.hpc.scheduler = "mock"
    descriptor = {"reference_dirs": [str(folder)], "project_dirs": []}
    digest = capture(descriptor, settings)["sha256"]
    snapshot = request_inputs.stage(work, descriptor["reference_dirs"], [], settings, digest)
    approval = {"descriptor": descriptor, "sha256": digest, "approval_id": "a", "runner_id": settings.runner.id}
    (work / "jobs").mkdir()
    script = work / "jobs" / "test.sh"
    script.write_text("#!/bin/sh\ncd /tmp\n")
    class Transfer:
        def path(self, value):
            return "/remote/" + value
    class Scheduler:
        def transfer(self):
            return Transfer()
        def submit(self, *args):
            pytest.fail("remote drift must not submit")
    monkeypatch.setattr(jobs_module, "upload_job", lambda *args: None)
    def refuse(*args):
        raise transfer_module.HashMismatch("fixture remote drift")
    monkeypatch.setattr(transfer_module, "verify_remote_data", refuse)
    async def approve(_):
        return {"approved": True}
    manifest = {"files": snapshot["files"], "directories": snapshot["directories"], "strict_snapshot": True}
    result = await jobs_module.submit_job(settings, Scheduler(), work, {"script": str(script)}, approve,
             remote_data={"request_id": "r", "request_snapshot": snapshot, "request_input_approval": approval,
                          "inputs": {"request-inputs": manifest}})
    assert "remote input verification failed" in result["error"]


async def test_fresh_request_preflights_before_direct_and_resume_refuses_drift(source):
    from labhq.models import TaskResult
    from labhq.orchestrator.cso import Orchestrator
    from labhq.runner.daemon import Runner
    from tests.test_hpc_whole_request import hub_for

    settings, folder, _ = source
    settings.hpc.scheduler = "mock"
    runner = Runner(settings)
    async def dispatch(task):
        if task.meta.get("kind") == "hpc_input_preflight":
            return await runner.run_task(task)
        assert task.meta.get("kind") == "direct"
        assert task.meta["hpc_input_approval"]["sha256"]
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="done",
                          cost_usd=0, cost_known=True)
    hub = hub_for(dispatch)
    hub.agent_runner["worker"] = settings.runner.id
    hub.agents["worker"]["runner_id"] = settings.runner.id
    hub.runners = {settings.runner.id: object()}
    hub.runner_capabilities = {settings.runner.id: {"scheduler": "mock", "hpc_input_preflight": True}}
    hub.s.research.enabled = False
    req = hub.requests["r"]
    req.update(mode="direct", agent_id="worker", project_dirs=[str(folder)])
    async def approve(**card):
        return {"approved": True, "approval_id": "a", "note": "1: a"}
    hub.request_approval = approve
    await Orchestrator(hub).run_request("r")
    kinds = [task.meta["kind"] for task in hub.calls]
    assert kinds[0] == "hpc_input_preflight" and kinds[-1] == "direct"
    assert req["status"] == "done"
    hub.calls.clear()
    (folder / "wanted.txt").write_text("changed after approval")
    await Orchestrator(hub).run_request("r", resume=True)
    assert req["status"] == "failed"
    assert [task.meta["kind"] for task in hub.calls] == ["hpc_input_preflight"]


async def test_runner_stages_approved_inputs_before_adapter(tmp_path, monkeypatch):
    from labhq.adapters.mock import MockAdapter
    from labhq.models import AgentSpec, Engine, Task, TaskResult
    from labhq.research.input_proof import capture
    from labhq.runner.daemon import Runner
    from tests.test_workspace_boundary import _settings
    import labhq.hpc_transfer as transfer_module

    settings = _settings(tmp_path)
    settings.hpc.scheduler = "mock"
    folder = tmp_path / "reference"
    folder.mkdir()
    (folder / "input.txt").write_text("approved")
    settings.runner.reference_roots = [str(folder)]
    descriptor = {"reference_dirs": [str(folder)], "project_dirs": []}
    approval = {"descriptor": descriptor, "sha256": capture(descriptor, settings)["sha256"],
                "runner_id": settings.runner.id, "approval_id": "a"}
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.mock, builtin_mcp=["hpc"])
    runner = Runner(settings)
    monkeypatch.setattr(runner.registry, "get", lambda _: agent)
    class FakeTransfer:
        def path(self, relative):
            return "/remote/" + relative
    monkeypatch.setattr(runner.scheduler, "transfer", lambda: FakeTransfer())
    def uploaded(transfer, root, snapshot, request_id):
        assert (root / "ref-1" / "input.txt").read_text() == "approved"
        return {"data": "snapshot/work"}
    monkeypatch.setattr(transfer_module, "upload_request_snapshot", uploaded)
    called = []
    async def run(adapter, ctx):
        called.append(True)
        assert str(folder) not in ctx.read_dirs and str(folder) not in ctx.extra_dirs
        assert ctx.task.meta["hpc_request_snapshot"]["files"]
        return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="fixture")
    monkeypatch.setattr(MockAdapter, "run", run)
    task = Task(agent_id="worker", request_id="r", prompt="test", meta={
        "kind": "step", "step_id": "s1", **descriptor, "hpc_input_approval": approval,
        "execution_location": {"mode": "hpc", "runner_id": settings.runner.id}})
    await runner.run_task(task)
    assert called == [True]


async def test_fresh_unverifiable_input_stops_before_briefing():
    from labhq.models import TaskResult
    from labhq.orchestrator.cso import Orchestrator
    from tests.test_hpc_whole_request import hub_for

    async def dispatch(task):
        assert task.meta["kind"] == "hpc_input_preflight"
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="unverifiable")
    hub = hub_for(dispatch)
    hub.s.orchestrator.chief_of_staff_agent = "cso"
    hub.requests["r"]["project_dirs"] = ["/not-verified"]
    async def choose(**card):
        return {"approved": True, "note": "1: a", "approval_id": "a"}
    hub.request_approval = choose
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "failed"
    assert [task.meta["kind"] for task in hub.calls] == ["hpc_input_preflight"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX remote helper is exercised in Linux CI")
@pytest.mark.parametrize("change", ["none", "alter", "remove", "add_empty_dir"])
def test_remote_snapshot_verifies_exact_inventory(source, cluster, change):
    from labhq.hpc_transfer import TransferError, upload_request_snapshot, verify_remote_data
    from tests.test_hpc_remote_data import helper_transfer

    settings, folder, work = source
    snapshot = request_inputs.stage(work, [str(folder)], [], settings)
    transfer = helper_transfer(cluster)
    manifest = upload_request_snapshot(transfer, work / snapshot["root"], snapshot, "r")
    root = cluster.path(manifest["data"])
    if change == "alter":
        (root / "ref-1" / "wanted.txt").write_text("changed")
    elif change == "remove":
        (root / "ref-1" / "wanted.txt").unlink()
    elif change == "add_empty_dir":
        (root / "extra").mkdir()
    if change == "none":
        verify_remote_data(transfer, manifest, "r")
    else:
        with pytest.raises(TransferError):
            verify_remote_data(transfer, manifest, "r")
