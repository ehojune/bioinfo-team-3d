"""setup-paper2agent patches Paper2Agent's Windows path defect and records it in provenance (#662)."""

import json
from pathlib import PureWindowsPath
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from labhq.recruit import paper2agent as p2a

SOURCE = "https://example.org/Paper2Agent.git"
SCRIPT = "paper2skill/scripts/reading_package.py"
BUGGY = '''\
record = {'evidence': str(evidence.relative_to(work))}
files = {str(p.relative_to(stage)): 1 for p in items}
rel = str(path.relative_to(root))
found = {str(p.relative_to(output)) for p in outputs}
'''


def _case(tmp_path, monkeypatch, script):
    repo = tmp_path / "cache" / "Paper2Agent"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {SOURCE}\n', encoding="utf-8")
    skill = repo / "skills" / "paper2agent"
    (skill / "paper2skill" / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("skill\n", encoding="utf-8")
    if script is not None:
        (skill / SCRIPT).write_text(script, encoding="utf-8")
    dst = {e: tmp_path / e / "skills" / "paper2agent" for e in ("claude_code", "codex")}
    monkeypatch.setattr(p2a.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=0)))
    monkeypatch.setattr(p2a, "skill_path", lambda engine: dst[engine])
    monkeypatch.setattr(p2a, "head_commit", lambda repo: "a" * 40)
    return SimpleNamespace(cache=tmp_path / "cache", dst=dst, skill=skill)


def _install(case):
    return p2a.install_skill(SOURCE, case.cache, destinations=list(case.dst.values()))


def _marker(path):
    return json.loads((path / p2a.SOURCE_MARKER).read_text(encoding="utf-8"))


def test_install_patches_all_four_sites_and_records_provenance(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, BUGGY)
    _install(case)
    for dst in case.dst.values():
        text = (dst / SCRIPT).read_text(encoding="utf-8")
        assert "str(" not in text and text.count(".relative_to(") == 4 and text.count(".as_posix()") == 4
        assert _marker(dst)["patches"] == [{
            "id": "windows-relative-to-posix", "file": SCRIPT,
            "upstream_commit": "8c2d059165ef8cdcb70dbea76655b9c2b55b38e6", "result": "applied"}]
    assert (case.skill / SCRIPT).read_text(encoding="utf-8") == BUGGY  # the cached upstream checkout is untouched
    assert p2a.skill_patched(case.dst["claude_code"])


def test_patched_expressions_match_on_windows_paths_where_the_original_did_not(tmp_path, monkeypatch):
    work, stage = PureWindowsPath("C:/b/work"), PureWindowsPath("C:/b/stage")
    child = stage / "sub" / "a.txt"
    before = str(child.relative_to(stage))
    after = child.relative_to(stage).as_posix()
    manifest_key = "sub/a.txt"
    assert before != manifest_key and after == manifest_key
    case = _case(tmp_path, monkeypatch, BUGGY)
    _install(case)
    text = (case.dst["codex"] / SCRIPT).read_text(encoding="utf-8")
    env = {"evidence": work / "e" / "x.md", "work": work, "p": child, "stage": stage, "path": child, "root": stage,
           "items": [child], "outputs": [child], "output": stage}
    for line in text.splitlines():
        value = eval(line.split("=", 1)[1].strip(), dict(env))  # one namespace: comprehensions see it on 3.10 too
        keys = list(value) if isinstance(value, (dict, set)) else [value]
        assert keys and all("\\" not in str(key) for key in keys), line


def test_reinstall_of_a_patched_copy_changes_nothing(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, BUGGY)
    _install(case)
    first = (case.dst["claude_code"] / SCRIPT).read_bytes()
    patched = (case.skill / SCRIPT)
    patched.write_bytes(first)  # upstream now ships the fix (or a patched cache)
    _install(case)
    assert (case.dst["claude_code"] / SCRIPT).read_bytes() == first
    assert _marker(case.dst["claude_code"])["patches"][0]["result"] == "already_patched"


def test_changed_upstream_fails_and_keeps_existing_installs(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, BUGGY)
    _install(case)
    before = {e: (d / SCRIPT).read_bytes() for e, d in case.dst.items()}
    (case.skill / SCRIPT).write_text(BUGGY.replace("str(path.relative_to(root))", "rewritten(path, root)"),
                                     encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not match"):
        _install(case)
    assert {e: (d / SCRIPT).read_bytes() for e, d in case.dst.items()} == before


def test_half_patched_script_is_a_mismatch_not_a_silent_pass(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, BUGGY.replace("str(path.relative_to(root))", "path.relative_to(root).as_posix()"))
    with pytest.raises(RuntimeError, match="does not match"):
        _install(case)


def test_absent_script_is_recorded_not_failed_and_unpatched_copy_is_detected(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, None)
    _install(case)
    assert _marker(case.dst["codex"])["patches"][0]["result"] == "file_absent"
    old = tmp_path / "old"
    (old / "paper2skill" / "scripts").mkdir(parents=True)
    (old / SCRIPT).write_text(BUGGY, encoding="utf-8")
    assert not p2a.skill_patched(old)  # doctor treats an earlier, unpatched install as not ready
    half = tmp_path / "half"
    (half / "paper2skill" / "scripts").mkdir(parents=True)
    (half / SCRIPT).write_text(BUGGY.replace("str(path.relative_to(root))", "path.relative_to(root).as_posix()"),
                               encoding="utf-8")
    assert not p2a.skill_patched(half)  # a half-patched install is not ready either; --fix reinstalls or reports it
