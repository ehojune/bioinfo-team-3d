"""Annotation capture and real ancestor staging, with synthetic records only."""
import hashlib
import json
import os
import subprocess

import pytest

from labhq.models import AgentSpec, Task
from labhq.runner.annotation_outputs import collect_annotation_outputs
from labhq.runner.step_inputs import MAX_FILES, stage_inputs
from labhq.runner.workspace import TaskWorkspace


def workspace(root, task_id="source", step="search"):
    return TaskWorkspace(root, Task(id=task_id, request_id="fixture-request", agent_id="worker",
                                   prompt="fixture", meta={"step_id": step}),
                         AgentSpec(id="worker", name="Worker", role="test"))


def put(ws, path, content=b"fixture"):
    target = ws.dir / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def scan(ws, *, entries=2000, depth=10, hash_limit=16 * 1024 * 1024):
    rows, note = ws.scan_output_records([], entries, depth, hash_limit)
    return rows, [note] if note else []


def collect(ws, outputs=(), hashes=None, *, max_file=16 * 1024 * 1024,
            max_total=32 * 1024 * 1024, records=None, notes=None):
    if records is None:
        records, scanned_notes = scan(ws)
        notes = scanned_notes if notes is None else notes
    return collect_annotation_outputs(ws.dir, records, list(outputs), hashes or {},
                                      max_file=max_file, max_total=max_total, scan_notes=notes or [])


def test_unlisted_large_korean_records_reach_next_step_with_same_sha(tmp_path):
    upstream = workspace(tmp_path)
    payload = json.dumps({"results": [{"id": i, "abstract": "가상 문헌 초록 " * 300}
                                      for i in range(631)]}, ensure_ascii=False).encode("utf-8")
    assert len(payload) > 2 * 1024 * 1024
    paths = ["outputs/annotation/search-page.json", "outputs/annotation_queries.jsonl"]
    put(upstream, paths[0], payload)
    put(upstream, paths[1], b'{"fixture":true}\n')
    put(upstream, "outputs/unrelated.json")
    collected = collect(upstream)  # staff declared no outputs
    assert not collected.excluded
    assert set(collected.outputs) == set(paths)
    upstream.update_run("source", ok=True)
    downstream = workspace(tmp_path, "consumer", "synthesis")
    artifact = {"task_id": "source", "workdir": str(upstream.dir), "outputs": collected.outputs,
                "output_sha256": collected.hashes}
    staged, refused = stage_inputs(downstream, {"search": artifact}, tmp_path, [], [],
                                    16 * 1024 * 1024, 32 * 1024 * 1024)
    assert staged and not refused
    for path in paths:
        target = downstream.dir / "inputs/search" / path.removeprefix("outputs/")
        assert hashlib.sha256(target.read_bytes()).hexdigest() == collected.hashes[path]
        assert not target.is_symlink()
    copied = json.loads((downstream.dir / "inputs/search/annotation/search-page.json").read_bytes())
    assert len(copied["results"]) == 631
    assert not (downstream.dir / "inputs/search/unrelated.json").exists()


def test_existing_outputs_keep_priority_and_log_uses_last_slot(tmp_path):
    ws = workspace(tmp_path)
    existing = [f"outputs/f{i}.txt" for i in range(MAX_FILES - 1)]
    for path in [*existing, "outputs/annotation_queries.jsonl", "outputs/annotation/a.json"]:
        put(ws, path)
    rows, _ = scan(ws)
    hashes = {row["path"]: row["sha256"] for row in rows if row["path"] in existing}
    result = collect(ws, existing, hashes, records=rows)
    assert result.outputs == [*existing, "outputs/annotation_queries.jsonl"]
    assert "at most 200" in result.excluded["outputs/annotation/a.json"]
    assert len(result.hashes) == MAX_FILES


@pytest.mark.parametrize("limit", ["file", "total", "hash"])
def test_limits_name_excluded_files_without_exceeding_budget(tmp_path, limit):
    ws = workspace(tmp_path)
    path = "outputs/annotation/a.json"
    put(ws, path, b"a" * 20)
    rows, notes = scan(ws, hash_limit=10 if limit == "hash" else 100)
    result = collect(ws, records=rows, notes=notes,
                     max_file=10 if limit == "file" else 100,
                     max_total=10 if limit == "total" else 100)
    assert not result.outputs and not result.hashes
    assert result.excluded[path]


def test_total_budget_includes_existing_outputs_and_duplicates_are_not_counted_twice(tmp_path):
    ws = workspace(tmp_path)
    plain, annotation = "outputs/plain.txt", "outputs/annotation/a.json"
    put(ws, plain, b"a" * 6)
    put(ws, annotation, b"b" * 5)
    rows, _ = scan(ws)
    hashes = {row["path"]: row["sha256"] for row in rows}
    result = collect(ws, [plain, plain, annotation], hashes, records=rows, max_total=10)
    assert result.outputs == [plain]
    assert "bytes in all" in result.excluded[annotation]
    result = collect(ws, [plain, annotation, annotation], hashes, records=rows, max_total=11)
    assert result.outputs == [plain, annotation] and not result.excluded


