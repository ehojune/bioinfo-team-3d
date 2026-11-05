import importlib.util
import hashlib
import shutil
from pathlib import Path

import yaml
import pytest

spec = importlib.util.spec_from_file_location("notes_index", Path(__file__).resolve().parents[1] / "scripts" / "notes_index.py")
ni = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ni)


def write_entry(root, name, pr, rows):
    path = root / "patch_notes" / "entries" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"pr": pr, "rows": rows}, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_patch_notes_are_newest_first_with_stable_ties(tmp_path):
    write_entry(tmp_path, "a.yaml", 7, [
        {"sha": "aaaaaaa", "at": "2026-10-02 10:00", "text": "첫째"},
        {"sha": "ccccccc", "at": "2026-10-01 09:00", "text": "셋째"},
    ])
    write_entry(tmp_path, "b.yaml", None, [
        {"sha": "bbbbbbb", "at": "2026-10-02 10:00", "text": "둘째"},
    ])
    rendered = ni.render_patch_notes(tmp_path)
    assert rendered.index("aaaaaaa") < rendered.index("bbbbbbb") < rendered.index("ccccccc")
    assert "/pull/7/commits/aaaaaaa" in rendered
    assert "/commit/bbbbbbb" in rendered
    assert rendered.count("## 2026-10-02") == 1


def test_legacy_rows_can_keep_each_original_pr(tmp_path):
    path = tmp_path / "patch_notes" / "entries" / "_legacy.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"pr": None, "rows": [
        {"sha": "aaaaaaa", "at": "2026-10-01 10:00", "text": "one", "pr": 1},
        {"sha": "bbbbbbb", "at": "2026-10-01 09:00", "text": "two", "pr": None},
    ]}, sort_keys=False), encoding="utf-8")
    rendered = ni.render_patch_notes(tmp_path)
    assert "/pull/1/commits/aaaaaaa" in rendered
    assert "/commit/bbbbbbb" in rendered


def test_status_files_are_newest_first_and_legacy_is_last(tmp_path):
    status = tmp_path / "docs" / "status"
    status.mkdir(parents=True)
    (status / "2026-10-02-1000-b.md").write_text("## newer\n", encoding="utf-8")
    (status / "2026-10-01-1000-a.md").write_text("## older\n", encoding="utf-8")
    (status / "_legacy.md").write_text("## legacy\n", encoding="utf-8")
    rendered = ni.render_status(tmp_path)
    assert rendered.index("## newer") < rendered.index("## older") < rendered.index("## legacy")


def test_legacy_migration_recreates_the_old_indexes_byte_for_byte(tmp_path):
    root = Path(__file__).resolve().parents[1]
    patch_dir = tmp_path / "patch_notes" / "entries"
    status_dir = tmp_path / "docs" / "status"
    patch_dir.mkdir(parents=True)
    status_dir.mkdir(parents=True)
    # Easy explanations are an added presentation layer, not edits to the old record.
    legacy = yaml.safe_load((root / "patch_notes" / "entries" / "_legacy.yaml").read_text(encoding="utf-8"))
    for row in legacy["rows"]:
        row.pop("summary", None)
    (patch_dir / "_legacy.yaml").write_text(yaml.safe_dump(legacy, allow_unicode=True, sort_keys=False), encoding="utf-8")
    shutil.copyfile(root / "docs" / "status" / "_legacy.md", status_dir / "_legacy.md")
    notice = ni.GENERATED_NOTICE + "\n\n"
    patch = ni.render_patch_notes(tmp_path).replace(notice, "", 1).encode()
    status = ni.render_status(tmp_path).replace(notice, "", 1).encode()
    assert hashlib.sha256(patch).hexdigest() == "f354d5cad91494648ae1650a376d6b068601946db8ec26edc0a3137296521faa"
    assert hashlib.sha256(status).hexdigest() == "e61e0bc0f0385d76f967589ef4d48acd9736533e8502c85d317bed20f48efff1"


def test_an_entry_in_a_subfolder_fails_instead_of_dropping_its_rows(tmp_path):
    write_entry(tmp_path, "feature/foo.yaml", 7, [{"sha": "abc1234", "at": "2026-10-02 10:00", "text": "x"}])
    try:
        ni.patch_rows(tmp_path)
    except ValueError as exc:
        assert "바로 아래" in str(exc)
    else:
        raise AssertionError("a nested entries file must not be skipped silently")


def test_validate_rejects_a_row_the_generator_would_refuse(tmp_path):
    write_entry(tmp_path, "topic.yaml", 7, [{"sha": "abc1234", "text": "missing at"}])
    try:
        ni.render_patch_notes(tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("a row without `at` must fail before the merge, not on main")


def test_easy_explanation_precedes_the_unchanged_technical_record(tmp_path):
    technical = "재현 receipt의 ctx=Load 판정 (#774)"
    write_entry(tmp_path, "easy.yaml", 774, [{
        "sha": "abc1234", "at": "2026-10-20 12:34", "text": technical,
        "summary": "  쓰기 대상을 입력 파일과 혼동하지 않도록 고쳤습니다.  ",
    }])
    rendered = ni.render_patch_notes(tmp_path)
    assert f"**쓰기 대상을 입력 파일과 혼동하지 않도록 고쳤습니다.**<br>{technical}" in rendered
    assert "| 12:34 | [`abc1234`](https://github.com/ehojune/bioinfo-team-3d/pull/774/commits/abc1234) |" in rendered


def test_easy_explanation_cannot_break_the_markdown_table(tmp_path):
    write_entry(tmp_path, "easy.yaml", None, [{
        "sha": "abc1234", "at": "2026-10-20 12:34", "text": "기술 기록",
        "summary": "A | <B> **C**",
    }])
    rendered = ni.render_patch_notes(tmp_path)
    assert r"**A \| &lt;B&gt; \*\*C\*\***<br>기술 기록" in rendered


@pytest.mark.parametrize("summary", [None, "", "   ", "첫줄\n둘째줄", "첫줄\r둘째줄", 7, ["설명"]])
def test_invalid_easy_explanations_are_rejected_before_main_generation(tmp_path, summary):
    write_entry(tmp_path, "easy.yaml", 774, [{
        "sha": "abc1234", "at": "2026-10-20 12:34", "text": "기술 기록", "summary": summary,
    }])
    with pytest.raises(ValueError, match="summary"):
        ni.render_patch_notes(tmp_path)
