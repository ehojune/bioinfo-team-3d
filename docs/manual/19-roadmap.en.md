[← Manual index](../manual.en.md)

## Roadmap

The v1.0 scope was set by the PI's directive ([#1336](https://github.com/ehojune/bioinfo-team-3d/issues/1336), 2026-11-09). The earlier fine-grained roadmap (v0.25 to v1.25, PI decision ⑥ #298, 2026-10-03) moved into the past declarations and post-v1.0 goals below; the original table is in this file at `53a5b339`.

| Version | Definition | Pass criteria | Now | Remaining |
|---|---|---|---|---|
| v1.0 | A core research team that a bioinformatician trusts and uses every day. CSO → literature scout → analysis (bioinfo-agent) → biological interpretation → a cross-vendor reviewer finish a public-data study locally, on WSL and on HPC while sharing one HPC session | Four closing tasks done: HPC access stage D (#1295), session sharing (#1332), multi-staff HPC trial (#1331), one completed KRAS literature study (#910) | #1295, #1332 and #910 closed. #1331 is running the airway RNA-seq trial | Finish #1331 |

**v2.0 extensions that v1.0 keeps off by default.** The code stays; a setting turns them on.
- Semantic and ontology checks (layers 2 and 3): `semantics.mode`, `research.semantic_report` ([Semantic model](14-semantic-model.en.md))
- Paper2Agent contractor hiring: `recruit.enabled` ([Contract staff](10-contract-staff.en.md))

**Past declarations.**
- v0.25 (2026-10-04): 4 of the PI's 5 public-data requests reached review accept without developer help
- v0.5 (2026-10-08): two research requests went CP1 → CP2 → review accept → claim-anchored report, and `labhq verify` exited 0

**Goals after v1.0** (moved from the earlier roadmap):
- One controlled-data study, with audit records showing the original never left the cluster or entered an LLM conversation (former v0.9). The Windows runner does not start while a restricted zone exists
- Another researcher installs from the docs alone and finishes one public-data request (former v0.95). The automatic rehearsal on a fresh Windows clone and venv, `labhq init` and doctor exist
- Another researcher solves two research problems with their own controlled data (former v1)
- One manuscript draft whose figures and citations link to outputs through claim anchors (former v1.25)
- Staff control during a run and the runaway breaker (#59). Staff control is in ([Staff controls](06-web-office.en.md#staff-controls)). The breaker runs only as a shadow: when the runner sees the same tool and input 8 times in a row or 5 failed tool calls in a row, it emits one `agent.breaker` event and a feed warning, and the staff member is not stopped
- Memory across requests and project memory (#60), performance review (an evaluation set) and the formal probation evaluation (reproducing a paper's example)

Outside versions:
- **iOS app**: a SwiftUI (or Expo) app on the same WebSocket protocol and approval push (APNs). Until then, the home-screen web app
- **Governance layer**: an institution above the labs that handles rules, audit and budget. The structure is on hold
- fan-out mode (#62), triggers: schedule, issue label, webhook (#61)
- Cloud-resident agents (#79), choosing the project-report channel (#70)
- Changing staff models in the office (drag and drop), Tauri desktop packaging
