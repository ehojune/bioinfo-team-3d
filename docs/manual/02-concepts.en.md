[← Manual index](../manual.en.md)

## Core concepts

Terms that appear often in this document.

| Concept | One-line meaning | Details |
|---|---|---|
| gateway | The server that stores requests, approvals and events in SQLite and connects web, phone and CLI (`labhq gateway`) | [Structure](08-structure.en.md#structure-and-events) |
| runner | The daemon that actually launches staff CLIs. It only uses outbound connections to the gateway (`labhq runner`) | [Structure](08-structure.en.md#structure-and-events) |
| Staff and engine | A staff member is a YAML file (`agents/core/`) giving role, model and tools. The engine is the CLI that runs them (Claude Code, Codex; also Gemini and a generic `cli` adapter) | [Staff](09-staff-tools.en.md#staff-and-tools) |
| CSO, plan, step | The CSO splits a request into a plan (a JSON DAG); one staff member takes each step | [Structure](08-structure.en.md#structure-and-events) |
| orchestrate and direct requests | orchestrate starts from the CSO's plan. direct (`--agent`) goes straight to one staff member. `--plan-only` returns only the plan | [CLI](07-cli.en.md#command-line-cli) |
| Research lane | Requests the CSO classifies as research when `research.enabled` is on. They follow a fixed plan and PI checkpoints | [Research lane](13-research-lane.en.md#research-lane-and-verification) · [Research protocol](../research_protocol.en.md) |
| Checkpoints CP1–CP4 | Gates where a research request waits for a PI decision. CP1 (plan) and CP2 (evidence, when `evidence_checkpoint` is on) are wired; CP4 (acceptance) is opt-in via `final_checkpoint`; CP3 (selection) is not yet | [Research protocol §5](../research_protocol.en.md#5-pi-checkpoints-and-completion) |
| claim and evidence | The contract for writing a research step's result as claims and the evidence for them (`result v2`). Evidence must point to outputs actually collected | [Research protocol §3](../research_protocol.en.md#3-staff-result-contract) |
| domain pack | A bundle of field-specific judgment rules (`id@version`). Packs exist for single-cell DE and bulk two-condition DE | [Research lane](13-research-lane.en.md#research-lane-and-verification) |
| Approvals and the inbox | The PI decides HPC submission, risky commands, budget overruns and hiring. Pending items and history gather in the web inbox | [Safety measures](11-safety.en.md#safety-measures) · [Web office](06-web-office.en.md#web-office) |
| labhq_ask and consultation | The MCP tool a blocked staff member uses to ask. The CSO or a peer answers once in a read-only consultation; only risky matters go to the PI | [Connected tools](09-staff-tools.en.md#connected-tools) · [Configuration](15-config.en.md#configuration-reference) |
| Data zone | A grade per path: public, internal, restricted (`policy.data_zones`). Restricted originals are not opened by file tools; they are handled only inside HPC jobs | [Data zones](11-safety.en.md#data-zones) |
| Write gate | Claude staff get pre-approved writes only inside the work, project and upstream folders. Writes elsewhere go through approval | [Write scope](11-safety.en.md#approval-gate-and-write-scope) |
| PI private paths | A list that hides the PI's personal files (`~/.ssh`, browser profiles, labhq config and so on) from staff when they run under the same account (`policy.private_paths`). Claude staff get three layers (deny rules, gate, instructions); Codex staff under the Windows elevated sandbox get two (sandbox read denial and instructions); other engines get instructions only. It cannot stop a script that builds a path at run time | [PI private paths](11-safety.en.md#pi-private-paths) |
| Work folder, manifest, outputs | Each step gets a folder under `runner.workspace_root`. `manifest.json` records engine, model, session and cost; only files under `outputs/` count as output | [Components](18-components.en.md#components-and-status) · [Structure](08-structure.en.md#structure-and-events) |
| Round log | A development log kept per request. It can be pushed to `dev_log.repo` (private) | [Configuration](15-config.en.md#configuration-reference) · [HANDOFF](../../HANDOFF.md) |
| Instance | `--instance <name>` runs several labs on one PC. Config, state, ports and tokens are separate; engine logins and limits are shared | [Installation](04-install.en.md#installation-and-first-setup) |
| Subscription-limit wait | A step that hits an engine subscription limit is held until the reset time and resumed automatically | [Components](18-components.en.md#components-and-status) |
| Semantic model | Offline #136 provenance model and pilot. Live shadow removal is under validation in #1388 | [Details](14-semantic-model.en.md) |
| ontology and EDAM | 125 PI-reviewed vocabulary keys (`labhq/vocab/`), and the public bioinformatics ontology EDAM (75 terms) linked to 79 of those keys | [Semantic model](14-semantic-model.en.md#semantic-model-and-ontology) · [Configuration](15-config.en.md#configuration-reference) |
| Data-type declaration | An optional feature where the CSO writes data_type and format keys on a step's outputs (`plan.declare_output_types`, off by default) | [Configuration](15-config.en.md#configuration-reference) |
| Input–method fit | A verdict (`fit`, `mismatch`, `unknown`) on whether a step input's declared type suits the method. Runs during plan validation before CP1 | [Semantic model](14-semantic-model.en.md#semantic-model-and-ontology) · [Configuration](15-config.en.md#configuration-reference) |
| Actions A1 and A2 | A1 only records whether the 7 existing actions were possible at that moment. A2 runs only a single `request.followup` that the PI confirmed in the terminal | [Configuration](15-config.en.md#configuration-reference) |
