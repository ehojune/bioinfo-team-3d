"""Fetch a synthetic completed job through the POSIX inventory and runner wake paths."""

import json
import os
from pathlib import PurePosixPath

import pytest

from labhq.runner.daemon import Runner
from tests.test_hpc_remote_data import helper_transfer, prepare
from tests.test_hpc_transfer_1308 import cluster  # noqa: F401 - fixture for the fake gateway
from tests.test_workspace_boundary import _settings


@pytest.mark.skipif(os.name == "nt", reason="remote inventory helper needs POSIX directory descriptors")
@pytest.mark.parametrize("runtime", ["lib/modules", "jre/lib/rt.jar"])
async def test_installed_jdk_does_not_delay_receipt_and_wake(cluster, tmp_path, monkeypatch, runtime):
    transfer = helper_transfer(cluster, fetch_total_bytes=256)
    files = {
        "analysis/result.tsv": b"value\n42\n",
        "analysis/run.log": b"analysis finished\n",
        "jdk-results/release": b"report release",
        "jdk-results/bin/java": b"result named java",
    }
    spec = prepare(cluster, transfer, tmp_path, {
        **files,
        "tools/java-sdk/release": b"Java release metadata\n",
        "tools/java-sdk/bin/java": b"launcher",
        f"tools/java-sdk/{runtime}": b"x" * 240,
    })
    scheduler_log = b"scheduler finished\n"
    for rel in spec["logs"]:
        cluster.path(rel).write_bytes(scheduler_log)

    fetched_files = []
    fetch_file = transfer.fetch_file

    def observe_fetch(rel, *args, **kwargs):
        fetched_files.append(rel)
        return fetch_file(rel, *args, **kwargs)

    monkeypatch.setattr(transfer, "fetch_file", observe_fetch)
    runner = Runner(_settings(tmp_path))
    monkeypatch.setattr(runner.scheduler, "transfer", lambda: transfer)
    job_workdir = tmp_path / "step"
    job_workdir.mkdir()
    outputs = job_workdir / "outputs"
    logs = job_workdir / "jobs" / "logs"
    job = {"job_id": "42", "name": "align", "task_id": "t", "agent_id": "a", "state": "completed",
           "terminal": True, "exit_status": 0, "workdir": str(job_workdir),
           "transfer": {**spec, "fetch_to": str(outputs)}}
    runner.jobs["42"] = job
    runner.task_req["t"] = "r"
    emit_all = runner.emit_all

    async def observe_wake(events, **kwargs):
        if any(event.type == "jobs.finished" for event in events):
            assert job["fetched"]
            assert not any("/tools/java-sdk/" in rel for rel in fetched_files)
            for name, data in files.items():
                assert (outputs / name).read_bytes() == data
            for rel in spec["logs"]:
                assert (logs / PurePosixPath(rel).name).read_bytes() == scheduler_log
            receipt = json.loads((logs / f"{spec['stem']}.fetch.json").read_text(encoding="utf-8"))
            assert {row["path"] for row in receipt["fetched"]} == set(files)
            assert {"path": "tools/java-sdk", "reason": "environment folder"} in receipt["skipped"]
        await emit_all(events, **kwargs)

    monkeypatch.setattr(runner, "emit_all", observe_wake)
    try:
        await runner._wake_finished_jobs()
        await runner._wake_finished_jobs()
        wakes = [event for event in runner.store.pending() if event["type"] == "jobs.finished"]
        assert len(wakes) == 1
        assert wakes[0]["task_id"] == "t"
        assert wakes[0]["data"]["jobs"][0]["exit_status"] == 0
        assert cluster.path(f"{spec['work']}/tools/java-sdk/{runtime}").read_bytes() == b"x" * 240
    finally:
        runner.store.close()
