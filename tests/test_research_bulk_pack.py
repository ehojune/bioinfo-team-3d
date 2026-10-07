import copy
import itertools
import json
from pathlib import Path

import pytest
import yaml

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.research.contract import freeze_plan, validate_research_plan
from labhq.research.packs import (DomainRulePack, assess_pack_applicability, configured_packs, pack_refs,
                                  pack_snapshot, render_pack_catalog, select_applied_packs, select_legacy_applied_packs)
from labhq.settings import Settings
from tests.test_research_protocol import PACK as SINGLE_CELL_PACK
from tests.test_research_protocol import valid_pack_values as valid_single_cell_values
from tests.test_research_protocol import MiniHub, valid_plan


LEGACY_BULK_PACK = "bulk_tumor_normal@1"
BULK_PACK = "bulk_tumor_normal@2"
MODEL_FIELDS = ("expression_scale", "pairing", "primary_model")
ALLOWED_MODEL_COMBINATIONS = {
    ("raw_counts", "none", "count_glm_negative_binomial"),
    ("raw_counts", "partial", "paired_count_glm"),
    ("raw_counts", "complete", "paired_count_glm"),
    ("log2_normalized", "none", "unpaired"),
    ("log2_normalized", "partial", "mixed_model"),
    ("log2_normalized", "complete", "paired_t"),
    ("log2_normalized", "complete", "mixed_model"),
    ("other", "none", "unpaired"),
    ("other", "partial", "mixed_model"),
    ("other", "complete", "paired_t"),
    ("other", "complete", "mixed_model"),
}


def valid_bulk_values(pack_key=BULK_PACK):
    return {
        pack_key: {
            "fields": {
                "pairing": "complete",
                "pairing_evidence": "metadata.patient_id and tissue_type",
                "primary_model": "mixed_model",
                "model_rule": "use a paired model with at least 10 verified pairs",
                "expression_scale": "log2_normalized",
                "low_expression_filter": "remove probes below log2 intensity 5 in more than 80% of samples",
                "de_threshold": "FDR < 0.05 and absolute log2FC >= 1",
                "positive_controls": "EPCAM|up_in_first_condition|PMID:22028643",
            },
            "validators": {
                "bulk_tumor_normal.pairing_definition": "Pairing uses patient metadata, never expression similarity.",
                "bulk_tumor_normal.de_contract": "Scale, filter, and threshold are fixed before CP1.",
                "bulk_tumor_normal.positive_control_rows": "Every control names a gene, direction, and PMID.",
            },
            "acceptance": {
                "bulk_tumor_normal.core_statistics_applicable": "The comparison uses the core statistics contract.",
                "bulk_tumor_normal.core_estimand": "The core protocol names the condition effect.",
                "bulk_tumor_normal.core_analysis_unit": "The core protocol names the donor analysis unit.",
                "bulk_tumor_normal.core_comparison_groups": "The core protocol names case and control.",
                "bulk_tumor_normal.core_primary_outcomes": "The core protocol names expression.",
                "bulk_tumor_normal.core_multiple_testing": "The core protocol owns the FDR method.",
                "bulk_tumor_normal.core_sensitivity_analyses": "The core protocol owns sensitivity analyses.",
                "bulk_tumor_normal.model_compatibility": "The scale, pairing, and model row is allowed.",
            },
        }
    }


def _selected(*keys):
    settings = Settings()
    settings.research.active_packs = list(keys)
    return configured_packs(settings)


def _bulk_plan(**fields):
    selected = _selected(BULK_PACK)
    values = valid_bulk_values()
    values[BULK_PACK]["fields"].update(fields)
    plan = valid_plan(pack_values=values, topics=["bulk_rna_seq"])
    plan["brief"]["subject"] = "bulk tumor and normal tissue expression"
    return plan, selected


def _validate(plan, selected):
    plan["protocol"]["packs"] = pack_refs(selected)
    return validate_research_plan(plan, max_steps=2, active_packs=pack_snapshot(selected),
                                  pack_definitions=selected)


def test_bulk_pack_uses_core_statistics_as_the_only_source_for_overlapping_decisions():
    pack = _selected(BULK_PACK)[BULK_PACK].pack
    names = {field.name for field in pack.fields}
    assert not names & {"sample_unit", "multiple_testing", "sensitivity_analyses"}


@pytest.mark.parametrize("field", ["sample_unit", "multiple_testing", "sensitivity_analyses"])
def test_bulk_pack_rejects_legacy_duplicates_of_core_protocol_fields(field):
    plan, selected = _bulk_plan()
    plan["pack_values"][BULK_PACK]["fields"][field] = "conflicts with the core protocol"
    with pytest.raises(ValueError, match="undeclared fields"):
        _validate(plan, selected)


