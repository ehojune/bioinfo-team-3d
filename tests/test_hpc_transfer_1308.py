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
    remote = site.remote(f"t1/jobs/{script.name}").read_text(encoding="utf-8")
    assert f"cd {ROOT}/t1/work" in remote and str(site.workdir) not in remote  # the local folder is gone from the script
    assert f"cd {ROOT}/t1/work" in site.asked["detail"]["script_preview"]  # the PI saw what runs there
    assert site.remote("t1/work").is_dir() and site.remote("t1/jobs/logs").is_dir()
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
    assert ok["submitted"] and site.remote("t1/work/ref.txt").read_text(encoding="utf-8") == "reference"
    (site.tmp / "secret.txt").write_text("secret", encoding="utf-8")
    bad = submit(site, script, inputs=["../secret.txt"])
    assert "error" in bad and not site.remote("t1/work/secret.txt").exists()


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
    work, logs = site.remote("t1/work"), site.remote("t1/jobs/logs")
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
