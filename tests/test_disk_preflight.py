"""Disk checks and publication regressions use fake volumes, never operator settings."""
from collections import namedtuple
from pathlib import Path

import pytest
from pydantic import ValidationError

from labhq import disk_space, doctor, init_wizard, private_paths
from labhq.portfolio import EditionCheckError, build_portfolio_files, scan_publish_files
from labhq.settings import RunnerSettings, Settings

Space = namedtuple("Space", "total used free")


def space(total, free):
    return Space(total * disk_space.GIB, (total - free) * disk_space.GIB, free * disk_space.GIB)


def test_future_directory_uses_existing_ancestor_without_creation(tmp_path, monkeypatch):
    target = tmp_path / "future" / "work"
    seen = []
    monkeypatch.setattr(disk_space.shutil, "disk_usage", lambda p: seen.append(p) or space(100, 50))
    assert disk_space.usage(target).free == 50 * disk_space.GIB
    assert seen == [tmp_path]
    assert not target.parent.exists()


@pytest.mark.parametrize("free,gb,percent,expected", [(9, 10, 0, True), (10, 10, 5, False),
                                                       (20, 0, 25, True), (0, 0, 0, False)])
def test_thresholds(free, gb, percent, expected):
    assert disk_space.low(space(100, free), gb, percent) is expected


@pytest.mark.parametrize("key,value", [("disk_warning_free_gb", -1), ("disk_warning_free_percent", 101),
                                       ("disk_warning_free_gb", float("nan"))])
def test_invalid_thresholds_rejected(key, value):
    with pytest.raises(ValidationError):
        RunnerSettings(**{key: value})


def test_doctor_checks_each_location_read_only(tmp_path, monkeypatch):
    settings = Settings(runner={"workspace_root": str(tmp_path / "arbitrary")},
                        engines={"codex": {"bin": "codex", "env": {"CODEX_HOME": "staff-codex"}},
                                 "claude_code": {"bin": "claude", "env": {"CLAUDE_CONFIG_DIR": str(tmp_path / "staff-claude")}}})
    seen = []
    monkeypatch.setattr(doctor.tempfile, "gettempdir", lambda: str(tmp_path / "temporary"))
    def probe(path):
        seen.append(path)
        return space(100, 1 if path.name == "temporary" else 50)
    monkeypatch.setattr(disk_space, "usage", probe)
    rows = doctor._disk_rows(settings)
    assert len(rows) == 4
    assert [r["status"] for r in rows] == ["ok", "warn", "ok", "ok"]
    assert seen == [tmp_path / "arbitrary", tmp_path / "temporary",
                    tmp_path / "arbitrary/staff-codex", tmp_path / "staff-claude"]
    assert not list(tmp_path.iterdir())
    assert str(tmp_path) not in str(rows)
    settings.runner.disk_warning_free_gb = settings.runner.disk_warning_free_percent = 0
    assert all(r["status"] == "ok" for r in doctor._disk_rows(settings))


def test_doctor_unavailable_volume_warns_without_path_or_exception(tmp_path, monkeypatch):
    settings = Settings(runner={"workspace_root": str(tmp_path / "missing")})
    def fail(path):
        raise OSError("private volume detail")
    monkeypatch.setattr(disk_space, "usage", fail)
    rows = doctor._disk_rows(settings)
    assert all(row["status"] == "warn" for row in rows)
    assert "private volume detail" not in str(rows)


def test_init_suggests_data_volume_but_yes_keeps_default(tmp_path, monkeypatch, capsys):
    drive = tmp_path / "data-volume"
    monkeypatch.setattr(disk_space, "data_drives", lambda: [drive])
    monkeypatch.setattr(disk_space, "usage", lambda p: space(1000, 500) if p == drive else space(100, 2))
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("--yes must not prompt"))
    assert init_wizard._workspace_root("~/.labhq/runs", True, "trial") == "~/.labhq/runs"
    assert f"{drive.as_posix()}/.labhq/trial/runs" in capsys.readouterr().out
    assert not drive.exists()