def test_bulk_pack_requires_applicable_core_statistics():
    plan, selected = _bulk_plan()
    plan["protocol"]["statistics"] = {
        "applicable": False,
        "reason": "incorrectly treated as descriptive",
        "estimand": None,
        "analysis_unit": None,
        "comparison_groups": [],
        "primary_outcomes": [],
        "multiple_testing": None,
        "missing_and_exclusions": None,
        "effect_size_and_interval": None,
        "sensitivity_analyses": [],
        "not_applicable": {},
    }
    with pytest.raises(ValueError, match="bulk_tumor_normal.core_statistics_applicable"):
        _validate(plan, selected)


@pytest.mark.parametrize(
    ("field", "empty", "waiver", "rule_id"),
    [
        ("comparison_groups", [], True, "bulk_tumor_normal.core_comparison_groups"),
        ("multiple_testing", None, True, "bulk_tumor_normal.core_multiple_testing"),
        ("sensitivity_analyses", [], False, "bulk_tumor_normal.core_sensitivity_analyses"),
    ],
)
def test_bulk_pack_requires_core_statistical_decisions_even_when_core_allows_a_waiver(
        field, empty, waiver, rule_id):
    plan, selected = _bulk_plan()
    plan["protocol"]["statistics"][field] = empty
    if waiver:
        plan["protocol"]["statistics"].setdefault("not_applicable", {})[field] = "waived in the generic core"
    with pytest.raises(ValueError, match=rule_id):
        _validate(plan, selected)


@pytest.mark.parametrize("groups", [["tumor"], [""], ["", "  "], ["Tumor", "tumor "]])
def test_bulk_pack_requires_two_distinct_named_comparison_groups(groups):
    """PR #366 review: `present` passed a single group or [""], so a plan without a control group reached CP1."""
    plan, selected = _bulk_plan()
    plan["protocol"]["statistics"]["comparison_groups"] = groups
    with pytest.raises(ValueError, match="bulk_tumor_normal.core_comparison_groups"):
        _validate(plan, selected)


def test_bulk_model_choices_are_one_closed_decision_table():
    rules = {rule.id: rule for rule in _selected(BULK_PACK)[BULK_PACK].pack.rules}
    table = rules["bulk_tumor_normal.model_compatibility"].allowed_combinations
    assert tuple(table.fields) == MODEL_FIELDS
    assert {tuple(row) for row in table.rows} == ALLOWED_MODEL_COMBINATIONS


@pytest.mark.parametrize(
    "combination",
    list(itertools.product(
        ("raw_counts", "log2_normalized", "other"),
        ("none", "partial", "complete"),
        ("paired_t", "mixed_model", "unpaired", "count_glm_negative_binomial", "paired_count_glm"),
    )),
)
def test_every_bulk_scale_pairing_model_cell_is_accepted_or_rejected_by_the_closed_table(combination):
    plan, selected = _bulk_plan(**dict(zip(MODEL_FIELDS, combination)))
    if combination in ALLOWED_MODEL_COMBINATIONS:
        _validate(plan, selected)
    else:
        with pytest.raises(ValueError):
            _validate(plan, selected)


def test_bulk_pack_loads_with_schema_review_questions_and_failure_fixtures():
    selected = _selected(BULK_PACK)
    pack = selected[BULK_PACK].pack
    assert pack.key == BULK_PACK
    assert [field.name for field in pack.fields] == [
        "pairing", "pairing_evidence", "primary_model", "model_rule", "expression_scale",
        "low_expression_filter", "de_threshold", "positive_controls",
    ]
    assert [fixture.id for fixture in pack.fixtures] == ["paired_but_unpaired", "filter_undefined"]
    assert len(pack.reviewer_questions) == 3


