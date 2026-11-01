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


from labhq.runner.lookup_admission import LookupAdmission


def test_precedent_share_preserves_request_capacity(tmp_path):
    admission = LookupAdmission(tmp_path / "lookup.sqlite3", 9, precedent_share=0.5)
    def send(stage):
        return admission.admit("request", "task", "agent", "search", "example.org", stage=stage)

    assert all(send("precedent")["admitted"] for _ in range(4))
    assert send("precedent")["reason"] == "stage share exhausted"
    assert all(send(None)["admitted"] for _ in range(5))
    assert send(None)["reason"] == "exhausted"


def test_migration_and_restart_preserve_counts(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sends (request_id TEXT NOT NULL, seq INTEGER NOT NULL, task_id TEXT, agent_id TEXT, tool TEXT NOT NULL, host TEXT NOT NULL, admitted_at REAL NOT NULL, http_status INTEGER, outcome TEXT, PRIMARY KEY(request_id, seq))")
        db.execute("INSERT INTO sends VALUES ('r', 1, 'old', 'a', 'search', 'example.org', 0, NULL, NULL)")
    ledger = LookupAdmission(path, 4, precedent_share=0.5)
    assert ledger.usage('r')['used'] == 1
    assert ledger.admit('r', 't1', 'a', 'search', 'example.org', stage='precedent')['admitted']
    ledger = LookupAdmission(path, 4, precedent_share=0.5)
    assert ledger.admit('r', 't2', 'a', 'search', 'example.org', stage='precedent')['admitted']
    assert ledger.admit('r', 't2', 'a', 'search', 'example.org', stage='precedent')['reason'] == 'stage share exhausted'
    assert ledger.admit('r', 'plan', 'a', 'search', 'example.org')['admitted']
    assert ledger.admit('r', 'plan', 'a', 'search', 'example.org')['reason'] == 'exhausted'


def test_concurrent_migration_and_admission(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / 'concurrent.sqlite3'
    def attempt(i):
        ledger = LookupAdmission(path, 9, precedent_share=0.5)
        return ledger.admit('r', str(i), 'a', 'search', 'example.org', stage='precedent')['admitted']
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(attempt, range(24))) == 4
    assert LookupAdmission(path, 9, precedent_share=0.5).usage('r')['used'] == 4


def test_share_boundaries_and_prompt(tmp_path):
    from labhq.runner.lookup_admission import usage_note
    from labhq.settings import AnnotSettings
    from pydantic import ValidationError
    import pytest
    assert AnnotSettings().precedent_lookup_share == .5
    assert AnnotSettings(precedent_lookup_share=None).precedent_lookup_share is None
    for invalid in (-0.1, 1.1):
        with pytest.raises(ValidationError):
            AnnotSettings(precedent_lookup_share=invalid)
    for share, expected in [(None, True), (0, False), (0.5, False), (1, True)]:
        ledger = LookupAdmission(tmp_path / f'{share}.db', 1, precedent_share=share)
        assert ledger.admit('r', 't', 'a', 'search', 'example.org', stage='precedent')['admitted'] is expected
    ledger = LookupAdmission(tmp_path / 'prompt.db', 9, precedent_share=0.5)
    for _ in range(8):
        ledger.admit('r', 't', 'a', 'search', 'example.org')
    assert 'Pre-plan share: 0 of 4 used, 1 left' in usage_note(ledger, 'r', stage='precedent')


import httpx
import pytest
from labhq.runner.approvals import Broker
from labhq.runner.lookup_admission import LookupAdmission


@pytest.mark.asyncio
async def test_body_cannot_override_issued_stage(tmp_path):
    async def noop(*args):
        pass
    ledger = LookupAdmission(tmp_path / 'broker.db', 4, precedent_share=0.5)
    broker = Broker(0, noop, noop, noop, lookup_admission=ledger)
    token = broker.issue_task_token('t', 'a', 'r', stage='precedent')
    transport = httpx.ASGITransport(app=broker.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        for body in ({'tool': 'search', 'host': 'example.org', 'stage': 'plan'},
                     {'tool': 'search', 'host': 'example.org'},
                     {'tool': 'search', 'host': 'example.org', 'stage': None}):
            response = await client.post('/lookup/admit', json=body, headers={'x-labhq-token': token})
            assert response.status_code == 200
        assert response.json()['reason'] == 'stage share exhausted'


@pytest.mark.asyncio
@pytest.mark.parametrize('share', [0.25, None])
async def test_runner_supplies_settings_and_issues_stage(tmp_path, monkeypatch, share):
    from labhq.settings import Settings
    from tests import test_observed_outputs as helpers
    settings = Settings()
    settings.annot.request_lookup_limit = 8
    settings.annot.precedent_lookup_share = share
    monkeypatch.setattr(helpers, 'Settings', lambda: settings)
    runner = helpers._runner(tmp_path, monkeypatch, lambda _: None)
    assert runner.broker.lookup_admission.precedent_share == share
    issue = runner.broker.issue_task_token
    identities = []
    def capture(*args, **kwargs):
        token = issue(*args, **kwargs)
        identities.append(runner.broker.identities[token].copy())
        return token
    monkeypatch.setattr(runner.broker, 'issue_task_token', capture)
    result = await runner.run_task(helpers._task(kind='precedent'))
    assert result.ok
    assert identities[0]['stage'] == 'precedent'


def test_mixed_preplan_and_decimal_share(tmp_path):
    from labhq.runner.lookup_admission import usage_note
    for share, cap in [(0.29, 29), (0.58, 58)]:
        ledger = LookupAdmission(tmp_path / f"{share}.db", 100, precedent_share=share)
        for i in range(cap):
            assert ledger.admit("r", str(i), "a", "search", "example.org", stage=("briefing" if i % 2 else "precedent"))["admitted"]
        assert f"Pre-plan share: {cap} of {cap} used, 0 left" in usage_note(ledger, "r", stage="briefing")
        assert ledger.admit("r", "x", "a", "search", "example.org", stage="briefing")["reason"] == "stage share exhausted"
    ledger = LookupAdmission(tmp_path / "mixed.db", 9, precedent_share=0.5)
    for stage in ("briefing", "precedent", "briefing", "precedent"):
        assert ledger.admit("r", "t", "a", "search", "example.org", stage=stage)["admitted"]
    assert ledger.admit("r", "t", "a", "search", "example.org", stage="precedent")["reason"] == "stage share exhausted"
    assert all(ledger.admit("r", "t", "a", "search", "example.org", stage="plan")["admitted"] for _ in range(5))


def test_yaml_precise_share_survives_staff_copy_and_admission(tmp_path):
    from decimal import Decimal
    from labhq.runner.lookup_admission import usage_note
    from labhq.settings import Settings, write_staff_config

    config = tmp_path / "config.yaml"
    config.write_text("annot:\n  request_lookup_limit: 100\n  precedent_lookup_share: 0.29999999999999999\n", encoding="utf-8")
    settings = Settings.load(str(config))
    assert settings.annot.precedent_lookup_share == Decimal("0.29999999999999999")
    assert settings.model_dump(mode="json")["annot"]["precedent_lookup_share"] == "0.29999999999999999"
    staff = Settings.load(write_staff_config(settings, tmp_path / "staff"))
    assert staff.annot.precedent_lookup_share == settings.annot.precedent_lookup_share
    ledger = LookupAdmission(tmp_path / "precise.db", 100, precedent_share=staff.annot.precedent_lookup_share)
    for _ in range(29):
        assert ledger.admit("r", "t", "a", "search", "example.org", stage="precedent")["admitted"]
    assert ledger.admit("r", "t", "a", "search", "example.org", stage="briefing")["reason"] == "stage share exhausted"
    assert "Pre-plan share: 29 of 29 used, 0 left" in usage_note(ledger, "r", stage="precedent")
    assert ledger.admit("r", "t", "a", "search", "example.org", stage="plan")["admitted"]
