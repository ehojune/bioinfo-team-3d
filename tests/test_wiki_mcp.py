"""labhq_wiki: search/read/status over the linked wiki, one contract-v1 line per call (#827, phase 2)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

from labhq.models import AgentSpec, Engine
from labhq.runner.daemon import Runner
from labhq.settings import Settings, WikiSettings
from tests.test_wiki_index import make_settings

HITS_KEYS = {"rel_path", "title", "heading", "line_start", "line_end", "sha256", "mtime", "origin", "access_level",
             "source_level", "verified_against_primary", "freshness", "snippet_sha256", "snippet_chars"}


@pytest.fixture
def wiki_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("LABHQ_WORKDIR", str(tmp_path / "work"))
    (tmp_path / "work" / "outputs").mkdir(parents=True)
    from labhq.tools import wiki_mcp as module

    settings, root = make_settings(tmp_path)
    settings.wiki = WikiSettings(enabled=True, root=str(root), external_model_allowed=True)
    monkeypatch.setattr(module, "S", settings)
    monkeypatch.setattr(module, "WORKDIR", (tmp_path / "work").resolve())
    return module, tmp_path / "work" / "outputs" / "wiki_queries.jsonl"


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def call(function, *args, **kwargs):
    return asyncio.run(function(*args, **kwargs))


def test_search_logs_one_contract_line_without_text(wiki_mcp):
    module, log = wiki_mcp
    result = json.loads(call(module.search, "KRAS"))
    assert result["hits"] and all(hit["access_level"] == "wiki_summary" for hit in result["hits"])
    [row] = lines(log)
    assert row["v"] == 1 and row["tool"] == "search" and "error" not in row
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", row["ts"])
    assert row["query_sha256"] == hashlib.sha256(b"KRAS").hexdigest()
    assert len(row["hits"]) == len(result["hits"])
    for hit, logged in zip(result["hits"], row["hits"]):
        assert set(logged) == HITS_KEYS
        assert logged["source_level"] == "tertiary" and logged["verified_against_primary"] is False
        assert logged["snippet_sha256"] == hashlib.sha256(hit["snippet"].encode()).hexdigest()
        assert logged["snippet_chars"] == len(hit["snippet"])
        assert not logged["rel_path"].startswith(("/", "..")) and ":" not in logged["rel_path"]
    text = log.read_text(encoding="utf-8")
    assert result["hits"][0]["snippet"] not in text


def test_read_returns_the_range_and_logs_the_whole_file_hash(wiki_mcp):
    module, log = wiki_mcp
    hit = json.loads(call(module.search, "KRAS"))["hits"][0]
    answer = json.loads(call(module.read, hit["rel_path"], 1, 3))["hit"]
    assert (answer["line_start"], answer["line_end"]) == (1, 3)
    [_, row] = lines(log)
    assert row["tool"] == "read" and row["query_sha256"] is None and len(row["hits"]) == 1
    assert row["hits"][0]["sha256"] == hit["sha256"]


@pytest.mark.parametrize("path", ["../outside.md", "/etc/passwd", "api-credentials.md", "nope.md"])
def test_read_refuses_what_the_index_does_not_hold(wiki_mcp, path):
    module, log = wiki_mcp
    with pytest.raises(Exception, match="not_found"):
        call(module.read, path)
    assert lines(log)[-1]["error"] == "not_found" and lines(log)[-1]["hits"] == []


@pytest.mark.parametrize("start,end", [(0, 3), (5, 2), (1, 400), (9999, None)])
def test_read_refuses_a_bad_range(wiki_mcp, start, end):
    module, log = wiki_mcp
    with pytest.raises(Exception, match="range_invalid"):
        call(module.read, "kras-g12c.md", start, end)
    assert lines(log)[-1]["error"] == "range_invalid"


def test_a_disabled_wiki_is_one_logged_code(wiki_mcp, monkeypatch):
    module, log = wiki_mcp
    monkeypatch.setattr(module, "S", module.S.model_copy(update={"wiki": WikiSettings()}))
    with pytest.raises(Exception, match="wiki_disabled"):
        call(module.search, "KRAS")
    assert lines(log)[-1]["error"] == "wiki_disabled"


def test_status_hides_the_root(wiki_mcp):
    module, _ = wiki_mcp
    call(module.search, "KRAS")
    info = json.loads(call(module.status))
    assert info["available"] and "root" not in info and str(module.S.wiki.root) not in json.dumps(info)


def daemon(settings: Settings) -> Runner:
    instance = object.__new__(Runner)
    instance.s = settings
    return instance


@pytest.mark.parametrize("allowed,attached", [(True, True), (False, False)])
def test_the_tool_attaches_only_with_the_opt_in(tmp_path, allowed, attached):
    settings, root = make_settings(tmp_path)
    settings.wiki = WikiSettings(enabled=True, root=str(root), external_model_allowed=allowed)
    agent = AgentSpec(id="lit_scout", name="x", role="x", engine=Engine.claude_code, builtin_mcp=["wiki"])
    names = [server.name for server in daemon(settings)._mcp_servers(agent, {}, allow_ask=False)]
    assert ("labhq_wiki" in names) is attached
    plain = AgentSpec(id="other", name="x", role="x", engine=Engine.claude_code, builtin_mcp=["approval"])
    assert "labhq_wiki" not in [s.name for s in daemon(settings)._mcp_servers(plain, {}, allow_ask=False)]


def test_the_three_staff_carry_the_tool():
    for name in ("lit_scout", "biologist", "sci_reviewer"):
        text = (Path(__file__).parent.parent / "agents" / "core" / f"{name}.yaml").read_text(encoding="utf-8")
        assert re.search(r"^builtin_mcp:.*\bwiki\b", text, re.M), name


def test_a_bad_root_is_a_fixed_code_without_the_path(wiki_mcp, monkeypatch):
    module, log = wiki_mcp
    bad = module.S.model_copy(update={"wiki": module.S.wiki.model_copy(update={"root": str(log.parent.parent / "gone")})})
    monkeypatch.setattr(module, "S", bad)
    with pytest.raises(Exception, match="root_problem") as caught:
        call(module.search, "KRAS")
    assert str(log.parent.parent) not in str(caught.value)
    assert lines(log)[-1]["error"] == "root_problem"


def test_status_is_not_logged(wiki_mcp):
    module, log = wiki_mcp
    call(module.status)
    assert not log.exists()


def test_a_bad_root_is_a_fixed_code_without_the_path(wiki_mcp, monkeypatch):
    module, log = wiki_mcp
    gone = log.parent.parent / "gone"
    bad = module.S.model_copy(update={"wiki": module.S.wiki.model_copy(update={"root": str(gone)})})
    monkeypatch.setattr(module, "S", bad)
    with pytest.raises(Exception, match="root_problem") as caught:
        call(module.search, "KRAS")
    assert str(gone) not in str(caught.value) and str(log.parent.parent) not in str(caught.value)
    assert lines(log)[-1]["error"] == "root_problem"


def test_status_is_not_logged(wiki_mcp):
    module, log = wiki_mcp
    call(module.status)
    assert not log.exists()