def test_init_accepts_explicit_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_space, "usage", lambda p: space(1000, 500))
    monkeypatch.setattr("builtins.input", lambda prompt: str(tmp_path / "chosen"))
    assert init_wizard._workspace_root("~/.labhq/runs", False, None) == str(tmp_path / "chosen")
    assert not (tmp_path / "chosen").exists()


@pytest.mark.parametrize("root,text", [
    ("/analysis/odd-name", "/analysis/odd-name/task/file.txt"),
    ("R:/odd-name", "r:\\ODD-NAME\\task\\file.txt"),
    ("R:/odd-name", "/mnt/r/odd-name/task/file.txt"),
    ("R:/odd-name", "/r/odd-name/task/file.txt"),
    ("//fileserver/odd-name", "\\\\fileserver\\odd-name\\task\\file.txt"),
    ("custom-work", "custom-work/task/file.txt"),
])
@pytest.mark.parametrize("name", ["README.md", "run/code.py", "figure.svg"])
def test_arbitrary_workspace_is_private_even_without_data_zone(tmp_path, root, text, name):
    settings = Settings(runner={"workspace_root": root}, config_base=str(tmp_path))
    assert private_paths.mentions_workspace_path(text, settings)
    body = text.encode() if name.endswith(".svg") else text
    assert {"kind": "local_path", "path": name} in scan_publish_files({name: body}, settings=settings)


def test_workspace_prefix_and_relative_output_are_not_private(tmp_path):
    settings = Settings(runner={"workspace_root": "/analysis/odd-name"}, config_base=str(tmp_path))
    for text in ("/analysis/odd-name-other/file.txt", "outputs/summary.tsv", "https://example.org/odd-name"):
        assert not private_paths.mentions_workspace_path(text, settings)
        assert not scan_publish_files({"README.md": text}, settings=settings)


@pytest.mark.parametrize("root", ["runs", "./runs"])
@pytest.mark.parametrize("text", [
    "The workflow runs successfully.",
    "https://example.org/runs",
    "https://example.org/runs/results",
    "outputs/runs/summary.tsv",
])
def test_relative_workspace_name_is_not_a_word_or_url(tmp_path, root, text):
    settings = Settings(runner={"workspace_root": root}, config_base=str(tmp_path))
    assert not private_paths.mentions_workspace_path(text, settings)
    assert not scan_publish_files({"README.md": text}, settings=settings)


@pytest.mark.parametrize("root", ["runs", "./runs"])
@pytest.mark.parametrize("spelling", ["relative", "dot", "backslash", "absolute", "resolved"])
def test_relative_workspace_paths_still_block_publication(tmp_path, root, spelling):
    settings = Settings(runner={"workspace_root": root}, config_base=str(tmp_path))
    text = {"relative": "runs/task/input.txt", "dot": "./runs/task/input.txt",
            "backslash": r"runs\task\input.txt",
            "absolute": str(settings.path(root) / "task/input.txt"),
            "resolved": str(settings.path(root).resolve() / "task/input.txt")}[spelling]
    assert private_paths.mentions_workspace_path(text, settings)
    assert {"kind": "local_path", "path": "README.md"} in scan_publish_files(
        {"README.md": text}, settings=settings)


def test_edition_never_publishes_custom_workspace_after_cleaning(tmp_path):
    settings = Settings(runner={"workspace_root": "/analysis/odd-name"}, config_base=str(tmp_path))
    request = {"id": "req_example", "text": "Public result", "report": "Result: /analysis/odd-name/task/input.txt"}
    try:
        files = build_portfolio_files(request, settings, {}, {})
    except EditionCheckError as exc:
        assert "local_path" in str(exc)
    else:
        assert all("/analysis/odd-name" not in body for body in files.values() if isinstance(body, str))
