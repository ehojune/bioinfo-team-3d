"""#1331: job scripts reach the cluster with LF line endings, written from any runner OS.

On a Windows runner labhq wrote the job script in text mode, so it had CRLF, and sbatch refused it ("Batch script
contains DOS line breaks"). A staff script with CRLF passed as an input fails later on the cluster's bash; the submit
refuses it up front instead.
"""
import os

import pytest

from labhq.tools import hpc_mcp
from labhq.tools.scheduler import build_script


def test_job_script_is_written_with_lf(tmp_path):
    work = tmp_path / "task"
    work.mkdir()
    script = work / "jobs" / "s1.sh"
    hpc_mcp._prepare_job_files(work, script, work / "jobs" / "logs",
                               build_script("echo one\r\necho two\n", str(work / "outputs")))
    data = script.read_bytes()
    assert b"\r" not in data and data.startswith(b"#!/bin/bash\n") and b"echo two\n" in data


def test_crlf_shell_inputs(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "fetch.sh").write_bytes(b"#!/bin/bash\r\necho hi\r\n")
    (tmp_path / "scripts" / "ok.sh").write_bytes(b"#!/bin/bash\necho hi\n")
    (tmp_path / "scripts" / "run").write_bytes(b"#!/usr/bin/env bash\r\necho hi\r\n")  # no suffix, shell shebang
    (tmp_path / "scripts" / "tx.py").write_bytes(b"import sys\r\nprint(1)\r\n")  # python reads CRLF fine
    (tmp_path / "scripts" / "table.tsv").write_bytes(b"a\tb\r\n")
    inputs = ["scripts/fetch.sh", "scripts/ok.sh", "scripts/run", "scripts/tx.py", "scripts/table.tsv",
              "scripts/missing.sh"]
    assert hpc_mcp.crlf_shell_inputs(tmp_path, inputs) == ["scripts/fetch.sh", "scripts/run"]


@pytest.mark.skipif(os.name == "nt", reason="a file symlink needs a privilege on Windows")
def test_crlf_check_does_not_follow_links(tmp_path):
    (tmp_path / "elsewhere.sh").write_bytes(b"#!/bin/bash\r\n")
    (tmp_path / "link.sh").symlink_to(tmp_path / "elsewhere.sh")
    assert hpc_mcp.crlf_shell_inputs(tmp_path, ["link.sh"]) == []


async def test_submit_refuses_crlf_input_before_anything_is_written(tmp_path, monkeypatch):
    work = tmp_path / "task"
    (work / "scripts").mkdir(parents=True)
    (work / "scripts" / "fetch.sh").write_bytes(b"#!/bin/bash\r\necho hi\r\n")
    monkeypatch.setattr(hpc_mcp, "WORKDIR", work)
    monkeypatch.setenv("LABHQ_EXECUTION_MODE", "hpc")
    with pytest.raises(Exception) as caught:
        await hpc_mcp.hpc_submit("bash scripts/fetch.sh", "s1", inputs=["scripts/fetch.sh"])
    assert "CRLF" in str(caught.value) and "scripts/fetch.sh" in str(caught.value)
    assert not (work / "jobs").exists()


def test_crlf_check_stays_inside_the_workspace(tmp_path):
    work, outside = tmp_path / "task", tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    (outside / "x.sh").write_bytes(b"#!/bin/bash\r\n")
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(work / "dir"))
    else:
        (work / "dir").symlink_to(outside, target_is_directory=True)
    assert hpc_mcp.crlf_shell_inputs(work, ["dir/x.sh", str(outside / "x.sh")]) == []
