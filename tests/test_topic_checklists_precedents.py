from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest

from labhq.models import TaskResult
from labhq.orchestrator import cso
from labhq.orchestrator.cso import Orchestrator
from labhq.settings import Settings
from labhq.vocab import topic_checklists
from tests.test_cso import FakeHub
from tests.test_research_protocol import MiniHub, valid_plan


PRECEDENTS = {
    "papers": [
        {"title": "Paired expression best practices", "citations": ["PMID:12345678"]},
        {"title": "Gene-set testing review", "citations": ["https://example.org/review"]},
    ],
    "required": [
        {"analysis": "preserve pairing in gene-set tests", "why": "avoid an invalid null",
         "citations": ["PMID:12345678"]},
    ],
    "recommended": [
        {"analysis": "validate in another cohort", "why": "test portability",
         "citations": ["https://example.org/review"]},
    ],
    "limitations": [],
}


def general_plan(*, checklist=None, suggested_next=None, topics=None):
    return {
        "scope": {"verdict": "in", "reason": "bioinformatics"},
        "topics": ["bulk_rna_seq"] if topics is None else topics,
        "clarifying_questions": [],
        "assumptions": [],
        "checklist": checklist or {},
        "suggested_next": suggested_next or [],
        "steps": [{"id": "A", "agent_id": "worker", "instruction": "analyze",
                   "outputs": [], "depends_on": []}],
        "recruit": [],
        "notes": "",
    }


def bulk_answers():
    return {
        "batch": "step:A",
        "pairing": "step:A",
        "gene_set_test": "step:A",
        "independent_validation": "not_applicable: no independent public cohort",
    }


def test_briefing_prompts_require_original_methods_and_raise_the_word_limit():
    assert "≤400 words" in cso.BRIEFING_PROMPT
    assert "original study" in cso.BRIEFING_PROMPT
    assert all(term in cso.BRIEFING_PROMPT for term in (
        "normalization", "paired", "covariates", "batch", "statistical model", "validation"))
    staff = Path("agents/core/chief_of_staff.yaml").read_text(encoding="utf-8")
    assert "400 words" in staff and "original study" in staff and "PMC" in staff
    assert "reference, not a template" in cso.PLAN_PROMPT
    assert "reference, not a template" in cso.RESEARCH_PLAN_PROMPT


def test_topic_checklist_loads_known_topics_and_rejects_an_unknown_key(tmp_path):
    loaded = topic_checklists.load()
    assert [item.id for item in loaded["microarray_expression"]][-1] == "probe_mapping"
    assert {item.id for item in loaded["single_cell_rna_seq"]} == {"pseudobulk", "qc", "batch"}
    bad = tmp_path / "bad.yaml"
    bad.write_text("unknown_assay:\n  - id: x\n    check: x\n    why: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown topic checklist key.*unknown_assay"):
        topic_checklists.load(bad)


def test_one_id_under_two_declared_topics_keeps_both_checks(tmp_path):
    path = tmp_path / "shared.yaml"
    path.write_text("atac_seq:\n  - id: library_qc\n    check: report FRiP\n    why: noise\n"
                    "  - id: peaks\n    check: call peaks\n    why: regions\n"
                    "chip_seq:\n  - id: library_qc\n    check: report NRF and PBC\n    why: duplicates\n"
                    "  - id: peaks\n    check: call peaks\n    why: regions\n", encoding="utf-8")
    loaded = topic_checklists.load(path)
    required = topic_checklists.requirements(["atac_seq", "chip_seq"], loaded)
    assert [(item.id, item.check, item.why) for item in required] == [
        ("library_qc", "report FRiP / report NRF and PBC", "noise / duplicates"),
        ("peaks", "call peaks", "regions")]
    assert [item.check for item in topic_checklists.requirements(["chip_seq"], loaded)] == \
        ["report NRF and PBC", "call peaks"]


