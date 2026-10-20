"""#799 bench preparation: the existing coupled-step policy (#732) reaches every planning path, and plan examples for the
initial plan, a re-plan and a research continuation show what the bench counts as an unnecessary split or handoff.

Deterministic only: prompt constants, fixed plan dicts, `validate_steps` and the continuation reuse rule. No model,
CLI, process or network. The counting rules are the ones in docs/reference/bench-coarse-contract.md."""
from __future__ import annotations

import copy
from collections import defaultdict

from labhq.bioinfo_policy import BIOINFO_ASSIGNMENT_RULE
from labhq.orchestrator import cso
from labhq.research import continuation
from tests.test_research_continue import _round_one
from tests.test_research_protocol import valid_plan

ROSTER = {"analyst", "bioinfo-agent", "biologist", "data_steward", "engineer", "lit_scout", "qc_reviewer"}
# Roles that do non-pipeline analysis; a linear link between two of them is a coupled-split candidate.
ANALYSIS = {"analyst", "engineer"}
PIPELINE = "bioinfo-agent"
# Kept boundaries (contract): an independent QC check, biological interpretation, research validation/report phases.
BOUNDARY_ROLES = {"qc_reviewer", "biologist"}
BOUNDARY_PHASES = {"validation", "report"}


def plan_shape(steps: list[dict]) -> dict:
    """Bench counts for one plan (contract §2): steps, role handoffs, kept boundaries, split candidates and the
    outputs a handoff passes on."""
    by_id = {step["id"]: step for step in steps}
    children: dict[str, list[str]] = defaultdict(list)
    for step in steps:
        for parent in step.get("depends_on") or []:
            if parent in by_id:
                children[parent].append(step["id"])
    shape = {"steps": len(steps), "handoffs": 0, "boundary_handoffs": 0, "handoff_outputs": 0,
             "coupled_splits": 0, "pipeline_splits": 0}
    for step in steps:
        parents = [parent for parent in step.get("depends_on") or [] if parent in by_id]
        for parent_id in parents:
            parent = by_id[parent_id]
            linear = parents == [parent_id] and children[parent_id] == [step["id"]]
            if parent["agent_id"] != step["agent_id"]:
                shape["handoffs"] += 1
                shape["handoff_outputs"] += len(parent.get("outputs") or [])
                if step["agent_id"] in BOUNDARY_ROLES or step.get("phase") in BOUNDARY_PHASES:
                    shape["boundary_handoffs"] += 1
                    continue
            if not linear:
                continue
            if step["agent_id"] == PIPELINE and parent["agent_id"] in ANALYSIS | {PIPELINE}:
                shape["pipeline_splits"] += 1  # pipeline input preparation, execution or technical QC split apart
            elif step["agent_id"] in ANALYSIS and parent["agent_id"] in ANALYSIS:
                shape["coupled_splits"] += 1  # exploration, preprocessing and core statistics split apart
    return shape


def step(sid, agent, instruction, outputs, depends_on=()):
    return {"id": sid, "agent_id": agent, "instruction": instruction, "outputs": [f"outputs/{o}" for o in outputs],
            "depends_on": list(depends_on)}


def valid(steps):
    kept, _warnings = cso.validate_steps(copy.deepcopy(steps), ROSTER, 12)
    assert [s["id"] for s in kept] == [s["id"] for s in steps]  # the examples are real, dispatchable plans
    return steps


# --- the policy reaches every planning path ---------------------------------------------------------------------

def test_the_coupled_step_rule_opens_every_plan_template():
    assert "Keep a supported pipeline's input preparation, execution and technical QC together" in BIOINFO_ASSIGNMENT_RULE
    for name in ("PLAN_PROMPT", "RESEARCH_PLAN_PROMPT", "RESEARCH_CP2_PLAN_PROMPT", "REPLAN_PROMPT"):
        assert getattr(cso, name).startswith(BIOINFO_ASSIGNMENT_RULE), name


COHESIVE = ("keep tightly coupled non-pipeline analysis of the same data and question (exploration, preprocessing "
            "and core statistics) in one analysis step")


