"""#221 할 일 1: a direct run's workspace outputs become its result outputs, so the shadow provenance model sees them.

No plan declares a direct run's outputs. The runner lists outputs/ by the rules a declared output and the shadow
hash follow: regular files only, nothing through a link or junction, nothing in a restricted zone, within caps.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.research import semantics_shadow as shadow
from labhq.runner import workspace as workspace_module
from labhq.runner.daemon import Runner
from labhq.settings import DataZone, Settings
from tests.semantics_shadow_lab import line_for, run_lab


def _runner(tmp_path, monkeypatch, write, settings=None):
    settings = settings or Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.claude_code, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class Adapter:
        async def run(self, ctx):
            write(Path(ctx.workdir))
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    return runner


def _files(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        target = root.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


def _warnings(runner) -> list[str]:
    return [e["data"].get("text", "") for e in runner.store.pending()
            if e["type"] == "agent.log" and e["data"].get("level") == "warn"]


def _direct(tid: str, **meta) -> Task:
    return Task(id=tid, request_id="r1", agent_id="worker", prompt="make a table", meta={"kind": "direct", **meta})


@pytest.mark.asyncio
async def test_a_direct_run_reports_the_files_it_left_in_outputs(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch, lambda wd: _files(wd, {
        "outputs/counts.tsv": "species\tn\n", "outputs/sub/b.txt": "b", "outputs/a.tsv": "a",
        "notes.txt": "outside outputs"}))
    first = await runner.run_task(_direct("task-1"))
    assert first.outputs == ["outputs/a.tsv", "outputs/counts.tsv", "outputs/sub/b.txt"]
    # A retry in the same folder: labhq's own RESULT copies of both runs are not the agent's output.
    again = await runner.run_task(_direct("task-2", workdir=first.workdir))
    assert again.outputs == first.outputs
    assert (Path(first.workdir) / "outputs" / "RESULT_task-1.md").exists()


def _junction(link: Path, target: Path) -> None:
    if os.name == "nt":  # a junction needs no symlink privilege
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.mark.asyncio
async def test_a_link_or_junction_in_outputs_is_neither_listed_nor_followed(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    _files(outside, {"secret.tsv": "s"})
    file_link = {}

    def write(wd: Path) -> None:
        _files(wd, {"outputs/real.tsv": "r"})
        _junction(wd / "outputs" / "linked", outside)
        try:
            os.symlink(outside / "secret.tsv", wd / "outputs" / "alias.tsv")
            file_link["made"] = True
        except (OSError, NotImplementedError):  # Windows without Developer Mode: the junction case still runs
            pass

    runner = _runner(tmp_path, monkeypatch, write)
    result = await runner.run_task(_direct("task-l"))
    assert result.outputs == ["outputs/real.tsv"]
    assert (Path(result.workdir) / "outputs" / "linked" / "secret.tsv").exists()
    assert file_link.get("made") is None or (Path(result.workdir) / "outputs" / "alias.tsv").is_symlink()


@pytest.mark.asyncio
@pytest.mark.parametrize("zone", ["outputs/vault", "."])
async def test_a_restricted_zone_inside_or_around_outputs_is_not_listed(tmp_path, monkeypatch, zone):
    workdir = tmp_path / "workspace_root" / "fixed" / "task-z_worker"
    settings = Settings()
    settings.policy.data_zones = [DataZone(path=str(workdir / zone), level="restricted")]
    runner = _runner(tmp_path, monkeypatch, lambda wd: _files(wd, {
        "outputs/vault/raw.tsv": "controlled", "outputs/vault/deep/more.tsv": "controlled", "outputs/ok.tsv": "ok"}),
        settings)
    result = await runner.run_task(_direct("task-z", workdir=str(workdir)))
    assert result.outputs == (["outputs/ok.tsv"] if zone != "." else [])
    if zone == ".":
        assert any("통제 구역 안" in text for text in _warnings(runner))


@pytest.mark.asyncio
@pytest.mark.parametrize("mounted", ["outputs", "outputs/mnt"])
async def test_a_mounted_outputs_folder_or_subfolder_is_not_listed(tmp_path, monkeypatch, mounted):
    # Codex review P2: a mount at outputs/ itself was walked; its files live on another file system.
    real = workspace_module._is_mount
    monkeypatch.setattr(workspace_module, "_is_mount", lambda path: Path(path).as_posix().endswith(
        "task-m_worker/" + mounted) or real(path))
    runner = _runner(tmp_path, monkeypatch, lambda wd: _files(wd, {"outputs/mnt/far.tsv": "f", "outputs/near.tsv": "n"}))
    result = await runner.run_task(_direct("task-m"))
    assert result.outputs == ([] if mounted == "outputs" else ["outputs/near.tsv"])
    if mounted == "outputs":
        assert any("다른 파일 시스템" in text for text in _warnings(runner))


@pytest.mark.asyncio
async def test_the_listing_stops_at_the_file_cap_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "OUTPUT_SCAN_MAX_FILES", 3)
    runner = _runner(tmp_path, monkeypatch, lambda wd: _files(wd, {f"outputs/f{i}.tsv": str(i) for i in range(5)}))
    result = await runner.run_task(_direct("task-c"))
    assert result.outputs == ["outputs/f0.tsv", "outputs/f1.tsv", "outputs/f2.tsv"]
    assert any("상한 3개" in text for text in _warnings(runner))


@pytest.mark.asyncio
async def test_the_listing_stops_at_the_entry_and_depth_caps_and_says_so(tmp_path, monkeypatch):
    settings = Settings()
    settings.runner.reference_scan_max_entries = 4
    runner = _runner(tmp_path, monkeypatch, lambda wd: _files(wd, {f"outputs/e{i}.tsv": str(i) for i in range(6)}),
                     settings)
    wide = await runner.run_task(_direct("task-e"))
    # Which four depends on the file system's listing order (ext4 is not name order), not on the cap.
    assert len(wide.outputs) == 4 and set(wide.outputs) < {f"outputs/e{i}.tsv" for i in range(6)}
    settings = Settings()
    settings.runner.reference_scan_max_depth = 1
    deep = _runner(tmp_path / "deep", monkeypatch, lambda wd: _files(wd, {
        "outputs/top.tsv": "t", "outputs/a/one.tsv": "1", "outputs/a/b/two.tsv": "2"}), settings)
    result = await deep.run_task(_direct("task-d"))
    assert result.outputs == ["outputs/top.tsv", "outputs/a/one.tsv"]
    assert any("상한 4개" in text for text in _warnings(runner))
    assert any("깊이가 상한 1단계" in text for text in _warnings(deep))


@pytest.mark.asyncio
async def test_a_name_that_is_not_utf8_is_left_out_and_the_run_still_reports(tmp_path, monkeypatch):
    # A CP949 name unpacked on Linux reads back with surrogates; sending it as JSON text crashed the finished run.
    name = "\udcc7\udcd1"

    def write(wd: Path) -> None:
        _files(wd, {"outputs/ok.tsv": "ok"})
        try:
            open(os.path.join(wd, "outputs", f"{name}.tsv"), "w").close()
            os.mkdir(os.path.join(wd, "outputs", name))
            open(os.path.join(wd, "outputs", name, "inner.tsv"), "w").close()
        except (OSError, UnicodeError):
            pytest.skip("this file system refuses names that are not UTF-8")

    runner = _runner(tmp_path, monkeypatch, write)
    result = await runner.run_task(_direct("task-u"))
    assert result.ok and result.outputs == ["outputs/ok.tsv"]
    sent = [e for e in runner.store.pending() if e["type"] == "task.result"]
    assert [e["data"]["outputs"] for e in sent] == [["outputs/ok.tsv"]] and sent[0]["data"]["ok"]
    assert any("UTF-8로 읽을 수 없는 이름" in text for text in _warnings(runner))


@pytest.mark.asyncio
async def test_a_manifest_swapped_for_a_link_is_not_read_for_the_listing(tmp_path, monkeypatch):
    # #165: labhq never reads its manifest through a link the agent made (a FIFO there would hang the run).
    # Read through the link, the outside runs would hide the agent's own RESULT_x.md as labhq's copy.
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"runs": {"x": {}}}), encoding="utf-8")

    def write(wd: Path) -> None:
        _files(wd, {"outputs/RESULT_x.md": "the agent's own"})
        (wd / "manifest.json").unlink()
        try:
            os.symlink(outside, wd / "manifest.json")
        except (OSError, NotImplementedError):
            pytest.skip("this account cannot make a file symlink")

    runner = _runner(tmp_path, monkeypatch, write)
    result = await runner.run_task(_direct("task-f"))
    assert result.outputs == ["outputs/RESULT_x.md"]
    assert json.loads(outside.read_text(encoding="utf-8")) == {"runs": {"x": {}}}


def test_the_file_cap_is_the_shadow_hash_cap():
    assert workspace_module.OUTPUT_SCAN_MAX_FILES == shadow.HASH_MAX_FILES


@pytest.mark.asyncio
async def test_a_step_run_still_reports_only_its_declared_outputs(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch, lambda wd: _files(wd, {"outputs/a.tsv": "a", "outputs/b.tsv": "b"}))
    result = await runner.run_task(Task(id="task-s", request_id="r1", agent_id="worker", prompt="step",
                                        meta={"kind": "step", "step_id": "s1", "outputs": ["a.tsv"]}))
    assert result.outputs == ["outputs/a.tsv"]


@pytest.mark.asyncio
async def test_two_direct_requests_give_the_second_a_candidate_only_when_a_type_is_declared(tmp_path):
    lab = await run_lab(tmp_path, "shadow", ["표 만들기 [artifact]", "앞 표로 비율 [artifact]"],
                        project_id="shadowproj", request={"mode": "direct", "agent_id": "data_steward"})
    hub, (first, second) = lab["hub"], lab["rids"]
    assert hub.requests[first]["results"]["direct"]["outputs"] == ["outputs/artifact.txt"]
    lines = {line["request_id"]: line for line in lab["lines"] if line["type"] == "request"}
    assert set(lines) == {first, second} and lines[second]["lane"] == "direct"
    assert lines[first]["hash"]["observed_new"] == 1
    # Nothing in the live path declares a type yet (#221 할 일 2): the earlier output stays type_unknown.
    live = lines[second]["provenance"]
    assert live["history_artifacts"] == 1 and live["candidates"] == 0
    assert live["excluded"]["type_unknown"] == 1
    # With a declaration on the first run, the same rows give the second request one candidate.
    tid, row = next((k, v) for k, v in hub.store.all("task").items() if v.get("request_id") == first)
    row["payload"]["meta"]["output_types"] = {"outputs/artifact.txt": "raw_counts"}
    hub.store.put("task", tid, row)
    observed: dict = {}
    assert line_for(hub, first, observed)["hash"]["observed_new"] == 1
    declared = line_for(hub, second, observed)
    assert declared["hash"]["verified"] == 1
    assert declared["provenance"]["candidates"] == 1 and declared["provenance"]["excluded"]["type_unknown"] == 0
