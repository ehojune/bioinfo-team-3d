"""The precedent search's stored texts reach the research steps as read-only inputs (#951, attempts 13 and 14: the
scout stored the papers, no step could open them, and the report said "0 sources read")."""

import hashlib
import json
from pathlib import Path

import pytest

from labhq.models import Engine, Task
from labhq.orchestrator.cso import Orchestrator
from labhq.research import precedent_texts
from tests import test_research_continue as cont
from tests.test_private_paths import _capture_runner, _runner_settings
from tests.test_tool_card_explain import _system_commands

TEXT = "# Olomorasib\n\nORR 65% (n=100); PFS 7.1 months [95% CI 5.5-9.0].\n"


def _stored(root: Path, paper_id="PMID:38587856", text=TEXT, receipt_extra=None):
    """A text file and its tool receipt as the literature scout leaves them in its own workdir."""
    folder = root / "2026-10-25" / "task_precedent_lit_scout" / ".tmp" / "literature"
    folder.mkdir(parents=True)
    path = folder / "PMID_38587856.md"
    path.write_bytes(text.encode())  # bytes: text mode would turn each newline into CRLF on Windows
    digest = hashlib.sha256(text.encode()).hexdigest()
    receipt = {"coverage_version": 2, "paper_id": paper_id, "source": "fulltext tool", "access_level": "full_text",
               "extraction": "complete", "text_file": str(path), "text_sha256": digest, **(receipt_extra or {})}
    Path(str(path) + ".receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    return path, digest, receipt


def _record(receipt):
    return {"status": "ok", "papers": [{"title": "Olomorasib in KRAS G12C", "citations": ["PMID:38587856"],
                                        "fulltext_receipt": receipt}], "required": [], "recommended": [],
            "limitations": []}


def test_only_receipts_with_a_path_and_a_hash_become_inputs_and_names_are_safe():
    good = {"paper_id": "PMID:1", "text_file": "C:/x/a.md", "text_sha256": "a" * 64, "access_level": "full_text"}
    record = {"status": "ok", "papers": [
        {"title": "A", "fulltext_receipt": good},
        {"title": "A again", "fulltext_receipt": dict(good, paper_id="PMID:1b")},  # same bytes: once
        {"title": "no hash", "fulltext_receipt": {"text_file": "C:/x/b.md"}},
        {"title": "no receipt"},
        *({"title": f"P{i}", "fulltext_receipt": dict(good, text_sha256=f"{i:x}" * 64, text_file=f"C:/x/{i}.md")}
          for i in range(2, 9))]}
    found = precedent_texts.rows(record)
    assert len(found) == precedent_texts.MAX_TEXTS and found[0]["title"] == "A"
    assert precedent_texts.rows({"status": "warning", "papers": [{"fulltext_receipt": good}]}) == []
    assert precedent_texts.rows(None) == []
    name = precedent_texts.file_name({"paper_id": "https://x/../PMID:1?a=b", "text_sha256": "c" * 64}, 2)
    assert name.endswith("-cccccccc.md") and name.startswith("2-")
    assert not any(char in name for char in '/\\:?*"<>|')


@pytest.mark.asyncio
async def test_a_stored_text_is_readable_in_the_step_inputs_and_the_step_is_told_to_read_it_first(
        tmp_path, monkeypatch):
    settings = _runner_settings(tmp_path, None)
    root = Path(settings.runner.workspace_root)
    root.mkdir(exist_ok=True)
    path, digest, receipt = _stored(root)
    rows = precedent_texts.rows(_record(receipt))
    _system_commands(monkeypatch)
    runner, seen = _capture_runner(settings, monkeypatch, Engine.claude_code)
    task = Task(id="t1", agent_id="worker", request_id="r", prompt="extract ORR",
                meta={"kind": "step", "step_id": "clin", "upstream_artifacts": {}, "precedent_texts": rows})
    assert (await runner.run_task(task)).ok
    ctx = seen["ctx"]
    staged = ctx.workdir / "inputs" / "precedent" / precedent_texts.file_name(rows[0], 1)
    assert staged.read_bytes() == TEXT.encode() and hashlib.sha256(staged.read_bytes()).hexdigest() == digest
    assert "inputs/precedent/" in ctx.task.context and "BEFORE any lookup" in ctx.task.context
    assert staged.name in ctx.task.context and "access_level=full_text" in ctx.task.context
    assert str(ctx.workdir / "inputs" / "precedent") in ctx.private_open_reads  # the step may read it
    assert path.read_bytes() == TEXT.encode()  # the scout's own file is untouched


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["hash", "outside", "receipt_mismatch", "relative"])
async def test_a_text_that_fails_a_check_is_not_staged_and_the_step_still_runs(tmp_path, monkeypatch, fault):
    settings = _runner_settings(tmp_path, None)
    root = Path(settings.runner.workspace_root)
    root.mkdir(exist_ok=True)
    path, digest, receipt = _stored(root)
    row = precedent_texts.rows(_record(receipt))[0]
    if fault == "hash":
        path.write_text(TEXT + "tampered\n", encoding="utf-8")  # bytes differ from the declared hash
    elif fault == "outside":
        outside = tmp_path / "outside.md"
        outside.write_bytes(TEXT.encode())
        row = dict(row, text_file=str(outside))
    elif fault == "receipt_mismatch":
        Path(str(path) + ".receipt.json").write_text(json.dumps(dict(receipt, text_sha256="d" * 64)), encoding="utf-8")
    elif fault == "relative":
        row = dict(row, text_file="relative/path.md")  # not an absolute path in the workspace
    _system_commands(monkeypatch)
    runner, seen = _capture_runner(settings, monkeypatch, Engine.claude_code)
    task = Task(id="t1", agent_id="worker", request_id="r", prompt="extract ORR",
                meta={"kind": "step", "step_id": "clin", "upstream_artifacts": {}, "precedent_texts": [row]})
    assert (await runner.run_task(task)).ok
    ctx = seen["ctx"]
    assert not list((ctx.workdir / "inputs" / "precedent").glob("*.md"))
    assert "unavailable" in ctx.task.context and "BEFORE any lookup" not in ctx.task.context