def test_the_cohesive_analysis_rule_reaches_every_plan_template_with_its_boundaries():
    """PI's #799 requirement: coupled exploration, preprocessing and core statistics stay in one analysis step, while
    independent work, interpretation, QC, the other-vendor review and CP1/CP2 stay separate."""
    assert COHESIVE in BIOINFO_ASSIGNMENT_RULE
    for kept in ("split only work that is truly independent", "biological interpretation",
                 "independent technical or scientific QC step", "other-vendor science review",
                 "CP1/CP2 approval and evidence checks"):
        assert kept in BIOINFO_ASSIGNMENT_RULE, kept
    for name in ("PLAN_PROMPT", "RESEARCH_PLAN_PROMPT", "RESEARCH_CP2_PLAN_PROMPT", "REPLAN_PROMPT"):
        assert COHESIVE in getattr(cso, name), name
    # The nf-core assignment and the per-candidate contribution consent are unchanged.
    for boundary in ("nf-core", "Keep a supported pipeline's input preparation, execution and technical QC together",
                     "engineer or analyst, not bioinfo-agent", "Only a separate per-candidate PI yes"):
        assert boundary in BIOINFO_ASSIGNMENT_RULE, boundary


def test_a_continuation_appends_to_the_research_plan_and_does_not_replace_the_rule():
    plan = valid_plan(steps=2)
    note = continuation.plan_prompt(plan, "a" * 64, [{"step_id": "s2", "problem": "p", "request": "r"}], 2)
    assert note.startswith("\n\nContinuation (research round 2).")  # added after RESEARCH_PLAN_PROMPT, not instead
    assert BIOINFO_ASSIGNMENT_RULE not in note and "split" not in note.casefold()


# --- initial plan -----------------------------------------------------------------------------------------------

OVER_SPLIT = [
    step("s1", "data_steward", "Download GSE10072 series matrix", ["series_matrix.txt.gz"]),
    step("s2", "analyst", "Explore sample metadata and pairing", ["exploration.md"], ["s1"]),
    step("s3", "analyst", "Normalize and filter probes", ["expression.tsv"], ["s2"]),
    step("s4", "engineer", "Fit the paired limma model", ["de_table.tsv"], ["s3"]),
    step("s5", "qc_reviewer", "Check the DE result independently", ["qc.md"], ["s4"]),
    step("s6", "biologist", "Interpret the DE genes", ["interpretation.md"], ["s5"]),
]
COARSE = [
    step("s1", "data_steward", "Download GSE10072 series matrix", ["series_matrix.txt.gz"]),
    step("s2", "analyst", "Explore metadata and pairing, normalize and filter probes, then fit the paired limma model",
         ["exploration.md", "expression.tsv", "de_table.tsv"], ["s1"]),
    step("s3", "qc_reviewer", "Check the DE result independently", ["qc.md"], ["s2"]),
    step("s4", "biologist", "Interpret the DE genes", ["interpretation.md"], ["s3"]),
]


def test_an_initial_plan_that_splits_coupled_analysis_across_roles_is_counted():
    assert plan_shape(valid(OVER_SPLIT)) == {"steps": 6, "handoffs": 4, "boundary_handoffs": 2, "handoff_outputs": 4,
                                            "coupled_splits": 2, "pipeline_splits": 0}


def test_the_coarse_initial_plan_keeps_qc_and_interpretation_as_separate_boundaries():
    shape = plan_shape(valid(COARSE))
    assert shape == {"steps": 4, "handoffs": 3, "boundary_handoffs": 2, "handoff_outputs": 5,
                     "coupled_splits": 0, "pipeline_splits": 0}
    analysis = next(s for s in COARSE if s["agent_id"] == "analyst")
    qc = next(s for s in COARSE if s["agent_id"] == "qc_reviewer")
    assert qc["depends_on"] == [analysis["id"]]  # merging the analysis never absorbs the independent check


PIPELINE_OVER_SPLIT = [
    step("s1", "engineer", "Write the nf-core/rnaseq samplesheet", ["samplesheet.csv"]),
    step("s2", "bioinfo-agent", "Run nf-core/rnaseq", ["salmon.merged.gene_counts.tsv"], ["s1"]),
    step("s3", "bioinfo-agent", "Run MultiQC technical QC", ["multiqc_report.html"], ["s2"]),
    step("s4", "analyst", "Fit DESeq2 for the study contrast", ["de_table.tsv"], ["s3"]),
]
PIPELINE_COARSE = [
    step("s1", "bioinfo-agent", "Prepare the samplesheet, run nf-core/rnaseq and its MultiQC technical QC",
         ["samplesheet.csv", "salmon.merged.gene_counts.tsv", "multiqc_report.html"]),
    step("s2", "analyst", "Fit DESeq2 for the study contrast", ["de_table.tsv"], ["s1"]),
    step("s3", "qc_reviewer", "Check the DE result independently", ["qc.md"], ["s2"]),
]


