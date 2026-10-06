from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
from pathlib import Path

import pytest

from labhq.adapters.held_dir import HeldDir
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.request_bundle import build_request_bundle
from labhq.runner.daemon import Runner
from labhq.runner import workspace as workspace_mod
from labhq.runner.workspace import InputHashCache, TaskWorkspace, portable_input_path
from labhq.settings import Settings


def _workspace(tmp_path: Path) -> TaskWorkspace:
    task = Task(id="task-input", request_id="request-input", agent_id="worker", prompt="read")
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.claude_code)
    return TaskWorkspace(tmp_path / "runs", task, agent)


def _scan(ws: TaskWorkspace, root: Path, cache: InputHashCache, **kwargs):
    return ws.scan_input_records(
        [(root, "~/inputs")], kwargs.get("restricted", []), kwargs.get("private", []),
        kwargs.get("max_entries", 100), kwargs.get("max_depth", 8),
        kwargs.get("max_file_bytes", 1024), kwargs.get("max_total_bytes", 4096), cache)


def test_input_folder_hashes_files_and_reuses_the_persistent_cache_without_reading(tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    root.mkdir()
    source = root / "small.tsv"
    source.write_text("gene\nTP53\n", encoding="utf-8")
    ws = _workspace(tmp_path)
    cache_path = tmp_path / "state" / "input-sha256-cache.json"

    rows, notes = _scan(ws, root, InputHashCache(cache_path))

    assert notes == []
    assert rows == [{
        "path": "~/inputs/small.tsv", "size": source.stat().st_size,
        "mtime_ns": source.stat().st_mtime_ns,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }]
    real_sha = workspace_mod.hashlib.sha256

    def no_second_read(*args, **kwargs):
        raise AssertionError("cache hit read the input file again")

    monkeypatch.setattr(workspace_mod.hashlib, "sha256", no_second_read)
    cached, notes = _scan(ws, root, InputHashCache(cache_path))
    monkeypatch.setattr(workspace_mod.hashlib, "sha256", real_sha)
    assert notes == [] and cached[0]["sha256"] == rows[0]["sha256"]
    assert cached[0]["cached"] is True


ORIGINAL = "gene,TP53".encode()
REPLACED = "gene,KRAS".encode()  # same length as ORIGINAL


def test_same_size_replacement_with_restored_mtime_is_hashed_again(tmp_path):
    """PR #442 review: size and mtime alone let `cp -p` or os.utime hide a replaced file behind the cache."""
    root = tmp_path / "inputs"
    root.mkdir()
    source = root / "small.tsv"
    source.write_bytes(ORIGINAL)
    ws = _workspace(tmp_path)
    cache_path = tmp_path / "state" / "input-sha256-cache.json"
    first, _ = _scan(ws, root, InputHashCache(cache_path))
    before = source.stat()
    replacement = root / "replacement.tmp"
    replacement.write_bytes(REPLACED)
    os.replace(replacement, source)
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source.stat().st_size == before.st_size and source.stat().st_mtime_ns == before.st_mtime_ns

    again, _ = _scan(ws, root, InputHashCache(cache_path))
    assert again[0]["sha256"] == hashlib.sha256(REPLACED).hexdigest() != first[0]["sha256"]
    assert "cached" not in again[0]


def test_input_size_limit_and_corrupt_cache_rebuild_do_not_read_too_large_file(tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "large.bin").write_bytes(b"1234")
    cache_path = tmp_path / "state" / "input-sha256-cache.json"
    cache_path.parent.mkdir()
    cache_path.write_text("not json", encoding="utf-8")
    real_open = HeldDir.open_read_file

    def no_large_open(self, name):
        if name == "large.bin":
            raise AssertionError("too-large input was opened")
        return real_open(self, name)

    monkeypatch.setattr(HeldDir, "open_read_file", no_large_open)
    rows, _ = _scan(_workspace(tmp_path), root, InputHashCache(cache_path), max_file_bytes=3)

    assert rows[0]["skipped"] == "too_large" and rows[0]["sha256"] is None
    assert json.loads(cache_path.read_text(encoding="utf-8"))["schema"] == InputHashCache.SCHEMA


def test_input_total_byte_limit_skips_the_next_uncached_file(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "a.txt").write_bytes(b"123")
    (root / "b.txt").write_bytes(b"456")

    rows, _ = _scan(_workspace(tmp_path), root, InputHashCache(tmp_path / "state" / "cache.json"),
                    max_total_bytes=3)

    assert rows[0]["sha256"] == hashlib.sha256(b"123").hexdigest()
    assert rows[1]["sha256"] is None and rows[1]["skipped"] == "total_limit"


def test_restricted_and_private_input_roots_are_not_opened(tmp_path, monkeypatch):
    restricted, private = tmp_path / "restricted", tmp_path / "private"
    for root in (restricted, private):
        root.mkdir()
        (root / "secret.txt").write_text("do not read", encoding="utf-8")

    def no_file_open(*_args, **_kwargs):
        raise AssertionError("protected input file was opened")

    monkeypatch.setattr(HeldDir, "open_read_file", no_file_open)
    ws = _workspace(tmp_path)
    rows, notes = ws.scan_input_records(
        [(restricted, "~/restricted"), (private, "~/private")], [restricted], [private],
        100, 8, 1024, 4096, InputHashCache(tmp_path / "state" / "cache.json"))

    assert notes == []
    assert [(row["path"], row["skipped"]) for row in rows] == [
        ("~/restricted", "restricted"), ("~/private", "private")]


def test_input_directory_link_outside_is_recorded_but_not_followed(tmp_path):
    root, outside = tmp_path / "inputs", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "alias"
    try:
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(outside), str(link))
        else:
            os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create a directory link")

    rows, _ = _scan(_workspace(tmp_path), root, InputHashCache(tmp_path / "state" / "cache.json"))

    assert rows == [{"path": "~/inputs/alias", "size": None, "mtime_ns": None,
                     "sha256": None, "skipped": "link"}]
    assert "secret" not in json.dumps(rows)


