"""The runner reads the step's lookup status file into the result, and research limits carry it (#676)."""

import json

import pytest

from labhq.models import ExternalLookupRecord
from labhq.orchestrator.cso import Orchestrator
from labhq.tools.external_wait import STATUS_FILE
from tests.test_observed_outputs import _runner, _task, _write
from tests.test_research_report import _hub

TIMEOUT_ROWS = "".join(json.dumps(row) + "\n" for row in (
    {"tool": "chipatlas", "status": "waiting", "elapsed_s": 0, "timeout_s": 1200, "text": "**x**"},
    {"tool": "chipatlas", "status": "timeout", "elapsed_s": 1200, "timeout_s": 1200, "text": "<b>x</b>"},
    {"tool": "gtex", "status": "waiting", "elapsed_s": 1201, "timeout_s": 1200},
    {"tool": "gtex", "status": "cancelled", "elapsed_s": 1202, "timeout_s": 1200},
)).encode()
LINE = "`chipatlas` 조회 불가(시간 초과) 1건 · 단계 조회 경과 1200초/상한 1200초"


@pytest.mark.asyncio
async def test_runner_reads_the_status_file_into_the_result_and_warns_without_changing_success(tmp_path, monkeypatch):
    def write(workdir):
        _write(workdir, "outputs/table.tsv", b"a\tb\n1\t2\n")
        _write(workdir, STATUS_FILE, TIMEOUT_ROWS)

    runner = _runner(tmp_path, monkeypatch, write)
    done = await runner.run_task(_task(tid="task-lookup", outputs=["outputs/table.tsv"],
                                       required_outputs=["outputs/table.tsv"], optional_outputs=[]))
    assert done.ok and done.missing_outputs == []
    tools = {item.tool: item for item in done.external_lookups.tools}
    assert tools["chipatlas"].timeout == 1 and tools["gtex"].cancelled == 1 and tools["gtex"].timeout == 0
    warnings = [event["data"].get("text", "") for event in runner.store.pending()
                if event["type"] == "agent.log" and event["data"].get("level") == "warn"]
    lookup = [text for text in warnings if text.startswith("외부 조회 상태(")]
    assert len(lookup) == 1 and LINE in lookup[0] and "`gtex` 조회 취소 1건" in lookup[0]
    assert "**x**" not in lookup[0] and "<b>" not in lookup[0]

    failed = await runner.run_task(_task(tid="task-lookup-required", outputs=["outputs/table.tsv", "outputs/chip.tsv"],
                                         required_outputs=["outputs/table.tsv", "outputs/chip.tsv"],
                                         optional_outputs=[]))
    assert not failed.ok and failed.missing_outputs == ["outputs/chip.tsv"]  # a record never excuses a deliverable
    assert failed.external_lookups.tools[0].timeout == 1


@pytest.mark.asyncio
async def test_runner_without_a_status_file_keeps_the_result_shape(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch, lambda workdir: _write(workdir, "outputs/table.tsv", b"x\n"))
    result = await runner.run_task(_task(outputs=["outputs/table.tsv"]))
    assert result.external_lookups is None and "external_lookups" not in result.model_dump()


@pytest.mark.asyncio
async def test_research_cp2_card_and_report_limits_name_a_lookup_missing_from_the_ledger():
    hub = _hub()
    original = hub.reply

    async def reply(task):
        result = await original(task)
        if task.meta["kind"] == "step":
            # The ledger has no failed/unavailable row for this lookup; the record still reaches the limits.
            result.external_lookups = ExternalLookupRecord(
                tools=[{"tool": "chipatlas", "timeout": 1, "elapsed_s": 1200, "timeout_s": 1200}])
        return result

    hub.reply = reply
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done", req.get("error")
    card = json.dumps(next(a["detail"] for a in hub.approvals if a["kind"] == "research_evidence"), ensure_ascii=False)
    assert LINE in card and "호출·본문 독해 증명은 아님" in card
    assert LINE in req["report"].split("## 한계", 1)[1]
