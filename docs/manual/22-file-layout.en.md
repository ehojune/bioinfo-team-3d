[← Manual index](../manual.en.md)

## File layout

```
labhq/
  settings.py  models.py  policy.py  private_paths.py  registry.py  cli.py
  supervisor.py  store.py  durable.py  doctor.py  verify.py  request_bundle.py  reproduction_receipt.py
  portfolio.py  result_storage.py  report_contract.py  report_pdf.py  korean_qc.py
  web/          index.html, state.js, ui/*.js (2.5D office) · lab3d/ (3D) · vendor/three · manifest · icon
  integrations/ github · rounds (project issues, summary and report commits, public guard, round record, Codex review request)
  adapters/     base · claude_code · codex · gemini · antigravity · cli (custom agents) · read_only · mock
  runner/       daemon (gateway connection, execution, job watching) · approvals (local broker) · workspace (lab notebook, output scan) · bioinfo_agent
  tools/        scheduler (SGE/PBS/Slurm) · hpc_mcp · approval_mcp · ask_mcp · annot_mcp · _mcpcompat
  gateway/      server (FastAPI, WS, REST) · shutdown (shutdown handling)
  orchestrator/ cso (briefing → prior research/contract staff → plan DAG → CP1/CP2 → review → report)
  research/     contract (CP1, CP2, result v2) · continuation (step reuse) · packs (domain rules) ·
                semantics* (provenance/object shadow, input fit, A/B) · semantic_device_report (meaning-device report)
  evidence/     claims (claim and evidence ledger validation) · literal_check (citation and number matching)
  recruit/      paper2agent (paper MCP hiring, probation, contracts) · paper_skills (paper-methodology advisory skills)
  facilities/   signatures · fixes · packages (environment-failure error signatures, automatic-recovery allowlist)
  vocab/        output_types.yaml (125 keys) · topic_checklists.yaml (45 fields, 135 items) ·
                edam_subset.yaml · public_resources.tsv · declare · topic_checklists
  skills/       built-in writing and diagram skill plugins (humanizer, graphical-abstract)
agents/core/*.yaml       13 default roles (8 Claude Code, 5 Codex, including bioinfo-agent)
agents/contract/         contract-staff contracts (+ template, hosted AlphaGenome example)
config/labhq.example.yaml
scripts/                 publish_github.sh · check_public.sh · integrations.py · vocab_tables.py · patch_notes.py
tests/                   scheduler parsers · policy · registry · research contract/semantic model · MCP servers · adapters (fake CLI) ·
                         GitHub reports/summaries (fake API) · web (Node/browser) · whole flow (mock)
```
