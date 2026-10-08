"""Output type declarations on the research lane (#221 todo 2): CP1 hash compatibility, off/on, staff fields."""

import copy
import hashlib
import json

import pytest
from pydantic import ValidationError

from labhq import vocab
from labhq.intake import QUESTION_RULE
from labhq.models import TaskResult
from labhq.orchestrator import cso
from labhq.orchestrator.cso import Orchestrator
from labhq.research import contract as rc
from labhq.research.packs import configured_packs
from labhq.settings import Settings
from labhq.vocab import declare, topics
from tests.test_research_protocol import PACK, MiniHub, valid_pack_values, valid_plan

V = vocab.load()
CANARY = "CANARY-research-91c2"
# json.dumps without sorting: what the engines receive, byte for byte. The plan schema and prompt changed together
# when every configured pack began requiring either full values or an explicit not_applicable reason (PR #366).
# #373 changed only the prompt: binary wheels, planned fallbacks, and no PI question when a fallback exists. PR #378
# review requires every permitted path to write the same declared filename. #373 bench A now declares analysis
# scripts and result-determining references under outputs/. #373 direction 2 adds the shared three-category question
# rule and records research choices in protocol, so this fixed prompt hash changes again.
# PI 점검 R17 adds the optional numeric `budget_usd` to the plan schema and its rule to the prompt; a plan without
# it keeps its canonical JSON and hash (VALID_PLAN_SHA).
RESEARCH_PLAN_SCHEMA_SHA = "1041c1742eacc125f697b3b4a95822eb71b63b7c77839d668e89367162321c00"
RESEARCH_RESULT_SCHEMA_SHA = "9a767787fade091505fea149a8841845738ce30ce77cf6657cbca0aaa72af64d"
RESEARCH_PROMPT_SHA = "afc422989121a61de3b036e20152dc9d7f9b51edaabef628e4de5ab1fce04a9e"  # R17: budget_usd rule
VALID_PLAN_SHA = "f611461cc2dbb17e39159ec1df6a75d8f7b661eb39bbe42c8ed0438c5c45e213"


def raw_sha(value):
    return hashlib.sha256((value if isinstance(value, str) else json.dumps(value)).encode()).hexdigest()


def declared(plan, entries):
    plan = copy.deepcopy(plan)
    plan["steps"][0]["output_types"] = entries
    return plan


def test_engine_schemas_and_prompt_off_are_those_of_main():
    assert raw_sha(rc.RESEARCH_PLAN_SCHEMA) == RESEARCH_PLAN_SCHEMA_SHA
    assert raw_sha(rc.RESEARCH_RESULT_SCHEMA) == RESEARCH_RESULT_SCHEMA_SHA
    assert rc.research_plan_schema(False) is rc.RESEARCH_PLAN_SCHEMA
    args = dict(request="REQ", roster="ROSTER", capabilities="CAPS", briefing="BRIEF", max_steps=3,
                question_rule=QUESTION_RULE, intake="INTAKE", packs="PACKS", topics_rule=topics.prompt_rule(V))
    assert raw_sha(cso.RESEARCH_PLAN_PROMPT.format(**args, output_types_rule="")) == RESEARCH_PROMPT_SHA


def test_on_schema_offers_optional_entries_without_the_version_field():
    step = rc.research_plan_schema(True, declare.ENTRY_SCHEMA)["$defs"]["ResearchStep"]
    assert "output_types" in step["properties"] and "output_types" not in step["required"]
    assert "vocab" not in json.dumps(step["properties"]["output_types"])
    assert raw_sha(rc.RESEARCH_PLAN_SCHEMA) == RESEARCH_PLAN_SCHEMA_SHA


def test_plans_approved_before_221_keep_their_canonical_json_and_hash():
    plan = valid_plan()
    plan.pop("topics")
    plan.pop("checklist")
    plan.pop("suggested_next")
    assert rc.plan_sha256(plan) == VALID_PLAN_SHA
    assert rc.plan_sha256(declared(plan, [])) == VALID_PLAN_SHA
    assert "output_types" not in json.loads(rc.canonical_plan_json(plan))["steps"][0]


