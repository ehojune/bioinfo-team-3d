"""Files over the HPC session shell (#1308), against the fake gateway's sandbox folder. No real HPC."""
import hashlib
import os
import sys
from pathlib import Path

import pytest

from labhq import hpc_session as hs
from labhq import hpc_transfer as ht

GATEWAY = str(Path(__file__).with_name("fake_hpc_gateway.py"))
ROOT = "/hpc/work"


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    """A session on the fake gateway whose file system is the folder `sandbox`."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("SLURM_FAKE_SANDBOX", str(sandbox))
    counter = tmp_path / "starts.txt"
    session = hs.HpcSession(hs.SessionConfig(argv=[sys.executable, "-I", GATEWAY, "--counter", str(counter)],
                                             connect_timeout_s=20, command_timeout_s=20), sleep=lambda s: None)
    holder = type("Cluster", (), {})()
    holder.sandbox, holder.session, holder.counter = sandbox, session, counter
    holder.transfer = lambda **limits: ht.Transfer(session.run, ROOT, ht.Limits(**limits) if limits else ht.Limits())
    holder.logins = lambda: len(counter.read_text().split()) if counter.exists() else 0
    holder.path = lambda rel: sandbox / "hpc" / "work" / rel
    yield holder
    session.close()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_a_script_is_uploaded_and_checked_by_sha256(cluster):
    transfer = cluster.transfer()
    script = b"#!/bin/bash\necho hello\n"
    info = transfer.upload_bytes("t1/jobs/align.sh", script, executable=True)
    assert info == {"path": "t1/jobs/align.sh", "bytes": len(script), "sha256": sha(script)}
    assert cluster.path("t1/jobs/align.sh").read_bytes() == script
    assert transfer.remote_sha256("t1/jobs/align.sh") == sha(script)
    assert not [p for p in cluster.path("t1/jobs").iterdir() if ".up-" in p.name]  # no temporary file is left
    assert cluster.logins() == 1  # the whole transfer used the one shell


def test_a_file_of_many_blocks_arrives_whole(cluster):
    data = os.urandom(300_000)
    cluster.transfer().upload_bytes("big.bin", data)
    assert cluster.path("big.bin").read_bytes() == data


def test_a_corrupted_block_is_caught_and_nothing_is_placed(cluster):
    transfer = cluster.transfer()
    real_run = transfer.run
    state = {"n": 0}

    def corrupting(command):
        if command.startswith("printf %s "):
            state["n"] += 1
            if state["n"] == 2:
                command = command.replace("printf %s ", "printf %s AAAA", 1)  # a block that is not what was sent
        return real_run(command)

    transfer.run = corrupting
    with pytest.raises((ht.HashMismatch, ht.TransferError)):
        transfer.upload_bytes("t1/in.dat", os.urandom(20_000))
    assert not cluster.path("t1/in.dat").exists()
    assert not [p for p in cluster.path("t1").iterdir()]  # staged and encoded copies were removed


def test_a_file_over_the_size_limit_is_refused_with_the_way_out(cluster):
    transfer = cluster.transfer(upload_max_bytes=1000)
    with pytest.raises(ht.TooLarge, match="file transfer method"):
        transfer.upload_bytes("big.bin", b"x" * 1001)
    assert cluster.logins() == 0  # refused before any command


@pytest.mark.parametrize("bad", ["../escape.sh", "/etc/passwd", "a/../../b", "a b", "a;rm -rf x", "a'b", "a\\b", "a/./b",
                                 "", "-rf", "a//b", "a\nb", "$(id)", "a`b`"])
def test_paths_that_leave_the_folder_or_carry_shell_characters_are_rejected(cluster, bad):
    with pytest.raises(ht.PathRejected):
        ht.confine(bad)
    with pytest.raises(ht.PathRejected):
        cluster.transfer().upload_bytes(bad, b"x")


def test_the_work_folder_itself_must_be_plain():
    for bad in ("relative/dir", "/a/../b", "/a\nb"):
        with pytest.raises(ht.PathRejected):
            ht.Transfer(lambda c: None, bad)


def link(target, name):
    try:
        os.symlink(target, name, target_is_directory=Path(target).is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")


def test_a_link_that_leads_out_of_the_work_folder_is_refused(cluster, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    transfer = cluster.transfer()
    transfer.ensure_dir("")
    link(outside, cluster.path("escape"))
    with pytest.raises(ht.PathRejected):
        transfer.ensure_dir("escape")
    with pytest.raises(ht.PathRejected):
        transfer.upload_bytes("escape/stolen.txt", b"x")
    assert not list(outside.iterdir())


def test_a_file_that_is_a_link_is_not_overwritten_or_read(cluster, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    transfer = cluster.transfer()
    transfer.ensure_dir("t1")
    link(secret, cluster.path("t1/result.txt"))
    with pytest.raises(ht.PathRejected):
        transfer.upload_bytes("t1/result.txt", b"new")
    assert secret.read_text(encoding="utf-8") == "secret"
    with pytest.raises(ht.PathRejected):
        transfer.fetch_bytes("t1/result.txt")


def test_a_result_is_fetched_with_its_checksum(cluster, tmp_path):
    data = os.urandom(120_000)
    cluster.transfer().upload_bytes("t1/work/out.bin", data)
    local = tmp_path / "stage"
    info = cluster.transfer().fetch_file("t1/work/out.bin", local)
    assert (local / "t1" / "work" / "out.bin").read_bytes() == data
    assert info["sha256"] == sha(data) and info["bytes"] == len(data) and not info["truncated"]


def test_a_fetch_checks_the_cluster_checksum(cluster):
    cluster.transfer().upload_bytes("t1/a.txt", b"hello")
    transfer = cluster.transfer()
    real_run = transfer.run

    def lying(command):
        result = real_run(command)
        if command.startswith("sha256sum"):
            result.output = "0" * 64 + "  x\n"
        return result

    transfer.run = lying
    with pytest.raises(ht.HashMismatch):
        transfer.fetch_bytes("t1/a.txt")


def test_a_log_over_the_limit_comes_back_as_its_tail_but_a_result_is_refused(cluster):
    text = b"".join(b"line %06d\n" % i for i in range(20000))  # 240 KB
    cluster.transfer(upload_max_bytes=10_000_000).upload_bytes("t1/jobs/logs/run.out", text)
    transfer = cluster.transfer(fetch_max_bytes=50_000)
    data, info = transfer.fetch_bytes("t1/jobs/logs/run.out", tail=True)
    assert info["truncated"] and len(data) == 50_000 and text.endswith(data)
    with pytest.raises(ht.TooLarge, match="file transfer method"):
        transfer.fetch_bytes("t1/jobs/logs/run.out")


def test_the_files_a_job_left_are_listed_without_links(cluster, tmp_path):
    transfer = cluster.transfer()
    transfer.upload_bytes("t1/work/a.txt", b"aa")
    transfer.upload_bytes("t1/work/sub/b.txt", b"bbb")
    link(tmp_path, cluster.path("t1/work/dir-link"))
    assert transfer.list_files("t1/work") == [("a.txt", 2), ("sub/b.txt", 3)]


def test_a_local_write_stays_under_its_folder(tmp_path):
    root = tmp_path / "stage"
    root.mkdir()
    ht.write_confined(root, root / "a" / "b.txt", b"ok")
    assert (root / "a" / "b.txt").read_bytes() == b"ok"
    with pytest.raises(ht.PathRejected):
        ht.write_confined(root, tmp_path / "elsewhere.txt", b"x")
    with pytest.raises(ht.PathRejected):
        ht.write_confined(root, root / ".." / "elsewhere.txt", b"x")
    outside = tmp_path / "outside"
    outside.mkdir()
    link(outside, root / "jump")
    with pytest.raises(ht.PathRejected):
        ht.write_confined(root, root / "jump" / "x.txt", b"x")
    assert not list(outside.iterdir())


def test_a_local_upload_must_be_a_plain_file_inside_its_root(tmp_path, cluster):
    base = tmp_path / "work"
    base.mkdir()
    (base / "ok.txt").write_bytes(b"fine")
    (tmp_path / "other.txt").write_bytes(b"no")
    transfer = cluster.transfer()
    assert transfer.upload_file(base / "ok.txt", "t1/ok.txt", root=base)["bytes"] == 4
    with pytest.raises(ht.PathRejected):
        transfer.upload_file(tmp_path / "other.txt", "t1/other.txt", root=base)
    with pytest.raises(ht.PathRejected):
        transfer.upload_file(base / ".." / "other.txt", "t1/other.txt", root=base)
    link(tmp_path / "other.txt", base / "alias.txt")
    with pytest.raises(ht.PathRejected):
        transfer.upload_file(base / "alias.txt", "t1/alias.txt", root=base)


# ---- the runner: submit without a shared file system, fetch when the job ends ----

import asyncio
import json
from types import SimpleNamespace

from labhq import hpc_site
from labhq.settings import Settings
from labhq.tools import scheduler as sched
from labhq.tools.scheduler import build_script


@pytest.fixture
def site(tmp_path, monkeypatch):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("SLURM_FAKE_SANDBOX", str(sandbox))
    monkeypatch.setenv("LABHQ_SITES_DIR", str(tmp_path / "sites"))
    state = tmp_path / "slurm.json"
    argv = [sys.executable, "-I", GATEWAY, "--slurm-state", str(state), "--counter", str(tmp_path / "starts.txt")]
    hpc_site.save_profile(hpc_site.SiteProfile(name="fake", access_method="gateway_tunnel", connect_argv=argv,
                                               remote_exec=False, scheduler="slurm", remote_workdir=ROOT))
    settings = Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state")},
                                        "hpc": {"scheduler": "slurm", "user": "lab", "site_profile": "fake"}})
    settings.policy.approvals.hpc_core_hours_threshold = 1000
    workdir = tmp_path / "work"
    (workdir / "jobs" / "logs").mkdir(parents=True)
    yield SimpleNamespace(sandbox=sandbox, settings=settings, workdir=workdir, tmp=tmp_path,
                          remote=lambda rel: sandbox / "hpc" / "work" / rel)
    sched.close_sessions()


def write_script(site, name="align", body="echo hi > result.txt"):
    path = site.workdir / "jobs" / f"{name}_20261107.sh"
    path.write_text(build_script(body, str(site.workdir / "outputs")), encoding="utf-8")
    return path


def submit(site, script, **extra):
    from labhq.runner.hpc_jobs import submit_job

    async def approve(payload):
        site.asked = payload
        return {"approved": True}

    body = {"task_id": "t1", "agent_id": "a1", "script": str(script), "name": "align", "cores": 1, "mem": "1G",
            "walltime": "00:10:00", **extra}
    return asyncio.run(submit_job(site.settings, sched.Scheduler(site.settings.hpc), site.workdir, body, approve))


def test_a_job_is_put_on_the_cluster_and_submitted_from_there(site):
    site.settings.policy.approvals.hpc_core_hours_threshold = 0  # every submit goes to the PI: capture what it shows
    script = write_script(site)
    result = submit(site, script)
    assert result["submitted"] and result["job_id"] == "1000" and result["transfer"]["base"] == "t1"
    spec = result["transfer"]
    remote = site.remote(spec["script"]).read_text(encoding="utf-8")
    assert f"cd {ROOT}/{spec['work']}" in remote and str(site.workdir) not in remote  # the local folder is gone
    assert f"cd {ROOT}/{spec['work']}" in site.asked["detail"]["script_preview"]  # the PI saw what runs there
    assert site.remote(spec["work"]).is_dir() and site.remote(spec["dir"] + "/logs").is_dir()
    assert spec["dir"].startswith("t1/job-") and oct(site.remote(spec["dir"]).stat().st_mode & 0o777) in ("0o700", "0o777")
    assert result["script_sha256"] == sha(script.read_bytes())  # the record is of what the agent wrote


def test_a_failed_upload_means_nothing_is_submitted(site, monkeypatch):
    script = write_script(site)

    def broken(self, rel, data, **k):
        raise ht.HashMismatch("the cluster holds different bytes")

    monkeypatch.setattr(ht.Transfer, "upload_bytes", broken)
    result = submit(site, script)
    assert "error" in result and "not submitted" in result["error"] and "submitted" not in result
    assert not (site.tmp / "slurm.json").exists()  # sbatch never ran


def test_small_inputs_go_along_and_a_path_out_of_the_task_folder_is_refused(site):
    script = write_script(site)
    (site.workdir / "ref.txt").write_text("reference", encoding="utf-8")
    ok = submit(site, script, inputs=["ref.txt"])
    assert ok["submitted"] and site.remote(ok["transfer"]["work"] + "/ref.txt").read_text(encoding="utf-8") == "reference"
    (site.tmp / "secret.txt").write_text("secret", encoding="utf-8")
    bad = submit(site, script, inputs=["../secret.txt"])
    assert "error" in bad and not list((site.sandbox / "hpc" / "work" / "t1").glob("job-*/work/secret.txt"))


def test_a_script_without_a_cd_line_is_refused(site):
    script = site.workdir / "jobs" / "plain_1.sh"
    script.write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    result = submit(site, script)
    assert "error" in result and "cd" in result["error"]


def test_without_a_remote_workdir_nothing_is_uploaded(site, tmp_path):
    hpc_site.save_profile(hpc_site.SiteProfile(
        name="fake", access_method="gateway_tunnel", remote_exec=False, scheduler="slurm",
        connect_argv=[sys.executable, "-I", GATEWAY, "--slurm-state", str(tmp_path / "slurm.json")]))
    sched.close_sessions()
    scheduler = sched.Scheduler(site.settings.hpc)
    assert scheduler.session() is not None and scheduler.transfer() is None  # a shared file system: paths are used as they are


def finished_job(site, result):
    return {"job_id": result["job_id"], "task_id": "t1", "agent_id": "a1", "terminal": True, "workdir": str(site.workdir),
            "transfer": {**result["transfer"], "fetch_to": str(site.workdir / "outputs")}}


def stub_runner(site, job):
    from labhq.runner.daemon import Runner

    runner = Runner.__new__(Runner)
    runner.s, runner.scheduler = site.settings, sched.Scheduler(site.settings.hpc)
    runner.jobs, runner.task_req, runner.events = {"1000": job}, {"t1": "req_1"}, []
    runner.store = SimpleNamespace(put=lambda *a, **k: None)
    runner.notified, runner.tasks = set(), {}

    async def emit(event):
        runner.events.append(event)

    runner.emit = emit
    return runner


def test_a_finished_job_brings_its_logs_and_small_results_home(site):
    result = submit(site, write_script(site))
    work, logs = site.remote(result["transfer"]["work"]), site.remote(result["transfer"]["dir"] + "/logs")
    (work / "result.txt").write_text("answer", encoding="utf-8")
    (work / "sub").mkdir()
    (work / "sub" / "table.tsv").write_bytes(b"a\tb\n")
    (work / "huge.bin").write_bytes(b"x" * (1024 * 1024 + 1))
    (logs / "align_20261107.out").write_text("started\ndone\n", encoding="utf-8")
    job = finished_job(site, result)
    runner = stub_runner(site, job)
    asyncio.run(runner._fetch_finished_jobs())
    assert job["fetched"] and runner._fetch_settled(job)
    out = site.workdir / "outputs"
    assert (out / "result.txt").read_text(encoding="utf-8") == "answer"
    assert (out / "sub" / "table.tsv").read_bytes() == b"a\tb\n"
    assert not (out / "huge.bin").exists()  # left on the cluster
    assert (site.workdir / "jobs" / "logs" / "align_20261107.out").read_text(encoding="utf-8") == "started\ndone\n"
    manifest = json.loads((site.workdir / "jobs" / "logs" / "align_20261107.fetch.json").read_text(encoding="utf-8"))
    assert {f["path"]: f["sha256"] for f in manifest["fetched"]} == {"result.txt": sha(b"answer"),
                                                                     "sub/table.tsv": sha(b"a\tb\n")}
    assert [s["path"] for s in manifest["skipped"] if s["path"] == "huge.bin"] == ["huge.bin"]
    assert any("가져오지 않았습니다" in e.data["text"] for e in runner.events)  # the PI hears about the file left behind


def test_a_failed_fetch_is_retried_then_reported_and_the_agent_waits_meanwhile(site, monkeypatch):
    result = submit(site, write_script(site))
    job = finished_job(site, result)
    runner = stub_runner(site, job)

    def broken(self, *a, **k):
        raise ht.TransferError("shell gone")

    monkeypatch.setattr(ht.Transfer, "list_files", broken)
    for expected in (False, False, True):
        asyncio.run(runner._fetch_finished_jobs())
        assert runner._fetch_settled(job) is expected
    assert not job.get("fetched") and "shell gone" in job["fetch_error"]
    assert any("가져오지 못했습니다" in e.data["text"] for e in runner.events)
    asyncio.run(runner._fetch_finished_jobs())
    assert job["fetch_tries"] == 3  # no fourth try


def test_a_refused_upload_through_a_link_creates_nothing_outside(cluster, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    transfer = cluster.transfer()
    transfer.ensure_dir("")
    link(outside, cluster.path("escape"))
    with pytest.raises(ht.PathRejected):
        transfer.upload_bytes("escape/new/file.txt", b"x")
    assert not list(outside.iterdir())  # the folder behind the link was not even created


# ---- the tool's own path: hpc_submit -> broker -> submit_job -> the cluster ----

def run_tool(site, monkeypatch, **tool_args):
    import labhq.tools.hpc_mcp as hpc
    from labhq.runner.hpc_jobs import submit_job

    scheduler = sched.Scheduler(site.settings.hpc)

    def prepare(workdir, script_path, logs, body, *args, **kwargs):
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(body, encoding="utf-8")

    async def approve(payload):
        return {"approved": True}

    async def broker(path, payload, timeout):
        return await submit_job(site.settings, scheduler, site.workdir, payload, approve)

    monkeypatch.setattr(hpc, "S", site.settings)
    monkeypatch.setattr(hpc, "WORKDIR", site.workdir)
    monkeypatch.setattr(hpc, "SCHED", scheduler)
    monkeypatch.setattr(hpc, "TASK", "t1")
    monkeypatch.setattr(hpc, "AGENT", "a1")
    monkeypatch.setattr(hpc, "_prepare_job_files", prepare)
    monkeypatch.setattr(hpc, "_broker", broker)
    return json.loads(asyncio.run(hpc.hpc_submit("echo hi", "align", **tool_args)))


def test_inputs_given_to_the_tool_reach_the_cluster(site, monkeypatch):
    (site.workdir / "data").mkdir()
    (site.workdir / "data" / "ref.txt").write_text("reference", encoding="utf-8")
    out = run_tool(site, monkeypatch, inputs=["data/ref.txt"])
    assert out["submitted"] and next((site.sandbox / "hpc" / "work" / "t1").glob("job-*/work/data/ref.txt")).read_text(
        encoding="utf-8") == "reference"


@pytest.mark.parametrize("bad", [["../secret.txt"], ["/etc/passwd"], ["a"] * 21, [""], "ref.txt", [1]])
def test_the_tool_refuses_inputs_that_are_not_a_short_list_of_workspace_paths(site, monkeypatch, bad):
    with pytest.raises(Exception, match="inputs"):
        run_tool(site, monkeypatch, inputs=bad)
    assert not (site.tmp / "slurm.json").exists()


def test_the_runner_refuses_an_inputs_value_that_is_not_a_list(site):
    script = write_script(site)
    result = submit(site, script, inputs="ref.txt")
    assert "error" in result and "list" in result["error"]


def test_an_unparseable_submit_answer_does_not_carry_the_site_to_the_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("LABHQ_SITES_DIR", str(tmp_path / "sites"))
    monkeypatch.setenv("SLURM_FAKE_SBATCH_OUT", "Submitted on gw.example.org as user bob")
    monkeypatch.setenv("SLURM_FAKE_SANDBOX", str(tmp_path))
    argv = [sys.executable, "-I", GATEWAY, "--slurm-state", str(tmp_path / "slurm.json"),
            "-p", "2222", "bob@gw.example.org"]
    hpc_site.save_profile(hpc_site.SiteProfile(name="fake", access_method="ssh", connect_argv=argv, remote_exec=True,
                                               scheduler="slurm"))
    scheduler = sched.Scheduler(Settings.model_validate({
        "runner": {"state_dir": str(tmp_path / "state")},
        "hpc": {"scheduler": "slurm", "user": "lab", "site_profile": "fake"}}).hpc)
    try:
        with pytest.raises(RuntimeError) as caught:
            scheduler.submit("/work/job.sh", "align")
    finally:
        sched.close_sessions()
    message = str(caught.value)
    assert "cannot parse job id" in message and "may have been submitted" in message
    assert "gw.example.org" not in message and "bob" not in message


def test_the_cluster_name_of_a_slurm_m_submit_is_hidden_when_it_is_the_site(tmp_path, monkeypatch):
    from subprocess import CompletedProcess

    monkeypatch.setattr(sched.subprocess, "run", lambda args, **k: CompletedProcess(args, 0, "4100;gw.example.org\n", ""))
    scheduler = sched.Scheduler(Settings.model_validate({
        "runner": {"state_dir": str(tmp_path / "state")},
        "hpc": {"scheduler": "slurm", "user": "lab", "ssh_host": "bob@gw.example.org"}}).hpc)
    with pytest.raises(RuntimeError) as caught:
        scheduler.submit("/work/job.sh", "align")
    assert "gw.example.org" not in str(caught.value) and "4100" in str(caught.value)


# ---- review round: a fetch into a linked folder, transient fetch errors, the upload's time ----

def test_results_are_not_written_through_a_linked_output_folder(site, tmp_path):
    result = submit(site, write_script(site))
    (site.remote(result["transfer"]["work"]) / "escaped.txt").write_text("data", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    link(outside, site.workdir / "outputs")  # the agent made output_dir a link out of its workspace
    transfer = sched.Scheduler(site.settings.hpc).transfer()
    spec = {**result["transfer"]}
    with pytest.raises(ht.PathRejected):
        ht.fetch_results(transfer, spec, site.workdir / "jobs" / "logs", site.workdir / "outputs", base=site.workdir)
    assert not list(outside.iterdir())


def test_a_linked_logs_folder_or_a_folder_outside_the_task_is_refused(site, tmp_path):
    for bad in (tmp_path / "elsewhere", site.workdir / ".." / "x"):
        with pytest.raises(ht.PathRejected):
            ht.safe_local_root(site.workdir, bad)
    outside = tmp_path / "outside"
    outside.mkdir()
    link(outside, site.workdir / "jump")
    with pytest.raises(ht.PathRejected):
        ht.safe_local_root(site.workdir, site.workdir / "jump" / "deeper")
    made = ht.safe_local_root(site.workdir, site.workdir / "hpc_out")
    assert made.is_dir()


def test_a_transient_fetch_error_fails_the_whole_fetch_but_a_lasting_reason_only_skips(site, monkeypatch):
    result = submit(site, write_script(site))
    work = site.remote(result["transfer"]["work"])
    (work / "a.txt").write_text("aa", encoding="utf-8")
    (work / "huge.bin").write_bytes(b"x" * (1024 * 1024 + 1))
    transfer = sched.Scheduler(site.settings.hpc).transfer()
    spec = {**result["transfer"], "fetch_to": str(site.workdir / "outputs")}
    manifest = ht.fetch_results(transfer, spec, site.workdir / "jobs" / "logs", site.workdir / "outputs", base=site.workdir)
    assert [f["path"] for f in manifest["fetched"]] == ["a.txt"] and manifest["skipped"][-1]["path"] == "huge.bin"
    real = ht.Transfer.fetch_file

    def flaky(self, rel, *a, **k):
        if rel.endswith("a.txt"):
            raise ht.HashMismatch("the shell garbled a block")
        return real(self, rel, *a, **k)

    monkeypatch.setattr(ht.Transfer, "fetch_file", flaky)
    with pytest.raises(ht.HashMismatch):  # not "skipped": the runner retries the fetch
        ht.fetch_results(transfer, spec, site.workdir / "jobs" / "logs", site.workdir / "outputs", base=site.workdir)


def test_a_log_that_does_not_exist_is_skipped_not_an_error(site):
    result = submit(site, write_script(site))
    transfer = sched.Scheduler(site.settings.hpc).transfer()
    manifest = ht.fetch_results(transfer, {**result["transfer"]}, site.workdir / "jobs" / "logs",
                                site.workdir / "outputs", base=site.workdir)
    assert manifest["logs"] == [] and len(manifest["skipped"]) == 2 and transfer.exists(result["transfer"]["script"])


def test_an_upload_that_runs_out_of_time_stops_and_nothing_is_submitted(site):
    script = write_script(site)
    transfer = sched.Scheduler(site.settings.hpc).transfer()
    spec = ht.job_spec("t1", script.stem)
    with pytest.raises(ht.TransferError, match="time allowed"):
        ht.upload_job(transfer, spec, script.read_text(encoding="utf-8"), site.workdir, budget_s=-1)
    assert transfer.deadline is None  # the allowance does not leak into later commands
    assert not (site.tmp / "slurm.json").exists()


def test_the_submit_wait_includes_the_whole_upload_allowance_only_where_files_cross(site, monkeypatch):
    import labhq.tools.hpc_mcp as hpc

    monkeypatch.setattr(hpc, "S", site.settings)
    monkeypatch.setattr(hpc, "SCHED", sched.Scheduler(site.settings.hpc))
    monkeypatch.setattr(hpc, "SESSION_ERROR", None)
    one = hpc._hpc_wait_s()
    assert hpc._submit_wait_s() == one + one + ht.UPLOAD_BUDGET_S  # upload commands, the allowance, then the submit
    hpc_site.save_profile(hpc_site.SiteProfile(
        name="fake", access_method="gateway_tunnel", remote_exec=False, scheduler="slurm",
        connect_argv=[sys.executable, "-I", GATEWAY, "--slurm-state", str(site.tmp / "slurm.json")]))  # no remote_workdir
    sched.close_sessions()
    monkeypatch.setattr(hpc, "SCHED", sched.Scheduler(site.settings.hpc))
    assert hpc._submit_wait_s() == hpc._hpc_wait_s()


# ---- review round: errors keep the site out, and results too deep to list are named ----

def test_a_transfer_error_does_not_carry_the_site_or_the_work_folder(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    words = ("gw.example.org", "bob")
    seen = NS(output="mkdir: cannot create /scratch/bob/proj/t1: Permission denied (bob@gw.example.org)")
    transfer = ht.Transfer(lambda command: NS(exit_code=1, output=seen.output), "/scratch/bob/proj",
                           redact=lambda text: hs.redact(text, words))
    with pytest.raises(ht.TransferError) as caught:
        transfer.ensure_dir("t1")
    message = str(caught.value)
    assert "gw.example.org" not in message and "bob" not in message and "/scratch" not in message
    assert "<workdir>" in message and "Permission denied" in message


def test_the_scheduler_gives_its_transfer_the_sites_redactor(site, monkeypatch):
    transfer = sched.Scheduler(site.settings.hpc).transfer()
    assert transfer._redact is not None
    assert transfer._hide("see " + ROOT + "/t1 for details") == "see <workdir>/t1 for details"


def test_results_deeper_than_the_listing_are_named_as_left_behind(site):
    result = submit(site, write_script(site))
    work = site.remote(result["transfer"]["work"])
    (work / "a" / "b" / "c").mkdir(parents=True)
    (work / "a" / "b" / "c" / "deep.txt").write_text("deep", encoding="utf-8")
    (work / "a" / "top.txt").write_text("top", encoding="utf-8")
    transfer = sched.Scheduler(site.settings.hpc).transfer()
    manifest = ht.fetch_results(transfer, {**result["transfer"]}, site.workdir / "jobs" / "logs",
                                site.workdir / "outputs", base=site.workdir)
    assert [f["path"] for f in manifest["fetched"]] == ["a/top.txt"]
    deep = [s for s in manifest["skipped"] if s["path"] == "a/b/c/deep.txt"]
    assert deep and "deeper than 3" in deep[0]["reason"]


# ---- the boundary: nothing the runner sends out carries the site ----

LEAK = "ssh: connect to gw.example.org port 2222 as bob failed in /hpc/work/t1: refused"


def leaky_runner(site):
    runner = stub_runner(site, {"job_id": "1000", "task_id": "t1", "agent_id": "a1", "terminal": False,
                                "workdir": str(site.workdir)})
    runner.workspaces = {"t1": SimpleNamespace(dir=site.workdir, task=SimpleNamespace(meta={}))}
    runner.active_roots = {"t1": (False,)}
    runner._owns_job = lambda identity, **kwargs: asyncio.sleep(0, result=True)
    argv = [sys.executable, "-I", GATEWAY, "-p", "2222", "bob@gw.example.org"]
    hpc_site.save_profile(hpc_site.SiteProfile(name="fake", access_method="ssh", connect_argv=argv, remote_exec=True,
                                               scheduler="slurm", remote_workdir="/hpc/work"))
    sched.close_sessions()
    runner.s = Settings.model_validate({"runner": {"state_dir": str(site.tmp / "state")},
                                        "hpc": {"scheduler": "slurm", "user": "lab", "site_profile": "fake"}})
    runner.scheduler = sched.Scheduler(runner.s.hpc)
    return runner


def assert_clean(text):
    for word in ("gw.example.org", "bob", "2222", "/hpc/work"):
        assert word not in text, (word, text)


def test_what_goes_back_to_a_tool_never_carries_the_site(site, monkeypatch):
    runner = leaky_runner(site)

    def boom(self, *a, **k):
        raise RuntimeError(LEAK)

    monkeypatch.setattr(sched.Scheduler, "status", boom)
    monkeypatch.setattr(sched.Scheduler, "queue", boom)
    monkeypatch.setattr(sched.Scheduler, "cancel", boom)
    monkeypatch.setattr(sched.Scheduler, "submit", boom)
    for op in ("status", "queue", "cancel"):
        reply = asyncio.run(runner._on_scheduler_request(op, {"task_id": "t1", "agent_id": "a1", "job_id": "1000"}))
        assert_clean(reply["error"])
        assert "refused" in reply["error"]  # the reason stays, the site goes
    (site.workdir / "jobs").mkdir(exist_ok=True)
    script = write_script(site)
    runner.s.policy.approvals.hpc_core_hours_threshold = 1000
    runner.submitting, runner.broker = {}, SimpleNamespace()
    reply = asyncio.run(type(runner)._on_submit(runner, {"task_id": "t1", "agent_id": "a1", "script": str(script),
                                                         "name": "align", "cores": 1, "mem": "1G", "walltime": "00:10:00"}))
    assert "error" in reply
    assert_clean(reply["error"])


def test_only_the_text_fields_are_hidden_not_ids_names_or_paths(site):
    runner = leaky_runner(site)
    reply = runner._hide_hpc({"submitted": True, "job_id": "1000", "name": "bob_align", "script": "C:/Users/bob/job.sh",
                              "status": {"state": "failed", "detail": "bob@gw.example.org said no"},
                              "queue": [{"job_id": "1", "detail": "on gw.example.org"}], "result": "ok /hpc/work/x"})
    assert reply["name"] == "bob_align" and reply["script"] == "C:/Users/bob/job.sh" and reply["job_id"] == "1000"
    assert_clean(reply["status"]["detail"]) and assert_clean(reply["queue"][0]["detail"]) and assert_clean(reply["result"])


def test_a_notice_and_a_fetch_error_in_the_feed_never_carry_the_site(site, monkeypatch):
    runner = leaky_runner(site)
    runner.jobs["1000"].update(terminal=True, transfer={"base": "t1", "dir": "t1/job-x", "work": "t1/job-x/work", "logs": [], "stem": "x",
                                                        "fetch_to": str(site.workdir / "outputs")})
    asyncio.run(runner._emit_hpc_notice("needs_human", LEAK))

    def broken(self, *a, **k):
        raise ht.TransferError(LEAK)

    monkeypatch.setattr(ht.Transfer, "list_deeper", broken)
    for _ in range(3):
        asyncio.run(runner._fetch_finished_jobs())
    texts = [e.data["text"] for e in runner.events]
    assert len(texts) == 2 and all(assert_clean(text) is None for text in texts)
    assert_clean(runner.jobs["1000"]["fetch_error"])


# ---- review round: inputs are read through held handles, and unchanged inputs are not results ----

def test_a_local_input_is_read_through_handles_not_by_path(tmp_path, cluster, monkeypatch):
    base = tmp_path / "work"
    (base / "d").mkdir(parents=True)
    (base / "d" / "in.txt").write_bytes(b"input")

    def forbidden(self):
        raise AssertionError("the file was read by its path")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    assert ht.read_confined(base, "d/in.txt", 100) == b"input"
    info = cluster.transfer().upload_file(base / "d" / "in.txt", "t1/in.txt", root=base)
    assert info["sha256"] == sha(b"input")


def test_read_confined_refuses_links_and_big_files_and_other_kinds(tmp_path):
    base = tmp_path / "work"
    (base / "d").mkdir(parents=True)
    (base / "d" / "big.bin").write_bytes(b"x" * 11)
    with pytest.raises(ht.TooLarge):
        ht.read_confined(base, "d/big.bin", 10)
    with pytest.raises(ht.PathRejected):
        ht.read_confined(base, "d", 10)  # a folder
    with pytest.raises(ht.PathRejected):
        ht.read_confined(base, "d/missing.txt", 10)
    with pytest.raises(ht.PathRejected):
        ht.read_confined(base, "../work/d/big.bin", 100)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    link(outside / "secret.txt", base / "d" / "alias.txt")
    link(outside, base / "jump")
    with pytest.raises(ht.PathRejected):
        ht.read_confined(base, "d/alias.txt", 100)  # a link as the file
    with pytest.raises(ht.PathRejected):
        ht.read_confined(base, "jump/secret.txt", 100)  # a link as a folder on the way


def test_an_input_the_job_did_not_change_is_not_fetched_back_but_a_changed_one_is(site):
    (site.workdir / "ref.txt").write_text("reference", encoding="utf-8")
    (site.workdir / "other.txt").write_text("other", encoding="utf-8")
    result = submit(site, write_script(site), inputs=["ref.txt", "other.txt"])
    assert set(result["transfer"]["inputs"]) == {"ref.txt", "other.txt"}
    work = site.remote(result["transfer"]["work"])
    (work / "other.txt").write_text("other, rewritten by the job", encoding="utf-8")
    (work / "result.txt").write_text("answer", encoding="utf-8")
    transfer = sched.Scheduler(site.settings.hpc).transfer()
    manifest = ht.fetch_results(transfer, {**result["transfer"]}, site.workdir / "jobs" / "logs",
                                site.workdir / "outputs", base=site.workdir)
    assert sorted(f["path"] for f in manifest["fetched"]) == ["other.txt", "result.txt"]  # ref.txt stayed home
    assert not (site.workdir / "outputs" / "ref.txt").exists()


# ---- the job folder is made fresh: nothing laid beforehand, no sharing between jobs ----

def test_each_job_gets_its_own_unguessable_folder(site):
    first, second = submit(site, write_script(site)), submit(site, write_script(site, name="call"))
    one, two = first["transfer"], second["transfer"]
    assert one["dir"] != two["dir"] and one["dir"].startswith("t1/job-") and two["dir"].startswith("t1/job-")
    assert site.remote(one["dir"]).is_dir() and site.remote(two["dir"]).is_dir()
    assert len(one["dir"].split("job-")[1]) == 12


def test_a_job_folder_that_already_exists_is_refused_not_reused(cluster):
    transfer = cluster.transfer()
    transfer.ensure_dir("t1")
    cluster.path("t1/job-seeded").mkdir()
    (cluster.path("t1/job-seeded") / "planted.txt").write_text("planted", encoding="utf-8")
    with pytest.raises(ht.TransferError, match="making the job folder"):
        transfer.make_fresh_dir("t1/job-seeded")
    assert (cluster.path("t1/job-seeded") / "planted.txt").exists()  # left alone, not written into


def test_a_link_planted_at_the_job_folder_name_is_refused(cluster, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    transfer = cluster.transfer()
    transfer.ensure_dir("t1")
    link(outside, cluster.path("t1/job-planted"))
    with pytest.raises(ht.TransferError):
        transfer.make_fresh_dir("t1/job-planted")
    assert not list(outside.iterdir())


def test_a_fresh_folder_is_made_with_mode_700_and_nothing_else_is_in_it(cluster):
    transfer = cluster.transfer()
    real = transfer.make_fresh_dir("t1/job-new")
    assert real.endswith("/hpc/work/t1/job-new")
    assert list(cluster.path("t1/job-new").iterdir()) == []
    seen = []
    real_run = transfer.run
    transfer.run = lambda command: (seen.append(command), real_run(command))[1]
    transfer.make_fresh_dir("t1/job-other")
    assert any(command.startswith("mkdir -m 700 -- ") and " -p" not in command for command in seen)  # strict, no -p