@pytest.mark.parametrize(
    ("fields", "failure"),
    [
        ({}, None),
        ({"pairing": "partial", "primary_model": "mixed_model"}, None),
        ({"pairing": "complete", "primary_model": "paired_t"}, None),
        ({"pairing": "partial", "primary_model": "unpaired"},
         "bulk_tumor_normal.model_compatibility"),
        ({"pairing": "complete", "primary_model": "unpaired"},
         "bulk_tumor_normal.model_compatibility"),
        ({"pairing": "none", "primary_model": "paired_t"},
         "bulk_tumor_normal.model_compatibility"),
        ({"pairing": "none", "primary_model": "mixed_model"},
         "bulk_tumor_normal.model_compatibility"),
        ({"pairing": "none", "primary_model": "unpaired"}, None),
        ({"expression_scale": "raw_counts", "primary_model": "paired_count_glm"}, None),
        ({"expression_scale": "raw_counts", "primary_model": "paired_t"},
         "bulk_tumor_normal.model_compatibility"),
        ({"expression_scale": "log2_normalized", "primary_model": "paired_count_glm"},
         "bulk_tumor_normal.model_compatibility"),
        ({"pairing": "partial", "expression_scale": "raw_counts", "primary_model": "paired_count_glm"}, None),
        ({"pairing": "partial", "expression_scale": "raw_counts", "primary_model": "count_glm_negative_binomial"},
         "bulk_tumor_normal.model_compatibility"),
    ],
)
def test_bulk_pairing_and_expression_scale_rules(fields, failure):
    plan, selected = _bulk_plan(**fields)
    if failure is None:
        _validate(plan, selected)
    else:
        with pytest.raises(ValueError, match=failure):
            _validate(plan, selected)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("low_expression_filter", ""),
        ("de_threshold", ""),
        ("positive_controls", ""),
        ("positive_controls", "EPCAM|up_in_first_condition"),
        ("positive_controls", "EPCAM|up_in_first_condition|DOI:10.1/example"),
    ],
)
def test_bulk_required_methods_and_positive_control_rows_are_rejected(field, value):
    plan, selected = _bulk_plan(**{field: value})
    with pytest.raises(ValueError, match=field):
        _validate(plan, selected)


@pytest.mark.parametrize("topics", [["bulk_rna_seq"], ["microarray_expression"],
                                      ["single_cell_rna_seq", "microarray_expression"]])
def test_bulk_pack_applies_only_when_a_bulk_expression_topic_matches(topics):
    configured = _selected(BULK_PACK)
    expected = bool({"bulk_rna_seq", "microarray_expression"} & set(topics))
    values = valid_bulk_values() if expected else {}
    applied = select_applied_packs(configured, values, topics=topics)
    assert bool(applied) is expected


def test_empty_topics_do_not_apply_conditional_pack_and_warn():
    configured = _selected(BULK_PACK)
    applied, decisions, warnings = assess_pack_applicability(configured, [])
    assert applied == {}
    assert decisions[BULK_PACK]["matched_topics"] == []
    assert warnings == ["topics is empty; topic-conditioned packs were not applied"]


def test_a_pack_without_a_topic_condition_keeps_values_or_a_not_applicable_reason():
    # PR #390 review: with the documented pair [single_cell_de@2, bulk_tumor_normal@2], a bulk study must not be
    # forced to fill and freeze single-cell rules; a sentence-condition pack keeps the pre-topic answer.
    configured = _selected(SINGLE_CELL_PACK, BULK_PACK)
    bulk = valid_bulk_values(BULK_PACK)
    waiver = {SINGLE_CELL_PACK: {"not_applicable": "bulk tissue study, no single-cell data"}}

    applied = select_applied_packs(configured, {**bulk, **waiver}, topics=["bulk_rna_seq"])
    assert set(applied) == {BULK_PACK}
    _applied, decisions, _ = assess_pack_applicability(configured, ["bulk_rna_seq"], {**bulk, **waiver})
    assert decisions[SINGLE_CELL_PACK] == {"applied": False, "topics_any": [], "matched_topics": [],
                                           "reason": "not_applicable"}

    both = select_applied_packs(configured, {**bulk, **valid_single_cell_values()}, topics=["bulk_rna_seq"])
    assert set(both) == {BULK_PACK, SINGLE_CELL_PACK}
    with pytest.raises(ValueError, match="missing applied packs"):
        select_applied_packs(configured, bulk, topics=["bulk_rna_seq"])
    with pytest.raises(ValueError, match="one non-empty not_applicable reason"):
        select_applied_packs(configured, {**bulk, SINGLE_CELL_PACK: {"not_applicable": " "}}, topics=["bulk_rna_seq"])
    with pytest.raises(ValueError, match="cannot be not_applicable after topic selection"):
        select_applied_packs(configured, {BULK_PACK: {"not_applicable": "x"}, **waiver}, topics=["bulk_rna_seq"])


def test_v1_hash_matches_main_and_legacy_condition_is_unconditional():
    loaded = _selected(LEGACY_BULK_PACK)[LEGACY_BULK_PACK]
    assert loaded.sha256 == "fe42d8e8734b507da27f855fad87e0c09e994f0b34cd78230eb8704c502665b7"
    applied, decisions, warnings = assess_pack_applicability({loaded.pack.key: loaded}, [])
    assert list(applied) == [loaded.pack.key]
    assert decisions[loaded.pack.key]["reason"] == "no_topic_condition"
    assert warnings == []


