import copy
import itertools
import json
from pathlib import Path

import pytest

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.research.contract import validate_research_plan
from labhq.research.packs import (configured_packs, pack_refs, pack_snapshot, render_pack_catalog,
                                  select_applied_packs)
from labhq.settings import Settings
from tests.test_research_protocol import PACK as SINGLE_CELL_PACK
from tests.test_research_protocol import valid_pack_values as valid_single_cell_values
from tests.test_research_protocol import MiniHub, valid_plan


BULK_PACK = "bulk_tumor_normal@1"
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


def valid_bulk_values():
    return {
        BULK_PACK: {
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
    plan = valid_plan(pack_values=values)
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


def test_only_the_pack_matching_applies_when_is_frozen_when_both_are_configured():
    configured = _selected(SINGLE_CELL_PACK, BULK_PACK)

    bulk_values = valid_bulk_values()
    bulk_values[SINGLE_CELL_PACK] = {"not_applicable": "The request uses bulk, not single-cell, expression."}
    bulk_plan = valid_plan(pack_values=bulk_values)
    bulk = select_applied_packs(configured, bulk_plan["pack_values"])
    assert list(bulk) == [BULK_PACK]
    _validate(bulk_plan, bulk)

    single_values = valid_single_cell_values()
    single_values[BULK_PACK] = {"not_applicable": "The request uses single-cell, not bulk, expression."}
    single_plan = valid_plan(pack_values=single_values)
    single = select_applied_packs(configured, single_plan["pack_values"])
    assert list(single) == [SINGLE_CELL_PACK]
    _validate(single_plan, single)


def test_every_configured_pack_requires_values_or_a_not_applicable_reason():
    configured = _selected(SINGLE_CELL_PACK, BULK_PACK)
    with pytest.raises(ValueError, match=f"missing.*{SINGLE_CELL_PACK}"):
        select_applied_packs(configured, valid_bulk_values())

    no_reason = valid_bulk_values()
    no_reason[SINGLE_CELL_PACK] = {"not_applicable": ""}
    with pytest.raises(ValueError, match="non-empty not_applicable reason"):
        select_applied_packs(configured, no_reason)


async def test_cso_prompt_exposes_both_packs_but_cp1_freezes_only_the_matching_one():
    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = [SINGLE_CELL_PACK, BULK_PACK]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None

    missing = valid_bulk_values()
    answered = valid_bulk_values()
    reason = "The request compares bulk tissue and has no single-cell measurements."
    answered[SINGLE_CELL_PACK] = {"not_applicable": reason}
    replies = [missing, answered]

    async def reply(task):
        assert "every configured pack" in task.prompt
        assert f'"key": "{SINGLE_CELL_PACK}"' in task.prompt
        assert f'"key": "{BULK_PACK}"' in task.prompt
        if len(replies) == 1:
            assert f"pack_values is missing configured packs: ['{SINGLE_CELL_PACK}']" in task.prompt
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured=valid_plan(pack_values=replies.pop(0)))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                  text="Compare bulk tumor and normal expression")
    await Orchestrator(hub).run_request("r")

    request = hub.requests["r"]
    assert request.get("outcome") == "plan_approved", json.dumps(request, default=str, indent=2)
    assert len(hub.calls) == 2
    assert request["plan"]["pack_values"][SINGLE_CELL_PACK] == {"not_applicable": reason}
    assert [row["id"] for row in request["plan"]["protocol"]["packs"]] == ["bulk_tumor_normal"]
    assert list(request["research_contract"]["pack_snapshot"]) == [BULK_PACK]
    assert json.loads(hub.approvals[0]["detail"]["plan_canonical"])["pack_values"][SINGLE_CELL_PACK] == {
        "not_applicable": reason,
    }


def test_pack_catalog_exposes_bulk_pack_values_keys_and_applicability():
    rows = {row["key"]: row for row in map(json.loads, render_pack_catalog(_selected(BULK_PACK)).splitlines())}
    row = rows[BULK_PACK]
    assert "bulk" in row["applies_when"].lower()
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
    source = Path("labhq/research/packs/bulk_tumor_normal.yaml")
    text = source.read_text(encoding="utf-8")
    assert "tumor" in text and "PMID" in text
