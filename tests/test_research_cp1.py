"""#222: a public-data research request reaches the CP1 approval card, and a PLAN that cannot says why."""
import asyncio
import copy
import json
import sys
from pathlib import Path

import pytest
import uvicorn
import yaml
from pydantic import ValidationError

from labhq.gateway.server import RequestIn, create_app
from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.research import contract
from labhq.research.contract import RESEARCH_PLAN_SCHEMA, StatisticsPlan, validate_research_plan
from labhq.research.packs import configured_packs, pack_snapshot, render_pack_catalog
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.util import free_port
from tests.test_e2e_mock import _request_state, _until, _wait_for_request_terminal
from tests.test_research_protocol import PACK, MiniHub, valid_pack_values, valid_plan

REPO = Path(__file__).resolve().parents[1]
FAKE_CLAUDE = REPO / "tests" / "fixtures" / "fake_claude_cso.py"


def _research_settings():
    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = [PACK]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    return settings


def _refs(selected):
    return [{"id": loaded.pack.id, "version": loaded.pack.version, "sha256": loaded.sha256}
            for _key, loaded in sorted(selected.items())]


async def _plan_with(replies, *, text="GSE96583 공개 PBMC에서 IFN-β 자극 CD14+ 단핵구의 차등 발현을 donor pseudobulk로 비교"):
    settings = _research_settings()

    async def reply(task):
        assert task.meta["kind"] == "plan" and task.output_schema == RESEARCH_PLAN_SCHEMA
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=replies.pop(0))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text=text)
    await Orchestrator(hub).run_request("r")
    return hub, configured_packs(settings)


def test_pack_catalog_names_the_exact_pack_values_keys():
    # The live CSO took reviewer questions for acceptance ids: the catalog never said which ids they are.
    settings = _research_settings()
    selected = configured_packs(settings)
    pack = selected[PACK].pack
    rows = [json.loads(line) for line in render_pack_catalog(selected).splitlines()]
    keys = rows[0]["pack_values_keys"]
    assert keys["fields"] == [field.name for field in pack.fields]
    assert keys["validators"] == [validator.id for validator in pack.validators]
    assert keys["acceptance"] == [rule.id for rule in pack.rules]


async def test_cso_that_omits_or_miscopies_the_pack_hash_still_reaches_cp1():
    # The snapshot is configuration, not a CSO choice; a 64-hex hash is not something a model should copy.
    for packs in ([], [{"id": "single_cell_de", "version": "2", "sha256": "0" * 64}]):
        hub, selected = await _plan_with([valid_plan(packs, pack_values=valid_pack_values())])
        request = hub.requests["r"]
        assert [task.meta["kind"] for task in hub.calls] == ["plan"], request.get("report")
        assert request["status"] == "done" and request["outcome"] == "plan_approved", request.get("report")
        assert [approval["kind"] for approval in hub.approvals] == ["research_plan"]
        detail = hub.approvals[0]["detail"]
        assert detail["packs"] == _refs(selected)
        frozen = json.loads(detail["plan_canonical"])
        assert frozen["protocol"]["packs"] == _refs(selected) == request["plan"]["protocol"]["packs"]
        assert request["research_contract"]["pack_snapshot"] == pack_snapshot(selected)


async def test_correction_prompt_lists_every_problem_of_the_previous_plan():
    # Live run d1: the correction only showed the first error, and the second plan failed on the next one.
    first = valid_plan(pack_values=valid_pack_values())
    del first["protocol"]["statistics"]["primary_outcomes"]
    first["pack_values"][PACK]["fields"]["count_scale"] = "normalized_counts"
    first["pack_values"][PACK]["acceptance"] = {"donor_level_independence_preserved": "met"}
    hub, selected = await _plan_with([first, valid_plan(pack_values=valid_pack_values())])
    assert [task.meta["kind"] for task in hub.calls] == ["plan", "plan"]
    correction = hub.calls[1].prompt
    assert "protocol.statistics" in correction and "primary_outcomes" in correction
    assert f"pack_values[{PACK}].fields.count_scale must be one of" in correction
    for rule in selected[PACK].pack.rules:
        assert rule.id in correction
    assert "unexpected ['donor_level_independence_preserved']" in correction
    assert "errors.pydantic.dev" not in correction
    assert hub.requests["r"]["outcome"] == "plan_approved"
    assert [approval["kind"] for approval in hub.approvals] == ["research_plan"]


