"""An environment step succeeds only when its collected manifest equals the pinned binding (#674)."""
from __future__ import annotations

import json
import os

import pytest

from tests.test_observed_outputs import _runner, _task, _write

MANIFEST = "outputs/env/runner-manifest.json"
LOCK = "outputs/env/requirements.lock.txt"
WINDOWS = os.name == "nt"
PYTHON = ".venv/Scripts/python.exe" if WINDOWS else ".venv/bin/python"
BINDING = dict(version=1, logical_step_id="env", step_id="env", runner_id="lab pc 1",
               runtime="windows" if WINDOWS else "linux", venv_path=".venv", python_path=PYTHON)


async def run_producer(tmp_path, monkeypatch, manifest, *, interpreter=True, binding=None):
    def write(workdir):
        _write(workdir, LOCK, b"pandas==2.2\n")
        _write(workdir, MANIFEST, json.dumps(manifest).encode())
        if interpreter:
            _write(workdir, PYTHON, b"python")

    runner = _runner(tmp_path, monkeypatch, write)
    task = _task(outputs=[LOCK, MANIFEST], environment_binding=BINDING if binding is None else binding)
    return await runner.run_task(task)


@pytest.mark.asyncio
async def test_matching_manifest_and_interpreter_keep_the_environment_step_successful(tmp_path, monkeypatch):
    result = await run_producer(tmp_path, monkeypatch, BINDING)
    assert result.ok, result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("manifest", [
    {}, {**BINDING, "runner_id": "other"}, {**BINDING, "step_id": "env_other"},
    {**BINDING, "runtime": "wsl"}, {**BINDING, "version": 2}], ids=["empty", "stale_runner", "stale_step",
                                                                    "other_runtime", "version"])
async def test_a_manifest_that_differs_from_the_binding_fails_the_step(tmp_path, monkeypatch, manifest):
    result = await run_producer(tmp_path, monkeypatch, manifest)
    assert not result.ok
    assert "does not match the pinned binding" in result.error


@pytest.mark.asyncio
async def test_a_missing_interpreter_fails_the_step(tmp_path, monkeypatch):
    result = await run_producer(tmp_path, monkeypatch, BINDING, interpreter=False)
    assert not result.ok and "pinned binding" in result.error


@pytest.mark.asyncio
async def test_steps_without_a_binding_are_not_checked(tmp_path, monkeypatch):
    def write(workdir):
        _write(workdir, "outputs/table.tsv", b"x\n")

    result = await _runner(tmp_path, monkeypatch, write).run_task(_task(outputs=["outputs/table.tsv"]))
    assert result.ok
