"""Request topics are closed vocabulary keys and the only conditional-pack selector."""

import json

import pytest

from labhq import vocab
from labhq.orchestrator import cso
from labhq.research.contract import ResearchPlan
from labhq.vocab import topics
from tests.test_research_protocol import valid_plan


V = vocab.load()


def test_topics_are_sorted_deduplicated_and_prompted_with_definitions():
    normalized, unknown = topics.normalize(
        ["microarray_expression", "bulk_rna_seq", "microarray_expression"], V)
    assert normalized == ["bulk_rna_seq", "microarray_expression"]
    assert unknown == []
    rule = topics.prompt_rule(V)
    assert "bulk_rna_seq" in rule
    assert V.terms["bulk_rna_seq"].definition in rule
    assert "topics do not replace data, format, or operation" in rule


def test_general_lane_drops_unknown_topics_with_a_bounded_warning():
    plan = {"topics": ["bulk_rna_seq", "invented_topic", "bulk_rna_seq"], "warnings": []}
    normalized = cso.normalize_plan_topics(plan, V, strict=False)
    assert normalized["topics"] == ["bulk_rna_seq"]
    assert normalized["warnings"] == ["topics ignored (unknown_key 1)"]
    assert "invented_topic" not in json.dumps(normalized)


def test_research_lane_rejects_an_unknown_topic():
    plan = valid_plan(topics=["bulk_rna_seq", "invented_topic"])
    with pytest.raises(ValueError, match="unknown research topics.*invented_topic"):
        ResearchPlan.model_validate(plan)