async def test_malformed_agent_id_goes_to_the_correction_instead_of_failing_the_request():
    first = valid_plan(pack_values=valid_pack_values())
    first["steps"][0]["agent_id"] = ["worker"]
    hub, _selected = await _plan_with([first, valid_plan(pack_values=valid_pack_values())])
    assert [task.meta["kind"] for task in hub.calls] == ["plan", "plan"], hub.requests["r"].get("report")
    assert "steps.0.agent_id" in hub.calls[1].prompt
    assert hub.requests["r"]["outcome"] == "plan_approved"


async def test_plan_still_invalid_after_correction_tells_the_pi_why():
    bad = valid_plan(pack_values=valid_pack_values())
    bad["pack_values"][PACK]["acceptance"] = {"batch_condition_distinguishable": "met"}
    hub, selected = await _plan_with([bad, copy.deepcopy(bad)])
    request = hub.requests["r"]
    assert request["status"] == "failed" and request["outcome"] == "plan_invalid"
    assert hub.approvals == []
    report = request["report"]
    assert report.startswith("연구 계획 검증 실패"), report
    assert "CP1" in report and PACK in report
    assert "single_cell_de.donor_model" in report and "batch_condition_distinguishable" in report
    assert "ValueError" not in report and "Traceback" not in report
    assert request["error"].startswith("연구 계획 검증 실패")
    assert any("acceptance" in problem for problem in request["plan_validation"]["errors"])
    assert request["plan_validation"]["attempts"] == 2


def test_plan_errors_collect_schema_and_pack_problems_in_one_pass():
    settings = _research_settings()
    selected = configured_packs(settings)
    plan = valid_plan(_refs(selected), pack_values=valid_pack_values())
    del plan["protocol"]["statistics"]["primary_outcomes"]
    plan["pack_values"][PACK]["fields"]["count_scale"] = "normalized_counts"
    del plan["pack_values"][PACK]["acceptance"]["single_cell_de.donor_model"]
    errors = contract.research_plan_errors(plan, max_steps=2, active_packs=pack_snapshot(selected),
                                           pack_definitions=selected)
    assert any(e.startswith("protocol.statistics:") and "primary_outcomes" in e for e in errors), errors
    assert any(f"pack_values[{PACK}].fields.count_scale must be one of" in e for e in errors), errors
    assert any("missing ['single_cell_de.donor_model']" in e for e in errors), errors
    assert not any("errors.pydantic.dev" in e for e in errors)
    with pytest.raises(ValidationError):  # the strict validator keeps its exception type
        validate_research_plan(plan, max_steps=2, active_packs=pack_snapshot(selected), pack_definitions=selected)


def test_statistics_error_names_the_missing_core_field():
    with pytest.raises(ValidationError, match=r"missing: primary_outcomes"):
        StatisticsPlan.model_validate({"applicable": True, "reason": "comparison", "estimand": "effect",
                                       "analysis_unit": "donor", "comparison_groups": ["a", "b"],
                                       "multiple_testing": "FDR", "missing_and_exclusions": "none",
                                       "effect_size_and_interval": "CI"})


def _write_agents(root: Path) -> Path:
    core = root / "agents" / "core"
    core.mkdir(parents=True)
    (core / "cso.yaml").write_text((REPO / "agents" / "core" / "cso.yaml").read_text(encoding="utf-8"),
                                   encoding="utf-8")
    worker = {"id": "analyst", "name": "분석가", "role": "single-cell analysis", "engine": "mock",
              "system_prompt": "You analyse public single-cell data."}
    (core / "analyst.yaml").write_text(yaml.safe_dump(worker, allow_unicode=True), encoding="utf-8")
    return root / "agents"


