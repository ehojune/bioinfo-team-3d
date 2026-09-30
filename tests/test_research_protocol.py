import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator, PLAN_SCHEMA
from labhq.research.contract import (RESEARCH_PLAN_SCHEMA, ResearchResult, classify_intake,
                                     freeze_plan, refresh_plan_approval, validate_research_plan)
from labhq.research.packs import configured_packs, load_pack_catalog, pack_snapshot, select_packs
from labhq.settings import Settings


def valid_pack_values():
    return {
        "single_cell_de@1": {
            "fields": {
                "donor_id": "metadata.donor_id",
                "condition": "metadata.condition",
                "batch": "metadata.library_batch",
                "count_scale": "raw_counts",
                "independent_replicates": 4,
                "replicate_definition": "one biological donor",
                "model": "pseudobulk",
                "model_rationale": "The count model consumes raw counts and preserves donor independence.",
                "batch_design": "identifiable",
                "conclusion_mode": "condition_effect",
            },
            "validators": {
                "single_cell_de.donor_unit": "Aggregate cells within each donor before inference.",
                "single_cell_de.model_assumption": "Use a raw-count model with donor and batch terms.",
            },
            "acceptance": {
                "single_cell_de.donor_model": "Use donor-level pseudobulk fixed before CP1.",
                "single_cell_de.confounded_conclusion_mode": "Not active because the design is identifiable.",
                "single_cell_de.confounded_study_type": "Not active because the design is identifiable.",
                "single_cell_de.confounded_hypothesis": "Not active because the design is identifiable.",
                "single_cell_de.confounded_statistics": "Not active because the design is identifiable.",
                "single_cell_de.confounded_estimand": "Not active because the design is identifiable.",
            },
        }
    }


def valid_plan(packs=None, steps=1, pack_values=None):
    return {
        "schema_version": 2,
        "intake": {"work_kind": "research", "reason": "PI specified work_kind=research",
                   "scope_status": "in_scope", "confidence": "clear", "source": "explicit"},
        "brief": {"question": "Does condition change expression?", "purpose": "Choose the next assay",
                  "subject": "public single-cell cohort", "scope": "donor-level comparison",
                  "deliverables": ["ranked findings"], "completion_conditions": ["QC and effect interval reported"],
                  "study_type": "comparative", "primary_hypothesis": "condition changes expression",
                  "null_or_alternatives": ["no change", "batch explains the change"],
                  "distinguishing_observations": ["donor-level effect after batch adjustment"]},
        "protocol": {"revision": 1, "analysis_unit": "donor", "selection_criteria": ["eligible donor"],
                     "exclusion_criteria": ["failed QC"], "comparators": ["case", "control"],
                     "primary_metrics": ["log fold change"], "validation_methods": ["held-out donor QC"],
                     "resource_limits": ["one local planning call"], "stop_conditions": ["design not identifiable"],
                     "approval_conditions": ["CP1 before execution"], "not_applicable": {},
                     "data_boundaries": ["public metadata and counts only; no controlled raw data leaves its zone"],
                     "statistics": {"applicable": True, "reason": "group comparison", "estimand": "condition effect",
                                    "analysis_unit": "donor", "comparison_groups": ["case", "control"],
                                    "primary_outcomes": ["expression"], "multiple_testing": "FDR",
                                    "missing_and_exclusions": "pre-specified", "effect_size_and_interval": "estimate and CI",
                                    "sensitivity_analyses": ["batch-adjusted"]},
                     "packs": packs or []},
        "pack_values": pack_values or {},
        "clarifying_questions": [],
        "steps": [{"id": f"s{i + 1}", "agent_id": "worker", "instruction": f"work {i + 1}",
                   "phase": "analysis", "claim_ids": [f"c{i + 1}"], "input_refs": ["public-input"],
                   "outputs": [f"result{i + 1}.tsv"], "checks": ["donor-level QC"],
                   "evidence_slots": [{"id": f"e{i + 1}", "required": True, "description": "direct result"}],
                   "depends_on": []} for i in range(steps)],
        "recruit": [], "notes": "frozen before execution",
    }


class MiniHub:
    def __init__(self, settings, reply, *, mode="direct", work_kind="auto", text="convert this file"):
        self.s = settings
        self.requests = {"r": {"text": text, "mode": mode, "agent_id": "worker", "work_kind": work_kind,
                               "scope_status": "in_scope", "budget_usd": 10, "project_dirs": []}}
        self.agents = {name: {"id": name, "name": name, "role": "test", "engine": "mock"}
                       for name in ("cso", "worker")}
        self.reply = reply
        self.calls = []
        self.approvals = []
        self.events = []

    async def dispatch(self, task):
        self.calls.append(task)
        return await self.reply(task)

    async def publish(self, event):
        self.events.append(event)

    async def request_approval(self, **kwargs):
        self.approvals.append(kwargs)
        return {"approved": True, "note": "approved", "approval_id": "appr_test", "decided_at": 1.0}

    def supports_resume(self, agent_id):
        return False

    def result_map(self, rid):
        return {}

    def save_request(self, rid):
        pass