@pytest.mark.asyncio
async def test_approved_v1_request_resumes_with_its_frozen_pack_not_configured_v2():
    legacy = _selected(LEGACY_BULK_PACK)
    plan = valid_plan(pack_values=valid_bulk_values(LEGACY_BULK_PACK), topics=[])
    plan.pop("topics")  # Plans approved before topic routing did not carry this field.
    plan["protocol"]["packs"] = pack_refs(legacy)
    frozen = validate_research_plan(plan, max_steps=2, active_packs=pack_snapshot(legacy),
                                    pack_definitions=legacy).model_dump(mode="json")
    approval = freeze_plan(frozen, {"approved": True, "approval_id": "old-cp1", "decided_at": 1})

    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = [BULK_PACK]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None

    async def no_dispatch(task):
        raise AssertionError(f"approved plan-only resume dispatched {task.meta.get('kind')}")

    hub = MiniHub(settings, no_dispatch, mode="orchestrate", work_kind="research",
                  text="Resume the approved bulk study")
    hub.requests["r"].update(plan=frozen, research_contract={
        "schema_version": 1, "work_kind": "research", "execution_enabled": False,
        "plan_sha256": approval["target_sha256"], "pack_snapshot": pack_snapshot(legacy),
        "approval": approval,
    })
    await Orchestrator(hub).run_request("r", resume=True)

    assert hub.calls == []
    assert hub.requests["r"]["outcome"] == "plan_approved"
    assert hub.requests["r"]["research_contract"]["pack_snapshot"] == pack_snapshot(legacy)


@pytest.mark.asyncio
@pytest.mark.parametrize("applied", [True, False], ids=["applied-and-waived", "waived-only"])
async def test_pre_topic_contract_with_a_not_applicable_pack_resumes_by_its_own_rule(applied):
    # PR #390 review: a CP1 snapshot made before topic selection lists only the packs the plan applied, so a pack it
    # answered not_applicable is absent from the snapshot. The resume keeps that contract's rule and never gains the
    # topic-era pack_applicability record, so a later restart takes the same path.
    legacy = _selected(LEGACY_BULK_PACK) if applied else {}
    pack_values = {SINGLE_CELL_PACK: {"not_applicable": "bulk tissue study, no single-cell data"}}
    if applied:
        pack_values.update(valid_bulk_values(LEGACY_BULK_PACK))
    plan = valid_plan(pack_values=pack_values, topics=[])
    plan.pop("topics")
    plan["protocol"]["packs"] = pack_refs(legacy)
    frozen = validate_research_plan(plan, max_steps=2, active_packs=pack_snapshot(legacy),
                                    pack_definitions=legacy).model_dump(mode="json")
    approval = freeze_plan(frozen, {"approved": True, "approval_id": "old-cp1", "decided_at": 1})

    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = [BULK_PACK]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None

    async def no_dispatch(task):
        raise AssertionError(f"approved plan-only resume dispatched {task.meta.get('kind')}")

    hub = MiniHub(settings, no_dispatch, mode="orchestrate", work_kind="research",
                  text="Resume the approved bulk study")
    hub.requests["r"].update(plan=frozen, research_contract={
        "schema_version": 1, "work_kind": "research", "execution_enabled": False,
        "plan_sha256": approval["target_sha256"], "pack_snapshot": pack_snapshot(legacy),
        "approval": approval,
    })
    await Orchestrator(hub).run_request("r", resume=True)

    assert hub.calls == []
    assert hub.requests["r"]["outcome"] == "plan_approved"
    assert "pack_applicability" not in hub.requests["r"]["research_contract"]


