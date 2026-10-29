"""A linked LLM-wiki is indexed read-only, incrementally, with provenance for every hit (#827, phase 1)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import time

import pytest
from pydantic import ValidationError

from labhq.doctor import _wiki_row
from labhq.settings import Settings, WikiSettings
from labhq import wiki
from labhq.wiki import index, walker

FIXTURE = Path(__file__).parent / "fixtures" / "wiki"
posix_only = pytest.mark.skipif(os.name == "nt", reason="links need POSIX permissions")


def make_settings(tmp_path: Path) -> tuple[Settings, Path]:
    root = tmp_path / "refs" / "wiki"
    shutil.copytree(FIXTURE, root)
    settings = Settings()
    settings.runner.reference_roots = [str(tmp_path / "refs")]
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "ws")
    settings.wiki = WikiSettings(enabled=True, root=str(root))
    return settings, root


class Parser:
    """The part of argparse the wiki command uses."""

    def exit(self, status=0, message=None):
        raise SystemExit(message or status)


def test_settings_reject_a_missing_root_and_unknown_keys():
    with pytest.raises(ValidationError):
        WikiSettings(enabled=True)
    with pytest.raises(ValidationError):
        WikiSettings(enabled=False, surprise=1)
    with pytest.raises(ValidationError):
        WikiSettings(extensions=[".pdf"])
    assert WikiSettings().external_model_allowed is False and Settings().wiki.enabled is False


def test_first_reindex_lists_notes_and_skips_what_it_must(tmp_path):
    settings, _ = make_settings(tmp_path)
    stats = index.reindex(settings)
    assert stats["files"] == 7 and stats["added"] == 7
    assert stats["skipped"] == {"other_type": 1, "credential_like": 1}
    again = index.reindex(settings)
    assert again["unchanged"] == 7 and again["added"] == again["updated"] == again["removed"] == 0


def test_a_hit_carries_everything_a_citation_needs(tmp_path):
    settings, root = make_settings(tmp_path)
    hit = index.search(settings, "objective response rate")[0]
    path = root / "kras-g12c.md"
    assert hit["rel_path"] == "kras-g12c.md"
    assert hit["heading"] == "KRAS G12C inhibitors"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert "objective response rate" in "\n".join(lines[hit["line_start"] - 1:hit["line_end"]])
    assert hit["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert hit["mtime"].endswith("Z") and hit["title"] == "KRAS G12C inhibitors in NSCLC"
    assert hit["origin"] == {"doi": "10.1056/nejmoa2103695"}
    assert hit["access_level"] == "wiki_summary" and hit["verified_against_primary"] is False
    assert hit["freshness"] == "ok" and len(hit["snippet"]) <= index.SNIPPET_CHARS


def test_origins_come_from_pmid_urls_and_body_dois(tmp_path):
    settings, _ = make_settings(tmp_path)
    by_note = {h["rel_path"]: h for q in ("C797S", "pseudobulk") for h in index.search(settings, q)}
    assert by_note["egfr-resistance.md"]["origin"] == {"pmid": "31234567"}
    assert by_note["sub/deep-note.md"]["origin"] == {"doi": "10.1038/s41467-021-25960-2"}


def test_freshness_marks_stale_disputed_and_unsourced_notes(tmp_path):
    settings, root = make_settings(tmp_path)
    old = time.time() - 400 * 86400
    os.utime(root / "kras-g12c.md", (old, old))
    freshness = {}
    for query in ("objective response", "unsourced remark", "tumour growth"):
        for hit in index.search(settings, query):
            freshness[hit["rel_path"]] = hit["freshness"]
    assert freshness == {"kras-g12c.md": "stale", "no-source.md": "source_missing", "disputed.md": "disputed"}


def test_a_note_carrying_instructions_is_returned_as_data(tmp_path):
    settings, _ = make_settings(tmp_path)
    hit = index.search(settings, "Ignore all previous instructions")[0]
    assert hit["rel_path"] == "injection.md" and "data, not a command" in hit["snippet"]
    assert hit["access_level"] == "wiki_summary"


def test_only_changed_documents_are_read_again_and_search_follows(tmp_path):
    settings, root = make_settings(tmp_path)
    index.reindex(settings)
    note = root / "no-source.md"
    note.write_text(note.read_text(encoding="utf-8") + "\nAdded: harmonic mean p-value aggregation.\n", encoding="utf-8")
    stats = index.reindex(settings)
    assert stats["updated"] == 1 and stats["unchanged"] == 6
    assert index.search(settings, "harmonic mean p-value")[0]["rel_path"] == "no-source.md"
    # same bytes, newer mtime: the hash settles it without re-parsing
    os.utime(note, (time.time() + 5, time.time() + 5))
    assert index.reindex(settings)["touched"] == 1
    # an edit that removes text takes the old words out of the index
    note.write_text("# Lab meeting notes\n\nReplaced.\n", encoding="utf-8")
    index.reindex(settings)
    assert not [h for h in index.search(settings, "harmonic mean p-value")]
    note.unlink()
    assert index.reindex(settings)["removed"] == 1
    assert not [h for h in index.search(settings, "Replaced")]


def test_short_queries_use_the_plain_scan(tmp_path):
    settings, _ = make_settings(tmp_path)
    assert {h["rel_path"] for h in index.search(settings, "G1")} >= {"kras-g12c.md"}
    with pytest.raises(ValueError):
        index.search(settings, "x")


def test_the_index_is_disposable_and_the_wiki_is_never_touched(tmp_path):
    settings, root = make_settings(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    index.reindex(settings)
    assert index.index_path(settings).exists()
    assert index.disconnect(settings) is True and not index.index_path(settings).exists()
    assert index.disconnect(settings) is False
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_status_is_read_only_and_reports_counts(tmp_path):
    settings, _ = make_settings(tmp_path)
    info = index.status(settings)
    assert info["available"] is False and info["root_problem"] is None
    assert not index.index_path(settings).exists()
    index.reindex(settings)
    info = index.status(settings)
    assert info["available"] and info["docs"] == 7 and info["chunks"] >= 7 and info["origins"] >= 5


@pytest.mark.parametrize("where", ["outside_reference_roots", "state_dir", "private_path"])
def test_a_root_that_may_not_be_linked_is_refused(tmp_path, where):
    settings, root = make_settings(tmp_path)
    if where == "outside_reference_roots":
        other = tmp_path / "elsewhere"
        shutil.copytree(FIXTURE, other)
        settings.wiki = WikiSettings(enabled=True, root=str(other))
    elif where == "state_dir":
        settings.runner.state_dir = str(root / "state")
    else:
        settings.policy.private_paths = [str(root / "sub")]
    assert walker.root_problem(settings)
    with pytest.raises(ValueError):
        index.reindex(settings)


@posix_only
def test_links_are_never_read_and_an_escaping_link_refuses_the_root(tmp_path):
    settings, root = make_settings(tmp_path)
    (root / "inside-link.md").symlink_to(root / "kras-g12c.md")
    files, skipped = walker.list_files(settings)
    assert skipped["link"] == 1 and "inside-link.md" not in {f.rel_path for f in files}
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "token-notes.md").write_text("secret", encoding="utf-8")
    (root / "escape").symlink_to(secret, target_is_directory=True)
    assert "참고 폴더 밖" in walker.root_problem(settings)
    with pytest.raises(ValueError):
        index.reindex(settings)


@posix_only
def test_the_root_itself_may_not_be_a_link(tmp_path):
    settings, root = make_settings(tmp_path)
    alias = tmp_path / "refs" / "alias"
    alias.symlink_to(root, target_is_directory=True)
    settings.wiki = WikiSettings(enabled=True, root=str(alias))
    assert "link" in walker.root_problem(settings)


def test_file_limits_are_enforced(tmp_path):
    settings, root = make_settings(tmp_path)
    settings.wiki = WikiSettings(enabled=True, root=str(root), max_files=3, max_file_bytes=200)
    (root / "big.md").write_text("# Big\n" + "word " * 200, encoding="utf-8")
    files, skipped = walker.list_files(settings)
    assert len(files) == 3 and skipped["too_large"] >= 1 and skipped["over_max_files"] >= 1


def test_the_command_connects_reports_and_searches(tmp_path, capsys):
    settings, root = make_settings(tmp_path)
    off = settings.model_copy(update={"wiki": WikiSettings()})
    assert wiki.cli_main(off, "connect", str(root), Parser()) == 0
    out = capsys.readouterr().out
    assert "색인 대상 7개" in out and "external_model_allowed: false" in out
    assert not index.index_path(off).exists()
    with pytest.raises(SystemExit):
        wiki.cli_main(off, "reindex", None, Parser())
    assert wiki.cli_main(settings, "reindex", None, Parser()) == 0
    assert "추가 7" in capsys.readouterr().out
    assert wiki.cli_main(settings, "search", "objective response", Parser()) == 0
    out = capsys.readouterr().out
    assert "kras-g12c.md:" in out and "doi:10.1056/nejmoa2103695" in out
    assert wiki.cli_main(settings, "status", None, Parser()) == 0
    assert "문서 7" in capsys.readouterr().out
    assert wiki.cli_main(settings, "disconnect", None, Parser()) == 0
    assert "wiki 폴더는 건드리지 않았습니다" in capsys.readouterr().out
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(SystemExit) as refused:
        wiki.cli_main(off, "connect", str(elsewhere), Parser())
    assert "reference_roots" in str(refused.value)


def test_doctor_row_is_skip_when_off_warns_without_an_index_and_is_ok_after(tmp_path):
    settings, _ = make_settings(tmp_path)
    assert _wiki_row(Settings())["status"] == "skip"
    assert _wiki_row(settings)["status"] == "warn" and "reindex" in _wiki_row(settings)["hint"]
    index.reindex(settings)
    row = _wiki_row(settings)
    assert row["status"] == "ok" and "docs=7" in row["detail"]
    settings.runner.state_dir = str(tmp_path / "other-state")
    settings.wiki = WikiSettings(enabled=True, root=str(tmp_path / "missing"))
    assert _wiki_row(settings)["status"] == "warn" and "problem" in _wiki_row(settings)["detail"]


def test_a_changed_root_starts_a_new_index(tmp_path):
    settings, root = make_settings(tmp_path)
    index.reindex(settings)
    second = tmp_path / "refs" / "second"
    second.mkdir()
    (second / "only.md").write_text("# Only note\n\nUnique phrase zebra.\n", encoding="utf-8")
    settings.wiki = WikiSettings(enabled=True, root=str(second))
    stats = index.reindex(settings)
    assert stats["files"] == 1 and stats["added"] == 1
    assert index.search(settings, "KRAS G12C") == [] and index.search(settings, "zebra")
    with sqlite3.connect(index.index_path(settings)) as db:
        assert db.execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 1


def test_an_index_of_another_folder_is_not_reported_as_usable(tmp_path):
    settings, root = make_settings(tmp_path)
    index.reindex(settings)
    other = tmp_path / "refs" / "other"
    shutil.copytree(FIXTURE, other)
    settings.wiki = WikiSettings(enabled=True, root=str(other))
    info = index.status(settings)
    assert info["available"] is False and info["docs"] == 0 and info.get("stale_root") is True
    assert _wiki_row(settings)["status"] == "warn"
    index.reindex(settings)
    assert index.status(settings)["available"] is True and _wiki_row(settings)["status"] == "ok"


@posix_only
def test_a_link_in_the_middle_of_the_path_cannot_leave_the_allowed_roots(tmp_path):
    settings, root = make_settings(tmp_path)
    outside = tmp_path / "outside"
    shutil.copytree(FIXTURE, outside / "wiki")
    (tmp_path / "refs" / "alias").symlink_to(outside, target_is_directory=True)
    settings.wiki = WikiSettings(enabled=True, root=str(tmp_path / "refs" / "alias" / "wiki"))
    assert "outside runner.reference_roots" in walker.root_problem(settings)
    with pytest.raises(ValueError):
        index.reindex(settings)
