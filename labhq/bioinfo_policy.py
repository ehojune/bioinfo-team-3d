"""Shared bioinformatics assignment and contribution policy (PI #732)."""

PIPELINE_AUTHORS = frozenset({"bioinfo-agent", "engineer", "analyst"})

BIOINFO_ASSIGNMENT_RULE = (
    "Default standard bioinformatics analysis to bioinfo-agent using its supported nf-core pipelines, including "
    "suitable nf-core pipelines it finds through search. Keep a supported pipeline's input preparation, execution "
    "and technical QC together; analyst/biologist handles study design and biological interpretation. In a plan, "
    "keep tightly coupled non-pipeline analysis of the same data and question (exploration, preprocessing and core "
    "statistics) in one analysis step instead of handing it between employees; split only work that is truly "
    "independent (a different dataset, question or evidence stream). Keep the biological interpretation, an "
    "independent technical or scientific QC step, the other-vendor science review and the CP1/CP2 approval and "
    "evidence checks separate. For simple "
    "collection, processing or lookup that needs no pipeline, assign the appropriate employee directly (for example "
    "data_steward for public file collection). If no supported pipeline fits a complex new workflow, assign its "
    "implementation to an engineer or analyst, not bioinfo-agent. Record the selected pipeline or the reason for "
    "direct work or a different assignment in assumptions/protocol. After the new pipeline is complete, declare "
    "outputs/pipeline/<name>/manifest.json and its self-contained source files as a contribution candidate. "
    "The manifest uses schema 1, name matching its folder, title and summary; the bundle includes main.nf, "
    "nextflow.config and README.md. "
    "Only a separate per-candidate PI yes permits a bioinfo-agent contribution PR; the global pipeline_pr setting "
    "only enables this approval path. Never open the upstream PR yourself or treat a plan or evidence checkpoint "
    "approval as contribution consent.")
