"""Output hashes for the shadow (#150 B1): same-disk runner only, allowed zones only, regular files only, within
the caps. An earlier output is a reuse candidate only when its bytes still match the first observation."""
from __future__ import annotations

import builtins
import json
import os

import pytest

from labhq.research import semantics_shadow as shadow
from labhq.settings import DataZone
from tests.semantics_shadow_lab import fake_hub, line_for, request_row, task_row, workspace

TYPES = {"outputs/counts.tsv": "raw_counts"}


def _lab(tmp_path, *, files=None, host=None, outputs=("outputs/counts.tsv",), types=TYPES, **hub_options):
    wd, _ = workspace(tmp_path, "task_a1", "analyst", files or {"outputs/counts.tsv": b"gene\tn\nX\t1\n"}, host=host)
    wd_b, _ = workspace(tmp_path, "task_b1", "analyst", {})
    requests = {"req_a": request_row("req_a", [("s1", "analyst")], created_at=1.0),
                "req_b": request_row("req_b", [("s1", "analyst")], created_at=2.0)}
    tasks = {"task_a1": task_row("req_a", "task_a1", "s1", "analyst", wd, list(outputs), output_types=types),
             "task_b1": task_row("req_b", "task_b1", "s1", "analyst", wd_b, [])}
    return fake_hub(tmp_path, requests, tasks, **hub_options), wd


@pytest.fixture
def reads(monkeypatch):
    """Counts files the shadow opens under outputs/."""
    opened = []
    real = builtins.open

    def counting(path, *args, **kwargs):
        if "outputs" in str(path):
            opened.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(shadow, "open", counting, raising=False)
    return opened


def test_a_verified_earlier_output_with_a_declared_type_is_a_candidate(tmp_path):
    hub, _ = _lab(tmp_path)
    observed: dict = {}
    first = line_for(hub, "req_a", observed)
    assert first["hash"]["observed_new"] == 1 and first["hash"]["workspaces"] == {"ok": 1}
    second = line_for(hub, "req_b", observed)
    prov = second["provenance"]
    assert second["hash"]["verified"] == 1
    assert prov["candidates"] == 1 and len(prov["candidate_refs"]) == 1
    assert prov["candidate_refs"][0].startswith("sem:") and len(prov["candidate_refs"][0]) == 12
    assert all(v == 0 for v in prov["excluded"].values())
    assert "counts" not in json.dumps(second) and "outputs" not in json.dumps(second)


def test_a_replaced_file_is_never_promoted_even_with_the_same_size_and_mtime(tmp_path):
    hub, wd = _lab(tmp_path)
    observed: dict = {}
    line_for(hub, "req_a", observed)
    target = os.path.join(wd, "outputs", "counts.tsv")
    before = os.stat(target)
    with open(target, "wb") as handle:
        handle.write(b"gene\tn\nY\t2\n")
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    prov = line_for(hub, "req_b", observed)["provenance"]
    assert prov["candidates"] == 0 and prov["excluded"]["version_changed"] == 1


def test_an_output_never_observed_at_its_finish_is_hash_unknown(tmp_path):
    hub, _ = _lab(tmp_path)
    prov = line_for(hub, "req_b", {})["provenance"]
    assert prov["candidates"] == 0 and prov["excluded"]["hash_unknown"] == 1


def test_a_runner_on_another_host_is_not_read(tmp_path, reads):
    hub, _ = _lab(tmp_path, host="another-host")
    line = line_for(hub, "req_a", {})
    assert line["hash"]["workspaces"] == {"remote": 1} and line["hash"]["hashed"] == 0
    assert reads == []


@pytest.mark.parametrize("case", ["restricted", "outside", "public_project"])
def test_restricted_outside_or_public_project_zones_are_not_read(tmp_path, reads, case):
    runs = str(tmp_path / "runs")
    zones = {"restricted": [DataZone(path=runs, level="internal"),
                            DataZone(path=os.path.join(runs, "2026-10-01"), level="restricted")],
             "outside": [], "public_project": [DataZone(path=runs, level="internal")]}[case]
    hub, _ = _lab(tmp_path, zones=zones, visibility="public" if case == "public_project" else "private")
    observed: dict = {}
    line = line_for(hub, "req_a", observed)
    assert line["hash"]["workspaces"] == {"zone_excluded": 1} and line["hash"]["hashed"] == 0
    assert reads == [] and observed == {}
    assert line_for(hub, "req_b", observed)["provenance"]["excluded"]["zone_excluded"] == 1