@pytest.mark.asyncio
async def test_without_stored_texts_the_step_is_unchanged(tmp_path, monkeypatch):
    settings = _runner_settings(tmp_path, None)
    Path(settings.runner.workspace_root).mkdir(exist_ok=True)
    _system_commands(monkeypatch)
    runner, seen = _capture_runner(settings, monkeypatch, Engine.claude_code)
    task = Task(id="t1", agent_id="worker", request_id="r", prompt="work",
                meta={"kind": "step", "step_id": "clin", "upstream_artifacts": {}})
    assert (await runner.run_task(task)).ok
    ctx = seen["ctx"]
    assert not (ctx.workdir / "inputs" / "precedent").exists() and "precedent" not in ctx.task.context


@pytest.mark.asyncio
async def test_the_orchestrator_hands_the_stored_texts_to_each_step_and_names_them_to_the_report(tmp_path):
    hub = cont._hub(tmp_path, [cont.APPROVE, cont.APPROVE], reviews=[cont.ACCEPT])
    receipt = {"coverage_version": 2, "paper_id": "PMID:38587856", "source": "fulltext tool",
               "access_level": "full_text", "extraction": "complete", "text_file": str(tmp_path / "t.md"),
               "text_sha256": "e" * 64}
    hub.requests["r"]["analysis_precedents"] = _record(receipt)  # the scout already ran: no second search
    await Orchestrator(hub).run_request("r")

    steps = [task for task in hub.calls if task.meta["kind"] == "step"]
    assert steps and all(task.meta["precedent_texts"][0]["text_sha256"] == "e" * 64 for task in steps)
    [synth] = [task for task in hub.calls if task.meta["kind"] == "synthesis"]
    assert "Stored precedent texts recorded for this request" in synth.prompt and "step-level gap" in synth.prompt
    assert "may have been unavailable to a given step" in synth.prompt  # recorded, not claimed to have been opened


@pytest.mark.asyncio
async def test_without_precedent_texts_the_orchestrator_adds_nothing(tmp_path):
    hub = cont._hub(tmp_path, [cont.APPROVE, cont.APPROVE], reviews=[cont.ACCEPT])
    hub.requests["r"]["analysis_precedents"] = {"status": "warning", "papers": [], "required": [],
                                                 "recommended": [], "limitations": []}
    await Orchestrator(hub).run_request("r")
    steps = [task for task in hub.calls if task.meta["kind"] == "step"]
    assert steps and all("precedent_texts" not in task.meta for task in steps)
    [synth] = [task for task in hub.calls if task.meta["kind"] == "synthesis"]
    assert "Stored precedent texts" not in synth.prompt



class _Ws:
    def __init__(self, folder):
        self.dir = folder


def _rows(root):
    rows, files = [], []
    folder = root / "scout" / ".tmp" / "literature"
    folder.mkdir(parents=True, exist_ok=True)
    for index, body in enumerate(("alpha", "bravo", "charlie"), 1):
        path = folder / f"p{index}.md"
        path.write_bytes(body.encode())
        digest = hashlib.sha256(body.encode()).hexdigest()
        Path(str(path) + ".receipt.json").write_text(json.dumps({"paper_id": f"PMID:{index}", "text_sha256": digest}))
        rows.append({"paper_id": f"PMID:{index}", "title": f"T{index}", "text_file": str(path), "text_sha256": digest,
                     "access_level": "full_text", "extraction": "complete", "source": "s"})
        files.append(path)
    return rows, files


def test_one_bad_copy_omits_only_its_own_text(tmp_path):
    from labhq.runner.step_inputs import stage_precedent_texts

    root = tmp_path / "ws"
    rows, files = _rows(root)
    workdir = root / "step"
    workdir.mkdir()
    files[1].write_bytes(b"bravo CHANGED")  # passes every metadata check, fails the byte hash during the copy
    staged, refused, listing = stage_precedent_texts(_Ws(workdir), rows, root, [], 1024, 1024)
    assert [name.split("-")[1] for name, _ in listing] == ["PMID_1", "PMID_3"]  # the others are still there
    assert len(refused) == 1 and "PMID:2" in next(iter(refused))
    assert {f.name for f in (workdir / "inputs" / "precedent").glob("*.md")} == {name for name, _ in listing}
    assert staged == [str(workdir / "inputs" / "precedent")]


def test_the_precedent_copies_draw_on_the_same_byte_budget_as_the_ancestor_inputs(tmp_path):
    from labhq.runner.step_inputs import stage_precedent_texts

    root = tmp_path / "ws"
    rows, _files = _rows(root)
    workdir = root / "step"
    workdir.mkdir()
    budget = [7]  # ancestors used the rest: "alpha" (5 bytes) fits, "charlie" (7 bytes) does not
    staged, refused, listing = stage_precedent_texts(_Ws(workdir), [rows[0], rows[2]], root, [], 1024, 1024,
                                                     remaining=budget)
    assert [name.split("-")[1] for name, _ in listing] == ["PMID_1"] and len(refused) == 1
    assert budget[0] == 2  # the shared counter was spent, not a private copy of it