@pytest.mark.parametrize("kind", ["hardlink", "symlink", "folder_symlink"])
def test_links_are_never_collected_even_when_staff_list_them(tmp_path, kind):
    ws = workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.json").write_bytes(b"external fixture")
    path = "outputs/annotation/a.json"
    try:
        if kind == "folder_symlink":
            (ws.dir / "outputs/annotation").symlink_to(outside, target_is_directory=True)
        else:
            (ws.dir / "outputs/annotation").mkdir()
            if kind == "hardlink":
                os.link(outside / "a.json", ws.dir / path)
            else:
                (ws.dir / path).symlink_to(outside / "a.json")
    except OSError as exc:
        pytest.skip(f"native link unavailable: {exc}")
    result = collect(ws, [path])
    assert not result.outputs and not result.hashes
    assert result.excluded[path]


def test_modified_and_unscanned_files_have_explicit_reasons(tmp_path):
    ws = workspace(tmp_path)
    path = "outputs/annotation/a.json"
    target = put(ws, path)
    rows, _ = scan(ws)
    target.write_bytes(b"changed fixture")
    missing = "outputs/annotation/not-scanned.json"
    result = collect(ws, [missing], records=rows)
    assert not result.outputs
    assert "changed" in result.excluded[path]
    assert "not present" in result.excluded[missing]


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction")
def test_windows_junction_is_reported_and_never_followed(tmp_path):
    ws = workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.json").write_bytes(b"external fixture")
    junction = ws.dir / "outputs/annotation"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                             capture_output=True)
    assert created.returncode == 0, created.stderr
    result = collect(ws, ["outputs/annotation/a.json"])
    assert not result.outputs and not result.hashes
    assert "outputs/annotation" in result.excluded
    assert "outputs/annotation/a.json" in result.excluded


def test_source_parent_swapped_for_junction_after_scan_is_refused(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    path = "outputs/annotation/a.json"
    put(ws, path)
    rows, _ = scan(ws)
    from labhq.adapters.held_dir import HeldDir, NotPlainFolder
    original = HeldDir.child

    def swapped(self, name, expect=None):
        if name == "annotation":
            raise NotPlainFolder("fixture: replaced with reparse point after scan")
        return original(self, name, expect)

    monkeypatch.setattr(HeldDir, "child", swapped)
    result = collect(ws, records=rows)
    assert not result.outputs
    assert "link or unreadable" in result.excluded[path]


def test_entry_limit_is_reported_instead_of_claiming_complete_capture(tmp_path):
    ws = workspace(tmp_path)
    for i in range(6):
        put(ws, f"outputs/annotation/{i}.json")
    rows, notes = scan(ws, entries=3)
    assert notes
    result = collect(ws, records=rows, notes=notes)
    assert len(result.outputs) < 6
    assert "additional annotation paths may be missing" in result.excluded["outputs/annotation/* (scan)"]


def test_scan_truncation_and_non_annotation_files(tmp_path):
    ws = workspace(tmp_path)
    put(ws, "outputs/annotation/a.json")
    put(ws, "outputs/annotation/nested/b.json")
    put(ws, "outputs/annotation-other.json")
    rows, notes = scan(ws, depth=1)
    assert notes
    result = collect(ws, records=rows, notes=notes)
    assert "outputs/annotation/* (scan)" in result.excluded
    assert "outputs/annotation-other.json" not in result.outputs
    assert "outputs/annotation/nested/b.json" not in result.outputs


@pytest.mark.parametrize("name", ["auth.json", "bad$name.json"])
def test_protected_and_nonportable_names_are_reported(tmp_path, name):
    ws = workspace(tmp_path)
    path = "outputs/annotation/" + name
    put(ws, path)
    result = collect(ws)
    assert not result.outputs and path in result.excluded


def test_consumer_rejects_changed_hash_and_clears_partial_snapshot(tmp_path):
    ws = workspace(tmp_path)
    path = "outputs/annotation/a.json"
    target = put(ws, path)
    result = collect(ws)
    assert result.outputs == [path]
    ws.update_run("source", ok=True)
    target.write_bytes(b"changed after collection")
    consumer = workspace(tmp_path, "consumer", "synthesis")
    artifact = {"task_id": "source", "workdir": str(ws.dir), "outputs": result.outputs,
                "output_sha256": result.hashes}
    staged, refused = stage_inputs(consumer, {"search": artifact}, tmp_path, [], [], 100, 1000)
    assert not staged and "search" in refused
    assert not (consumer.dir / "inputs/search/annotation/a.json").exists()
