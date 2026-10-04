import asyncio
import csv
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from labhq.evidence.audit import verify_request
from labhq.cli import main, render
from labhq.gateway.server import Hub
from labhq.orchestrator.cso import RESEARCH_STEP_PROMPT, STEP_PROMPT
from labhq.request_bundle import build_request_bundle
from labhq.settings import Settings
import labhq.request_bundle as request_bundle_module


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configured(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.runner.bundle_max_file_mb = 1
    return settings


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def request_fixture(tmp_path: Path):
    settings = configured(tmp_path)
    root = Path(settings.runner.workspace_root)
    first = root / "2026-10-04" / "task_first_analyst"
    second = root / "2026-10-04" / "task_second_analyst"
    old = root / "2026-10-04" / "task_old_analyst"
    upstream = first / "outputs" / "data" / "input.tsv"
    write(upstream, "value\n7\n")
    write(first / "manifest.json", json.dumps({"host": platform.node()}))
    write(first / "outputs" / "scripts" / "make.py", "print('make')\n")
    outside = tmp_path / "outside" / "keep.tsv"
    write(outside, "outside\n")
    script = second / "outputs" / "scripts" / "analyze.py"
    write(second / "manifest.json", json.dumps({"host": platform.node()}))
    write(script, "\n".join([
        "from pathlib import Path",
        f'UPSTREAM = Path(r"{upstream}")',
        f'OTHER = r"{outside}"',
        'Path(__file__).with_name("rerun.txt").write_text(UPSTREAM.read_text(encoding="utf-8"), encoding="utf-8")',
        "",
    ]))
    write(second / "outputs" / "final.txt", f"source={upstream.as_posix()}\n")
    large = second / "outputs" / "large.bin"
    large.write_bytes(b"x" * (1024 * 1024 + 1))
    write(old / "outputs" / "obsolete.txt", "superseded\n")
    results = {
        "s1": {
            "task_id": "t-first", "agent_id": "analyst", "ok": True, "status": "done",
            "workdir": str(first), "workdir_id": first.name,
            "outputs": ["outputs/data/input.tsv"],
            "output_sha256": {"outputs/data/input.tsv": digest(upstream)},
        },
        "s2": {
            "task_id": "t-second", "agent_id": "analyst", "ok": True, "status": "done",
            "workdir": str(second), "workdir_id": second.name,
            "outputs": ["outputs/scripts/analyze.py", "outputs/final.txt", "outputs/large.bin"],
            "output_sha256": {
                "outputs/scripts/analyze.py": digest(script),
                "outputs/final.txt": digest(second / "outputs" / "final.txt"),
                "outputs/large.bin": digest(large),
            },
        },
    }
    request = {
        "id": "req_bundle", "text": "two steps", "mode": "orchestrate", "status": "done",
        "report": f"Result from {upstream}", "report_appendix": "original appendix",
        "plan": {"steps": [
            {"id": "s1", "depends_on": [], "outputs": ["outputs/data/input.tsv"]},
            {"id": "s2", "depends_on": ["s1"], "outputs": ["outputs/final.txt"]},
        ]},
        "results": results,
        "replan_history": [{"status": "applied", "retired": ["old"], "prior_results": {
            "old": {"workdir_id": old.name, "outputs": ["outputs/obsolete.txt"]}}}],
    }
    tasks = {
        "t-old": {"request_id": request["id"], "step_id": "s1", "completed": True,
                  "result": {"task_id": "t-old", "workdir": str(old), "workdir_id": old.name,
                             "outputs": ["outputs/obsolete.txt"]}},
        "t-first": {"request_id": request["id"], "step_id": "s1", "completed": True,
                    "result": results["s1"]},
        "t-second": {"request_id": request["id"], "step_id": "s2", "completed": True,
                     "result": results["s2"]},
    }
    return settings, request, tasks, upstream, outside, old


def test_bundle_is_portable_filtered_and_idempotent(tmp_path):
    settings, request, tasks, upstream, outside, old = request_fixture(tmp_path)
    original_script = (Path(request["results"]["s2"]["workdir"]) / "outputs/scripts/analyze.py").read_bytes()

    built = build_request_bundle(request, settings, tasks)
    bundle = Path(built["path"])
    assert bundle == Path(settings.runner.workspace_root) / "requests" / request["id"]
    assert built["rewritten_files"] == 3
    assert (bundle / "steps/s1/data/input.tsv").is_file()
    assert (bundle / "steps/s1/scripts/make.py").is_file()  # scripts are copied even when undeclared
    assert not (bundle / "steps/s2/large.bin").exists()
    assert not (bundle / "steps/s1/obsolete.txt").exists()
    assert not (bundle / "steps/old/obsolete.txt").exists()

    bundled_script = (bundle / "steps/s2/scripts/analyze.py").read_text(encoding="utf-8")
    assert str(upstream) not in bundled_script and upstream.as_posix() not in bundled_script
    assert str(outside) in bundled_script  # a path outside this request's step workdirs is untouched
    assert "대체됨" in (bundle / "report_appendix.md").read_text(encoding="utf-8")
    assert old.name in (bundle / "report_appendix.md").read_text(encoding="utf-8")

    with (bundle / "MANIFEST.tsv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    large_row = next(row for row in rows if row["relative_path"] == "steps/s2/large.bin")
    assert large_row["status"] == "not copied: size"
    assert large_row["original_path"].endswith("outputs\\large.bin") or large_row["original_path"].endswith("outputs/large.bin")
    summary = next(row for row in rows if row["relative_path"] == ".")
    assert summary["rewritten_files"] == "3"
    assert str(outside) in summary["remaining_absolute_paths"]
    assert "/../" not in summary["remaining_absolute_paths"]

    moved = tmp_path / "moved-bundle"
    shutil.copytree(bundle, moved)
    assert "steps/s1/data/input.tsv" in bundled_script
    assert "묶음의 루트에서 실행" in (bundle / "README.md").read_text(encoding="utf-8")
    subprocess.run([sys.executable, "steps/s2/scripts/analyze.py"], check=True, cwd=moved)
    assert (moved / "steps/s2/scripts/rerun.txt").read_text(encoding="utf-8") == "value\n7\n"

    first_snapshot = {p.relative_to(bundle).as_posix(): digest(p) for p in bundle.rglob("*") if p.is_file()}
    rebuilt = build_request_bundle(request, settings, tasks)
    second_snapshot = {p.relative_to(bundle).as_posix(): digest(p) for p in bundle.rglob("*") if p.is_file()}
    assert rebuilt == built and second_snapshot == first_snapshot
    assert (Path(request["results"]["s2"]["workdir"]) / "outputs/scripts/analyze.py").read_bytes() == original_script
    assert verify_request(request, settings)["exit_code"] == 0  # verify still reads the originals


def test_markdown_links_stay_document_relative(tmp_path):
    settings, request, tasks, upstream, _outside, _old = request_fixture(tmp_path)
    workdir = Path(request["results"]["s2"]["workdir"])
    note = workdir / "outputs/docs/note.md"
    write(note, f"[input]({upstream})\n")
    request["results"]["s2"]["outputs"].append("outputs/docs/note.md")

    bundle = Path(build_request_bundle(request, settings, tasks)["path"])

    assert "[input](../../s1/data/input.tsv)" in (bundle / "steps/s2/docs/note.md").read_text(encoding="utf-8")


def test_size_limit_skips_open_file_hash_and_copy(tmp_path, monkeypatch):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    copied = []
    real_copy = request_bundle_module._copy_held

    def record_copy(stream, info, destination):
        copied.append(destination.name)
        assert destination.name != "large.bin"
        return real_copy(stream, info, destination)

    monkeypatch.setattr(request_bundle_module, "_copy_held", record_copy)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    with (bundle / "MANIFEST.tsv").open(encoding="utf-8", newline="") as handle:
        row = next(row for row in csv.DictReader(handle, delimiter="\t")
                   if row["relative_path"] == "steps/s2/large.bin")
    assert row["status"] == "not copied: size" and row["sha256"] == ""
    assert "large.bin" not in copied


def test_no_located_workdir_is_not_recorded_as_success(tmp_path):
    settings = configured(tmp_path)
    request = {"id": "missing", "report": "body", "report_appendix": "", "plan": {"steps": [{"id": "s1"}]},
               "results": {"s1": {"workdir": str(tmp_path / "gone"), "workdir_id": "task_gone",
                                    "outputs": ["outputs/result.txt"]}}}

    with pytest.raises(OSError, match="단계 작업 폴더를 하나도 찾지 못했습니다"):
        build_request_bundle(request, settings)
    assert not (Path(settings.runner.workspace_root) / "requests/missing").exists()


def test_parent_swap_never_reads_the_replacement(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    root = Path(settings.runner.workspace_root)
    workdir = root / "2026-10-04" / "task_race"
    source_dir = workdir / "outputs/data"
    outside = tmp_path / "outside"
    write(workdir / "manifest.json", json.dumps({"host": platform.node()}))
    write(source_dir / "safe.txt", "safe\n")
    write(outside / "safe.txt", "SECRET\n")
    request = {"id": "race", "report": "ok", "report_appendix": "", "plan": {"steps": [{"id": "s1"}]},
               "results": {"s1": {"workdir": str(workdir), "workdir_id": workdir.name,
                                    "outputs": ["outputs/data/safe.txt"]}}}
    real_child = request_bundle_module.HeldDir.child
    attempted = False

    def swap_before_open(self, name, expect=None):
        nonlocal attempted
        if name == "data" and not attempted:
            attempted = True
            try:
                source_dir.rename(workdir / "outputs/data-original")
                source_dir.symlink_to(outside, target_is_directory=True)
            except OSError:  # Windows held parent handles deny the replacement itself.
                pass
        return real_child(self, name, expect)

    monkeypatch.setattr(request_bundle_module.HeldDir, "child", swap_before_open)
    bundle = Path(build_request_bundle(request, settings)["path"])
    copied = bundle / "steps/s1/data/safe.txt"
    assert not copied.exists() or copied.read_text(encoding="utf-8") == "safe\n"
    assert all(b"SECRET" not in path.read_bytes() for path in bundle.rglob("*") if path.is_file())


@pytest.mark.asyncio
async def test_remote_runner_records_warning_without_gateway_path(tmp_path):
    settings = configured(tmp_path)
    workdir = Path(settings.runner.workspace_root) / "2026-10-04" / "task_remote"
    write(workdir / "manifest.json", json.dumps({"host": "another-host"}))
    write(workdir / "outputs/answer.txt", "answer\n")
    hub = Hub(settings)
    hub.requests["remote"] = {"id": "remote", "mode": "direct", "status": "done", "report": "body",
                              "report_appendix": "appendix", "results": {"direct": {
                                  "workdir": str(workdir), "workdir_id": workdir.name,
                                  "outputs": ["outputs/answer.txt"]}}, "plan": {"steps": []}}
    data = {"ok": True, "report": "body", "report_appendix": "appendix"}

    task = hub.schedule_terminal("remote", "request.completed", data)
    await asyncio.wait_for(task, 2)

    note = "runner가 다른 PC라 묶음을 만들지 않음"
    assert data["bundle_warning"] == note and "bundle_path" not in data
    assert note in hub.requests["remote"]["report_appendix"]
    assert hub.events[-1]["data"]["bundle_warning"] == note
    await asyncio.sleep(0)
    hub.store.close()


@pytest.mark.asyncio
async def test_scheduled_bundle_uses_one_worker_without_blocking_loop(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    hub = Hub(settings)
    hub.requests["worker"] = {"id": "worker", "mode": "direct", "status": "done",
                              "results": {"direct": {"workdir_id": "task_worker"}},
                              "plan": {"steps": []}, "report": "body", "report_appendix": ""}
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    calls = []

    def slow_bundle(*_args):
        calls.append(threading.get_ident())
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return {"path": str(tmp_path / "bundle")}

    monkeypatch.setattr(request_bundle_module, "build_request_bundle", slow_bundle)
    data = {"ok": True}
    task = hub.schedule_terminal("worker", "request.completed", data)
    assert hub.schedule_terminal("worker", "request.completed", data) is task
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.sleep(0)  # the event loop remains available while the worker is blocked
    release.set()
    await asyncio.wait_for(task, 2)
    assert len(calls) == 1 and calls[0] != threading.get_ident()
    assert data["bundle_path"] == str(tmp_path / "bundle")
    hub.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode, research, terminal", [
    ("direct", False, "request.completed"),
    ("orchestrate", False, "request.completed"),
    ("orchestrate", True, "request.failed"),
])
async def test_terminal_hook_builds_direct_general_research_and_failed_bundles(tmp_path, mode, research, terminal):
    settings = configured(tmp_path)
    workdir = Path(settings.runner.workspace_root) / "2026-10-04" / f"task_{mode}_{research}"
    output = workdir / "outputs" / "answer.txt"
    write(output, "answer\n")
    write(workdir / "manifest.json", json.dumps({"host": platform.node()}))
    rid = f"req_{mode}_{research}_{terminal}"
    result = {"task_id": rid, "agent_id": "analyst", "ok": terminal == "request.completed",
              "workdir": str(workdir), "workdir_id": workdir.name, "outputs": ["outputs/answer.txt"]}
    hub = Hub(settings)
    hub.requests[rid] = {
        "id": rid, "text": "request", "mode": mode, "status": "done" if terminal == "request.completed" else "failed",
        "report": "body", "report_appendix": "appendix", "results": {"direct" if mode == "direct" else "s1": result},
        "plan": {"steps": [] if mode == "direct" else [{"id": "s1", "depends_on": []}]},
        **({"research_contract": {"schema_version": 1}} if research else {}),
    }
    hub.store.put("task", rid, {"request_id": rid, "step_id": None if mode == "direct" else "s1",
                                "kind": "direct" if mode == "direct" else "step", "completed": True,
                                "result": result})
    data = {"ok": terminal == "request.completed", "report": "body", "report_appendix": "appendix"}

    hub.commit_terminal(rid, terminal, data)

    assert Path(hub.requests[rid]["bundle_path"]).is_dir()
    assert data["bundle_path"] == hub.requests[rid]["bundle_path"]
    assert hub.events[-1]["data"]["bundle_path"] == data["bundle_path"]
    await asyncio_sleep()
    hub.store.close()


async def asyncio_sleep():
    # Let commit_terminal's client-delivery task leave the event loop cleanly.
    import asyncio
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_bundle_failure_is_warning_only_and_restart_does_not_rebuild(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    hub = Hub(settings)
    rid = "req_warning"
    hub.requests[rid] = {"id": rid, "text": "request", "mode": "direct", "status": "done",
                         "report": "body", "report_appendix": "appendix", "results": {}}
    calls = []

    def fail(*_args):
        calls.append("called")
        raise OSError("disk full")

    monkeypatch.setattr("labhq.request_bundle.build_request_bundle", fail)
    data = {"ok": True, "report": "body", "report_appendix": "appendix"}
    hub.commit_terminal(rid, "request.completed", data)
    assert hub.requests[rid]["status"] == "done"
    assert "disk full" in data["bundle_warning"]
    assert calls == ["called"]
    await asyncio_sleep()
    hub.store.close()

    calls.clear()
    restored = Hub(settings)
    assert restored.requests[rid]["status"] == "done"
    assert calls == []
    restored.store.close()


def test_step_prompts_and_cli_completion_show_bundle(capsys):
    rule = "collect those paths in one variable block at the top"
    assert rule in RESEARCH_STEP_PROMPT and rule in STEP_PROMPT
    render({"type": "request.completed", "ts": 1, "data": {
        "ok": True, "cost_usd": 0, "bundle_path": "C:/runs/requests/r1"}})
    assert "요청 묶음: C:/runs/requests/r1" in capsys.readouterr().out


def test_cli_status_shows_recent_bundle(monkeypatch, capsys):
    def api(_settings, _method, path):
        if path == "/api/health":
            return {"runners": []}
        if path == "/api/approvals":
            return []
        if "status=running" in path:
            return []
        return [{"id": "r1", "status": "done", "bundle_path": "C:/runs/requests/r1"}]

    monkeypatch.setattr("labhq.cli._api", api)
    main(["status"])
    assert "요청 묶음: C:/runs/requests/r1" in capsys.readouterr().out