@pytest.mark.asyncio
async def test_topic_era_contract_with_a_waived_sentence_pack_resumes_by_its_snapshot():
    # PR #390 review: a contract made after topics that waived a sentence-condition pack froze only the applied
    # bulk pack; the resume must follow that snapshot instead of deciding applicability again.
    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = [SINGLE_CELL_PACK, BULK_PACK]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    values = {**valid_bulk_values(BULK_PACK),
              SINGLE_CELL_PACK: {"not_applicable": "bulk tissue study, no single-cell data"}}

    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured=valid_plan(pack_values=values, topics=["bulk_rna_seq"]))

    first = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="Compare bulk expression")
    await Orchestrator(first).run_request("r")
    planned = first.requests["r"]
    assert planned.get("outcome") == "plan_approved", json.dumps(planned, default=str, indent=2)
    assert list(planned["research_contract"]["pack_snapshot"]) == [BULK_PACK]
    assert planned["research_contract"]["pack_applicability"][SINGLE_CELL_PACK]["reason"] == "not_applicable"

    async def no_dispatch(task):
        raise AssertionError(f"approved plan-only resume dispatched {task.meta.get('kind')}")

    hub = MiniHub(settings, no_dispatch, mode="orchestrate", work_kind="research", text="Compare bulk expression")
    hub.requests["r"].update(plan=copy.deepcopy(planned["plan"]),
                             research_contract=copy.deepcopy(planned["research_contract"]))
    await Orchestrator(hub).run_request("r", resume=True)

    assert hub.calls == []
    assert hub.requests["r"]["outcome"] == "plan_approved"
    assert hub.requests["r"]["research_contract"]["pack_applicability"] == \
        planned["research_contract"]["pack_applicability"]


def test_legacy_pack_selection_keeps_the_one_reason_waiver_rule():
    frozen = _selected(LEGACY_BULK_PACK)
    values = valid_bulk_values(LEGACY_BULK_PACK)[LEGACY_BULK_PACK]
    waiver = {"not_applicable": "not this study"}
    assert set(select_legacy_applied_packs(frozen, {LEGACY_BULK_PACK: values, SINGLE_CELL_PACK: waiver})) == \
        {LEGACY_BULK_PACK}
    for bad, message in [
        ({SINGLE_CELL_PACK: waiver}, "missing frozen packs"),
        ({LEGACY_BULK_PACK: waiver}, "cannot waive a pack frozen as applied"),
        ({LEGACY_BULK_PACK: values, SINGLE_CELL_PACK: {"fields": {}}}, "do not apply"),
        ({LEGACY_BULK_PACK: values, SINGLE_CELL_PACK: {"not_applicable": " "}}, "do not apply"),
        ({LEGACY_BULK_PACK: values, SINGLE_CELL_PACK: {"not_applicable": "x", "extra": 1}}, "do not apply"),
    ]:
        with pytest.raises(ValueError, match=message):
            select_legacy_applied_packs(frozen, bad)


def test_every_configured_pack_requires_values_or_a_not_applicable_reason():
    configured = _selected(BULK_PACK)
    with pytest.raises(ValueError, match=f"missing.*{BULK_PACK}"):
        select_applied_packs(configured, {}, topics=["bulk_rna_seq"])


async def test_cso_uses_topics_to_freeze_the_matching_pack_and_its_basis():
    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = [BULK_PACK]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None

    async def reply(task):
        assert "applied packs only" in task.prompt
        assert f'"key": "{BULK_PACK}"' in task.prompt
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured=valid_plan(pack_values=valid_bulk_values(), topics=["microarray_expression"]))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                  text="Compare bulk tumor and normal expression")
    await Orchestrator(hub).run_request("r")

    request = hub.requests["r"]
    assert request.get("outcome") == "plan_approved", json.dumps(request, default=str, indent=2)
    assert len(hub.calls) == 1
    assert [row["id"] for row in request["plan"]["protocol"]["packs"]] == ["bulk_tumor_normal"]
    assert list(request["research_contract"]["pack_snapshot"]) == [BULK_PACK]
    decision = request["plan"]["pack_applicability"][BULK_PACK]
    assert decision["applied"] and decision["matched_topics"] == ["microarray_expression"]
    assert request["research_contract"]["pack_applicability"] == request["plan"]["pack_applicability"]


async def test_cso_spellings_of_pack_and_checklist_keys_reach_cp1_without_a_correction():
    # The v0.5 trial CSO wrote the pack id without @version and checklist answers as topic.id (2026-10-07).
    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = [BULK_PACK]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None

    async def reply(task):
        plan = valid_plan(pack_values={"bulk_tumor_normal": valid_bulk_values()[BULK_PACK]},
                          topics=["microarray_expression"])
        plan["checklist"] = {f"microarray_expression.{key}" if index % 2 else f"microarray_expression/{key}": value
                             for index, (key, value) in enumerate(plan["checklist"].items())}
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=plan)

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                  text="Compare bulk tumor and normal expression")
    await Orchestrator(hub).run_request("r")

    request = hub.requests["r"]
    assert request.get("outcome") == "plan_approved", json.dumps(request, default=str, indent=2)
    assert len(hub.calls) == 1
    assert list(request["plan"]["pack_values"]) == [BULK_PACK]
    assert set(request["plan"]["checklist"]) == {"batch", "pairing", "gene_set_test", "independent_validation",
                                                 "probe_mapping"}