def test_checklist_requirements_and_answer_contract_are_topic_scoped():
    loaded = topic_checklists.load()
    assert topic_checklists.requirements([], loaded) == []
    assert topic_checklists.requirements(["not_a_topic"], loaded) == []
    assert [item.id for item in topic_checklists.requirements(["atac_seq"], loaded)] == \
        [item.id for item in loaded["atac_seq"]]
    required = topic_checklists.requirements(["bulk_rna_seq"], loaded)
    assert [item.id for item in required] == ["batch", "pairing", "gene_set_test", "independent_validation"]
    assert topic_checklists.answer_errors({}, required, ["A"])
    assert topic_checklists.answer_errors({**bulk_answers(), "batch": "step:missing"}, required, ["A"])
    assert topic_checklists.answer_errors(bulk_answers(), required, ["A"]) == []
    answers = {**bulk_answers(), "pairing": "assumption: metadata identifies pairs"}
    # Bench C (#373): only assumptions are limitations; a check answered not_applicable does not apply at all.
    assert topic_checklists.limitations(answers) == ["pairing: metadata identifies pairs"]
    assert topic_checklists.not_applicable(answers) == ["independent_validation"]


class ParallelHub(FakeHub):
    def __init__(self, *, precedent_agent="lit_scout", precedent_result=None, plans=None):
        self.started: set[str] = set()
        self.release = asyncio.Event()
        self.precedent_result = precedent_result or TaskResult(
            task_id="precedent", agent_id="lit_scout", ok=True, structured=PRECEDENTS)
        self.plans = list(plans or [general_plan(
            checklist={**bulk_answers(), "precedent.1": "step:A"},
            suggested_next=["Validate in another cohort — precedent review recommendation"]
        )])

        async def dispatch(task):
            kind = task.meta["kind"]
            if kind in {"briefing", "precedent"}:
                self.started.add(kind)
                if len(self.started) == 2:
                    self.release.set()
                await self.release.wait()
                if kind == "briefing":
                    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="brief")
                return self.precedent_result.model_copy(update={"task_id": task.id, "agent_id": task.agent_id})
            if kind == "plan":
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                                  structured=self.plans.pop(0))
            raise AssertionError(kind)

        super().__init__(dispatch)
        self.s.orchestrator.chief_of_staff_agent = "chief_of_staff"
        self.s.orchestrator.precedent_agent = precedent_agent
        self.s.orchestrator.reviewer_agent = None
        self.requests["r"]["mode"] = "plan_only"
        self.agents.update({
            "chief_of_staff": {"id": "chief_of_staff", "name": "chief", "role": "brief", "engine": "mock"},
            "lit_scout": {"id": "lit_scout", "name": "lit", "role": "precedent", "engine": "mock"},
        })


@pytest.mark.asyncio
async def test_briefing_and_precedent_dispatch_in_parallel_and_store_structured_result():
    hub = ParallelHub()
    running = asyncio.create_task(Orchestrator(hub).run_request("r"))
    await asyncio.wait_for(hub.release.wait(), timeout=1)
    assert hub.started == {"briefing", "precedent"}
    await running
    record = hub.requests["r"]["analysis_precedents"]
    assert record["status"] == "ok" and record["required"][0]["id"] == "precedent.1"
    plan = next(task for task in hub.calls if task.meta["kind"] == "plan")
    assert "Analysis precedents" in plan.prompt
    assert "precedent.1" in plan.prompt and "validate in another cohort" in plan.prompt
    assert "bulk_rna_seq" in plan.prompt and "gene_set_test" in plan.prompt
    assert [task.meta["kind"] for task in hub.calls[:2]] == ["briefing", "precedent"] or set(
        task.meta["kind"] for task in hub.calls[:2]) == {"briefing", "precedent"}


@pytest.mark.asyncio
async def test_precedent_none_skips_stage_and_empty_topics_add_no_checklist_requirement():
    hub = ParallelHub(precedent_agent=None, plans=[general_plan(topics=[], checklist={}, suggested_next=[])])
    hub.release.set()
    await Orchestrator(hub).run_request("r")
    assert "precedent" not in [task.meta["kind"] for task in hub.calls]
    assert "analysis_precedents" not in hub.requests["r"]
    assert not hub.requests["r"]["plan"].get("warnings")