def test_a_link_inside_the_workspace_is_not_followed(tmp_path, reads):
    secret = tmp_path / "elsewhere.tsv"
    secret.write_bytes(b"not for the shadow")
    hub, wd = _lab(tmp_path, files={"outputs/keep.txt": b"x"})
    try:
        os.symlink(secret, os.path.join(wd, "outputs", "counts.tsv"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need a privilege on this host")
    line = line_for(hub, "req_a", {})
    assert line["hash"]["skipped"] == {"not_regular": 1} and reads == []


def test_a_unc_workdir_is_not_read(tmp_path, reads):
    hub, _ = _lab(tmp_path)
    hub.store.rows["task"]["task_a1"]["result"]["workdir"] = "\\\\fileserver\\share\\runs\\task_a1_analyst"
    line = line_for(hub, "req_a", {})
    assert line["hash"]["workspaces"] == {"remote": 1} and reads == []


@pytest.mark.parametrize("cap", ["files", "file_size"])
def test_past_a_cap_the_request_is_incomplete(tmp_path, monkeypatch, cap):
    if cap == "files":
        monkeypatch.setattr(shadow, "HASH_MAX_FILES", 1)
    else:
        monkeypatch.setattr(shadow, "HASH_MAX_FILE", 4)
    files = {"outputs/counts.tsv": b"gene\tn\n", "outputs/more.tsv": b"gene\tn\n"}
    hub, _ = _lab(tmp_path, files=files, outputs=tuple(files),
                  types={"outputs/counts.tsv": "raw_counts", "outputs/more.tsv": "raw_counts"})
    observed: dict = {}
    first = line_for(hub, "req_a", observed)
    assert first["provenance"]["incomplete"] is True
    assert first["hash"]["skipped"] == ({"budget": 1} if cap == "files" else {"too_large": 2})
    second = line_for(hub, "req_b", observed)["provenance"]
    # what the capped first look did see is still checked; what it missed stays unknown
    assert second["excluded"]["hash_unknown"] == (1 if cap == "files" else 2)
    assert second["candidates"] == (1 if cap == "files" else 0)


def test_one_broken_reporter_row_never_turns_two_reporters_into_one(tmp_path):
    hub, wd = _lab(tmp_path)
    observed: dict = {}
    line_for(hub, "req_a", observed)
    second = task_row("req_a", "task_a2", "s1", "analyst", wd, ["outputs/counts.tsv"], output_types=TYPES, attempt=2)
    hub.store.rows["task"]["task_a2"] = second
    assert line_for(hub, "req_b", observed)["provenance"]["excluded"]["not_generated"] == 1
    second["result"]["missing_outputs"] = "broken"  # the second reporter's row no longer parses
    prov = line_for(hub, "req_b", observed)["provenance"]
    assert prov["rows_invalid"] == 1 and prov["candidates"] == 0 and prov["excluded"]["incomplete"] == 1


def test_a_file_changing_while_hashed_is_unstable(tmp_path, monkeypatch):
    hub, wd = _lab(tmp_path)
    target = os.path.join(wd, "outputs", "counts.tsv")
    real = os.stat

    def moving(path, *args, **kwargs):
        st = real(path, *args, **kwargs)
        if str(path) == target:
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid,
                                   st.st_size + 1, int(st.st_atime), int(st.st_mtime), int(st.st_ctime)))
        return st

    monkeypatch.setattr(shadow.os, "stat", moving)
    line = line_for(hub, "req_a", {})
    assert line["hash"]["skipped"] == {"unstable": 1} and line["hash"]["observed_new"] == 0


def test_a_junction_inside_the_workspace_is_not_followed(tmp_path, reads):
    if os.name != "nt":
        pytest.skip("junctions are a Windows link")
    import _winapi
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "counts.tsv").write_bytes(b"not for the shadow")
    hub, wd = _lab(tmp_path, files={"outputs/keep.txt": b"x"}, outputs=("outputs/sub/counts.tsv",),
                   types={"outputs/sub/counts.tsv": "raw_counts"})
    _winapi.CreateJunction(str(outside), os.path.join(wd, "outputs", "sub"))
    line = line_for(hub, "req_a", {})
    assert line["hash"]["skipped"] == {"not_regular": 1} and reads == []


def test_a_same_host_manifest_fills_the_run_fields(tmp_path):
    """No output to hash, so only the manifest read differs: agent_spec_sha256 and the run's session."""
    same = line_for(_lab(tmp_path / "same", outputs=(), types=None)[0], "req_a", {})
    remote = line_for(_lab(tmp_path / "remote", outputs=(), types=None, host="another-host")[0], "req_a", {})
    assert same["hash"]["workspaces"] == {"ok": 1} and remote["hash"]["workspaces"] == {"remote": 1}
    assert same["provenance"]["unknown_ratio"] < remote["provenance"]["unknown_ratio"]


def test_a_manifest_inside_a_restricted_zone_is_not_read(tmp_path, monkeypatch):
    opened = []
    real = builtins.open

    def counting(path, *args, **kwargs):
        opened.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(shadow, "open", counting, raising=False)
    runs = str(tmp_path / "runs")
    manifest = os.path.join(runs, "2026-10-01", "task_a1_analyst", "manifest.json")
    hub, _ = _lab(tmp_path, zones=[DataZone(path=runs, level="internal"), DataZone(path=manifest, level="restricted")])
    line = line_for(hub, "req_a", {})
    assert line["hash"]["workspaces"] == {"zone_excluded": 1} and line["hash"]["hashed"] == 0
    assert not [p for p in opened if p.endswith("manifest.json") and "task_a1" in p]