def test_a_non_empty_declaration_is_part_of_the_approved_contract():
    plan = valid_plan()
    entry = {"name": "outputs/result1.tsv", "data_type": "de_table", "format": "tsv", "vocab": V.sha256}
    typed = declared(plan, [entry])
    assert rc.plan_sha256(typed) != VALID_PLAN_SHA
    receipt = rc.freeze_plan(typed, {"approved": True, "approval_id": "a1", "decided_at": 1})
    assert rc.refresh_plan_approval(typed, receipt)["status"] == "approved"
    changed = declared(plan, [{**entry, "data_type": "table"}])
    assert rc.refresh_plan_approval(changed, receipt)["status"] == "needs_reapproval"
    assert rc.refresh_plan_approval(declared(plan, [{**entry, "vocab": "0" * 64}]), receipt)["reapproval_required"]
    assert json.loads(rc.canonical_plan_json(typed))["steps"][0]["output_types"] == [entry]


def test_the_contract_itself_stays_strict_about_entry_shape():
    with pytest.raises(ValidationError):
        rc.ResearchPlan.model_validate(declared(valid_plan(), [{"name": "outputs/result1.tsv", "data_type": 7}]))


def research_hub(replies, *, declare_on):
    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = [PACK]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    settings.plan.declare_output_types = declare_on
    refs = [{"id": loaded.pack.id, "version": loaded.pack.version, "sha256": loaded.sha256}
            for loaded in configured_packs(settings).values()]
    plans = [r(refs) for r in replies]
    schemas = []

    async def reply(task):
        schemas.append(task.output_schema)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=plans.pop(0))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")
    return hub, schemas


def plan_with(entries):
    return lambda refs: declared(valid_plan(refs, pack_values=valid_pack_values()), entries)


@pytest.mark.asyncio
async def test_off_removes_declarations_and_freezes_the_plan_of_main():
    hub, schemas = research_hub([plan_with([{"name": "result1.tsv", "data_type": "de_table"}])], declare_on=False)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["outcome"] == "plan_approved" and [t.meta["kind"] for t in hub.calls] == ["plan"]
    assert raw_sha(schemas[0]) == RESEARCH_PLAN_SCHEMA_SHA and "output_types" not in hub.calls[0].prompt
    assert "output_types" not in req["plan"]["steps"][0] and "output_types_stats" not in req
    expected = plan_with([])(req["plan"]["protocol"]["packs"])
    expected["steps"][0]["outputs"] = ["outputs/result1.tsv"]
    expected["pack_applicability"] = req["plan"]["pack_applicability"]
    expected["warnings"] = req["plan"].get("warnings", [])
    undeclared = rc.plan_sha256(expected)
    assert req["research_contract"]["plan_sha256"] == undeclared


@pytest.mark.asyncio
@pytest.mark.parametrize("entries, issues", [
    ([{"name": "result1.tsv", "data_type": 7}, {"name": f"{CANARY}.tsv", "format": "tsv"},
      {"name": "result1.tsv", "data_type": CANARY, "format": "tsv", "extra": 1}, "raw"], {"too_many": 1}),
    ([{"name": "result1.tsv", "data_type": CANARY, "format": 7}], {"unknown_key": 1, "bad_value": 1}),
    ({"result1.tsv": "de_table"}, {"bad_shape": 1}),
])
async def test_malformed_declarations_never_fail_the_plan_or_cost_a_correction_call(entries, issues):
    hub, schemas = research_hub([plan_with(entries)], declare_on=True)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["outcome"] == "plan_approved" and [t.meta["kind"] for t in hub.calls] == ["plan"]
    assert "output_types" in schemas[0]["$defs"]["ResearchStep"]["properties"]
    assert "output_types" not in req["plan"]["steps"][0]
    assert req["output_types_stats"]["issues"] == issues  # 4 entries for 1 output: all dropped (too_many)
    assert CANARY not in json.dumps([req, hub.events, hub.approvals], ensure_ascii=False, default=str)