@pytest.mark.asyncio
async def test_precedent_parse_failure_warns_and_planning_continues():
    failed = TaskResult(task_id="p", agent_id="lit_scout", ok=True, text="not json")
    hub = ParallelHub(precedent_result=failed, plans=[general_plan(
        checklist=bulk_answers(), suggested_next=[])])
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "done"
    assert hub.requests["r"]["analysis_precedents"]["status"] == "warning"
    assert any("precedent" in warning for warning in hub.requests["r"]["plan"]["warnings"])


@pytest.mark.asyncio
async def test_saved_precedents_are_not_dispatched_again_after_restart():
    hub = ParallelHub(plans=[general_plan(
        checklist={**bulk_answers(), "precedent.1": "step:A"}, suggested_next=[])])
    hub.release.set()
    stored = copy.deepcopy(PRECEDENTS)
    stored["status"] = "ok"
    stored["required"][0]["id"] = "precedent.1"
    hub.requests["r"]["analysis_precedents"] = stored
    await Orchestrator(hub).run_request("r", resume=True)
    assert "precedent" not in [task.meta["kind"] for task in hub.calls]


@pytest.mark.asyncio
async def test_general_missing_checklist_gets_one_correction_then_runs():
    plans = [general_plan(checklist={}), general_plan(checklist=bulk_answers())]
    hub = ParallelHub(precedent_agent=None, plans=plans)
    hub.release.set()
    await Orchestrator(hub).run_request("r")
    assert [task.meta["kind"] for task in hub.calls].count("plan") == 2
    assert hub.requests["r"]["plan"]["checklist"] == bulk_answers()


@pytest.mark.asyncio
async def test_general_still_missing_checklist_warns_after_one_correction():
    hub = ParallelHub(precedent_agent=None, plans=[general_plan(checklist={}), general_plan(checklist={})])
    hub.release.set()
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "done"
    assert any("checklist unanswered after correction" in warning
               for warning in hub.requests["r"]["plan"]["warnings"])


@pytest.mark.asyncio
async def test_research_missing_checklist_is_a_plan_validation_error_after_correction():
    settings = Settings()
    settings.research.enabled = True
    settings.orchestrator.chief_of_staff_agent = None
    settings.orchestrator.precedent_agent = None
    settings.orchestrator.reviewer_agent = None
    plans = [valid_plan(), valid_plan()]
    for plan in plans:
        plan.pop("checklist")

    async def reply(task):
        assert task.meta["kind"] == "plan"
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=plans.pop(0))

    hub = MiniHub(settings, reply, mode="orchestrate", work_kind="research", text="compare conditions")
    await Orchestrator(hub).run_request("r")
    assert [task.meta["kind"] for task in hub.calls] == ["plan", "plan"]
    assert hub.requests["r"]["status"] == "failed" and hub.requests["r"]["outcome"] == "plan_invalid"
    assert "checklist" in " ".join(hub.requests["r"]["plan_validation"]["errors"])


def test_plan_schemas_offer_checklist_and_suggested_next_without_requiring_them():
    for schema in (cso.PLAN_SCHEMA, cso.RESEARCH_PLAN_SCHEMA):
        assert "checklist" in schema["properties"]
        assert schema["properties"]["suggested_next"]["maxItems"] == 8
        assert "checklist" not in schema["required"] and "suggested_next" not in schema["required"]


def test_review_report_and_solo_contexts_carry_requirements_answers_and_citations():
    record = cso.normalize_precedents(PRECEDENTS)
    plan = general_plan(
        checklist={**bulk_answers(), "pairing": "assumption: metadata identifies pairs",
                   "precedent.1": "step:A"},
        suggested_next=["Validate in another cohort — https://example.org/review"],
    )
    catalog = topic_checklists.load()
    review = cso.plan_review_context(plan, catalog, record)
    report = cso.plan_report_context(plan, catalog, record)

    assert all(value in review for value in ("batch", "gene_set_test", "precedent.1", "step:A",
                                               "PMID:12345678", "https://example.org/review"))
    assert "pairing: metadata identifies pairs" in report
    limits_part, _, skipped_part = report.partition("Checks that do not apply")
    assert "independent_validation" not in limits_part and "independent_validation" in skipped_part
    assert "leave them out of the report body" in skipped_part
    assert "Analysis precedents" in report
    assert "선행 연구 기준" in cso.SOLO_PROMPT
    assert "선행 연구 기준" in cso.SYNTH_PROMPT and "선행 연구 기준" in cso.RESEARCH_SYNTH_PROMPT
    assert "declared topic checklist" in cso.REVIEW_PROMPT
    assert "declared topic checklist" in cso.RESEARCH_REVIEW_PROMPT