async def test_wrong_pack_key_correction_names_the_applied_pack_not_an_empty_snapshot():
    # A wrong key once made the correction say "must equal the configured snapshot: []"; the CSO then dropped its
    # pack values and the request failed (v0.5 trial, 2026-10-07).
    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = [BULK_PACK]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None
    prompts = []

    async def reply(task):
        prompts.append(task.prompt)
        key = "bulk_tumor_normal@9" if len(prompts) == 1 else BULK_PACK
        plan = valid_plan(pack_values={key: valid_bulk_values()[BULK_PACK]}, topics=["microarray_expression"])
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=plan)

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                  text="Compare bulk tumor and normal expression")
    await Orchestrator(hub).run_request("r")

    assert len(prompts) == 2
    correction = prompts[1][prompts[1].index("The previous research PLAN failed validation"):]
    assert f"the applied pack keys are ['{BULK_PACK}']" in correction
    assert f"configured snapshot: ['{BULK_PACK}']" in correction
    assert "configured snapshot: []" not in correction
    assert hub.requests["r"].get("outcome") == "plan_approved"


def test_key_normalization_keeps_exact_keys_and_ambiguous_ids():
    from labhq.research.packs import normalize_pack_keys
    from labhq.vocab.topic_checklists import ChecklistItem, normalize_answers

    two_versions = _selected(LEGACY_BULK_PACK, BULK_PACK)
    assert normalize_pack_keys(two_versions, {"bulk_tumor_normal": {}}) == {"bulk_tumor_normal": {}}
    one = _selected(BULK_PACK)
    assert normalize_pack_keys(one, {"bulk_tumor_normal": 1}) == {BULK_PACK: 1}
    assert normalize_pack_keys(one, {"bulk_tumor_normal": 1, BULK_PACK: 2}) == {"bulk_tumor_normal": 1, BULK_PACK: 2}

    catalog = {"microarray_expression": [ChecklistItem("batch", "c", "w", "microarray_expression")],
               "bulk_rna_seq": [ChecklistItem("batch", "c2", "w2", "bulk_rna_seq")]}
    topics = ["microarray_expression"]
    assert normalize_answers({"microarray_expression.batch": "step:s1"}, topics, catalog) == {"batch": "step:s1"}
    # An exact answer leaves the alias as an extra key; validation reads only the exact one.
    both = {"batch": "step:s2", "microarray_expression/batch": "step:s1"}
    assert normalize_answers(both, topics, catalog) == both
    # Only a declared topic's alias is re-keyed; anything else is left for validation to report.
    assert normalize_answers({"bulk_rna_seq.batch": "step:s1"}, topics, catalog) == {"bulk_rna_seq.batch": "step:s1"}
    # Two declared topics share the id: the same answer merges, different answers are not silently dropped.
    two = ["microarray_expression", "bulk_rna_seq"]
    same = {"microarray_expression.batch": "step:s1", "bulk_rna_seq.batch": "step:s1"}
    assert normalize_answers(same, two, catalog) == {"batch": "step:s1"}
    split = {"microarray_expression.batch": "step:s1", "bulk_rna_seq.batch": "step:s2"}
    assert normalize_answers(split, two, catalog) == split
    from labhq.vocab.topic_checklists import answer_errors, requirements
    assert answer_errors(normalize_answers(split, two, catalog), requirements(two, catalog), ["s1", "s2"])


async def test_empty_topics_warning_is_frozen_and_visible_on_the_cp1_card():
    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = [BULK_PACK]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None

    async def reply(task):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured=valid_plan(pack_values={}, topics=[]))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="Inspect expression data")
    await Orchestrator(hub).run_request("r")
    warning = "topics is empty; topic-conditioned packs were not applied"
    precedent_warning = "analysis precedents unavailable (agent_unavailable)"
    assert hub.requests["r"]["outcome"] == "plan_approved"
    assert hub.requests["r"]["plan"]["warnings"] == [warning, precedent_warning]
    detail = hub.approvals[0]["detail"]
    assert detail["warnings"] == [warning, precedent_warning]
    assert json.loads(detail["plan_canonical"])["warnings"] == [warning, precedent_warning]