@pytest.mark.asyncio
async def test_on_freezes_valid_declarations_with_their_version_into_cp1():
    entries = [{"name": "result1.tsv", "data_type": "de_table", "format": "tsv"}]
    hub, _ = research_hub([plan_with(entries)], declare_on=True)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["plan"]["steps"][0]["output_types"] == [
        {"name": "outputs/result1.tsv", "data_type": "de_table", "format": "tsv", "vocab": V.sha256}]
    canonical = hub.approvals[0]["detail"]["plan_canonical"]
    assert json.loads(canonical)["steps"][0]["output_types"] == req["plan"]["steps"][0]["output_types"]
    assert hashlib.sha256(canonical.encode()).hexdigest() == req["research_contract"]["approval"]["target_sha256"]
    assert req["output_types_stats"] == {"outputs": 1, "data_declared": 1, "format_declared": 1, "issues": {},
                                         "vocab": V.sha256}


@pytest.mark.asyncio
async def test_turning_off_after_approval_keeps_the_approved_declarations_and_receipt():
    entries = [{"name": "result1.tsv", "data_type": "de_table"}]
    hub, _ = research_hub([plan_with(entries)], declare_on=True)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    approved = req["research_contract"]["plan_sha256"]
    hub.s.plan.declare_output_types = False
    req["status"] = "interrupted"
    await Orchestrator(hub).run_request("r", resume=True)
    assert len(hub.approvals) == 1  # the same hash: no second CP1
    assert req["research_contract"]["plan_sha256"] == approved
    assert req["research_contract"]["approval"]["status"] == "approved"
    assert req["plan"]["steps"][0]["output_types"][0]["data_type"] == "de_table"


@pytest.mark.asyncio
async def test_resume_under_a_reduced_max_steps_fails_and_keeps_the_approved_plan():
    """#282 on the research lane: a stored, PI-approved plan is never cut to a lower max_steps or sent to CP1 again."""
    hub, _ = research_hub([plan_with([])], declare_on=False)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    approved, steps = req["research_contract"]["plan_sha256"], copy.deepcopy(req["plan"]["steps"])
    hub.s.orchestrator.max_steps = len(steps) - 1
    req["status"] = "interrupted"
    await Orchestrator(hub).run_request("r", resume=True)
    assert req["status"] == "failed" and f"maximum is {len(steps) - 1}" in req["error"]
    assert len(hub.approvals) == 1 and req["plan"]["steps"] == steps
    assert req["research_contract"]["plan_sha256"] == approved
    assert req["research_contract"]["approval"]["status"] == "approved"


def test_pack_values_never_fill_a_declaration():
    entries, _ = declare.normalize_entries(["result1.tsv"], None, V)
    plan = cso.prepare_research_declarations(valid_plan(pack_values=valid_pack_values()), V, {})
    assert entries == [] and "output_types" not in plan["steps"][0]


def test_staff_type_fields_are_optional_and_a_bad_value_drops_only_the_field():
    base = {"schema_version": 2, "plan_sha256": "0" * 64, "step_id": "s1", "claims": [], "evidence": [], "links": [],
            "not_established": [], "failures": [], "method_changes": []}
    old = rc.ResearchResult.model_validate({**base, "artifact_refs": [{"artifact_id": "a", "path": "outputs/x.tsv"}]})
    assert old.model_dump(mode="json")["artifact_refs"] == [{"artifact_id": "a", "path": "outputs/x.tsv"}]
    typed = rc.ResearchResult.model_validate({**base, "artifact_refs": [
        {"artifact_id": "a", "path": "outputs/x.tsv", "data_type": "de_table", "format": {"x": 1}}]})
    assert typed.artifact_refs[0].data_type == "de_table" and typed.artifact_refs[0].format is None
    assert typed.model_dump(mode="json")["artifact_refs"][0] == {"artifact_id": "a", "path": "outputs/x.tsv",
                                                                  "data_type": "de_table"}
