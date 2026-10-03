import copy
import json

import pytest
from pydantic import ValidationError

from labhq.adapters.base import RunContext, RunState
from labhq.adapters.codex import CodexAdapter
from labhq.evidence.claims import Quantity
from labhq.models import AgentSpec, Engine, Task
from labhq.orchestrator.cso import (PLAN_SCHEMA, RESEARCH_LANE_REVIEW_SCHEMA, REVIEW_SCHEMA,
                                    plan_schema, replan_schema)
from labhq.recruit.paper2agent import OFFER_SCHEMA
from labhq.research.contract import (RESEARCH_PLAN_SCHEMA, RESEARCH_RESULT_SCHEMA, RESEARCH_STEP_SCHEMA,
                                     ResearchResult, research_plan_schema)
from labhq.research.review import RESEARCH_REVIEW_SCHEMA
from labhq.util import openai_strict_schema, strip_optional_nulls
from labhq.vocab.declare import ENTRY_SCHEMA
from labhq.settings import Settings


UNSUPPORTED_TYPE_CONSTRAINTS = {
    "minLength", "maxLength", "pattern", "format",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minItems", "maxItems",
}


def _objects(value):
    if isinstance(value, dict):
        if value.get("type") == "object" or "properties" in value:
            yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _schema_keys(value, *, schema_node=True):
    if isinstance(value, dict):
        if schema_node:
            yield from value
        for key, child in value.items():
            if key in {"properties", "$defs", "definitions"} and isinstance(child, dict):
                for schema in child.values():
                    yield from _schema_keys(schema)
            elif schema_node:
                yield from _schema_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _schema_keys(child)


@pytest.mark.parametrize("schema", [
    PLAN_SCHEMA, plan_schema(True), replan_schema(False), replan_schema(True), REVIEW_SCHEMA,
    RESEARCH_PLAN_SCHEMA, research_plan_schema(True, ENTRY_SCHEMA), RESEARCH_RESULT_SCHEMA,
    RESEARCH_STEP_SCHEMA, RESEARCH_LANE_REVIEW_SCHEMA, RESEARCH_REVIEW_SCHEMA, OFFER_SCHEMA,
])
def test_every_engine_output_schema_converts_to_openai_strict_without_mutation(schema):
    original = copy.deepcopy(schema)
    converted = openai_strict_schema(schema)

    assert schema == original
    for node in _objects(converted):
        properties = node.get("properties", {})
        assert node["additionalProperties"] is False
        assert node["required"] == list(properties)
    assert not any("default" in node for node in _objects(converted))


@pytest.mark.parametrize("schema", [
    PLAN_SCHEMA, plan_schema(True), replan_schema(False), replan_schema(True), REVIEW_SCHEMA,
    RESEARCH_PLAN_SCHEMA, research_plan_schema(True, ENTRY_SCHEMA), RESEARCH_RESULT_SCHEMA,
    RESEARCH_STEP_SCHEMA, RESEARCH_LANE_REVIEW_SCHEMA, RESEARCH_REVIEW_SCHEMA, OFFER_SCHEMA,
])
def test_engine_output_schema_omits_unsupported_type_constraints(schema):
    assert UNSUPPORTED_TYPE_CONSTRAINTS.isdisjoint(_schema_keys(openai_strict_schema(schema)))


def test_format_property_name_is_not_treated_as_schema_keyword():
    converted = openai_strict_schema(research_plan_schema(True, ENTRY_SCHEMA))
    output_types = converted["$defs"]["ResearchStep"]["properties"]["output_types"]

    assert "format" in output_types["anyOf"][0]["items"]["properties"]


def test_optional_array_uses_supported_anyof_transport():
    converted = openai_strict_schema(RESEARCH_STEP_SCHEMA)
    assumptions = converted["$defs"]["Comparison"]["properties"]["assumptions"]

    assert assumptions["anyOf"][0]["type"] == "array"
    assert assumptions["anyOf"][1] == {"type": "null"}


def test_original_pydantic_validation_keeps_removed_transport_constraints():
    converted = openai_strict_schema(RESEARCH_STEP_SCHEMA)
    invalid = {
        "schema_version": 2, "plan_sha256": "a" * 64, "step_id": "",
        "claims": [], "evidence": [], "links": [], "artifact_refs": [],
        "not_established": [], "failures": [], "method_changes": [],
    }

    assert RESEARCH_STEP_SCHEMA["properties"]["step_id"]["minLength"] == 1
    assert "minLength" not in converted["properties"]["step_id"]
    with pytest.raises(ValidationError, match="at least 1 character"):
        ResearchResult.model_validate(invalid)


@pytest.mark.parametrize("schema", [RESEARCH_LANE_REVIEW_SCHEMA, OFFER_SCHEMA])
def test_already_strict_schema_is_unchanged(schema):
    assert openai_strict_schema(schema) == schema


def test_dictionary_schema_round_trips_nested_values_through_refs():
    schema = {
        "$defs": {
            "Value": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "metadata": {"type": "object", "additionalProperties": {"type": "integer"}},
                },
                "required": ["label", "metadata"],
            },
        },
        "type": "object",
        "properties": {
            "values": {"type": "object", "additionalProperties": {"$ref": "#/$defs/Value"}},
        },
        "required": ["values"],
    }
    response = {"values": [{"key": "sample", "value": {
        "label": "kept", "metadata": [{"key": "depth", "value": 7}],
    }}]}

    converted = openai_strict_schema(schema)
    values = converted["properties"]["values"]
    nested = converted["$defs"]["Value"]["properties"]["metadata"]
    values_entry = converted["$defs"][values["items"]["$ref"].rsplit("/", 1)[-1]]

    assert values["type"] == "array" and nested["type"] == "array"
    assert values_entry["properties"]["value"] == {"$ref": "#/$defs/Value"}
    assert strip_optional_nulls(response, schema) == {
        "values": {"sample": {"label": "kept", "metadata": {"depth": 7}}},
    }


