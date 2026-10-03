import copy
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


def valid_bulk_values():
    return {
        BULK_PACK: {
            "fields": {
                "sample_unit": "one tissue sample from one patient",
                "pairing": "complete",
                "pairing_evidence": "metadata.patient_id and tissue_type",
                "primary_model": "mixed_model",
                "model_rule": "use a paired model with at least 10 verified pairs",
                "expression_scale": "log2_normalized",
                "low_expression_filter": "remove probes below log2 intensity 5 in more than 80% of samples",
                "de_threshold": "FDR < 0.05 and absolute log2FC >= 1",
                "multiple_testing": "Benjamini-Hochberg FDR",
                "positive_controls": "EPCAM|up_in_first_condition|PMID:22028643",
                "sensitivity_analyses": "repeat with all labeled samples and with the paired subset only",
            },
            "validators": {
                "bulk_tumor_normal.pairing_definition": "Pairing uses patient metadata, never expression similarity.",
                "bulk_tumor_normal.de_contract": "Scale, filter, threshold, and multiplicity are fixed before CP1.",
                "bulk_tumor_normal.positive_control_rows": "Every control names a gene, direction, and PMID.",
            },
            "acceptance": {
                "bulk_tumor_normal.partial_mixed_model": "Not active because pairing is complete.",
                "bulk_tumor_normal.complete_paired_model": "The mixed model preserves complete pairing.",
                "bulk_tumor_normal.raw_counts_count_model": "Not active because input is log2 normalized.",
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


def test_bulk_pack_loads_with_schema_review_questions_and_failure_fixtures():
    selected = _selected(BULK_PACK)
    pack = selected[BULK_PACK].pack
    assert pack.key == BULK_PACK
    assert [field.name for field in pack.fields] == [
        "sample_unit", "pairing", "pairing_evidence", "primary_model", "model_rule",
        "expression_scale", "low_expression_filter", "de_threshold", "multiple_testing",
        "positive_controls", "sensitivity_analyses",
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
         "bulk_tumor_normal.partial_mixed_model"),
        ({"pairing": "complete", "primary_model": "unpaired"},
         "bulk_tumor_normal.complete_paired_model"),
        ({"expression_scale": "raw_counts"}, "bulk_tumor_normal.raw_counts_count_model"),
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

    bulk_plan = valid_plan(pack_values=valid_bulk_values())
    bulk = select_applied_packs(configured, bulk_plan["pack_values"])
    assert list(bulk) == [BULK_PACK]
    _validate(bulk_plan, bulk)

    single_plan = valid_plan(pack_values=valid_single_cell_values())
    single = select_applied_packs(configured, single_plan["pack_values"])
    assert list(single) == [SINGLE_CELL_PACK]
    _validate(single_plan, single)


async def test_cso_prompt_exposes_both_packs_but_cp1_freezes_only_the_matching_one():
    settings = Settings()
    settings.research.enabled = True
    settings.research.active_packs = [SINGLE_CELL_PACK, BULK_PACK]
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.reviewer_agent = None

    async def reply(task):
        assert "whose `applies_when` matches this request" in task.prompt
        assert f'"key": "{SINGLE_CELL_PACK}"' in task.prompt
        assert f'"key": "{BULK_PACK}"' in task.prompt
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured=valid_plan(pack_values=valid_bulk_values()))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research",
                  text="Compare bulk tumor and normal expression")
    await Orchestrator(hub).run_request("r")

    request = hub.requests["r"]
    assert request["outcome"] == "plan_approved"
    assert list(request["plan"]["pack_values"]) == [BULK_PACK]
    assert [row["id"] for row in request["plan"]["protocol"]["packs"]] == ["bulk_tumor_normal"]
    assert list(request["research_contract"]["pack_snapshot"]) == [BULK_PACK]


def test_pack_catalog_exposes_bulk_pack_values_keys_and_applicability():
    rows = {row["key"]: row for row in map(json.loads, render_pack_catalog(_selected(BULK_PACK)).splitlines())}
    row = rows[BULK_PACK]
    assert "bulk" in row["applies_when"].lower()
    assert row["pack_values_keys"]["fields"] == [
        "sample_unit", "pairing", "pairing_evidence", "primary_model", "model_rule",
        "expression_scale", "low_expression_filter", "de_threshold", "multiple_testing",
        "positive_controls", "sensitivity_analyses",
    ]
    assert row["pack_values_keys"]["acceptance"] == [
        "bulk_tumor_normal.partial_mixed_model",
        "bulk_tumor_normal.complete_paired_model",
        "bulk_tumor_normal.raw_counts_count_model",
    ]


def test_pack_files_are_read_as_utf8_on_every_platform():
    source = Path("labhq/research/packs/bulk_tumor_normal.yaml")
    text = source.read_text(encoding="utf-8")
    assert "tumor" in text and "PMID" in text
