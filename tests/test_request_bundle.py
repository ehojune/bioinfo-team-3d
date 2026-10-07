import asyncio
import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path, PurePosixPath

import pytest

from labhq.evidence.audit import render_verify, verify_request
from labhq.cli import _verify, main, render
from labhq.gateway.server import Hub
from labhq.orchestrator.cso import RESEARCH_STEP_PROMPT, STEP_PROMPT
from labhq.request_bundle import build_request_bundle
from labhq.ro_crate import (FORMAT_MARKER, INLINE_CONTEXT, METADATA_FILE, PROCESS_RUN_PROFILE, RO_CRATE_PROFILE,
                            build_ro_crate, verify_bundle_copy)
from labhq.settings import Settings
import labhq.request_bundle as request_bundle_module
import labhq.ro_crate as ro_crate_module


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
    reference = second / "outputs" / "reference" / "genes.tsv"
    write(second / "manifest.json", json.dumps({"host": platform.node()}))
    write(reference, "gene\nTP53\n")
    write(script, "\n".join([
        "from pathlib import Path",
        f'UPSTREAM = Path(r"{upstream}")',
        f'OTHER = r"{outside}"',
        'LOCAL = Path("outputs/reference/genes.tsv")',
        'Path("outputs/result.tsv").write_text(',
        '    UPSTREAM.read_text(encoding="utf-8") + LOCAL.read_text(encoding="utf-8"), encoding="utf-8")',
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
            "outputs": ["outputs/scripts/analyze.py", "outputs/reference/genes.tsv",
                        "outputs/final.txt", "outputs/large.bin"],
            "output_sha256": {
                "outputs/scripts/analyze.py": digest(script),
                "outputs/reference/genes.tsv": digest(reference),
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


def manifest_rows(bundle: Path) -> dict[str, dict[str, str]]:
    with (bundle / "MANIFEST.tsv").open(encoding="utf-8", newline="") as handle:
        return {row["relative_path"]: row for row in csv.DictReader(handle, delimiter="\t")}


def crate_entities(bundle: Path) -> dict[str, dict]:
    crate = json.loads((bundle / METADATA_FILE).read_text(encoding="utf-8"))
    return {entity["@id"]: entity for entity in crate["@graph"]}


@pytest.mark.parametrize("failure", ["changed", "missing", "link"])
def test_recorded_output_failure_is_manifested_and_marks_bundle_incomplete(tmp_path, failure):
    settings = configured(tmp_path)
    root = Path(settings.runner.workspace_root)
    workdir = root / "2026-10-04" / f"task_{failure}"
    output = workdir / "outputs/result.txt"
    write(workdir / "manifest.json", json.dumps({"host": platform.node()}))
    write(output, "during run\n")
    recorded_hash = digest(output)
    if failure == "changed":
        write(output, "after run\n")
        expected = "not copied: changed since run"
    elif failure == "missing":
        output.unlink()
        expected = "not copied: missing"
    else:
        target = tmp_path / "outside.txt"
        write(target, "outside\n")
        output.unlink()
        try:
            output.symlink_to(target)
        except OSError:
            pytest.skip("file symlinks are unavailable")
        expected = "not copied: changed since run"
    request = {
        "id": f"recorded_{failure}", "report": "ok", "report_appendix": "",
        "plan": {"steps": [{"id": "s1", "depends_on": []}]},
        "results": {"s1": {
            "workdir": str(workdir), "workdir_id": workdir.name,
            "outputs": ["outputs/result.txt"],
            "output_sha256": {"outputs/result.txt": recorded_hash},
        }},
    }

    built = build_request_bundle(request, settings)
    bundle = Path(built["path"])
    row = manifest_rows(bundle)["steps/s1/outputs/result.txt"]

    assert row["status"] == expected
    assert not (bundle / "steps/s1/outputs/result.txt").exists()
    assert built["status"] == "incomplete"
    assert "요청 묶음 상태: incomplete" in (bundle / "report_appendix.md").read_text(encoding="utf-8")


def test_only_runner_recorded_and_hashed_outputs_are_copied(tmp_path):
    settings = configured(tmp_path)
    root = Path(settings.runner.workspace_root)
    workdir = root / "2026-10-04" / "task_recorded_only"
    recorded = workdir / "outputs/result.txt"
    undeclared = workdir / "outputs/scripts/unlisted.py"
    hash_only = workdir / "outputs/hash-only.txt"
    write(workdir / "manifest.json", json.dumps({"host": platform.node()}))
    write(recorded, "recorded\n")
    write(undeclared, "print('unlisted')\n")
    write(hash_only, "hash only\n")
    request = {
        "id": "recorded_only", "report": "ok", "report_appendix": "",
        "plan": {"steps": [{"id": "s1", "depends_on": []}]},
        "results": {"s1": {
            "workdir": str(workdir), "workdir_id": workdir.name,
            "outputs": ["outputs/result.txt", "outputs/scripts/unlisted.py"],
            "output_sha256": {
                "outputs/result.txt": digest(recorded),
                "outputs/hash-only.txt": digest(hash_only),
            },
        }},
    }

    bundle = Path(build_request_bundle(request, settings)["path"])

    assert (bundle / "steps/s1/outputs/result.txt").is_file()
    assert not (bundle / "steps/s1/outputs/scripts/unlisted.py").exists()
    assert not (bundle / "steps/s1/outputs/hash-only.txt").exists()
    assert "steps/s1/outputs/scripts/unlisted.py" not in manifest_rows(bundle)
    assert "steps/s1/outputs/hash-only.txt" not in manifest_rows(bundle)


def test_workdir_rewrite_requires_a_path_boundary_and_posix_case(tmp_path):
    pattern = request_bundle_module._path_prefix_pattern(PurePosixPath("/tmp/foo"))
    text = "\n".join([
        "/tmp/foo/outputs/a.tsv",
        "/var/tmp/foo/outputs/b.tsv",
        "/tmp/Foo/outputs/c.tsv",
        "x=/tmp/foo/outputs/d.tsv",
    ])

    rewritten = pattern.sub(lambda match: f"REL/{match.group('tail')}", text)

    assert "REL/a.tsv" in rewritten
    assert "/var/tmp/foo/outputs/b.tsv" in rewritten
    assert "/tmp/Foo/outputs/c.tsv" in rewritten
    assert "x=REL/d.tsv" in rewritten


def test_windows_workdir_rewrite_is_case_insensitive_at_a_boundary():
    pattern = request_bundle_module._path_prefix_pattern(r"C:\\Runs\\Task")

    assert pattern.search(r'"c:\\runs\\task\\outputs\\a.tsv"')
    assert not pattern.search(r"prefixC:\\Runs\\Task\\outputs\\a.tsv")


def test_bundle_is_portable_filtered_and_idempotent(tmp_path):
    settings, request, tasks, upstream, outside, old = request_fixture(tmp_path)
    original_script = (Path(request["results"]["s2"]["workdir"]) / "outputs/scripts/analyze.py").read_bytes()

    built = build_request_bundle(request, settings, tasks)
    bundle = Path(built["path"])
    assert bundle == Path(settings.runner.workspace_root) / "requests" / request["id"]
    assert built["rewritten_files"] == 3
    assert (bundle / "steps/s1/outputs/data/input.tsv").is_file()
    assert not (bundle / "steps/s1/outputs/scripts/make.py").exists()
    assert (bundle / "steps/s2/outputs/reference/genes.tsv").is_file()
    assert not (bundle / "steps/s2/outputs/large.bin").exists()
    assert not (bundle / "steps/s1/outputs/obsolete.txt").exists()
    assert not (bundle / "steps/old/outputs/obsolete.txt").exists()

    bundled_script = (bundle / "steps/s2/outputs/scripts/analyze.py").read_text(encoding="utf-8")
    assert str(upstream) not in bundled_script and upstream.as_posix() not in bundled_script
    assert str(outside) in bundled_script  # a path outside this request's step workdirs is untouched
    assert "대체됨" in (bundle / "report_appendix.md").read_text(encoding="utf-8")
    assert old.name in (bundle / "report_appendix.md").read_text(encoding="utf-8")

    with (bundle / "MANIFEST.tsv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    large_row = next(row for row in rows if row["relative_path"] == "steps/s2/outputs/large.bin")
    assert large_row["status"] == "not copied: size"
    assert large_row["original_path"].endswith("outputs\\large.bin") or large_row["original_path"].endswith("outputs/large.bin")
    summary = next(row for row in rows if row["relative_path"] == ".")
    assert summary["rewritten_files"] == "3"
    assert str(outside) in summary["remaining_absolute_paths"]
    assert "/../" not in summary["remaining_absolute_paths"]

    moved = tmp_path / "moved-bundle"
    shutil.copytree(bundle, moved)
    assert "../s1/outputs/data/input.tsv" in bundled_script
    readme = (bundle / "README.md").read_text(encoding="utf-8")
    assert "각 단계 폴더에서 실행" in readme
    commands = [line[3:-1] for line in readme.splitlines()
                if line.startswith("- `cd steps/") and line.endswith("`")]
    assert commands
    for command in commands:
        subprocess.run(command, check=True, cwd=moved, shell=True)
    assert (moved / "steps/s2/outputs/result.tsv").read_text(encoding="utf-8") == "value\n7\ngene\nTP53\n"

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
    request["results"]["s2"]["output_sha256"]["outputs/docs/note.md"] = digest(note)

    bundle = Path(build_request_bundle(request, settings, tasks)["path"])

    assert "[input](../../../s1/outputs/data/input.tsv)" in (
        bundle / "steps/s2/outputs/docs/note.md").read_text(encoding="utf-8")


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
                   if row["relative_path"] == "steps/s2/outputs/large.bin")
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
                                    "outputs": ["outputs/data/safe.txt"],
                                    "output_sha256": {"outputs/data/safe.txt": digest(source_dir / "safe.txt")}}}}
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
    copied = bundle / "steps/s1/outputs/data/safe.txt"
    assert not copied.exists() or copied.read_text(encoding="utf-8") == "safe\n"
    assert all(b"SECRET" not in path.read_bytes() for path in bundle.rglob("*") if path.is_file())


def test_bundle_does_not_list_outputs_directories(tmp_path, monkeypatch):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)

    def fail_listing(*_args, **_kwargs):
        raise AssertionError("request bundle must not enumerate outputs")

    monkeypatch.setattr(request_bundle_module.HeldDir, "entries", fail_listing)

    bundle = Path(build_request_bundle(request, settings, tasks)["path"])

    assert (bundle / "steps/s1/outputs/data/input.tsv").is_file()