def test_an_empty_precedent_search_is_a_warning_that_keeps_its_reasons():
    # PR #395 review: all-empty arrays are schema-valid; stored as ok they would drop precedent checks silently.
    record = cso.normalize_precedents({"papers": [], "required": [], "recommended": [],
                                       "limitations": ["PubMed was unreachable"]})
    assert record["status"] == "warning" and "no_papers" in record["warning"]
    assert record["limitations"] == ["PubMed was unreachable"]
    assert "unavailable" in cso.analysis_precedents_text(record)


class EarlyEndHub(FakeHub):
    """Briefing fails at once while the literature scout is still running (PR #395 review)."""

    def __init__(self, *, runner_answers_cancel):
        self.cancel_sent: list[str] = []
        self.scout_cancelled = asyncio.Event()
        self.scout_reaped = False
        cancel_arrived = asyncio.Event()

        async def dispatch(task):
            kind = task.meta["kind"]
            if kind == "briefing":
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="brief tool down")
            if kind == "precedent":
                if runner_answers_cancel:
                    self.task_runner[task.id] = "pc-a"
                try:
                    await cancel_arrived.wait()
                except asyncio.CancelledError:
                    self.scout_reaped = True
                    raise
                return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, error="cancelled by PI")
            raise AssertionError(kind)

        async def send_runner(runner_id, message):
            self.cancel_sent.append(message["task_id"])
            cancel_arrived.set()

        super().__init__(dispatch)
        self.task_runner: dict[str, str] = {}
        self.send_runner = send_runner
        self.s.orchestrator.chief_of_staff_agent = "chief_of_staff"
        self.s.orchestrator.precedent_agent = "lit_scout"
        self.s.orchestrator.reviewer_agent = None
        self.agents.update({
            "chief_of_staff": {"id": "chief_of_staff", "name": "chief", "role": "brief", "engine": "mock"},
            "lit_scout": {"id": "lit_scout", "name": "lit", "role": "precedent", "engine": "mock"},
        })


@pytest.mark.asyncio
async def test_a_failed_briefing_cancels_the_scout_on_its_runner_and_waits_for_it():
    hub = EarlyEndHub(runner_answers_cancel=True)
    await asyncio.wait_for(Orchestrator(hub).run_request("r"), timeout=5)
    assert hub.requests["r"]["status"] == "failed"
    assert len(hub.cancel_sent) == 1 and not hub.scout_reaped  # the runner's cancelled result came back


@pytest.mark.asyncio
async def test_a_failed_briefing_reaps_a_scout_whose_runner_never_answers(monkeypatch):
    monkeypatch.setattr(cso, "PRECEDENT_CANCEL_GRACE_S", 0.05)
    hub = EarlyEndHub(runner_answers_cancel=False)
    await asyncio.wait_for(Orchestrator(hub).run_request("r"), timeout=5)
    assert hub.requests["r"]["status"] == "failed"
    assert hub.cancel_sent == [] and hub.scout_reaped


def test_plan_prompt_carries_checks_and_the_review_carries_reasons():
    # The plan prompt lists every topic's items, so it leaves out the reasons; the reviewer gets them for the
    # declared topics only.
    catalog = topic_checklists.load()
    rule = topic_checklists.prompt_rule(catalog)
    item = catalog["bulk_rna_seq"][0]
    assert item.check in rule and item.why not in rule and "Why:" not in rule
    review = cso.plan_review_context(general_plan(checklist=bulk_answers()), catalog, None)
    assert f"{item.check} Why: {item.why}" in review
