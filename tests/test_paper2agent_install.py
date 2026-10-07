"""Paper2Agent replacement preserves both installed copies when preparation/publication fails."""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from labhq.recruit import paper2agent


SOURCE = "https://example.org/Paper2Agent.git"


def _source_skill(cache_dir):
    repo = cache_dir / "Paper2Agent"
    (repo / ".git").mkdir(parents=True)
    skill = repo / "skills" / "paper2agent"
    (skill / "assets").mkdir(parents=True)
    (skill / "SKILL.md").write_text("new skill\n", encoding="utf-8")
    (skill / "assets" / "reference.txt").write_text("new asset\n", encoding="utf-8")
    return skill


@pytest.fixture
def install_case(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    skill = _source_skill(cache_dir)
    destinations = {engine: tmp_path / engine / "skills" / "paper2agent"
                    for engine in ("claude_code", "codex")}
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(paper2agent.subprocess, "run", run)
    monkeypatch.setattr(paper2agent, "skill_path", lambda engine: destinations[engine])
    return SimpleNamespace(cache_dir=cache_dir, skill=skill, destinations=destinations, run=run)


def _old_skills(case):
    for engine, dst in case.destinations.items():
        dst.mkdir(parents=True)
        (dst / "SKILL.md").write_text(f"old {engine}\n", encoding="utf-8")
        (dst / "old-only.txt").write_text(f"old asset {engine}\n", encoding="utf-8")


def _assert_old_skills(case):
    for engine, dst in case.destinations.items():
        assert (dst / "SKILL.md").read_text(encoding="utf-8") == f"old {engine}\n"
        assert (dst / "old-only.txt").read_text(encoding="utf-8") == f"old asset {engine}\n"
        assert sorted(p.name for p in dst.iterdir()) == ["SKILL.md", "old-only.txt"]


def _assert_no_staging(case):
    assert not [stage for dst in case.destinations.values() for stage in dst.parent.glob(".paper2agent-*")]


@pytest.mark.parametrize("existing", [False, True])
def test_successful_install_prepares_and_publishes_both_engine_copies(install_case, existing):
    case = install_case
    if existing:
        _old_skills(case)
    assert paper2agent.install_skill(SOURCE, case.cache_dir) == list(case.destinations.values())
    case.run.assert_called_once_with(
        ["git", "-C", str(case.cache_dir / "Paper2Agent"), "pull", "--ff-only"], check=True)
    for dst in case.destinations.values():
        assert (dst / "SKILL.md").read_text(encoding="utf-8") == "new skill\n"
        assert (dst / "assets" / "reference.txt").read_text(encoding="utf-8") == "new asset\n"
        assert not (dst / "old-only.txt").exists()
    _assert_no_staging(case)


def test_fresh_clone_command_is_mocked_and_its_source_is_validated(install_case):
    case = install_case
    shutil.rmtree(case.cache_dir / "Paper2Agent")
    case.run.side_effect = lambda *_args, **_kwargs: _source_skill(case.cache_dir)
    assert paper2agent.install_skill(SOURCE, case.cache_dir) == list(case.destinations.values())
    case.run.assert_called_once_with(
        ["git", "clone", "--depth", "1", SOURCE, str(case.cache_dir / "Paper2Agent")], check=True)
    _assert_no_staging(case)


@pytest.mark.parametrize("missing", ["directory", "SKILL.md"])
def test_missing_source_never_changes_existing_installs(install_case, monkeypatch, missing):
    case = install_case
    _old_skills(case)
    if missing == "directory":
        shutil.rmtree(case.skill)
    else:
        (case.skill / "SKILL.md").unlink()
    copy = Mock()
    monkeypatch.setattr(paper2agent.shutil, "copytree", copy)
    with pytest.raises(FileNotFoundError, match="missing SKILL.md"):
        paper2agent.install_skill(SOURCE, case.cache_dir)
    copy.assert_not_called()
    _assert_old_skills(case)
    _assert_no_staging(case)


def test_git_failure_never_changes_existing_installs(install_case):
    case = install_case
    _old_skills(case)
    case.run.side_effect = subprocess.CalledProcessError(1, "git")
    with pytest.raises(subprocess.CalledProcessError):
        paper2agent.install_skill(SOURCE, case.cache_dir)
    _assert_old_skills(case)
    _assert_no_staging(case)


@pytest.mark.parametrize("failed_copy", [1, 2])
def test_partial_first_or_second_staging_copy_preserves_both_installs(install_case, monkeypatch, failed_copy):
    case = install_case
    _old_skills(case)
    copytree = shutil.copytree
    copies = 0

    def fail_copy(src, dst, *args, **kwargs):
        nonlocal copies
        if Path(src) == case.skill:
            copies += 1
            if copies == failed_copy:
                Path(dst).mkdir()
                (Path(dst) / "partial.txt").write_text("partial", encoding="utf-8")
                raise OSError("injected staging copy failure")
        return copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(paper2agent.shutil, "copytree", fail_copy)
    with pytest.raises(OSError, match="injected staging copy failure"):
        paper2agent.install_skill(SOURCE, case.cache_dir)
    assert copies == failed_copy
    _assert_old_skills(case)
    _assert_no_staging(case)


def test_staged_skill_validation_preserves_both_installs(install_case, monkeypatch):
    case = install_case
    _old_skills(case)
    copytree = shutil.copytree

    def lose_skill(src, dst, *args, **kwargs):
        result = copytree(src, dst, *args, **kwargs)
        if Path(src) == case.skill:
            (Path(dst) / "SKILL.md").unlink()
        return result

    monkeypatch.setattr(paper2agent.shutil, "copytree", lose_skill)
    with pytest.raises(FileNotFoundError, match="Staged Paper2Agent"):
        paper2agent.install_skill(SOURCE, case.cache_dir)
    _assert_old_skills(case)
    _assert_no_staging(case)


@pytest.mark.parametrize("existing", [False, True])
def test_second_publish_failure_restores_both_previous_states(install_case, monkeypatch, existing):
    case = install_case
    if existing:
        _old_skills(case)
    rename = Path.rename

    def fail_publish(path, target):
        if path.name == "new" and Path(target) == case.destinations["codex"]:
            raise OSError("injected second publication failure")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_publish)
    with pytest.raises(OSError, match="injected second publication failure"):
        paper2agent.install_skill(SOURCE, case.cache_dir)
    if existing:
        _assert_old_skills(case)
    else:
        assert not any(dst.exists() for dst in case.destinations.values())
    _assert_no_staging(case)


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_failed_rollback_keeps_the_old_backup_and_reports_its_path(install_case, monkeypatch, caplog,
                                                                cleanup_failure):
    case = install_case
    _old_skills(case)
    rename = Path.rename

    def fail_publish_and_restore(path, target):
        if path.name == "new" and Path(target) == case.destinations["codex"]:
            raise OSError("injected second publication failure")
        if path.name == "backup" and Path(target) == case.destinations["claude_code"]:
            raise OSError("injected rollback failure")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_publish_and_restore)
    if cleanup_failure:
        rmtree = shutil.rmtree

        def fail_cleanup(path, *args, **kwargs):
            path = Path(path)
            if path.parent == case.destinations["codex"].parent and path.name.startswith(".paper2agent-"):
                raise OSError("injected staging cleanup failure")
            return rmtree(path, *args, **kwargs)

        monkeypatch.setattr(paper2agent.shutil, "rmtree", fail_cleanup)
    with pytest.raises(RuntimeError, match="manual recovery") as caught:
        paper2agent.install_skill(SOURCE, case.cache_dir)
    assert isinstance(caught.value.__cause__, OSError)
    stages = list(case.destinations["claude_code"].parent.glob(".paper2agent-*"))
    assert len(stages) == 1
    backup = stages[0] / "backup"
    assert str(backup) in str(caught.value)
    assert (backup / "SKILL.md").read_text(encoding="utf-8") == "old claude_code\n"
    assert (backup / "old-only.txt").read_text(encoding="utf-8") == "old asset claude_code\n"
    assert (case.destinations["codex"] / "SKILL.md").read_text(encoding="utf-8") == "old codex\n"
    codex_stages = list(case.destinations["codex"].parent.glob(".paper2agent-*"))
    if cleanup_failure:
        assert len(codex_stages) == 1
        assert str(codex_stages[0]) in caplog.text
        assert "injected staging cleanup failure" in caplog.text
    else:
        assert not codex_stages


def test_successful_publication_returns_valid_installs_when_staging_cleanup_fails(install_case, monkeypatch, caplog):
    case = install_case
    _old_skills(case)
    monkeypatch.setattr(paper2agent.shutil, "rmtree", Mock(side_effect=OSError("injected cleanup failure")))
    assert paper2agent.install_skill(SOURCE, case.cache_dir) == list(case.destinations.values())
    for dst in case.destinations.values():
        assert (dst / "SKILL.md").read_text(encoding="utf-8") == "new skill\n"
        assert (dst / "assets" / "reference.txt").read_text(encoding="utf-8") == "new asset\n"
        stages = list(dst.parent.glob(".paper2agent-*"))
        assert len(stages) == 1
        assert str(stages[0]) in caplog.text