def test_dictionary_transport_rejects_duplicate_keys():
    schema = {"type": "object", "additionalProperties": {"type": "string"}}
    response = [{"key": "same", "value": "first"}, {"key": "same", "value": "second"}]

    with pytest.raises(ValueError, match="duplicate dictionary key 'same'"):
        strip_optional_nulls(response, schema)


def test_mixed_fixed_and_arbitrary_object_is_rejected_explicitly():
    schema = {
        "type": "object",
        "properties": {"fixed": {"type": "string"}},
        "required": ["fixed"],
        "additionalProperties": {"type": "integer"},
    }

    with pytest.raises(ValueError, match="properties and schema-valued additionalProperties"):
        openai_strict_schema(schema)


def test_quantity_unknown_reasons_survive_dictionary_transport():
    schema = {
        "$defs": {"Quantity": RESEARCH_STEP_SCHEMA["$defs"]["Quantity"]},
        "type": "object",
        "properties": {"quantity": {"$ref": "#/$defs/Quantity"}},
        "required": ["quantity"],
    }
    response = {"quantity": {
        "id": "q1", "measure": "reported effect", "value": None, "unit": None,
        "conditions": None, "denominator": None, "method": None, "uncertainty": None,
        "unknown": [
            {"key": "value", "value": "the source did not report an estimate"},
            {"key": "unit", "value": "the scale cannot be interpreted"},
            {"key": "conditions", "value": "the assay context is unavailable"},
            {"key": "denominator", "value": "the sample base is unavailable"},
        ],
    }}

    cleaned = strip_optional_nulls(response, schema)
    parsed = Quantity.model_validate(cleaned["quantity"])

    assert parsed.unknown["value"] == "the source did not report an estimate"


def test_optional_nulls_are_removed_recursively_but_required_nulls_remain():
    schema = {
        "$defs": {"Child": {"type": "object", "properties": {
            "name": {"type": "string"}, "note": {"type": "string"}}, "required": ["name"]}},
        "type": "object",
        "properties": {
            "required": {"type": "string"},
            "optional_ref": {"$ref": "#/$defs/Child"},
            "children": {"type": "array", "items": {"$ref": "#/$defs/Child"}},
        },
        "required": ["required", "children"],
    }
    response = {
        "required": None,
        "optional_ref": None,
        "children": [{"name": "kept", "note": None}],
    }

    cleaned = strip_optional_nulls(response, schema)

    assert cleaned == {"required": None, "children": [{"name": "kept"}]}
    assert response["optional_ref"] is None


def test_research_result_optional_nulls_clean_before_original_model_validation():
    response = {
        "schema_version": 2, "plan_sha256": "a" * 64, "step_id": "s1",
        "claims": [], "evidence": [], "links": [], "artifact_refs": [],
        "not_established": [], "failures": [], "method_changes": [],
    }
    strict = openai_strict_schema(RESEARCH_RESULT_SCHEMA)
    strict_response = copy.deepcopy(response)
    strict_response["blocking_decision"] = None
    # Codex must emit the optional fields because the strict transport schema requires them.
    strict_response["claims"] = [{
        "id": "c1", "revision": 1, "statement": "candidate mechanism", "scope": "public cohort",
        "kind": "hypothesis", "status": "proposed", "importance": "minor",
        "status_reason": "requires testing", "limitations": None, "supersedes": None,
        "comparisons": None,
    }]
    assert set(strict["$defs"]["Claim"]["required"]) == set(strict["$defs"]["Claim"]["properties"])

    cleaned = strip_optional_nulls(strict_response, RESEARCH_STEP_SCHEMA)
    parsed = ResearchResult.model_validate(cleaned)

    assert "blocking_decision" not in cleaned
    assert parsed.claims[0].limitations == []
    assert parsed.claims[0].supersedes is None
    with pytest.raises(ValidationError):
        ResearchResult.model_validate({**cleaned, "step_id": None})


def test_codex_adapter_writes_strict_copy_and_cleans_its_structured_result(tmp_path):
    schema = {"type": "object", "properties": {
        "required": {"type": "string"}, "optional": {"type": "string", "default": "fallback"},
    }, "required": ["required"]}
    original = copy.deepcopy(schema)
    task = Task(agent_id="worker", prompt="q", output_schema=schema)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.codex,
                      builtin_mcp=[], system_prompt="You are the worker.")

    async def emit(*_args):
        pass

    ctx = RunContext(task=task, agent=agent, workdir=tmp_path, settings=Settings(), mcp_servers=[],
                     env={}, emit=emit, prompt="q")
    adapter = CodexAdapter(ctx.settings)
    adapter.prepare(ctx)
    written = json.loads((tmp_path / ".labhq" / "output_schema.json").read_text(encoding="utf-8"))
    result = adapter.finalize(
        RunState(final_text=json.dumps({"required": "kept", "optional": None}), result_seen=True), ctx, 0)

    assert written == openai_strict_schema(schema)
    assert result.structured == {"required": "kept"}
    assert schema == original
