import csv
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from labhq.evidence.audit import verify_request
from labhq.cli import main, render
from labhq.gateway.server import Hub
from labhq.orchestrator.cso import RESEARCH_STEP_PROMPT, STEP_PROMPT
from labhq.request_bundle import build_request_bundle
from labhq.settings import Settings


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
    write(first / "outputs" / "scripts" / "make.py", "print('make')\n")
    outside = tmp_path / "outside" / "keep.tsv"
    write(outside, "outside\n")
    script = second / "outputs" / "scripts" / "analyze.py"
    write(script, "\n".join([
        "from pathlib import Path",
        f'UPSTREAM = Path(__file__).parent / r"{upstream}"',
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
    subprocess.run([sys.executable, str(moved / "steps/s2/scripts/analyze.py")], check=True, cwd=tmp_path)
    assert (moved / "steps/s2/scripts/rerun.txt").read_text(encoding="utf-8") == "value\n7\n"

    first_snapshot = {p.relative_to(bundle).as_posix(): digest(p) for p in bundle.rglob("*") if p.is_file()}
    rebuilt = build_request_bundle(request, settings, tasks)
    second_snapshot = {p.relative_to(bundle).as_posix(): digest(p) for p in bundle.rglob("*") if p.is_file()}
    assert rebuilt == built and second_snapshot == first_snapshot
    assert (Path(request["results"]["s2"]["workdir"]) / "outputs/scripts/analyze.py").read_bytes() == original_script
    assert verify_request(request, settings)["exit_code"] == 0  # verify still reads the originals


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