def test_unsafe_script_name_is_listed_without_a_command(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    workdir = Path(request["results"]["s1"]["workdir"])
    unsafe = workdir / "outputs/scripts/unsafe; echo PWN.py"
    write(unsafe, "print('safe contents')\n")
    request["results"]["s1"]["outputs"].append("outputs/scripts/unsafe; echo PWN.py")
    request["results"]["s1"]["output_sha256"]["outputs/scripts/unsafe; echo PWN.py"] = digest(unsafe)

    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    readme = (bundle / "README.md").read_text(encoding="utf-8")
    relative = "steps/s1/outputs/scripts/unsafe; echo PWN.py"

    assert (bundle / relative).is_file()
    assert f"이름이 안전하지 않아 명령을 만들지 않음: `{relative}`" in readme
    assert f'python "{relative}"' not in readme


def test_script_commands_follow_plan_topology_before_command_name(tmp_path):
    settings = configured(tmp_path)
    root = Path(settings.runner.workspace_root)
    results = {}
    for step_id, script in (("down", "finish.R"), ("up", "prepare.py")):
        workdir = root / "2026-10-04" / f"task_{step_id}"
        write(workdir / "manifest.json", json.dumps({"host": platform.node()}))
        script_path = workdir / "outputs/scripts" / script
        write(script_path, "# rerun\n")
        results[step_id] = {
            "workdir": str(workdir), "workdir_id": workdir.name,
            "outputs": [f"outputs/scripts/{script}"],
            "output_sha256": {f"outputs/scripts/{script}": digest(script_path)},
        }
    request = {
        "id": "topology", "report": "ok", "report_appendix": "",
        "plan": {"steps": [
            {"id": "down", "depends_on": ["up"]},
            {"id": "up", "depends_on": []},
        ]},
        "results": results,
    }

    bundle = Path(build_request_bundle(request, settings)["path"])
    readme = (bundle / "README.md").read_text(encoding="utf-8")

    assert readme.index("cd steps/up && python outputs/scripts/prepare.py") < readme.index(
        "cd steps/down && Rscript outputs/scripts/finish.R")


def test_python_command_comes_from_the_step_runner_and_unknown_is_disclosed(tmp_path):
    settings = configured(tmp_path)
    root = Path(settings.runner.workspace_root)
    results = {}
    tasks = {}
    for step_id, task_id, runner_id in (("known", "t-known", "runner-a"),
                                        ("unknown", "t-unknown", "runner-b")):
        workdir = root / "2026-10-04" / f"task_{step_id}"
        write(workdir / "manifest.json", json.dumps({"host": platform.node()}))
        script_path = workdir / "outputs/scripts/run.py"
        write(script_path, "print('ok')\n")
        results[step_id] = {"task_id": task_id, "workdir": str(workdir),
                            "workdir_id": workdir.name, "outputs": ["outputs/scripts/run.py"],
                            "output_sha256": {"outputs/scripts/run.py": digest(script_path)}}
        tasks[task_id] = {"request_id": "python_command", "step_id": step_id,
                          "runner_id": runner_id, "completed": True, "result": results[step_id]}
    request = {"id": "python_command", "report": "ok", "report_appendix": "",
               "plan": {"steps": [{"id": "known", "depends_on": []},
                                   {"id": "unknown", "depends_on": ["known"]}]},
               "results": results}
    capabilities = {"runner-a": {"local_software": {"python": {"command": "py"}}}}

    bundle = Path(build_request_bundle(request, settings, tasks, capabilities)["path"])
    readme = (bundle / "README.md").read_text(encoding="utf-8")

    assert "cd steps/known && py outputs/scripts/run.py" in readme
    assert "cd steps/unknown && python outputs/scripts/run.py" in readme
    assert "runner가 쓴 python 명령을 확인하지 못함" in readme


def test_unreported_outputs_are_copied_after_recorded_ones_without_marking_incomplete(tmp_path):
    """Bench C t6 (#423): a gene-set copy a script read was never reported, so the bundle could not rerun it."""
    settings = configured(tmp_path)
    settings.runner.bundle_max_files = 3
    root = Path(settings.runner.workspace_root)
    first, second = root / "2026-10-05" / "task_a", root / "2026-10-05" / "task_b"
    for workdir in (first, second):
        write(workdir / "manifest.json", json.dumps({"host": platform.node()}))
    write(first / "outputs/table.tsv", "gene\nTP53\n")
    for name in ("genes.gmt", "matrix.tsv", "figure.svg"):
        write(first / "outputs/reference" / name, name)
    (first / "outputs/huge.bin").write_bytes(b"x" * (1024 * 1024 + 1))
    write(second / "outputs/result.txt", "done\n")
    request = {
        "id": "unreported", "report": "ok", "report_appendix": "",
        "plan": {"steps": [{"id": "s1", "depends_on": []}, {"id": "s2", "depends_on": ["s1"]}]},
        "results": {
            "s1": {"workdir": str(first), "workdir_id": first.name, "outputs": ["outputs/table.tsv"],
                   "output_sha256": {"outputs/table.tsv": digest(first / "outputs/table.tsv")},
                   "unreported_outputs": ["outputs/huge.bin", "outputs/reference/figure.svg",
                                          "outputs/reference/genes.gmt", "outputs/reference/matrix.tsv",
                                          "../escape.txt"]},
            "s2": {"workdir": str(second), "workdir_id": second.name, "outputs": ["outputs/result.txt"],
                   "output_sha256": {"outputs/result.txt": digest(second / "outputs/result.txt")}},
        },
    }

    built = build_request_bundle(request, settings)
    bundle = Path(built["path"])
    rows = manifest_rows(bundle)

    # Both recorded outputs first; one file slot is left for the unreported ones, in listed order.
    assert rows["steps/s1/outputs/table.tsv"]["status"] == "copied"
    assert rows["steps/s2/outputs/result.txt"]["status"] == "copied"
    assert rows["steps/s1/outputs/huge.bin"]["status"] == "not copied (unreported): size"
    assert rows["steps/s1/outputs/reference/figure.svg"]["status"] == "copied (unreported)"
    assert rows["steps/s1/outputs/reference/figure.svg"]["sha256"] == digest(first / "outputs/reference/figure.svg")
    assert rows["steps/s1/outputs/reference/genes.gmt"]["status"] == "not copied (unreported): total limit"
    assert (bundle / "steps/s1/outputs/reference/figure.svg").read_text(encoding="utf-8") == "figure.svg"
    assert not any("escape" in path for path in rows)
    assert built["status"] == "complete"
    appendix = (bundle / "report_appendix.md").read_text(encoding="utf-8")
    assert "보고하지 않은 산출 3개 미복사" in appendix
    assert "누적 상한으로 기록 산출 일부를 복사하지 않음" not in appendix


def test_link_script_restores_upstream_inputs_so_a_copied_script_reruns(tmp_path):
    """A step reads upstream files as inputs/<step>/... (#423); the bundle relinks them to steps/<step>/outputs."""
    import sys

    settings = configured(tmp_path)
    root = Path(settings.runner.workspace_root)
    first, second = root / "2026-10-05" / "task_a", root / "2026-10-05" / "task_b"
    for workdir in (first, second):
        write(workdir / "manifest.json", json.dumps({"host": platform.node()}))
    write(first / "outputs/data/input.tsv", "value\n7\n")
    script = second / "outputs/scripts/analyze.py"
    write(script, "\n".join([
        "from pathlib import Path",
        'UPSTREAM = Path("inputs/s1/data/input.tsv")',
        'Path("outputs/result.tsv").write_text(UPSTREAM.read_text(encoding="utf-8"), encoding="utf-8")',
        "",
    ]))
    request = {
        "id": "linked", "report": "ok", "report_appendix": "",
        "plan": {"steps": [{"id": "s1", "depends_on": []}, {"id": "s2", "depends_on": ["s1", "gone"]}]},
        "results": {
            "s1": {"workdir": str(first), "workdir_id": first.name, "outputs": ["outputs/data/input.tsv"],
                   "output_sha256": {"outputs/data/input.tsv": digest(first / "outputs/data/input.tsv")}},
            "s2": {"workdir": str(second), "workdir_id": second.name, "outputs": ["outputs/scripts/analyze.py"],
                   "output_sha256": {"outputs/scripts/analyze.py": digest(script)}},
        },
    }

    bundle = Path(build_request_bundle(request, settings)["path"])
    readme = (bundle / "README.md").read_text(encoding="utf-8")
    assert "먼저 `python link_inputs.py`" in readme
    assert manifest_rows(bundle)["link_inputs.py"]["status"] == "generated"
    moved = shutil.move(str(bundle), str(tmp_path / "elsewhere"))  # the bundle runs from any folder

    linked = subprocess.run([sys.executable, "link_inputs.py"], cwd=moved, capture_output=True, text=True)
    assert linked.returncode == 0, linked.stderr
    assert "steps/s2/inputs/s1 -> steps/s1/outputs" in linked.stdout
    assert not (Path(moved) / "steps/s2/inputs/gone").exists()
    ran = subprocess.run([sys.executable, "outputs/scripts/analyze.py"], cwd=Path(moved) / "steps/s2",
                         capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert (Path(moved) / "steps/s2/outputs/result.tsv").read_text(encoding="utf-8") == "value\n7\n"


@pytest.mark.parametrize("total_bytes,max_files", [(1024 * 1024, 1), (5, 10)])
def test_request_total_limits_record_every_omission(tmp_path, total_bytes, max_files):
    settings = configured(tmp_path)
    settings.runner.bundle_max_total_mb = total_bytes / (1024 * 1024)
    settings.runner.bundle_max_files = max_files
    root = Path(settings.runner.workspace_root)
    workdir = root / "2026-10-04" / "task_limits"
    write(workdir / "manifest.json", json.dumps({"host": platform.node()}))
    for name in ("a.txt", "b.txt", "c.txt"):
        write(workdir / "outputs" / name, "xxx")
    request = {
        "id": f"limits_{total_bytes}_{max_files}", "report": "ok", "report_appendix": "",
        "plan": {"steps": [{"id": "s1", "depends_on": []}]},
        "results": {"s1": {
            "workdir": str(workdir), "workdir_id": workdir.name,
            "outputs": [f"outputs/{name}" for name in ("a.txt", "b.txt", "c.txt")],
            "output_sha256": {f"outputs/{name}": digest(workdir / "outputs" / name)
                              for name in ("a.txt", "b.txt", "c.txt")},
        }},
    }

    bundle = Path(build_request_bundle(request, settings)["path"])
    with (bundle / "MANIFEST.tsv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    by_path = {row["relative_path"]: row for row in rows}

    assert by_path["steps/s1/outputs/a.txt"]["status"] == "copied"
    assert by_path["steps/s1/outputs/b.txt"]["status"] == "not copied: total limit"
    assert by_path["steps/s1/outputs/c.txt"]["status"] == "not copied: total limit"
    assert "누적 상한으로 기록 산출 일부를 복사하지 않음" in (
        bundle / "report_appendix.md").read_text(encoding="utf-8")


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
        return {"path": str(tmp_path / "bundle"), "status": "complete"}

    monkeypatch.setattr(request_bundle_module, "build_request_bundle", slow_bundle)
    data = {"ok": True}
    task = hub.schedule_terminal("worker", "request.completed", data)
    assert hub.schedule_terminal("worker", "request.completed", data) is task
    await asyncio.wait_for(started.wait(), 2)
    stored = hub.store.get("request", "worker")
    assert stored["status"] == "done" and stored["report"] == "body"
    assert any(event["type"] == "request.completed" for event in hub.store.events_since(0))

    restored = Hub(settings)
    assert restored.requests["worker"]["status"] == "done"
    assert not any(entry["approval"].get("kind") == "resume" for entry in restored.approvals.values())
    assert calls == [calls[0]]  # restart does not schedule another bundle build
    restored.store.close()

    await asyncio.sleep(0)  # the event loop remains available while the worker is blocked
    release.set()
    await asyncio.wait_for(task, 2)
    assert len(calls) == 1 and calls[0] != threading.get_ident()
    assert data["bundle_path"] == str(tmp_path / "bundle")
    assert [event["type"] for event in hub.store.events_since(0)].count("request.bundle") == 1
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
              "workdir": str(workdir), "workdir_id": workdir.name, "outputs": ["outputs/answer.txt"],
              "output_sha256": {"outputs/answer.txt": digest(output)}}
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
    assert data["bundle_status"] == "complete"
    assert hub.events[-1]["data"]["bundle_path"] == data["bundle_path"]
    assert hub.events[-1]["data"]["bundle_status"] == "complete"
    # The grade reaches the request and its event, so the web shows it next to the path (#423).
    assert data["bundle_grade"] == hub.requests[rid]["bundle_grade"] == hub.events[-1]["data"]["bundle_grade"]
    assert data["bundle_grade"] in ("replayable", "documented")
    await asyncio_sleep()
    hub.store.close()


@pytest.mark.asyncio
async def test_incomplete_bundle_status_is_stored_in_bundle_event(tmp_path):
    settings = configured(tmp_path)
    workdir = Path(settings.runner.workspace_root) / "2026-10-04" / "task_incomplete"
    output = workdir / "outputs/answer.txt"
    write(workdir / "manifest.json", json.dumps({"host": platform.node()}))
    write(output, "during run\n")
    recorded_hash = digest(output)
    write(output, "after run\n")
    result = {"task_id": "t-incomplete", "workdir": str(workdir), "workdir_id": workdir.name,
              "outputs": ["outputs/answer.txt"],
              "output_sha256": {"outputs/answer.txt": recorded_hash}}
    hub = Hub(settings)
    hub.requests["incomplete"] = {
        "id": "incomplete", "mode": "direct", "status": "done", "report": "body",
        "report_appendix": "appendix", "results": {"direct": result}, "plan": {"steps": []},
    }
    hub.store.put("task", "t-incomplete", {"request_id": "incomplete", "kind": "direct",
                                             "completed": True, "result": result})
    data = {"ok": True, "report": "body", "report_appendix": "appendix"}

    hub.commit_terminal("incomplete", "request.completed", data)

    assert data["bundle_status"] == "incomplete"
    assert hub.events[-1]["type"] == "request.bundle"
    assert hub.events[-1]["data"]["bundle_status"] == "incomplete"
    assert "요청 묶음 상태: incomplete" in Path(data["bundle_path"], "report_appendix.md").read_text(
        encoding="utf-8")
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
    for rule in ("upstream step files by their relative paths under inputs/<step id>/",
                 "collected in one variable block at the top", "never write an absolute path into a script"):
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
        return [{"id": "r1", "status": "done", "bundle_path": "C:/runs/requests/r1",
                 "bundle_warning": "RO-Crate metadata unavailable"}]

    monkeypatch.setattr("labhq.cli._api", api)
    main(["status"])
    output = capsys.readouterr().out
    assert "요청 묶음: C:/runs/requests/r1" in output
    assert "경고: RO-Crate metadata unavailable" in output


def _graded(tmp_path, files: dict[str, dict[str, str]], deps: dict[str, list[str]]):
    """Bundle with each step's recorded files {step: {outputs/...: text}}; returns (built, README text)."""
    settings = configured(tmp_path)
    root = Path(settings.runner.workspace_root)
    results = {}
    for step_id, outputs in files.items():
        workdir = root / "2026-10-06" / f"task_{step_id}"
        write(workdir / "manifest.json", json.dumps({"host": platform.node()}))
        for rel, text in outputs.items():
            write(workdir / rel, text)
        results[step_id] = {"workdir": str(workdir), "workdir_id": workdir.name, "outputs": list(outputs),
                            "output_sha256": {rel: digest(workdir / rel) for rel in outputs}}
    request = {"id": f"grade_{len(tmp_path.name)}", "report": "ok", "report_appendix": "",
               "plan": {"steps": [{"id": sid, "depends_on": deps.get(sid, [])} for sid in files]},
               "results": results}
    built = build_request_bundle(request, settings)
    return built, (Path(built["path"]) / "README.md").read_text(encoding="utf-8")


def test_a_bundle_with_scripts_and_an_inherited_environment_record_is_replayable(tmp_path):
    built, readme = _graded(tmp_path, {
        "env": {"outputs/env/requirements.lock.txt": "pandas==2.2.2\n"},
        "de": {"outputs/scripts/de.py": "import pandas\n", "outputs/de.tsv": "gene\tlogFC\n",
               "outputs/summary.md": "# DE\n"},
        "lit": {"outputs/notes.md": "# papers\n"},  # prose only: no script asked
    }, {"de": ["env"]})

    assert (built["grade"], built["grade_reasons"]) == ("replayable", [])
    assert "## 재현 등급\n\n- `replayable`\n" in readme
    assert "`rerun_verified`는 다른 곳에서 다시 돌린 기록이 있을 때만" in readme


def test_missing_scripts_environment_or_a_kept_absolute_path_lowers_the_grade_with_reasons(tmp_path):
    outside = "C:\\data\\cohort.tsv" if os.name == "nt" else "/data/cohort.tsv"
    built, readme = _graded(tmp_path, {
        "qc": {"outputs/qc.tsv": "sample\tok\n"},  # data, no script
        "de": {"outputs/scripts/de.py": f'DATA = r"{outside}"\n', "outputs/de.tsv": "x\n"},  # no env, abs path
    }, {"de": ["qc"]})

    assert built["grade"] == "documented"
    assert built["grade_reasons"] == [
        "스크립트 없이 데이터 산출만 있는 단계: qc",
        "환경 기록(outputs/env/)이 없는 단계: de",
        "절대경로가 남은 스크립트: steps/de/outputs/scripts/de.py",
    ]
    assert "- `documented`\n- 스크립트 없이 데이터 산출만 있는 단계: qc\n" in readme
    appendix = (Path(built["path"]) / "report_appendix.md").read_text(encoding="utf-8")
    assert "- 재현 등급: documented (스크립트 없이 데이터 산출만 있는 단계: qc; " in appendix


def test_a_txt_data_output_without_a_script_is_not_replayable(tmp_path):
    """PR #431 review: counts.txt or variants.txt is data, so its step needs a script like a .tsv would."""
    built, _readme = _graded(tmp_path, {"count": {"outputs/counts.txt": "TP53 12\n", "outputs/notes.md": "# n\n"}}, {})
    assert built["grade"] == "documented"
    assert built["grade_reasons"] == ["스크립트 없이 데이터 산출만 있는 단계: count"]


def test_ro_crate_is_deterministic_and_uses_the_pinned_profiles(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    request["finished_at"] = 1791352800

    first = Path(build_request_bundle(request, settings, tasks)["path"])
    first_bytes = (first / METADATA_FILE).read_bytes()
    second = Path(build_request_bundle(request, settings, tasks)["path"])
    crate = json.loads((second / METADATA_FILE).read_text(encoding="utf-8"))
    entities = {entity["@id"]: entity for entity in crate["@graph"]}

    assert (second / METADATA_FILE).read_bytes() == first_bytes
    assert crate["@context"] == INLINE_CONTEXT
    assert INLINE_CONTEXT["conformsTo"] == "http://purl.org/dc/terms/conformsTo"
    assert entities[METADATA_FILE]["conformsTo"] == [{"@id": RO_CRATE_PROFILE},
                                                        {"@id": PROCESS_RUN_PROFILE}]
    assert entities["./"]["identifier"] == request["id"]
    assert entities["./"]["conformsTo"] == {"@id": PROCESS_RUN_PROFILE}
    assert entities["./"]["datePublished"] == "2026-10-07T06:00:00+00:00"
    assert manifest_rows(second)[METADATA_FILE]["sha256"] == digest(second / METADATA_FILE)
    assert "sha256" not in entities["MANIFEST.tsv"]


def test_ro_crate_records_only_executed_steps_and_failed_status(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    request["plan"]["steps"].append({"id": "planned-only", "depends_on": []})
    request["results"]["s2"]["ok"] = False
    second_manifest = Path(request["results"]["s2"]["workdir"]) / "manifest.json"
    second_manifest.write_text(json.dumps({
        "host": platform.node(), "engine": "codex", "model": "gpt-test",
        "runs": {"t-second": {"started_at": 1791352800, "ended_at": 1791352860,
                                "engine_cli_version": "1.2.3"}},
    }), encoding="utf-8")

    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    entities = crate_entities(bundle)
    actions = [entity for entity in entities.values() if entity.get("@type") == "CreateAction"]

    assert len(actions) == 2
    assert not any("planned-only" in entity.get("name", "") for entity in actions)
    failed = next(entity for entity in actions if entity["name"] == "Run step s2")
    assert failed["actionStatus"] == {"@id": "https://schema.org/FailedActionStatus"}
    assert failed["startTime"] == "2026-10-07T06:00:00+00:00"
    assert failed["endTime"] == "2026-10-07T06:01:00+00:00"
    assert "object" not in failed and "agent" not in failed
    instrument = entities[failed["instrument"]["@id"]]
    assert instrument["@type"] == "SoftwareApplication"
    assert instrument["softwareVersion"] == "1.2.3"
    assert not any(entity.get("@type") == "Person" for entity in entities.values())


def test_input_inventory_is_context_not_action_object_and_private_rows_are_omitted(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    manifest = Path(request["results"]["s2"]["workdir"]) / "manifest.json"
    manifest.write_text(json.dumps({
        "host": platform.node(), "engine": "codex",
        "runs": {"t-second": {"input_files": [
            {"path": r"C:\visible\public.tsv", "size": 7, "mtime_ns": 1, "sha256": "a" * 64},
            {"path": r"D:\controlled\patient.tsv", "skipped": "private"},
            {"path": "/restricted/cohort.tsv", "skipped": "restricted"},
        ]}},
    }), encoding="utf-8")

    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    crate_text = (bundle / METADATA_FILE).read_text(encoding="utf-8")
    entities = crate_entities(bundle)
    action = next(entity for entity in entities.values() if entity.get("name") == "Run step s2")
    scans = [entity for entity in entities.values()
             if entity.get("@type") == "Observation" and str(entity.get("@id", "")).startswith("#input-scan-")]

    assert "object" not in action
    assert len(scans) == 1 and scans[0]["about"] == {"@id": action["@id"]}
    assert "public.tsv" in crate_text and "a" * 64 in crate_text
    assert "patient.tsv" not in crate_text and "cohort.tsv" not in crate_text
    assert "C:\\visible" not in crate_text and "/restricted" not in crate_text
    assert "protected item(s) were omitted" in scans[0]["description"]


def test_output_uri_is_relative_encoded_and_case_preserving(tmp_path):
    settings = configured(tmp_path)
    workdir = Path(settings.runner.workspace_root) / "2026-10-07" / "task_uri"
    output = workdir / "outputs" / "Data" / "Case File.TXT"
    write(workdir / "manifest.json", json.dumps({"host": platform.node(), "engine": "mock"}))
    write(output, "value\n")
    request = {"id": "uri", "report": "ok", "report_appendix": "", "plan": {"steps": []},
               "results": {"direct": {"task_id": "t", "ok": True, "workdir": str(workdir),
                                        "workdir_id": workdir.name,
                                        "outputs": ["outputs/Data/Case File.TXT"],
                                        "output_sha256": {"outputs/Data/Case File.TXT": digest(output)}}}}

    bundle = Path(build_request_bundle(request, settings)["path"])
    entities = crate_entities(bundle)

    assert "steps/direct/outputs/Data/Case%20File.TXT" in entities
    assert not any(str(identifier).startswith(("C:", "file:")) for identifier in entities)


def test_ro_crate_privacy_refusal_is_warning_only(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    manifest = Path(request["results"]["s1"]["workdir"]) / "manifest.json"
    manifest.write_text(json.dumps({"host": platform.node(), "engine": r"C:\private\engine"}), encoding="utf-8")

    built = build_request_bundle(request, settings, tasks)
    bundle = Path(built["path"])

    assert "crate_warning" in built
    assert not (bundle / METADATA_FILE).exists()
    assert FORMAT_MARKER in (bundle / "README.md").read_text(encoding="utf-8")
    assert "RO-Crate metadata를 만들지 못했습니다" in (bundle / "report_appendix.md").read_text(encoding="utf-8")


def test_ro_crate_allows_consecutive_dots_inside_names():
    crate = build_ro_crate(
        {"id": "req..1"},
        [{"relative_path": "sample..final.tsv", "status": "generated", "size": 1,
          "sha256": "a" * 64}],
        [],
        {},
    )

    entities = {entity["@id"]: entity for entity in crate["@graph"]}
    assert entities["./"]["identifier"] == "req..1"
    assert "sample..final.tsv" in entities


@pytest.mark.parametrize("value", ["../x", "a/../b", "%2e%2e/x", r"a\..\b"])
def test_ro_crate_rejects_parent_path_components_after_uri_decoding(value):
    with pytest.raises(ValueError, match="안전하지 않은 경로"):
        build_ro_crate({"id": value}, [], [], {})


UNSAFE_BUNDLE_PATHS = [
    "../../x", "//server/share/x", "C:/outside/x", r"\\?\C:\outside\x",
    "file:///outside/x", "safe.txt:ads",
]


@pytest.mark.parametrize("relative", UNSAFE_BUNDLE_PATHS)
def test_plain_file_rejects_unsafe_paths_before_filesystem_access(tmp_path, monkeypatch, relative):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("filesystem access happened before lexical validation")

    monkeypatch.setattr("labhq.adapters.owned.is_link", unexpected)
    monkeypatch.setattr(Path, "is_file", unexpected)
    monkeypatch.setattr(Path, "resolve", unexpected)

    with pytest.raises(OSError, match="unsafe bundle path"):
        ro_crate_module._plain_file(tmp_path, relative)


@pytest.mark.parametrize("source", ["manifest", "crate"])
@pytest.mark.parametrize("relative", UNSAFE_BUNDLE_PATHS)
def test_verify_rejects_unsafe_declared_paths_before_opening_them(
        tmp_path, monkeypatch, source, relative):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    if source == "manifest":
        path = bundle / "MANIFEST.tsv"
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            fields, rows = reader.fieldnames, list(reader)
        next(row for row in rows if row["relative_path"] == "README.md")["relative_path"] = relative
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    else:
        path = bundle / METADATA_FILE
        crate = json.loads(path.read_text(encoding="utf-8"))
        next(entity for entity in crate["@graph"] if entity.get("@id") == "README.md")["@id"] = relative
        path.write_text(json.dumps(crate), encoding="utf-8")

    original = ro_crate_module._plain_file

    def guarded_open(root, candidate):
        assert candidate != relative
        return original(root, candidate)

    monkeypatch.setattr(ro_crate_module, "_plain_file", guarded_open)
    report = verify_bundle_copy(bundle)

    assert report["problems"]
    assert any("안전하지" in problem or "unsafe local entity id" in problem
               for problem in report["problems"])


@pytest.mark.parametrize("target", ["payload", "manifest", "crate"])
def test_verify_detects_bundle_file_manifest_and_crate_tampering(tmp_path, target):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    if target == "payload":
        write(bundle / "steps/s1/outputs/data/input.tsv", "tampered\n")
    elif target == "manifest":
        path = bundle / "MANIFEST.tsv"
        text = path.read_text(encoding="utf-8")
        recorded = manifest_rows(bundle)["README.md"]["sha256"]
        path.write_text(text.replace(recorded, "0" * 64, 1), encoding="utf-8")
    else:
        crate = json.loads((bundle / METADATA_FILE).read_text(encoding="utf-8"))
        file_entity = next(entity for entity in crate["@graph"] if entity.get("sha256"))
        file_entity["sha256"] = "0" * 64
        (bundle / METADATA_FILE).write_text(json.dumps(crate), encoding="utf-8")

    report = verify_request(request, settings)

    assert report["exit_code"] == 1
    assert any("요청 묶음 사본" in problem for problem in report["problems"])


@pytest.mark.parametrize(("field", "value", "message"), [
    ("size", "", "size가 정수가 아닙니다"),
    ("size", "1.5", "size가 정수가 아닙니다"),
    ("sha256", "", "sha256이 64자리 hex가 아닙니다"),
    ("sha256", "g" * 64, "sha256이 64자리 hex가 아닙니다"),
])
def test_verify_rejects_missing_or_malformed_manifest_integrity_fields(
        tmp_path, field, value, message):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    path = bundle / "MANIFEST.tsv"
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields, rows = reader.fieldnames, list(reader)
    next(row for row in rows if row["relative_path"] == "README.md")[field] = value
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    report = verify_request(request, settings)

    assert report["exit_code"] == 1
    assert any(message in problem and "README.md" in problem for problem in report["problems"])


def test_verify_rejects_linked_metadata_before_file_checks(tmp_path, monkeypatch):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    metadata = bundle / METADATA_FILE
    target = tmp_path / "metadata-target"
    original_is_file = Path.is_file
    metadata.unlink()
    try:
        if os.name == "nt":
            import _winapi
            target.mkdir()
            _winapi.CreateJunction(str(target), str(metadata))
        else:
            write(target, "{}\n")
            metadata.symlink_to(target)
    except OSError:
        pytest.skip("metadata links are unavailable")

    def guarded_is_file(path):
        if path == metadata:
            raise AssertionError("metadata is_file followed a link before validation")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", guarded_is_file)
    try:
        report = verify_bundle_copy(bundle)
        assert any("link or junction" in problem for problem in report["problems"])
    finally:
        if os.name == "nt":
            metadata.rmdir()
        else:
            metadata.unlink()


def test_verify_detects_dangling_and_duplicate_ro_crate_ids(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    crate = json.loads((bundle / METADATA_FILE).read_text(encoding="utf-8"))
    crate["@graph"][1]["mentions"] = {"@id": "#missing"}
    crate["@graph"].append(dict(crate["@graph"][1]))
    (bundle / METADATA_FILE).write_text(json.dumps(crate), encoding="utf-8")

    report = verify_bundle_copy(bundle)

    assert any("reference가 해소되지" in problem for problem in report["problems"])
    assert any("@id가 중복" in problem for problem in report["problems"])


@pytest.mark.parametrize(("reference", "message"), [
    ("http://[", "reference URL이 올바르지"),
    ([], "reference @id가 문자열이 아닙니다"),
])
def test_cli_verify_reports_invalid_reference_without_traceback(
        tmp_path, monkeypatch, capsys, reference, message):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    crate = json.loads((bundle / METADATA_FILE).read_text(encoding="utf-8"))
    crate["@graph"][1]["mentions"] = {"@id": reference}
    (bundle / METADATA_FILE).write_text(json.dumps(crate), encoding="utf-8")
    monkeypatch.setattr("labhq.cli._api", lambda *_args, **_kwargs: request)

    exit_code = _verify(settings, request["id"], as_json=True, bundle=None)
    report = json.loads(capsys.readouterr().out)

    assert exit_code == report["exit_code"] == 1
    assert any(message in problem for problem in report["problems"])


def test_verify_rejects_a_link_or_junction_inside_the_bundle(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    data_dir = bundle / "steps/s1/outputs/data"
    outside = tmp_path / "junction-target"
    write(outside / "input.tsv", "value\n7\n")
    shutil.rmtree(data_dir)
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(data_dir))
        else:
            data_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory links are unavailable")
    try:
        report = verify_bundle_copy(bundle)
        assert any("link or junction" in problem for problem in report["problems"])
    finally:
        if os.name == "nt":
            data_dir.rmdir()
        else:
            data_dir.unlink()


def test_verify_detects_an_unregistered_file_in_the_bundle(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    write(bundle / "injected.txt", "not inventoried\n")

    report = verify_bundle_copy(bundle)

    assert any("MANIFEST에 등록되지 않은 파일" in problem and "injected.txt" in problem
               for problem in report["problems"])


def test_verify_detects_an_unregistered_link_without_following_it(tmp_path):
    settings, request, tasks, _upstream, outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    target = outside.parent
    link = bundle / "injected-link"
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory links are unavailable")
    try:
        report = verify_bundle_copy(bundle)
        assert any("link 또는 junction" in problem and "injected-link" in problem
                   for problem in report["problems"])
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_old_bundle_without_ro_crate_keeps_the_previous_verdict(tmp_path):
    settings = configured(tmp_path)
    request = {"id": "old_bundle", "status": "done", "results": {}, "plan": {"steps": []}}
    bundle = Path(settings.runner.workspace_root) / "requests" / request["id"]
    write(bundle / "README.md", "# old request bundle\n")

    report = verify_request(request, settings)

    assert report["exit_code"] == 0
    assert report["request_bundle"]["present"] is True
    assert report["request_bundle"]["crate"] is False


def test_verify_detects_manifested_crate_and_readme_removed_together(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    (bundle / METADATA_FILE).unlink()
    (bundle / "README.md").unlink()

    report = verify_request(request, settings)

    assert report["exit_code"] == 1
    assert any("MANIFEST에 기록된 ro-crate-metadata.json이 없습니다" in problem
               for problem in report["problems"])
    assert any("MANIFEST에 기록된 README.md가 없습니다" in problem
               for problem in report["problems"])


def test_verify_reports_unreadable_readme_before_legacy_fallback(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    (bundle / METADATA_FILE).unlink()
    (bundle / "README.md").write_bytes(b"\xff")

    report = verify_request(request, settings)

    assert report["exit_code"] == 1
    assert any("README.md를 읽지 못했습니다" in problem for problem in report["problems"])


def test_verify_requires_bundle_folder_and_identifier_to_match_request_id(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])

    report = verify_bundle_copy(bundle, "request_a")

    assert any("묶음 폴더 이름이 요청 ID와 다릅니다" in problem for problem in report["problems"])
    assert any("root Dataset identifier가 요청 ID와 다릅니다" in problem
               for problem in report["problems"])


def test_verify_request_rejects_another_requests_bundle(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])
    request["id"] = "request_a"
    expected_bundle = bundle.with_name(request["id"])
    bundle.rename(expected_bundle)
    request["bundle_path"] = str(expected_bundle)

    report = verify_request(request, settings)

    assert report["exit_code"] == 1
    assert any("root Dataset identifier가 요청 ID와 다릅니다" in problem
               for problem in report["problems"])


def test_recorded_bundle_missing_from_disk_is_a_problem(tmp_path):
    settings = configured(tmp_path)
    request = {"id": "deleted_bundle", "status": "done", "results": {}, "plan": {"steps": []},
               "bundle_path": str(Path(settings.runner.workspace_root) / "requests" / "deleted_bundle")}

    report = verify_request(request, settings)

    assert report["exit_code"] == 1
    assert report["request_bundle"]["present"] is False
    assert any("기록된 묶음 폴더가 없습니다" in problem for problem in report["problems"])


def test_unrecorded_missing_bundle_keeps_the_previous_verdict(tmp_path):
    settings = configured(tmp_path)
    request = {"id": "old_without_bundle", "status": "done", "results": {}, "plan": {"steps": []}}

    report = verify_request(request, settings)

    assert report["exit_code"] == 0
    assert report["request_bundle"]["present"] is False


def test_new_bundle_missing_ro_crate_is_a_problem(tmp_path):
    settings = configured(tmp_path)
    request = {"id": "new_missing", "status": "done", "results": {}, "plan": {"steps": []}}
    bundle = Path(settings.runner.workspace_root) / "requests" / request["id"]
    write(bundle / "README.md", FORMAT_MARKER + "\n")

    report = verify_request(request, settings)

    assert report["exit_code"] == 1
    assert any(METADATA_FILE in problem for problem in report["problems"])


def test_external_input_hash_is_compared_as_a_record_not_rehashed(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    manifest = Path(request["results"]["s1"]["workdir"]) / "manifest.json"
    manifest.write_text(json.dumps({
        "host": platform.node(), "engine": "mock",
        "runs": {"t-first": {"input_files": [
            {"path": "reference/source.tsv", "size": 10, "mtime_ns": 1, "sha256": "b" * 64},
        ]}},
    }), encoding="utf-8")
    Path(build_request_bundle(request, settings, tasks)["path"])

    report = verify_request(request, settings)

    assert report["exit_code"] == 0
    assert report["request_bundle"]["external_input_hashes"] == 1
    rendered = render_verify(report)
    assert "기록끼리 1개 대조" in rendered and "원본 재해시 아님" in rendered


@pytest.mark.skipif(not os.environ.get("LABHQ_ROCRATE_VALIDATOR"), reason="official validator CI job only")
def test_official_ro_crate_validator_offline(tmp_path):
    settings, request, tasks, _upstream, _outside, _old = request_fixture(tmp_path)
    request["finished_at"] = 1791352800
    bundle = Path(build_request_bundle(request, settings, tasks)["path"])

    checked = subprocess.run([
        "rocrate-validator", "validate", "--offline", "--no-auto-profile",
        "--profile-identifier", "process-run-crate", "--requirement-severity", "REQUIRED",
        "--skip-availability-check", "--no-paging", str(bundle),
    ], capture_output=True, text=True)

    assert checked.returncode == 0, checked.stdout + checked.stderr


class _LockedRename:
    """A temp bundle whose rename hits a Windows sharing lock a few times (WinError 5)."""

    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    def replace(self, target):
        self.calls += 1
        if self.calls <= self.failures:
            raise PermissionError(13, "Access is denied")
        return target


def test_bundle_rename_retries_a_brief_windows_lock(monkeypatch):
    # The v0.5 trial lost its bundle to one WinError 5 right after the copy (2026-10-08).
    monkeypatch.setattr(request_bundle_module, "RENAME_RETRY_DELAYS", (0, 0, 0))
    locked = _LockedRename(failures=2)
    request_bundle_module._replace_dir(locked, Path("target"), windows=True)
    assert locked.calls == 3


@pytest.mark.parametrize("windows, failures, calls", [(True, 9, 4), (False, 1, 1)])
def test_bundle_rename_gives_up_after_the_retries_or_off_windows(monkeypatch, windows, failures, calls):
    monkeypatch.setattr(request_bundle_module, "RENAME_RETRY_DELAYS", (0, 0, 0))
    locked = _LockedRename(failures=failures)
    with pytest.raises(PermissionError):
        request_bundle_module._replace_dir(locked, Path("target"), windows=windows)
    assert locked.calls == calls
