"""Synthetic audit transport, fake adapter, real result serialization and CP2; no wiki or staff process."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labhq.evidence.wiki_contract import AUDIT, MAX_AUDIT_BYTES, capture_audit, collect_audit, step_findings
from labhq.gateway.server import create_app, event_without_wiki_audit, SavedResults, snapshot_event, SNAPSHOT_RESULT_CHARS
from labhq.models import Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.research.continuation import archive_round
from labhq.settings import Settings
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
async def test_remote_result_reaches_actual_cp2_with_fail_closed_findings(fault, flow, legacy_workdir=None):
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
        elif fault == "replaced-missing" and task.meta["kind"] == "step":
            record = {"error": "missing"}
        elif fault == "replaced-absent" and task.meta["kind"] == "step":
            record = None
        elif fault == "replaced-invalid" and task.meta["kind"] == "step":
            record = {**_record(), "sha256": "f" * 64}
        elif fault == "changed-record" and task.meta["kind"] != "step":
            changed = json.loads(_audit())
            changed["hits"][0]["line_start"] = 7
            record = _record(json.dumps(changed))
        ledger = _wiki_result(hub, task)
        if flow == "salvage":
            ledger["evidence"].append({"id": "bad", "kind": "observation"})
        if flow == "correction" and task.meta["kind"] == "step":
            ledger["step_id"] = "wrong_step"
        if fault == "unrecorded":
            next(row for row in ledger["evidence"] if row["id"] == "e_wiki")["source"]["locator"] += "bad"
        if fault == "changed-record" and task.meta["kind"] != "step":
            next(row for row in ledger["evidence"] if row["id"] == "e_wiki")["source"]["locator"] = (
                "wiki:notes/example.md#L7-L8@" + "b" * 12)
        if legacy_workdir is not None:
            if task.meta["kind"] != "step":
                (Path(legacy_workdir) / AUDIT).write_text(record["text"], encoding="utf-8")
            record = None
        result = TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=ledger,
                            outputs=[path], workdir=legacy_workdir or "/unavailable-remote-workdir", wiki_audit=record,
                            unreported_outputs=[AUDIT] if legacy_workdir and task.meta["kind"] == "step" else [])
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
@pytest.mark.parametrize("fault", ["changed-record", "replaced-missing", "replaced-invalid", "replaced-absent"])
async def test_correction_cannot_replace_the_original_audit_or_its_error_state(fault):
    await test_remote_result_reaches_actual_cp2_with_fail_closed_findings(fault, "correction")


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["replaced-missing", "changed-record"])
async def test_old_runner_local_record_is_frozen_before_correction(tmp_path, fault):
    workdir = _workdir(tmp_path, _audit() if fault == "changed-record" else None)
    await test_remote_result_reaches_actual_cp2_with_fail_closed_findings(fault, "correction", workdir)


@pytest.mark.asyncio
async def test_gateway_keeps_internal_audit_but_omits_it_from_durable_and_live_events(tmp_path):
    import asyncio

    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    result = _result(wiki_audit=_record())
    event = {"type": "task.result", "task_id": result.task_id, "data": result.model_dump(mode="json")}
    hub.store.put("task", result.task_id, {"payload": {"agent_id": result.agent_id}})
    future = hub.futures[result.task_id] = asyncio.get_running_loop().create_future()
    sent = []

    class Client:
        async def send_text(self, text):
            sent.append(json.loads(text))

    hub.clients.add(Client())
    try:
        await hub.on_runner_message("synthetic-runner", event)
        internal = future.result()
        assert internal.wiki_audit == result.wiki_audit
        assert step_findings(_wiki_ledger(), None, collect_audit(internal)) == ([], [])
        assert hub.store.get("task", result.task_id)["result"]["wiki_audit"] == result.wiki_audit.model_dump()
        assert event["data"]["wiki_audit"] == result.wiki_audit.model_dump()
        for events in (hub.store.events_since(0), list(hub.events), sent):
            published = [item for item in events if item["type"] == "task.result"]
            assert len(published) == 1
            assert "wiki_audit" not in published[0]["data"]
            assert published[0]["data"]["task_id"] == result.task_id
    finally:
        hub.store.close()


@pytest.mark.parametrize("text", ["brief", "x" * (SNAPSHOT_RESULT_CHARS + 1)])
def test_snapshot_removes_legacy_audit_without_mutating_the_internal_event(text):
    event = {"type": "task.result", "seq": 7, "data": {"text": text, "wiki_audit": _record()}}
    snapshot = snapshot_event(event)
    assert "wiki_audit" not in snapshot["data"]
    assert snapshot["data"]["text"] == text[:SNAPSHOT_RESULT_CHARS]
    assert snapshot["seq"] == 7
    assert event["data"] == {"text": text, "wiki_audit": _record()}


@pytest.mark.parametrize("audit", [None, {"error": "missing audit file"}])
def test_empty_audit_preserves_the_existing_result_event_shape(audit):
    event = {"type": "task.result", "data": _result(text="brief", wiki_audit=audit).model_dump(mode="json")}
    assert not (event["data"]["wiki_audit"] or {}).get("text")
    assert event_without_wiki_audit(event) is event
    assert snapshot_event(event) is event


def test_legacy_stored_events_are_filtered_on_reconnect_and_http_replay(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    event = hub.store.append_event({"type": "task.result", "task_id": "t",
                                   "data": {"text": "brief", "wiki_audit": _record()}},
                                  settings.gateway.event_buffer)
    hub.events.append(event)
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    with TestClient(app) as client:
        for suffix in ("", "?since=0"):
            replay = client.get("/api/events" + suffix, headers=headers).json()
            result = next(item for item in replay if item["type"] == "task.result")
            assert "wiki_audit" not in result["data"]
            assert result["seq"] == event["seq"]
        for suffix in ("", "&since=0"):
            with client.websocket_connect(f"/ws/client?token={settings.gateway.client_token}" + suffix) as ws:
                replay = ws.receive_json()
                if replay["type"] == "snapshot":
                    replay = next(item for item in replay["data"]["recent_events"] if item["type"] == "task.result")
                assert "wiki_audit" not in replay["data"]
                assert replay["seq"] == event["seq"]
        assert hub.store.events_since(0)[0]["data"]["wiki_audit"] == _record()


def test_request_detail_omits_all_audit_payloads_but_preserves_internal_saved_results(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    hub.requests["r"] = {"id": "r", "text": "synthetic", "status": "done"}
    results = SavedResults(hub, "r")
    for sid in ("s1", "s2"):
        results[sid] = _result(wiki_audit=_record())
    results["plain"] = _result(text="plain", wiki_audit={"error": "missing audit file"})
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    with TestClient(app) as client:
        response = client.get("/api/requests/r", headers=headers)
        assert response.status_code == 200
        projected = response.json()["results"]
        for sid in ("s1", "s2"):
            assert "wiki_audit" not in projected[sid]
            assert hub.requests["r"]["results"][sid]["wiki_audit"] == results[sid].wiki_audit.model_dump()
            stored = TaskResult.model_validate(hub.store.get("request", "r")["results"][sid])
            assert step_findings(_wiki_ledger(), None, collect_audit(stored)) == ([], [])
        assert projected["plain"] == results["plain"].model_dump(mode="json")


@pytest.mark.parametrize("current_results", [True, False])
def test_request_detail_filters_archived_and_recovery_result_copies_without_mutation(tmp_path, current_results):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    app = create_app(settings)
    hub = app.state.hub
    business_data = {"task_id": "domain-task", "agent_id": "domain-agent", "ok": True,
                     "wiki_audit": {"text": "business data"},
                     "nested": [{"task_id": "nested", "agent_id": "domain", "ok": False, "wiki_audit": "keep"}],
                     "partial": {"task_id": "domain-item", "wiki_audit": 7}}
    result = _result(wiki_audit=_record(), structured=business_data).model_dump(mode="json")
    plain = _result(text="plain").model_dump(mode="json")
    request = {"id": "r", "text": "synthetic", "status": "done", "research_contract": {"round": 1},
               "metadata": business_data}
    first = archive_round(request, {"s1": result, "plain": plain})
    request["research_contract"]["round"] = 2
    second = archive_round(request, {"s2": result})
    request["research_contract"].update(
        rounds=[first, second],
        continuation={"ended_before_dispatch": {"task_results": {"plan": result}},
                      "refusal_history": [{"task_results": {"plan": result}}]})
    request.update(solo_result=result, pending_questions=[{"previous_result": result}])
    if current_results:
        request["results"] = {"current": result}
    hub.requests["r"] = request
    hub.save_request("r")
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    with TestClient(app) as client:
        before = copy.deepcopy(hub.requests["r"])
        response = client.get("/api/requests/r", headers=headers)
        assert response.status_code == 200
        projected = response.json()
        archived = projected["research_contract"]["rounds"]
        copies = [archived[0]["results"]["s1"], archived[1]["results"]["s2"], projected["solo_result"],
                  projected["pending_questions"][0]["previous_result"],
                  projected["research_contract"]["continuation"]["ended_before_dispatch"]["task_results"]["plan"],
                  projected["research_contract"]["continuation"]["refusal_history"][0]["task_results"]["plan"]]
        if current_results:
            copies.append(projected["results"]["current"])
        else:
            assert "results" not in projected
        for exported in copies:
            assert exported == {key: value for key, value in result.items() if key != "wiki_audit"}
            assert exported["structured"] == business_data
        assert projected["metadata"] == business_data
        assert archived[0]["results"]["plain"] == plain
        assert hub.requests["r"] == before
        assert hub.store.get("request", "r") == before
        for original in hub.store.get("request", "r")["research_contract"]["rounds"]:
            saved = next(iter(original["results"].values()))
            assert step_findings(_wiki_ledger(), None, collect_audit(TaskResult.model_validate(saved))) == ([], [])