def test_windows_input_paths_use_forward_slashes_and_home_tilde():
    assert portable_input_path(r"C:\Users\pi\data\table.tsv", r"C:\Users\pi") == "~/data/table.tsv"
    assert portable_input_path(r"D:\cohort\table.tsv", r"C:\Users\pi") == "D:/cohort/table.tsv"


@pytest.mark.asyncio
async def test_runner_records_project_input_hash_in_the_step_manifest(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "cohort.tsv"
    source.write_text("sample\nS1\n", encoding="utf-8")
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.claude_code)
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class Adapter:
        async def run(self, ctx):
            assert str(project.resolve()) in ctx.extra_dirs
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    task = Task(id="task-project", request_id="request-project", agent_id=agent.id, prompt="read",
                meta={"kind": "step", "step_id": "s1", "project_dirs": [str(project)]})

    result = await runner.run_task(task)
    manifest = json.loads((Path(result.workdir) / "manifest.json").read_text(encoding="utf-8"))
    [record] = manifest["runs"][task.id]["input_files"]
    assert record["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert record["size"] == source.stat().st_size and record["mtime_ns"] == source.stat().st_mtime_ns
    assert "changed_during_step" not in record


async def test_input_the_step_rewrites_keeps_its_pre_run_hash_and_is_marked_changed(tmp_path, monkeypatch):
    """PR #442 review: project folders are writable, so the record must hold what the step read."""
    project = tmp_path / "project"
    project.mkdir()
    source = project / "cohort.tsv"
    original = "sample,S1".encode()
    source.write_bytes(original)
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.claude_code)
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class Adapter:
        async def run(self, ctx):
            source.write_bytes("sample,S1 normalized".encode())  # the step rewrites its input in place
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    task = Task(id="task-rewrite", request_id="request-rewrite", agent_id=agent.id, prompt="read",
                meta={"kind": "step", "step_id": "s1", "project_dirs": [str(project)]})

    result = await runner.run_task(task)
    manifest = json.loads((Path(result.workdir) / "manifest.json").read_text(encoding="utf-8"))
    [record] = manifest["runs"][task.id]["input_files"]
    assert record["sha256"] == hashlib.sha256(original).hexdigest()
    assert record["changed_during_step"] is True
    assert "실행 중에 바뀌었습니다" in manifest["runs"][task.id]["input_files_incomplete"]


def test_request_bundle_lists_inputs_and_adds_the_hash_skip_summary(tmp_path):
    settings = Settings()
    settings.runner.workspace_root = str(tmp_path / "runs")
    workdir = Path(settings.runner.workspace_root) / "2026-10-06" / "task_s1"
    output = workdir / "outputs" / "answer.md"
    output.parent.mkdir(parents=True)
    output.write_text("answer\n", encoding="utf-8")
    manifest = {
        "host": platform.node(),
        "runs": {"task-1": {"input_files": [
            {"path": "inputs/s0/table.tsv", "size": 7, "mtime_ns": 10, "sha256": "a" * 64},
            {"path": "~/private", "size": None, "mtime_ns": None, "sha256": None,
             "skipped": "private"},
        ]}},
    }
    (workdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = {"task_id": "task-1", "workdir": str(workdir), "workdir_id": workdir.name,
              "outputs": ["outputs/answer.md"],
              "output_sha256": {"outputs/answer.md": hashlib.sha256(output.read_bytes()).hexdigest()}}
    request = {"id": "input-bundle", "report": "ok", "report_appendix": "",
               "plan": {"steps": [{"id": "s1", "depends_on": ["s0"]}]}, "results": {"s1": result}}

    built = build_request_bundle(request, settings)
    bundle = Path(built["path"])
    with (bundle / "INPUTS.tsv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert rows[0]["path"] == "steps/s1/inputs/s0/table.tsv" and rows[0]["sha256"] == "a" * 64
    assert rows[1]["path"] == "~/private" and rows[1]["skipped"] == "private"
    assert "입력 1개 hash, 1개 생략 (private 1)" in (bundle / "README.md").read_text(encoding="utf-8")
