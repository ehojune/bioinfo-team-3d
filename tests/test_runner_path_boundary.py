"""Runner wire paths are independent of the gateway host; no network or WSL calls."""
import asyncio
import json
import re
from dataclasses import dataclass, is_dataclass
from pathlib import Path, PurePath
from types import SimpleNamespace
from typing import get_args, get_type_hints

import pytest

from labhq.gateway.server import Hub
from test_wsl_setup import settings


def connect(hub, runner_id, runtime):
    sent = []

    async def send(payload):
        sent.append(json.loads(payload))

    hub.runners[runner_id] = SimpleNamespace(send_text=send)
    hub.runner_locks[runner_id] = asyncio.Lock()
    hub.runner_capabilities[runner_id] = {"runtime_env": runtime}
    return sent


@pytest.mark.asyncio
async def test_remote_gateway_wsl_result_reaches_windows_runner(settings, monkeypatch):
    from labhq.gateway import server

    hub = Hub(settings)
    # Replace only this module's OS view, not pathlib's global OS module.
    monkeypatch.setattr(server, "os", SimpleNamespace(name="posix"))
    connect(hub, "wsl", "wsl")
    sent = connect(hub, "native", "windows")
    future = asyncio.get_running_loop().create_future()
    hub.futures["first"] = future
    await hub.on_runner_message("wsl", {"type": "task.result", "task_id": "first", "data": {
        "task_id": "first", "agent_id": "bioinfo-agent", "ok": True,
        "workdir": "/mnt/c/Shared/first", "outputs": ["outputs/table.tsv"]}})
    result = future.result()
    assert result.workdir == "C:/Shared/first"
    await hub.send_runner("native", {"type": "task", "task": {"meta": {
        "upstream_dirs": [result.workdir], "upstream_steps": {"first": result.workdir}}}})
    assert sent[-1]["task"]["meta"]["upstream_steps"] == {"first": "C:/Shared/first"}
    # A persisted pre-upgrade /mnt path must also work on Windows dispatch.
    await hub.send_runner("native", {"type": "task", "task": {"meta": {
        "workdir": "/mnt/c/Shared/resume"}}})
    assert sent[-1]["task"]["meta"]["workdir"] == "C:/Shared/resume"


@pytest.mark.asyncio
async def test_wsl_project_and_reference_dispatch(settings):
    hub = Hub(settings)
    sent = connect(hub, "wsl", "wsl")
    meta = {"project_dirs": ["C:/Shared/project"], "reference_dirs": ["D:/Refs/input.tsv"]}
    await hub.send_runner("wsl", {"type": "task", "task": {"meta": meta}})
    assert sent[-1]["task"]["meta"] == {
        "project_dirs": ["/mnt/c/Shared/project"], "reference_dirs": ["/mnt/d/Refs/input.tsv"]}
    assert meta["project_dirs"] == ["C:/Shared/project"]


def test_wsl_reference_root_configuration(settings):
    from labhq import wsl_setup
    from labhq.settings import ProjectSettings

    settings.runner.workspace_root = "C:/Shared/Runs"
    settings.policy.private_paths = []
    settings.runner.reference_roots = ["D:/Refs"]
    settings.projects = [ProjectSettings(id="project", name="Project", local_dir="C:/Shared/project")]
    shared = wsl_setup._shared_settings(settings)
    assert shared["reference_roots"] == ["/mnt/d/Refs", "/mnt/c/Shared/project"]


def unregistered_paths(model):
    """Find path names/types independently of the converter's field selection."""
    from pydantic import BaseModel
    from labhq.models import PathShape
    from labhq.wsl_paths import PATH_FIELDS

    def parts(hint):
        yield hint
        for arg in get_args(hint):
            yield from parts(arg)

    missing, seen = set(), set()

    def visit(cls):
        if cls in seen:
            return
        seen.add(cls)
        for name, hint in get_type_hints(cls, include_extras=True).items():
            hints = list(parts(hint))
            shapes = [part for part in hints if isinstance(part, PathShape)]
            named = re.search(r"(^|_)(paths?|dirs?|roots?|cwd|workdir|outputs|files)($|_)", name)
            typed = any(isinstance(part, type) and issubclass(part, PurePath) for part in hints)
            if (named or typed or shapes) and name not in PATH_FIELDS:
                missing.add(f"{cls.__name__}.{name}")
            if shapes and name in PATH_FIELDS:
                assert shapes == [PATH_FIELDS[name]], name
            for part in hints:
                if isinstance(part, type) and (is_dataclass(part) or issubclass(part, BaseModel)):
                    visit(part)

    visit(model)
    return missing


def test_model_path_field_list_is_complete():
    from labhq.models import Task, TaskResult, Event, RunnerPathFields, AgentSpec
    from labhq.intake import Reference

    for model in (Task, TaskResult, Event, RunnerPathFields, AgentSpec, Reference):
        assert unregistered_paths(model) == set()


def test_completeness_guard_detects_new_named_and_typed_fields():
    from pydantic import create_model
    from labhq.models import Task, RunnerPathFields

    future = create_model("FutureTask", __base__=Task, checkpoint_path=(str, ""), artifact=(Path, Path(".")))

    @dataclass
    class FutureMeta(RunnerPathFields):
        cache_dir: str

    assert unregistered_paths(future) == {"FutureTask.checkpoint_path", "FutureTask.artifact"}
    assert unregistered_paths(FutureMeta) == {"FutureMeta.cache_dir"}


