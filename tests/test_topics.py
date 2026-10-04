"""Request topics are closed vocabulary keys and the only conditional-pack selector."""

import json

import pytest

from labhq import vocab
from labhq.models import TaskResult
from labhq.orchestrator import cso
from labhq.orchestrator.cso import Orchestrator
from labhq.research.contract import ResearchPlan
from labhq.vocab import topics
from tests.test_cso import FakeHub
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

    compact = topics.prompt_rule(V, include_definitions=False)
    assert "bulk_rna_seq" in compact and V.terms["bulk_rna_seq"].definition not in compact
    assert len(compact) < 800


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


@pytest.mark.asyncio
async def test_general_plan_keeps_topic_and_step_warnings_together():
    async def dispatch(task):
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured={
                "topics": ["bulk_rna_seq", "invented_topic"],
                "steps": [{"id": "A", "agent_id": "worker", "instruction": "write answer.md",
                           "outputs": ["answer.md"], "depends_on": []}],
                "clarifying_questions": [], "recruit": [], "notes": "",
            })
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="done")

    hub = FakeHub(dispatch)
    hub.s.orchestrator.reviewer_agent = None
    await Orchestrator(hub).run_request("r")

    warnings = hub.requests["r"]["plan"]["warnings"]
    assert "topics ignored (unknown_key 1)" in warnings
    assert any("moved under outputs/" in warning for warning in warnings)
    plan_event = next(event for event in hub.events if event["type"] == "request.plan")
    assert "topics ignored (unknown_key 1)" in plan_event["data"]["warnings"]

