import copy
import itertools
import json
from pathlib import Path

import pytest

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.research.contract import freeze_plan, validate_research_plan
from labhq.research.packs import (assess_pack_applicability, configured_packs, pack_refs, pack_snapshot,
                                  render_pack_catalog, select_applied_packs, select_legacy_applied_packs)
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
    assert hub.requests["r"]["outcome"] == "plan_approved"
    assert hub.requests["r"]["plan"]["warnings"] == [warning]
    detail = hub.approvals[0]["detail"]
    assert detail["warnings"] == [warning]
    assert json.loads(detail["plan_canonical"])["warnings"] == [warning]


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
