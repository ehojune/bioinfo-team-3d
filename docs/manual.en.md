# labhq manual

> English edition of [manual.md](manual.md) (Korean, the source of truth). When the Korean manual changes, update this file in the same PR. The README, PI Q&A, research protocol and vocabulary tables also have English editions; other linked documents (HANDOFF, `reference/` and so on) are still in Korean.

If you are new, start with "Getting started on the web" in the [README](../README.en.md). This document comes after that: command line, configuration, safety measures, the research lane, known limits.
The PI's questions and decisions are in [PI Q&A](pi-qa.en.md); development rules and the work queue are in [AGENTS.md](../AGENTS.md) and [HANDOFF.md](../HANDOFF.md) (Korean).
For how the development checkout preserves author and agent marks, see [Git hooks](reference/git-hook-authorship.md).

This page is the index. The text lives in one file per chapter under `docs/manual/`; the link under each heading below goes to that section. Old `manual.en.md#…` links still land on the heading here.

| Chapter | What it covers |
|---|---|
| [What the PI sees](manual/01-pi-text.en.md#what-the-pi-sees) | Plain-language conversion (`pi_language.py`) and Korean style check (`korean_qc.py`) |
| [Core concepts](manual/02-concepts.en.md#core-concepts) | Terms that come up often |
| [Tour: the mock demo](manual/03-demo.en.md#tour-the-mock-demo) | See the office without API keys |
| [Installation and first setup](manual/04-install.en.md#installation-and-first-setup) | `labhq init`, instances, HPC consultation, doctor |
| [Starting and connecting](manual/05-run.en.md#starting-and-connecting) | gateway and runner, browser and phone |
| [Web office](manual/06-web-office.en.md#web-office) | 2.5D and 3D screens and features |
| [Command line (CLI)](manual/07-cli.en.md#command-line-cli) | `send`, `approve`, `verify`, `recruit`, and more |
| [Structure and events](manual/08-structure.en.md#structure-and-events) | Request flow, architecture, events, REST, environment failures |
| [Staff and tools](manual/09-staff-tools.en.md#staff-and-tools) | Org chart, connected tools, bioinfo-agent |
| [Contract staff](manual/10-contract-staff.en.md#contract-staff-paper2agent) | Paper2Agent hiring and contracts |
| [Safety measures](manual/11-safety.en.md#safety-measures) | Approval gates, data zones, PI private paths, budget |
| [Per-project GitHub reports](manual/12-github-reports.en.md#per-project-github-reports) | Issues, report commits, public guard |
| [Research lane and verification](manual/13-research-lane.en.md#research-lane-and-verification) | CP1 and CP2, claim anchors, `labhq verify` |
| [Semantic model and ontology](manual/14-semantic-model.en.md#semantic-model-and-ontology) | EDAM, input-fit checks and the offline provenance model |
| [Configuration reference](manual/15-config.en.md#configuration-reference) | State, HPC, staff, engines, reference material, semantic model |
| [Comparison bench](manual/16-bench.en.md#comparison-bench) | labhq versus a single session |
| [Known limits](manual/17-known-limits.en.md#known-limits) | What to check on first run |
| [Components and status](manual/18-components.en.md#components-and-status) · [Roadmap](manual/19-roadmap.en.md#roadmap) | What is done and what remains |
| [Background](manual/20-background.en.md#background-the-virtual-biotech) · [File layout](manual/22-file-layout.en.md#file-layout) · [License](manual/23-license.en.md#license) | |

## What the PI sees

[→ Read this section](manual/01-pi-text.en.md)

## Core concepts

[→ Read this section](manual/02-concepts.en.md)

## Tour: the mock demo

[→ Read this section](manual/03-demo.en.md)

## Installation and first setup

[→ Read this section](manual/04-install.en.md)

### Analyzing in this PC's WSL

[→ Read this section](manual/04-install.en.md#analyzing-in-this-pcs-wsl)

## Starting and connecting

[→ Read this section](manual/05-run.en.md)

### Shutting down from the web

[→ Read this section](manual/05-run.en.md#shutting-down-from-the-web)

## Web office

[→ Read this section](manual/06-web-office.en.md)

### Staff controls

[→ Read this section](manual/06-web-office.en.md#staff-controls)

## Command line (CLI)

[→ Read this section](manual/07-cli.en.md)

### Searching past requests

[→ Read this section](manual/07-cli.en.md#searching-past-requests)

### Request lifetime and PI review

[→ Read this section](manual/07-cli.en.md#request-lifetime-and-pi-review)

### Solo handling

[→ Read this section](manual/07-cli.en.md#solo-handling)

## Structure and events

[→ Read this section](manual/08-structure.en.md)

### Disk writes and slow disks

[→ Read this section](manual/08-structure.en.md)

### Topic checklists and the prior-research standard

[→ Read this section](manual/08-structure.en.md#topic-checklists-and-the-prior-research-standard)

### Common result contract for reports

[→ Read this section](manual/08-structure.en.md#common-result-contract-for-reports)

### Request bundle

[→ Read this section](manual/08-structure.en.md#request-bundle)

### Overall layers and module architecture

[→ Read this section](manual/08-structure.en.md#overall-layers-and-module-architecture)

### Report PDF

[→ Read this section](manual/08-structure.en.md#report-pdf)

### Login wait

[→ Read this section](manual/08-structure.en.md#login-wait)

### Step failure and missing evidence cells

[→ Read this section](manual/08-structure.en.md#step-failure-and-missing-evidence-cells)

### Environment failure and error signatures

[→ Read this section](manual/08-structure.en.md#environment-failure-and-error-signatures)

## Staff and tools

[→ Read this section](manual/09-staff-tools.en.md)

### Connected tools

[→ Read this section](manual/09-staff-tools.en.md#connected-tools)

### Public-resource lookup tools

[→ Read this section](manual/09-staff-tools.en.md#public-resource-lookup-tools)

### Criteria for dividing roles, engines and tools

[→ Read this section](manual/09-staff-tools.en.md#criteria-for-dividing-roles-engines-and-tools)

### Connecting bioinfo-agent

[→ Read this section](manual/09-staff-tools.en.md#connecting-bioinfo-agent)

## Contract staff (Paper2Agent)

[→ Read this section](manual/10-contract-staff.en.md)

### Three types

[→ Read this section](manual/10-contract-staff.en.md#three-types)

### How it works

[→ Read this section](manual/10-contract-staff.en.md#how-it-works)

### Cautions

[→ Read this section](manual/10-contract-staff.en.md#cautions)

## Safety measures

[→ Read this section](manual/11-safety.en.md)

### Runaway detection (shadow)

[→ Read this section](manual/11-safety.en.md#runaway-detection-shadow)

### Approval gate and write scope

[→ Read this section](manual/11-safety.en.md#approval-gate-and-write-scope)

### Data zones

[→ Read this section](manual/11-safety.en.md#data-zones)

### PI private paths

[→ Read this section](manual/11-safety.en.md#pi-private-paths)

### Switched-job workspace

[→ Read this section](manual/11-safety.en.md#switched-job-workspace)

### Stop conditions at a glance

[→ Read this section](manual/11-safety.en.md#stop-conditions-at-a-glance)

### Approval and budget

[→ Read this section](manual/11-safety.en.md#approval-and-budget)

## Per-project GitHub reports

[→ Read this section](manual/12-github-reports.en.md)

### Where results are saved

[→ Read this section](manual/12-github-reports.en.md#where-results-are-saved)

### The editor and checking the summary

[→ Read this section](manual/12-github-reports.en.md#the-editor-and-checking-the-summary)

### Progress reports for registered projects

[→ Read this section](manual/12-github-reports.en.md#progress-reports-for-registered-projects)

## Research lane and verification

[→ Read this section](manual/13-research-lane.en.md)

### Turning it on

[→ Read this section](manual/13-research-lane.en.md#turning-it-on)

### CP1

[→ Read this section](manual/13-research-lane.en.md#cp1)

### Steps and CP2

[→ Read this section](manual/13-research-lane.en.md#steps-and-cp2)

### Review and continuation

[→ Read this section](manual/13-research-lane.en.md#review-and-continuation)

### Report and verify

[→ Read this section](manual/13-research-lane.en.md#report-and-verify)

### Final draft acceptance (CP4)

[→ Read this section](manual/13-research-lane.en.md#final-draft-acceptance-cp4)

## Semantic model and ontology

[→ Read this section](manual/14-semantic-model.en.md)

## Configuration reference

[→ Read this section](manual/15-config.en.md)

### Linking my LLM-wiki (#827)

[→ Read this section](manual/15-config.en.md#linking-my-llm-wiki-827)

## Comparison bench

[→ Read this section](manual/16-bench.en.md)

## Known limits

[→ Read this section](manual/17-known-limits.en.md)

## Components and status

[→ Read this section](manual/18-components.en.md)

## Roadmap

[→ Read this section](manual/19-roadmap.en.md)

## Background: The Virtual Biotech

[→ Read this section](manual/20-background.en.md)

### What changed in moving to our lab

[→ Read this section](manual/20-background.en.md#what-changed-in-moving-to-our-lab)

## Publishing to your own repository

[→ Read this section](manual/21-publish.en.md)

## File layout

[→ Read this section](manual/22-file-layout.en.md)

## License

[→ Read this section](manual/23-license.en.md)

## Interpreter reservation accounting

[→ Read this section](manual/24-interpreter-accounting.en.md)