def test_intake_classifies_simple_research_and_ambiguous():
    assert classify_intake("TSV를 CSV로 형식 변환해 줘").work_kind == "simple"
    assert classify_intake("후보 바이오마커를 선택해 줘").work_kind == "research"
    ambiguous = classify_intake("이 자료를 살펴봐 줘")
    assert ambiguous.work_kind == "research" and ambiguous.confidence == "ambiguous"
    assert classify_intake("anything", "simple").source == "explicit"


def test_research_plan_and_result_contracts_are_strict():
    plan = valid_plan()
    parsed = validate_research_plan(plan, max_steps=2, active_packs={})
    assert parsed.protocol.analysis_unit == "donor"
    assert {"intake", "brief", "protocol", "steps"} <= set(RESEARCH_PLAN_SCHEMA["required"])
    with pytest.raises(ValidationError, match="completion_conditions"):
        broken = copy.deepcopy(plan)
        del broken["brief"]["completion_conditions"]
        validate_research_plan(broken, max_steps=2, active_packs={})
    with pytest.raises(ValueError, match="re-plan without truncation"):
        validate_research_plan(valid_plan(steps=2), max_steps=1, active_packs={})
    with pytest.raises(ValidationError, match="plan_sha256"):
        ResearchResult.model_validate({"schema_version": 2, "step_id": "s1", "findings": [], "evidence": [],
                                       "artifact_refs": [], "not_established": [], "failures": [],
                                       "method_changes": []})


def test_plan_approval_is_invalidated_by_any_contract_change():
    plan = valid_plan()
    receipt = freeze_plan(plan, {"approved": True, "approval_id": "a1", "note": "ok", "decided_at": 1})
    assert refresh_plan_approval(plan, receipt)["status"] == "approved"
    changed = copy.deepcopy(plan)
    changed["protocol"]["primary_metrics"] = ["odds ratio"]
    refreshed = refresh_plan_approval(changed, receipt)
    assert refreshed["status"] == "needs_reapproval" and refreshed["reapproval_required"]
    assert refreshed["target_sha256"] != refreshed["current_sha256"]


def test_builtin_pack_loads_with_hash_and_conflicts_fail(tmp_path):
    settings = Settings()
    settings.research.active_packs = ["single_cell_de@1"]
    selected = configured_packs(settings)
    snapshot = pack_snapshot(selected)
    assert list(snapshot) == ["single_cell_de@1"] and len(snapshot["single_cell_de@1"]) == 64
    pack = selected["single_cell_de@1"].pack
    assert pack.core_contract == "extend_only" and pack.sources[0].license

    base = pack.model_dump(mode="json")
    other = copy.deepcopy(base)
    other["id"] = "other_pack"
    other["validators"][0]["requirement"] = "Treat every cell as independent."
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(other, sort_keys=False), encoding="utf-8")
    catalog = load_pack_catalog([tmp_path])
    with pytest.raises(ValueError, match="conflict on validator"):
        select_packs(catalog, ["single_cell_de@1", "other_pack@1"])


def test_active_pack_requires_fields_validators_and_acceptance_before_cp1():
    settings = Settings()
    settings.research.active_packs = ["single_cell_de@1"]
    selected = configured_packs(settings)
    refs = [{"id": loaded.pack.id, "version": loaded.pack.version, "sha256": loaded.sha256}
            for loaded in selected.values()]
    snapshot = pack_snapshot(selected)
    complete = valid_plan(refs, pack_values=valid_pack_values())
    validate_research_plan(complete, max_steps=2, active_packs=snapshot, pack_definitions=selected)

    for field in ("donor_id", "batch", "count_scale"):
        broken = copy.deepcopy(complete)
        del broken["pack_values"]["single_cell_de@1"]["fields"][field]
        with pytest.raises(ValueError, match=field):
            validate_research_plan(broken, max_steps=2, active_packs=snapshot, pack_definitions=selected)

    invalid = copy.deepcopy(complete)
    invalid["pack_values"]["single_cell_de@1"]["fields"]["count_scale"] = "unknown_scale"
    with pytest.raises(ValueError, match="count_scale"):
        validate_research_plan(invalid, max_steps=2, active_packs=snapshot, pack_definitions=selected)

    for section in ("validators", "acceptance"):
        broken = copy.deepcopy(complete)
        broken["pack_values"]["single_cell_de@1"][section] = {}
        with pytest.raises(ValueError, match=section):
            validate_research_plan(broken, max_steps=2, active_packs=snapshot, pack_definitions=selected)