def test_pack_catalog_exposes_bulk_pack_values_keys_and_applicability():
    rows = {row["key"]: row for row in map(json.loads, render_pack_catalog(_selected(BULK_PACK)).splitlines())}
    row = rows[BULK_PACK]
    assert row["applies_when"] == {
        "description": "Bulk microarray or bulk RNA-seq expression compares two tissues or conditions, such as tumor versus normal.",
        "topics_any": ["bulk_rna_seq", "microarray_expression"],
    }
    assert row["pack_values_keys"]["fields"] == [
        "pairing", "pairing_evidence", "primary_model", "model_rule", "expression_scale",
        "low_expression_filter", "de_threshold", "positive_controls",
    ]
    assert row["pack_values_keys"]["acceptance"] == [
        "bulk_tumor_normal.core_statistics_applicable",
        "bulk_tumor_normal.core_estimand",
        "bulk_tumor_normal.core_analysis_unit",
        "bulk_tumor_normal.core_comparison_groups",
        "bulk_tumor_normal.core_primary_outcomes",
        "bulk_tumor_normal.core_multiple_testing",
        "bulk_tumor_normal.core_sensitivity_analyses",
        "bulk_tumor_normal.model_compatibility",
    ]


def test_pack_files_are_read_as_utf8_on_every_platform():
    for name in ("bulk_tumor_normal.yaml", "bulk_tumor_normal_v2.yaml"):
        text = (Path("labhq/research/packs") / name).read_text(encoding="utf-8")
        assert "tumor" in text and "PMID" in text


SINGLE_CELL_V3 = "single_cell_de@3"


def _single_cell_v3_values():
    return {SINGLE_CELL_V3: valid_single_cell_values()[SINGLE_CELL_PACK]}


@pytest.mark.parametrize("topics, expected", [
    (["single_cell_rna_seq"], {SINGLE_CELL_V3}),
    (["bulk_rna_seq"], {BULK_PACK}),
    (["single_cell_rna_seq", "bulk_rna_seq"], {SINGLE_CELL_V3, BULK_PACK}),
    ([], set()),
])
def test_single_cell_v3_and_bulk_v2_apply_by_topic_without_waivers(topics, expected):
    # #370: a single-cell request gets the single-cell rules and a bulk one does not, with no not_applicable
    # answers to write, because both current versions carry a topic condition.
    configured = _selected(SINGLE_CELL_V3, BULK_PACK)
    values = {**(_single_cell_v3_values() if SINGLE_CELL_V3 in expected else {}),
              **(valid_bulk_values(BULK_PACK) if BULK_PACK in expected else {})}
    assert set(select_applied_packs(configured, values, topics=topics)) == expected


def test_single_cell_v3_keeps_v2_rules_and_v2_stays_for_resumes():
    catalog = configured_packs(Settings(), [SINGLE_CELL_PACK, SINGLE_CELL_V3])
    v2, v3 = catalog[SINGLE_CELL_PACK].pack, catalog[SINGLE_CELL_V3].pack
    assert isinstance(v2.applies_when, str)  # approved @2 contracts resume unchanged
    assert v3.applies_when.topics_any == ["single_cell_rna_seq"]
    assert v3.applies_when.description == v2.applies_when
    assert [rule.id for rule in v3.rules] == [rule.id for rule in v2.rules]
    assert [field.name for field in v3.fields] == [field.name for field in v2.fields]


def _bulk_pack_with_table(extra_fields, column, cells):
    raw = yaml.safe_load((Path("labhq/research/packs") / "bulk_tumor_normal_v2.yaml").read_text(encoding="utf-8"))
    raw["fields"] += extra_fields
    raw["rules"].append({"id": "bulk_tumor_normal.extra_table", "description": "Extra closed table.",
                         "allowed_combinations": {"fields": ["pairing", column],
                                                  "rows": [["none", cell] for cell in cells]}})
    return raw


MIN_PAIRS = {"name": "min_pairs", "description": "Pairs needed.", "value_type": "integer", "minimum": 3}
FLAG = {"name": "flag", "description": "Optional flag.", "value_type": "boolean", "required": False}


@pytest.mark.parametrize("extra, column, cell, detail", [
    ([MIN_PAIRS], "min_pairs", "3", "min_pairs must be integer"),
    ([MIN_PAIRS], "min_pairs", True, "min_pairs must be integer"),
    ([MIN_PAIRS], "min_pairs", 2, "min_pairs must be at least 3"),
    ([FLAG], "flag", 1, "flag must be boolean"),
    ([], "positive_controls", "TP53", "positive_controls must match"),
    ([], "pairing_evidence", None, "pairing_evidence cannot be null"),
    ([FLAG], "flag", None, "flag cannot be null"),
    ([], "primary_model", "paired", "primary_model must be one of"),
])
def test_combination_cells_no_valid_answer_can_match_are_rejected_at_load(extra, column, cell, detail):
    # PR #366 review P2: a cell of the wrong type, pattern or minimum is a row no plan can ever pass.
    with pytest.raises(ValueError, match=f"combination value .* is not allowed for {column} \({detail}"):
        DomainRulePack.model_validate(_bulk_pack_with_table(extra, column, [cell]))


