"""Synthetic audit transport, fake adapter, real result serialization and CP2; no wiki or staff process."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from labhq.evidence.wiki_contract import AUDIT, MAX_AUDIT_BYTES, capture_audit, collect_audit, step_findings
from labhq.models import Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from tests.test_read_only_profile import _runner, _staff, _wire
from tests.test_research_cp2 import CP1, _settings
from tests.test_research_protocol import MiniHub, valid_plan
from tests.test_wiki_evidence import _audit, _wiki_ledger, _wiki_result, _workdir


def _record(text=None):
    text = _audit() if text is None else text
    return {"text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _result(**updates):
    return TaskResult.model_validate({"task_id": "t", "agent_id": "a", "ok": True,
                                      "workdir": "/unavailable-remote-workdir", **updates})


def test_remote_serialization_collects_and_checks_exact_utf8_bytes_without_disk(monkeypatch):
    # Non-ASCII and CRLF must survive without read_text newline conversion.
    text = _audit().replace('"Example"', '"메모"').replace('\n', '\r\n')
    result = TaskResult.model_validate_json(_result(wiki_audit=_record(text)).model_dump_json())

    def no_disk(*args, **kwargs):
        pytest.fail("gateway must not read the runner workdir")

    monkeypatch.setattr(Path, "open", no_disk)
    collected = collect_audit(result)
    assert collected.outputs == [AUDIT]
    assert collected.output_sha256[AUDIT] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert step_findings(_wiki_ledger(), result.workdir, collected) == ([], [])


@pytest.mark.parametrize("record", [
    {}, {"text": "{broken", "sha256": "a" * 64}, _record("{broken"),
    {**_record(), "sha256": "0" * 64}, {"error": "missing file"},
    {"text": "x" * (MAX_AUDIT_BYTES + 1), "sha256": "a" * 64},
    {"text": "한" * (MAX_AUDIT_BYTES // 3 + 1), "sha256": "a" * 64},
    {"text": 123}, {"text": "\ud800"}, {**_record(), "unexpected": True},
], ids=["empty", "broken-hash", "malformed-jsonl", "wrong-hash", "missing", "oversized-ascii",
        "oversized-utf8", "bad-type", "bad-unicode", "unknown-field"])
def test_invalid_transport_never_falls_back_to_valid_local_file(tmp_path, record):
    workdir = _workdir(tmp_path, _audit())
    result = _result(workdir=workdir, wiki_audit=record)
    result = TaskResult.model_validate_json(result.model_dump_json())
    rows, claims = step_findings(_wiki_ledger(relation="supports"), workdir, result)
    assert [row["row_id"] for row in rows] == ["e1", "e3"]
    assert [claim["claim_id"] for claim in claims] == ["c1"]
    assert AUDIT not in collect_audit(result).outputs


def test_collected_hash_conflict_is_refused():
    result = _result(wiki_audit=_record(), outputs=[AUDIT], output_sha256={AUDIT: "f" * 64})
    rows, _ = step_findings(_wiki_ledger(), result.workdir, result)
    assert "collected-output hash mismatch" in rows[0]["reason"]
    assert collect_audit(result).output_sha256[AUDIT] == "f" * 64


def test_old_result_uses_existing_local_audit_or_refuses_when_unavailable(tmp_path):
    old = _result(workdir=_workdir(tmp_path, _audit()))
    assert old.wiki_audit is None
    assert step_findings(_wiki_ledger(), old.workdir, old) == ([], [])
    assert step_findings(_wiki_ledger(), None, _result())[0]
    assert step_findings({"evidence": []}, None, _result(wiki_audit={"error": "missing"})) == ([], [])


def test_every_transported_wiki_row_is_checked():
    ledger = _wiki_ledger()
    second = json.loads(json.dumps(ledger["evidence"][0]))
    second["id"] = "unrecorded"
    second["source"]["locator"] += "bad"
    ledger["evidence"].append(second)
    rows, _ = step_findings(ledger, None, _result(wiki_audit=_record()))
    assert [row["row_id"] for row in rows] == ["unrecorded"]


@pytest.mark.parametrize("kind", ["normal", "missing", "oversized", "invalid-utf8", "hardlink", "symlink"])
def test_capture_bounds_and_hashes_plain_runner_file(tmp_path, kind):
    root = tmp_path / "work"
    _workdir(root, None)
    path = root / AUDIT
    if kind == "normal":
        path.write_bytes(_audit().encode("utf-8"))
    elif kind == "oversized":
        path.write_bytes(b" " * (MAX_AUDIT_BYTES + 1))
    elif kind == "invalid-utf8":
        path.write_bytes(b"\xff")
    elif kind in {"hardlink", "symlink"}:
        target = tmp_path / "outside.jsonl"
        target.write_text(_audit(), encoding="utf-8")
        if kind == "hardlink":
            os.link(target, path)
        else:
            try:
                path.symlink_to(target)
            except OSError:
                pytest.skip("symlinks unavailable")
    result = capture_audit(_result(workdir=str(root)))
    assert result.wiki_audit is not None
    if kind == "normal":
        assert result.outputs == [AUDIT]
        assert result.unreported_outputs == []
        assert result.wiki_audit.text == _audit()
        assert result.output_sha256[AUDIT] == hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        assert result.wiki_audit.error and AUDIT not in result.outputs


@pytest.mark.asyncio
async def test_actual_runner_captures_undeclared_audit_before_return(tmp_path, monkeypatch):
    runner, seen = _runner(tmp_path), []

    def write(ctx):
        (ctx.workdir / AUDIT).write_bytes(_audit().encode("utf-8"))

    _wire(runner, monkeypatch, _staff(tmp_path), seen, write)
    result = await runner.run_task(Task(agent_id="worker", prompt="synthetic", meta={"kind": "step"}))
    assert result.ok, result.error
    assert result.wiki_audit is not None and result.wiki_audit.error is None
    assert AUDIT in result.outputs and AUDIT not in result.unreported_outputs
    assert result.wiki_audit.sha256 == result.output_sha256[AUDIT]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing", "wrong-hash", "malformed", "oversized", "unrecorded"])
@pytest.mark.parametrize("flow", ["accept", "salvage", "correction"])
async def test_remote_result_reaches_actual_cp2_with_fail_closed_findings(fault, flow):
    holder = {}

    async def reply(task):
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=valid_plan())
        hub = holder["hub"]
        path = hub.requests["r"]["plan"]["steps"][0]["outputs"][0]
        record = _record()
        if fault == "missing":
            record = {"error": "missing"}
        elif fault == "wrong-hash":
            record["sha256"] = "f" * 64
        elif fault == "malformed":
            record = _record("{broken")
        elif fault == "oversized":
            record = _record("x" * (MAX_AUDIT_BYTES + 1))
        elif fault == "changed-record" and task.meta["kind"] != "step":
            record = _record(_audit() + "\n")
        ledger = _wiki_result(hub, task)
        if flow == "salvage":
            ledger["evidence"].append({"id": "bad", "kind": "observation"})
        if flow == "correction" and task.meta["kind"] == "step":
            ledger["step_id"] = "wrong_step"
        if fault == "unrecorded":
            next(row for row in ledger["evidence"] if row["id"] == "e_wiki")["source"]["locator"] += "bad"
        result = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=ledger,
                            outputs=[path], workdir="/unavailable-remote-workdir", wiki_audit=record)
        return TaskResult.model_validate_json(result.model_dump_json())

    hub = holder["hub"] = MiniHub(_settings(), reply, mode="orchestrate", work_kind="research", text="compare")
    decisions = [CP1, {"approved": True, "choice": "approve", "note": ""}]

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        return {**decisions.pop(0), "approval_id": f"a{len(hub.approvals)}", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")
    card = hub.approvals[1]["detail"]
    refused = [row for row in card.get("refused_rows", []) if row["row_id"] == "e_wiki"]
    bound_refused = [row for row in card.get("refused_evidence", []) if row["evidence_id"] == "e_wiki"]
    if fault is None:
        assert not refused and not bound_refused
    else:
        assert refused and refused[0]["reason"].startswith("wiki audit: ")
        assert bound_refused


@pytest.mark.asyncio
async def test_correction_cannot_replace_the_original_collected_audit_bytes():
    await test_remote_result_reaches_actual_cp2_with_fail_closed_findings("changed-record", "correction")