def test_confounded_single_cell_plan_cannot_claim_a_condition_effect():
    settings = Settings()
    settings.research.active_packs = ["single_cell_de@1"]
    selected = configured_packs(settings)
    refs = [{"id": loaded.pack.id, "version": loaded.pack.version, "sha256": loaded.sha256}
            for loaded in selected.values()]
    plan = valid_plan(refs, pack_values=valid_pack_values())
    plan["pack_values"]["single_cell_de@1"]["fields"]["batch_design"] = "fully_confounded_not_identifiable"

    with pytest.raises(ValueError, match="single_cell_de.confounded_conclusion_mode"):
        validate_research_plan(plan, max_steps=2, active_packs=pack_snapshot(selected),
                               pack_definitions=selected)

    plan["pack_values"]["single_cell_de@1"]["fields"]["conclusion_mode"] = "descriptive_only"
    plan["brief"]["study_type"] = "exploratory"
    plan["brief"]["primary_hypothesis"] = None
    plan["protocol"]["statistics"]["applicable"] = False
    plan["protocol"]["statistics"]["estimand"] = None
    validate_research_plan(plan, max_steps=2, active_packs=pack_snapshot(selected),
                           pack_definitions=selected)

    violations = [
        (("brief", "primary_hypothesis"), "condition changes expression",
         "single_cell_de.confounded_hypothesis"),
        (("protocol", "statistics", "applicable"), True,
         "single_cell_de.confounded_statistics"),
        (("protocol", "statistics", "estimand"), "condition effect",
         "single_cell_de.confounded_estimand"),
    ]
    for path, value, rule_id in violations:
        broken = copy.deepcopy(plan)
        target = broken
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        if rule_id == "single_cell_de.confounded_statistics":
            broken["protocol"]["statistics"]["estimand"] = "condition effect"
        with pytest.raises(ValueError, match=rule_id):
            validate_research_plan(broken, max_steps=2, active_packs=pack_snapshot(selected),
                                   pack_definitions=selected)


@pytest.mark.parametrize(
    ("predicate", "message"),
    [
        ({"field": "batch_design", "equals": "fully_confounded_not_identifiable"}, "equals"),
        ({"field": "unknown_pack_field", "value": "x"}, "unknown_pack_field"),
    ],
)
def test_pack_loader_rejects_unknown_rule_operators_and_fields(tmp_path, predicate, message):
    source = Path("labhq/research/packs/single_cell_de.yaml")
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["rules"] = [{
        "id": "single_cell_de.invalid_syntax",
        "description": "Invalid rule used to exercise loader validation.",
        "when": predicate,
        "require": {"field": "brief.study_type", "in": ["exploratory", "technical"]},
    }]
    (tmp_path / "invalid.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises((ValidationError, ValueError), match=message):
        load_pack_catalog([tmp_path])


@pytest.mark.asyncio
async def test_default_off_and_enabled_simple_direct_keep_one_call_and_same_result():
    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="same result")

    off = Settings()
    hub_off = MiniHub(off, reply, work_kind="research", text="form a new hypothesis")
    await Orchestrator(hub_off).run_request("r")
    on = Settings()
    on.research.enabled = True
    hub_simple = MiniHub(on, reply, work_kind="simple", text="convert this file")
    await Orchestrator(hub_simple).run_request("r")
    assert [task.meta["kind"] for task in hub_off.calls] == ["direct"]
    assert [task.meta["kind"] for task in hub_simple.calls] == ["direct"]
    assert hub_off.requests["r"]["report"] == hub_simple.requests["r"]["report"] == "same result"
    assert hub_off.calls[0].output_schema is hub_simple.calls[0].output_schema is None


@pytest.mark.asyncio
async def test_research_direct_is_rejected_without_dispatch():
    async def reply(task):
        raise AssertionError("research direct must not dispatch")

    settings = Settings()
    settings.research.enabled = True
    hub = MiniHub(settings, reply, work_kind="research", text="test a hypothesis")
    await Orchestrator(hub).run_request("r")
    assert hub.calls == []
    assert hub.requests["r"]["status"] == "failed" and hub.requests["r"]["outcome"] == "needs_research"


