# References

[한국어](references.md) · [README](../README.en.md#further-reading)

Sources used for design, implementation, and tool connections. The last column links to repository evidence; projects reviewed without adoption are marked as such.
Tool connections describe configuration, not verified live access.

| Name | What it is | How labhq used it |
|---|---|---|
| [The Virtual Biotech](https://github.com/harrisongzhang/TheVirtualBiotech) | Virtual biotech research team | Informed the CSO, specialist, and scientific reviewer structure and claim–evidence verification ([background](manual.md#배경-the-virtual-biotech), [verification](../README.en.md#four-things-labhq-works-hard-on)). |
| [Paper2Agent](https://github.com/jmiao24/Paper2Agent) | Research papers and code turned into agents | Tools produced by the external conversion skill enter recruitment, probation, and contracts as temporary staff ([process](manual.md#파견직-제도-paper2agent)). |
| [Munder Difflin](https://github.com/HarnessMD/munder-difflin) | Office app for CLI agents | Informed the office, right tabs, staff cards, and operational lessons for staff control ([#64](https://github.com/ehojune/bioinfo-team-3d/issues/64), [#57](https://github.com/ehojune/bioinfo-team-3d/issues/57)). |
| [EDAM](https://doi.org/10.7490/f1000research.1118900.1) | Ontology of data, methods, and topics | Selected term IDs, labels, and ancestors are mapped to local vocabulary ([source and version](../labhq/vocab/NOTICE.md)). |
| [RO-Crate](https://w3id.org/ro/crate/1.1) · [Process Run Crate](https://w3id.org/ro/wfrun/process/0.5) | Research output and execution metadata specifications | Request bundles use RO-Crate 1.1 and Process Run Crate 0.5 JSON-LD metadata ([manual](manual.md#요청-묶음)). |
| [nf-core](https://nf-co.re/) | Collection of Nextflow analysis pipelines | Supports routine bioinfo-agent analyses and the workflow-engine-first policy ([overview](../README.en.md#how-it-works), [policy](manual.md#구성요소와-상태)). |
| [bioinfo-agent](https://github.com/ehojune/bioinfo-agent) | Claude Code agent for bioinformatics | Connected as a staff member for pipeline selection, execution, and QC ([connection](manual.md#bioinfo-agent-연결하기)). |
| [open-science](https://github.com/aipoch/open-science) | Local AI research workbench | Its review informed follow-up improvements to permission gates, recruitment provenance, citation checks, and reproducibility records ([review and follow-ups](https://github.com/ehojune/bioinfo-team-3d/issues/316)). |
| [BioMCP](https://github.com/genomoncology/biomcp) | Biomedical query CLI and MCP | Reviewed its design for retrieving only the information needed at each step ([#41](https://github.com/ehojune/bioinfo-team-3d/issues/41)). |
| [ToolUniverse](https://github.com/mims-harvard/ToolUniverse) | Scientific tool discovery and execution framework | Reviewed tool discovery and role-specific profiles; it is not a default connection ([#64](https://github.com/ehojune/bioinfo-team-3d/issues/64)). |
| [PubMed](https://pubmed.ncbi.nlm.nih.gov/) | Biomedical literature database | Connected for literature searches and citation checks during scientific review ([tools](manual.md#연결된-도구)). |
| [bioRxiv](https://www.biorxiv.org/) · [medRxiv](https://www.medrxiv.org/) | Life science and medical preprint servers | Connected through MCP for finding research before publication ([tools](manual.md#연결된-도구)). |
| [ChEMBL](https://www.ebi.ac.uk/chembl/) | Compound and bioactivity database | Connected through MCP for compound searches ([tools](manual.md#연결된-도구)). |
| [ClinicalTrials.gov](https://clinicaltrials.gov/) | Clinical trial registry | Connected through MCP for clinical trial searches ([tools](manual.md#연결된-도구)). |
| [Open Targets](https://platform.opentargets.org/) | Target–disease evidence platform | Connected through MCP for target searches ([tools](manual.md#연결된-도구)). |
| [Crossref](https://www.crossref.org/) | DOI and publication metadata service | Used alongside DOI and NCBI lookups in optional live source checks ([protocol](research_protocol.md)). |
| [Topic evidence papers](reference/topic_papers.md) | 1,286 papers across 43 topics | Supplies method, standard, and benchmark evidence for topic checklists, with individual citations kept in the existing list ([vocabulary and checklists](vocabulary.md), [evidence records](reference/README.md)). |