@pytest.mark.parametrize("target,prefix", [("wsl", "/mnt/c/Shared"), ("windows", "C:/Shared")])
def test_task_result_event_manifest_path_shapes_and_opaque_content(target, prefix):
    from labhq.wsl_paths import message_paths

    source = "/mnt/c/Shared" if target == "windows" else "C:/Shared"
    opaque = {"path": source + "/literal", "outputs": [source + "/literal"]}
    message = {"task": {"prompt": source, "context": source, "output_schema": opaque, "meta": {
        "project_dirs": [source + "/project"], "reference_dirs": [source + "/refs"],
        "upstream_steps": {"first": source + "/first"}, "upstream_dirs": [source + "/first"],
        "source_workdir": source + "/source", "consult_refs": ["outputs/ref.tsv"],
        "workspace_files": opaque,
        "references": [{"kind": "path", "value": source + "/refs/input.tsv"},
                       {"kind": "url", "value": "https://example.org/path"}],
        "paper_skills": [{"skill_dir": source + "/skills"}]}},
        "data": {"workdir": source + "/run", "outputs": ["outputs/a.tsv", source + "/run/outputs/b.tsv"],
                 "missing_outputs": [source + "/missing"], "unreported_outputs": [source + "/extra"],
                 "output_sha256": {source + "/run/outputs/b.tsv": "abc"},
                 "output_tool_use_ids": {source + "/run/outputs/b.tsv": "tool-1"},
                 "output_types": {source + "/run/outputs/b.tsv": {"id": "table"}},
                 "manifest_path": source + "/run/manifest.json", "bundle_path": source + "/bundle",
                 "provenance": {"runs": {"task": {"input_files": [{"path": source + "/input.tsv"}],
                                                  "read_only_roots": {"ref": source + "/refs"}}}},
                 "text": source, "structured": opaque}}
    snapshot = json.dumps(message)
    converted = message_paths(message, target)
    meta, result = converted["task"]["meta"], converted["data"]
    assert meta["project_dirs"] == [prefix + "/project"]
    assert meta["reference_dirs"] == [prefix + "/refs"]
    assert meta["upstream_steps"] == {"first": prefix + "/first"}
    assert meta["source_workdir"] == prefix + "/source"
    assert meta["consult_refs"] == ["outputs/ref.tsv"]
    assert meta["paper_skills"][0]["skill_dir"] == prefix + "/skills"
    assert meta["references"][0]["value"] == prefix + "/refs/input.tsv"
    assert meta["references"][1]["value"] == "https://example.org/path"
    assert result["outputs"] == ["outputs/a.tsv", prefix + "/run/outputs/b.tsv"]
    assert result["workdir"] == prefix + "/run"
    assert result["missing_outputs"] == [prefix + "/missing"]
    assert result["unreported_outputs"] == [prefix + "/extra"]
    for name in ("output_sha256", "output_tool_use_ids", "output_types"):
        assert list(result[name]) == [prefix + "/run/outputs/b.tsv"]
    assert result["manifest_path"] == prefix + "/run/manifest.json"
    assert result["bundle_path"] == prefix + "/bundle"
    run = result["provenance"]["runs"]["task"]
    assert run["input_files"] == [{"path": prefix + "/input.tsv"}]
    assert run["read_only_roots"] == {"ref": prefix + "/refs"}
    assert result["structured"] == meta["workspace_files"] == converted["task"]["output_schema"] == opaque
    assert result["text"] == converted["task"]["prompt"] == converted["task"]["context"] == source
    assert json.dumps(message) == snapshot
    assert message_paths(converted, target) == converted
    assert message_paths(message, "linux") == message


@pytest.mark.asyncio
async def test_event_paths_use_runner_runtime_on_remote_gateway(settings, monkeypatch):
    from labhq.gateway import server

    hub = Hub(settings)
    connect(hub, "wsl", "wsl")
    monkeypatch.setattr(server, "os", SimpleNamespace(name="posix"))
    published = []

    async def publish(event, **kwargs):
        published.append(event)

    monkeypatch.setattr(hub, "publish", publish)
    await hub.on_runner_message("wsl", {"type": "agent.tool", "data": {
        "name": "Read", "input": {"file_path": "/mnt/d/Refs/a.tsv"}}})
    assert published[-1]["data"]["input"]["file_path"] == "D:/Refs/a.tsv"


@pytest.mark.asyncio
async def test_dispatched_project_and_reference_files_are_readable(settings, tmp_path, monkeypatch):
    from labhq.models import Task
    from labhq.runner.daemon import Runner
    from labhq import wsl_paths

    project, refs = tmp_path / "project", tmp_path / "refs"
    project.mkdir()
    refs.mkdir()
    (project / "data.tsv").write_text("project input", encoding="utf-8")
    (refs / "ref.tsv").write_text("reference input", encoding="utf-8")
    mounts = {"C:/Shared/project": str(project), "D:/Refs": str(refs)}
    original = wsl_paths.shared_path
    monkeypatch.setattr(wsl_paths, "shared_path", lambda raw, target: mounts.get(raw) or original(raw, target))
    settings.runner.reference_roots = [str(refs)]
    hub = Hub(settings)
    sent = connect(hub, "wsl", "wsl")
    await hub.send_runner("wsl", {"type": "task", "task": Task(agent_id="bioinfo-agent", prompt="read", meta={
        "project_dirs": ["C:/Shared/project"], "reference_dirs": ["D:/Refs"]}).model_dump()})
    task = Task.model_validate(sent[-1]["task"])
    assert (Path(task.meta["project_dirs"][0]) / "data.tsv").read_text() == "project input"
    kept, skipped, refused = Runner(settings)._reference_dirs(task, task.meta["project_dirs"])
    assert not skipped and not refused
    assert (Path(kept[0]) / "ref.tsv").read_text() == "reference input"