@pytest.mark.parametrize("command_limit", [None, 25_000], ids=["default-limit", "over-command-line-limit"])
async def test_public_research_request_reaches_cp1_card_through_the_claude_schema_path(tmp_path, monkeypatch,
                                                                                      command_limit):
    # The live rerun's CSO re-plan was 33,091 characters, past the Windows limit; a lowered limit takes that path.
    from labhq.adapters import base
    if command_limit:
        monkeypatch.setattr(base, "command_line_limit", lambda: command_limit, raising=False)
    s = _research_settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    gport = free_port()
    s.gateway.port, s.gateway.url = gport, f"ws://127.0.0.1:{gport}"
    s.runner.broker_port, s.runner.job_poll_s = free_port(), 1
    s.runner.workspace_root = str(tmp_path / "runs")
    s.runner.talent_dir = str(tmp_path / "talent")
    s.runner.agents_dir = str(_write_agents(tmp_path))
    s.engines.claude_code.bin = sys.executable
    s.engines.claude_code.prefix_args = [str(FAKE_CLAUDE)]
    s.hpc.scheduler = "mock"
    selected = configured_packs(s)

    app = create_app(s)
    hub = app.state.hub
    seen: list[dict] = []
    publish = hub.publish

    async def tap(ev, **kwargs):
        seen.append(ev)
        await publish(ev, **kwargs)
        if ev.get("type") == "approval.requested":
            aid = ev["data"]["id"]

            async def approve():
                await _until(lambda: aid in hub.approvals, 10, f"approval {aid} to be pending",
                             lambda: {"pending": sorted(hub.approvals)})
                await hub.resolve_approval(aid, True, "CP1 ok")

            asyncio.get_running_loop().create_task(approve())

    hub.publish = tap
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=gport, log_level="warning"))
    tasks = [asyncio.create_task(server.serve())]
    await _until(lambda: server.started, 10, "gateway server startup", lambda: {"started": server.started})
    runner = Runner(s)
    tasks.append(asyncio.create_task(runner.run_forever()))
    try:
        await _until(lambda: {"cso", "analyst"} <= set(hub.agents), 30, "runner agent registration",
                     lambda: {"agents": list(hub.agents)})
        text = ("GSE96583(공개 PBMC, IFN-β 자극 대 대조 scRNA-seq)에서 CD14+ 단핵구의 자극 반응 유전자를 "
                "donor 단위 pseudobulk로 비교하는 연구 계획을 세워 줘. 공개 count matrix만 쓴다.")
        for marker, plan_calls in (("", 1), (" [draft-mistakes]", 2)):
            rid = hub.create_request(RequestIn(text=text + marker, work_kind="research"))
            await _wait_for_request_terminal(hub, rid, seen, 60)
            request = hub.requests[rid]
            assert request["status"] == "done", (request.get("report"), _request_state(hub, rid, seen))
            assert request["outcome"] == "plan_approved"
            plans = sorted((task for task in hub.store.all("task").values()
                            if task.get("request_id") == rid and task["payload"]["meta"].get("kind") == "plan"),
                           key=lambda task: task["dispatched_at"])
            assert len(plans) == plan_calls, [task["result"].get("error") for task in plans]
            assert all(task["payload"]["output_schema"] == RESEARCH_PLAN_SCHEMA for task in plans)
            assert all(task["result"]["structured"]["protocol"].get("packs") in (None, []) for task in plans)
            assert "pack_values_keys" in plans[0]["payload"]["prompt"]
            cards = [event["data"] for event in seen if event["type"] == "approval.requested"
                     and event.get("request_id") == rid and event["data"]["kind"] == "research_plan"]
            assert len(cards) == 1
            assert cards[0]["detail"]["packs"] == _refs(selected)
            assert json.loads(cards[0]["detail"]["plan_canonical"]) == request["plan"]
            assert request["research_contract"]["approval"]["approved"] is True
            carried = [json.loads(line) for task in plans for line in
                       (Path(task["result"]["workdir"]) / ".labhq" / "fake_cli_prompts.jsonl")
                       .read_text(encoding="utf-8").splitlines()]
            assert len(carried) >= plan_calls
            pointer = [prompt.startswith("Read TASK") for prompt in carried[-plan_calls:]]
            expected_pointer = ([True] * plan_calls if command_limit else
                                ([False] if plan_calls == 1 else [False, True]))
            assert pointer == expected_pointer, carried
    finally:
        runner.stop()
        server.should_exit = True
        _, pending = await asyncio.wait(tasks, timeout=10)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
