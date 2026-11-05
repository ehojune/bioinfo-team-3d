[← Manual index](../manual.en.md)

## Components and status

- **Included**: the live web office (2.5D and 3D, phone-ready), the runner daemon, 4 CLI adapters (Claude Code · Codex · Gemini ·
  a generic CLI for self-made agents), SGE/PBS/Slurm HPC tools (MCP), the approval gate, the gateway, the CSO orchestrator,
  **per-project GitHub updates**, contract-staff hiring, contracts and talent pool, a mock engine, and tests
- **Not yet**: an iOS app (for now, add the web to the home screen and use it like an app — [Web office](06-web-office.en.md#web-office)), a governance layer ([Roadmap](19-roadmap.en.md#roadmap))

Things that were not requested but were added because needed.

| # | Component | Why it is needed | Status |
|---|---|---|---|
| 1 | **Phone approval gate** | A person decides HPC submission (by core and time criteria), risky bash, budget overruns and hiring contract staff | Implemented |
| 2 | **Controlled-access data zones** | So DUA data originals do not enter LLM conversations: file tools blocked, Bash on those paths needs approval, originals are processed only inside HPC jobs and only summaries are read | Implemented (guardrails, [Safety measures](11-safety.en.md#safety-measures)) |
| 3 | **HPC sleep/wake** | Do not keep an LLM session on during long jobs → end the turn after submission, and a job that already finished while the agent ran is resumed in the same session | Implemented |
| 3-1 | **Subscription-limit wait** | Park same-engine steps until the reset time of Claude Code, Codex and agy and resume automatically. Default wait 1 hour, at most 7 days after first detection | Implemented |
| 4 | **Cross-vendor science review** | The Virtual Biotech's three-criteria review + a different company's model to spread blind spots | Implemented |
| 5 | **Budget cap · model tiering** | Caps per task (`--max-budget-usd`) and per request, phone approval if exceeded. Opus for judgment, sonnet for repetitive work | Implemented |
| 6 | **Lab notebook / provenance record** | Per task `TASK.md`, `manifest.json` (spec hash, engine, model, session, cost), `events.jsonl`, `jobs.jsonl`, job scripts and logs | Implemented |
| 7 | **Talent pool** | Rehire contract staff once hired without reconversion | Implemented |
| 8 | **Agent performance review** | Regression tests on every model or prompt change with a small task set whose answers are known (for example finding a known bad-QC sample) | Roadmap |
| 9 | **Project memory** | Inject per-project decision logs and glossaries as CLAUDE.md/AGENTS.md to avoid repeating the same question | Roadmap |
| 10 | **Fan-out mode** | Process "the same task × thousands of items" (variant, paper and sample screening) in parallel with a cheap model + JSON schema + source field | Roadmap |
| 11 | **Clarifying-question loop** | Before planning, the CSO asks and the PI answers on the phone, then it replans. 2–4 options per question (buttons), whether free input is allowed, depth (about 30/60/90 minutes) (#34, #36) | Implemented |
| 12 | **Kill switch · audit** | Task and request cancel (web, CLI, API), the full event log | Partial |
| 13 | **Workflow engine first** | Analysts and engineers use nf-core/Snakemake, with version and container pinning as the default policy | Prompt policy |
| 14 | **Staff query `labhq_ask`** | Ask the CSO, facilities team or peers, escalate only hard stops to the PI's phone, then resume the same session | Implemented |