def test_combination_cells_that_a_valid_answer_can_match_still_load():
    assert DomainRulePack.model_validate(_bulk_pack_with_table([MIN_PAIRS], "min_pairs", [3, 4]))
    assert DomainRulePack.model_validate(_bulk_pack_with_table([FLAG], "flag", [True, False]))


@pytest.mark.parametrize("column, cell", [
    ("brief.study_type", "bogus"),
    ("protocol.statistics.applicable", 1),
    ("protocol.revision", True),
    ("protocol.statistics.comparison_groups", "tumor"),
    ("brief.subject", None),
    ("brief.subject", ""),
    ("protocol.revision", 0),
])
def test_core_plan_field_cells_outside_the_plan_schema_are_rejected_at_load(column, cell):
    # PR #403 review P2: a core PLAN field's cell is checked against the PLAN schema type, not skipped.
    with pytest.raises(ValueError, match=f"is not allowed for {column} \({column} does not fit the PLAN schema"):
        DomainRulePack.model_validate(_bulk_pack_with_table([], column, [cell]))


def test_core_plan_field_cells_a_plan_can_hold_still_load():
    assert DomainRulePack.model_validate(_bulk_pack_with_table([], "brief.study_type", ["comparative", "technical"]))
    assert DomainRulePack.model_validate(_bulk_pack_with_table([], "protocol.statistics.applicable", [True, False]))
    assert DomainRulePack.model_validate(_bulk_pack_with_table([], "brief.primary_hypothesis", [None, "H1"]))


BULK_V3 = "bulk_tumor_normal@3"


def _bulk_v3_plan(**fields):
    selected = _selected(BULK_V3)
    values = valid_bulk_values(BULK_V3)
    values[BULK_V3]["fields"].update({"pairing_evidence": "geo_characteristics;sample_title", **fields})
    values[BULK_V3]["acceptance"]["bulk_tumor_normal.pairing_from_metadata"] = "Pairing names a metadata source."
    plan = valid_plan(pack_values=values, topics=["bulk_rna_seq"])
    plan["brief"]["subject"] = "bulk tumor and normal tissue expression"
    return plan, selected


@pytest.mark.parametrize("fields, failure", [
    ({}, None),
    ({"pairing_evidence": "supplementary_table"}, None),
    ({"pairing": "none", "primary_model": "unpaired", "pairing_evidence": "none"}, None),
    ({"pairing": "none", "primary_model": "unpaired", "pairing_evidence": "sample_title"}, None),
    ({"pairing_evidence": "metadata.patient_id and tissue_type"}, "pairing_evidence must match"),
    ({"pairing_evidence": "expression_correlation"}, "pairing_evidence must match"),
    ({"pairing_evidence": "none;sample_title"}, "pairing_evidence must match"),
    ({"pairing_evidence": "none"}, "bulk_tumor_normal.pairing_from_metadata"),
    ({"pairing": "partial", "pairing_evidence": "none"}, "bulk_tumor_normal.pairing_from_metadata"),
])
def test_bulk_v3_pairing_evidence_is_a_closed_metadata_source_list(fields, failure):
    # #369 2: pairing evidence named expression similarity in free text passed v2; v3 takes only metadata sources,
    # and partial or complete pairing must name one.
    plan, selected = _bulk_v3_plan(**fields)
    if failure is None:
        _validate(plan, selected)
    else:
        with pytest.raises(ValueError, match=failure):
            _validate(plan, selected)


def test_bulk_v3_keeps_v2_rules_and_topics_and_v2_stays_for_resumes():
    catalog = configured_packs(Settings(), [BULK_PACK, BULK_V3])
    v2, v3 = catalog[BULK_PACK].pack, catalog[BULK_V3].pack
    assert v3.applies_when.topics_any == v2.applies_when.topics_any
    assert [rule.id for rule in v3.rules] == [*(rule.id for rule in v2.rules[:-1]),
                                              "bulk_tumor_normal.pairing_from_metadata", v2.rules[-1].id]
    assert [field.name for field in v3.fields] == [field.name for field in v2.fields]
    v2_evidence = next(field for field in v2.fields if field.name == "pairing_evidence")
    assert v2_evidence.pattern is None  # approved @2 contracts resume with the free-text field