def test_the_pipeline_rule_counts_preparation_and_technical_qc_split_from_execution():
    assert plan_shape(valid(PIPELINE_OVER_SPLIT))["pipeline_splits"] == 2
    coarse = plan_shape(valid(PIPELINE_COARSE))
    # The pipeline step handing counts to the analyst is the policy's own role boundary (study statistics), not a split.
    assert coarse["pipeline_splits"] == 0 and coarse["coupled_splits"] == 0 and coarse["boundary_handoffs"] == 1


def test_independent_parallel_analyses_are_not_split_candidates():
    fan_out = [
        step("s1", "data_steward", "Download the matrix", ["matrix.tsv"]),
        step("s2", "analyst", "Differential expression", ["de.tsv"], ["s1"]),
        step("s3", "analyst", "Clustering", ["clusters.tsv"], ["s1"]),
        step("s4", "engineer", "Combine DE and clusters into one table", ["combined.tsv"], ["s2", "s3"]),
    ]
    shape = plan_shape(valid(fan_out))
    assert shape["coupled_splits"] == 0 and shape["handoffs"] == 4  # not linear: two parents, a shared parent


# --- re-plan ------------------------------------------------------------------------------------------------------

def test_a_replan_is_counted_on_the_kept_and_new_steps_together():
    kept = copy.deepcopy(COARSE[:1])  # s1 completed and is never rerun
    split_replacement = [
        step("r1", "analyst", "Re-normalize the probes", ["expression.tsv"], ["s1"]),
        step("r2", "engineer", "Re-fit the paired model", ["de_table.tsv"], ["r1"]),
        step("r3", "qc_reviewer", "Check the DE result independently", ["qc.md"], ["r2"]),
    ]
    coarse_replacement = [
        step("r1", "analyst", "Re-normalize the probes and re-fit the paired model", ["expression.tsv", "de_table.tsv"],
             ["s1"]),
        step("r2", "qc_reviewer", "Check the DE result independently", ["qc.md"], ["r1"]),
    ]
    assert plan_shape(valid(kept + split_replacement))["coupled_splits"] == 1
    assert plan_shape(valid(kept + coarse_replacement))["coupled_splits"] == 0


# --- research continuation ----------------------------------------------------------------------------------------

def _research(agents_phases: list[tuple[str, str]], depends: dict[str, list[str]]) -> dict:
    plan = valid_plan(steps=len(agents_phases))
    for spec, (agent, phase) in zip(plan["steps"], agents_phases):
        spec.update(agent_id=agent, phase=phase, depends_on=depends.get(spec["id"], []))
    return plan


def test_a_continuation_that_keeps_the_analysis_whole_expects_fewer_step_dispatches_than_one_that_splits_it():
    """The rerun list is the plan's EXPECTED step dispatches. It is not an observed CLI start count: retries, consults,
    finish/wake turns and review/report tasks add to the real count, which only the task records and the runner show
    (contract §3). Nothing here observes them, so they are not counted at all rather than counted as zero."""
    old = _research([("data_steward", "evidence"), ("analyst", "analysis"), ("qc_reviewer", "validation")],
                    {"s2": ["s1"], "s3": ["s2"]})
    results = _round_one(old)["results"]
    issue = [{"step_id": "s2"}]

    revised = copy.deepcopy(old)
    revised["steps"][1]["instruction"] = "work 2, with the paired design the reviewer asked for"
    reuse, rerun = continuation.split_reuse(old, revised, results, issue)
    assert reuse == ["s1"] and sorted(rerun) == ["s2", "s3"]  # 2 expected step dispatches in the new round
    assert plan_shape(revised["steps"])["coupled_splits"] == 0

    split = copy.deepcopy(revised)
    extra = {**copy.deepcopy(split["steps"][1]), "id": "s4", "agent_id": "engineer", "depends_on": ["s2"],
             "claim_ids": ["c4"], "outputs": ["result4.tsv"],
             "evidence_slots": [{"id": "e4", "required": True, "description": "direct result"}]}
    split["steps"].insert(2, extra)
    split["steps"][3]["depends_on"] = ["s4"]
    reuse, rerun = continuation.split_reuse(old, split, results, issue)
    assert reuse == ["s1"] and sorted(rerun) == ["s2", "s3", "s4"]  # one more expected dispatch and handoff
    shape = plan_shape(split["steps"])
    assert shape["coupled_splits"] == 1 and shape["boundary_handoffs"] == 1