@pytest.mark.asyncio
async def test_simple_plan_only_uses_legacy_schema_and_does_not_dispatch_steps():
    async def reply(task):
        assert task.meta["kind"] == "plan" and task.output_schema == PLAN_SCHEMA
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="legacy plan",
                          structured={"clarifying_questions": [], "steps": [{"id": "s1", "agent_id": "worker",
                                      "instruction": "convert", "outputs": ["out.csv"], "depends_on": []}],
                                      "recruit": [], "notes": ""})

    settings = Settings()
    settings.research.enabled = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    hub = MiniHub(settings, reply, mode="plan_only", work_kind="simple", text="convert TSV to CSV")
    await Orchestrator(hub).run_request("r")
    assert [task.meta["kind"] for task in hub.calls] == ["plan"]
    assert hub.requests["r"]["status"] == "done" and hub.requests["r"]["outcome"] == "plan_only"


@pytest.mark.asyncio
async def test_research_plan_replans_over_limit_then_freezes_without_employee_dispatch():
    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = ["single_cell_de@1"]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    settings.orchestrator.max_steps = 1
    selected = configured_packs(settings)
    refs = [{"id": loaded.pack.id, "version": loaded.pack.version, "sha256": loaded.sha256}
            for loaded in selected.values()]
    replies = [valid_plan(refs, steps=2), valid_plan(refs, steps=1, pack_values=valid_pack_values())]

    async def reply(task):
        assert task.meta["kind"] == "plan"
        assert task.output_schema == RESEARCH_PLAN_SCHEMA and task.output_schema != PLAN_SCHEMA
        assert "PR 1 pilot stops after CP1 approval" in task.prompt
        assert refs[0]["sha256"] in task.prompt
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=replies.pop(0))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")
    await Orchestrator(hub).run_request("r")
    request = hub.requests["r"]
    assert [task.meta["kind"] for task in hub.calls] == ["plan", "plan"], request
    assert len(request["plan"]["steps"]) == 1
    assert request["status"] == "done" and request["outcome"] == "plan_approved"
    assert request["research_contract"]["execution_enabled"] is False
    assert request["research_contract"]["approval"]["target_sha256"] == request["research_contract"]["plan_sha256"]
    assert request["research_contract"]["approval"]["request_id"] == "r"
    assert request["research_contract"]["approval"]["protocol_revision"] == 1
    assert hub.approvals[0]["kind"] == "research_plan" and len(hub.approvals[0]["summary"]) <= 700
    canonical = hub.approvals[0]["detail"]["plan_canonical"]
    assert json.loads(canonical) == request["plan"]
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == request["research_contract"]["plan_sha256"]
    assert not any(task.meta["kind"] == "step" for task in hub.calls)


@pytest.mark.asyncio
async def test_invalid_pack_values_replan_before_cp1():
    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = ["single_cell_de@1"]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    selected = configured_packs(settings)
    refs = [{"id": loaded.pack.id, "version": loaded.pack.version, "sha256": loaded.sha256}
            for loaded in selected.values()]
    incomplete = valid_plan(refs, pack_values=valid_pack_values())
    del incomplete["pack_values"]["single_cell_de@1"]["fields"]["donor_id"]
    replies = [incomplete, valid_plan(refs, pack_values=valid_pack_values())]

    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=replies.pop(0))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")
    await Orchestrator(hub).run_request("r")
    assert [task.meta["kind"] for task in hub.calls] == ["plan", "plan"]
    assert len(hub.approvals) == 1
    assert hub.requests["r"]["status"] == "done" and hub.requests["r"]["outcome"] == "plan_approved"


@pytest.mark.asyncio
async def test_budget_denial_after_research_plan_correction_stops_before_cp1():
    settings = Settings()
    settings.research.enabled = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    invalid = valid_plan()
    invalid["steps"] = []
    replies = [invalid, valid_plan()]

    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured=replies.pop(0), cost_usd=0.6)

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")
    hub.requests["r"]["budget_usd"] = 1.0

    async def approval(**kwargs):
        hub.approvals.append(kwargs)
        if kwargs["kind"] == "budget":
            return {"approved": False, "note": "denied", "approval_id": "budget_no", "decided_at": 1.0}
        return {"approved": True, "note": "approved", "approval_id": "appr_test", "decided_at": 1.0}

    hub.request_approval = approval
    await Orchestrator(hub).run_request("r")

    assert [task.meta["kind"] for task in hub.calls] == ["plan", "plan"]
    assert [item["kind"] for item in hub.approvals] == ["budget"]
    assert hub.requests["r"]["status"] == "failed"
    assert "예산 승인 거부" in hub.requests["r"]["report"]
