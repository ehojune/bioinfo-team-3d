[← Manual index](../manual.en.md)

## Command line (CLI)

**Work kind** next to the web office input (auto, simple, research) decides whether this request goes to CP1 and CP2 when the research lane is on. If auto, the [intake rules](../research_protocol.en.md) decide, and research can only be sent to the team (CSO). Whatever you do in the web office can also be done on the command line. Choose the config with `LABHQ_CONFIG` (or `--instance <name>`).

```bash
labhq send "Find the cell types with high CD276 in public lung adenocarcinoma scRNA-seq, through QC"   # CSO orchestration
labhq send --project my-project "Standard QC for the new WGS batch"       # also report the result to that project's GitHub
labhq send --agent analyst "Volcano plot from the DE results in outputs/"  # directly to one staff member
labhq send --team "Even if it looks small, include an independent review"  # turn off automatic solo handling and force the team
labhq send --work-kind simple "Metadata table for GSE10072 samples"        # without CP1/CP2 even if the research lane is on (research forces the opposite)
labhq send --ref scverse/scanpy --ref doi:10.1038/nature12373 "Reproduce it the same way"  # reference pointers (repeatable)
labhq send --plan-only "Write only the analysis plan with the same roster"  # ends before execution and science review
labhq send --plan-only --cso-model gpt-6-astra "Compare plans for the same request"  # change the CSO model for this request only
labhq send --dry-run "Preview calls and cost for the same request"        # no model, tool or network calls
labhq note req_123 "Please also make a table"                            # delivered from the next step of a running request
labhq cancel req_123         # cancel a request: stop running tasks, clean up waits and cards, partial-result report. Same if done twice
labhq task steer task_123 "Look at chr1 only"   # staff control during a run: steer | pause | resume | halt (#59)
labhq watch                  # live events. Approval results are written as allowed, declined, revision requested or timed out
labhq approvals              # pending approvals → labhq approve <id> [--deny --note "..."], CP2 uses --choice approve|revise|deny (revise requires --note)
labhq approve <clarify-id> --note "1: a, 2: b"  # a question card is not sent without an answer, and the request is kept
labhq approve <question-id> --note "a"          # same for a staff question with options. Options appear in approvals and the decline message as a) b) …
labhq verify <request_id>    # recheck output sha256 and report claim anchors on the runner PC (--json, --bundle audit.zip)
labhq report <request_id> --pdf   # save a report with figures as PDF (--out file). Without a browser, HTML and the reason
labhq recruit --repo https://github.com/scverse/scanpy --focus "Preprocessing and clustering" --ttl 14   # if the hiring runner is off, it is not sent and returns 409
labhq talent                 # talent pool
labhq contract extend c_scanpy --days 14    # extend | release | activate | rehire
```

If a model safeguard refuses the plan, the `model_refused` reason is stored and shown in the web and `status`. There is no automatic retry or model switch. If you choose, on the decision card, another allowed CSO model or a revised request wording and approve, it plans as a new request and goes through the existing approval and safety checks again. "End as is" does not create a new request. The original failure record is kept. If the new plan of a continuation is refused, the previous round's result, end outcome and the cause of refusal are kept separately, and a previous result is not overwritten even when step names are the same. In that case, choosing another model resumes the same request and planned round from a new plan and CP1, keeping cumulative cost and budget. Only a wording revision explicitly creates a new request. A card whose request, plan or refusal record has changed is not applied.

`labhq send` (the default, which waits) and `labhq watch <request_id>` print, after the line saying the request ended and the report, the request bundle path and reproducibility grade that follow (`Request bundle:` and `Reproducibility grade:`), then finish. The gateway saves the finished record first and builds the bundle afterwards, so this information arrives late as a separate `request.bundle` event, and the CLI waits up to 60 seconds for that event (or the bundle field of the request record). A request that ran no step and has no bundle is not waited for, and if it does not finish within 60 seconds it says "the request bundle is still being built" and ends, and the path appears later in `labhq status`.

`labhq verify` counts an output a whole-request HPC step left in the HPC data folder on purpose (a file over the fetch cap, #1348) as "remote" (원격 보관), not as a problem. Its size comes from the remote list in the step folder's `manifest.json`. A file on this PC is still compared by sha256, and an output neither here nor in the remote list is "missing" (#1347). A report figure missing from the bundle for the same reason, with a size above 0 in the remote list, is a "report figure kept remotely" warning instead of a figure-link problem, and the summary line counts these separately; a figure with no record or size 0 is still a problem (#1347).

`send --dry-run` reads only the local config and staff YAML. It shows the calls up to the briefing, prior-research survey and first CSO plan, and the count per staff member, and leaves later steps that can be known only once a model makes the plan as **undetermined**. The `model_tier` in `agents/core/*.yaml` and `recruit.contract_model_tier` of contract staff are one of `fast`, `balanced` or `reasoning`. The per-call range is read from one place, `model_tier_prices`, and if the official source and check date are not written or the value is empty, it is **cost unknown**.

`model_tiers` (in `config/labhq.yaml`) is a table of which model each tier (`fast`, `balanced`, `reasoning`) means on each engine. It is empty by default, and while empty a tier picks no model, so every staff member runs the `model` in its own YAML (no model name or price is made up). Filling it in does two things. (1) A staff member whose YAML has **no** `model` runs the model its tier means on its engine. A `model` written in the staff YAML always wins, and a task's `agent_overrides` and the CSO model choice (`cso_model`) also come before the tier. (2) After a request, the cost summary reports a staff member whose **declared tier means another model than the one that actually ran** as `model_tier_mismatch:<staff>:<tier>:<model that ran>`, and the cost line reads like `선언 등급과 다른 모델: lit_scout(fast) → claude-opus-5-5`. An alias and a full name count as one model (`opus` is `claude-opus-5-5`; `gpt-6` is not `gpt-6-luna`). With no entry for the engine, or an empty table, there is no warning. A tier does not change effort, and `send --dry-run` shows `(tier·model)` on each row, or **model undetermined** when no model can be named. A way for a plan step to carry its own `model_tier` is open (`orchestrator.step_model_tiers`, off by default), but **CSO choosing a tier per step does not exist yet** (it is one part of #625 step 2, and only a stored plan's value is followed). When on, a step's tier can only be **lowered from** its staff member's declared `model_tier`: a higher one is put back to the declared tier by the server and noted in the plan warnings. `validation` and `report` steps never go below `balanced`, and a staff member with no declared tier ignores a step tier. The step tier is part of the plan hash, so the PI sees and approves it at CP1 (a plan without it keeps its hash). The runner runs the step on the `model_tiers` model of that engine, ahead of the staff YAML's explicit `model` (only a lower tier is ever sent), and changes nothing when the table has no entry for the engine. The cost record keeps the step's tier, so a deliberately lowered step is not flagged as `model_tier_mismatch`.

`labhq codex-review` is in [Per-project GitHub reports](12-github-reports.en.md#per-project-github-reports), and the bench and semantics commands are in [Comparison bench](16-bench.en.md#comparison-bench) and [Semantic model and ontology](14-semantic-model.en.md#semantic-model-and-ontology).

### Searching past requests

`labhq recall "gene"` finds done, failed, cancelled and declined requests in the current instance. `--instance NAME` is also supported.
It gives the same results as `GET /api/knowledge/search?q=gene` authenticated with the client token, at most 5 items with 2,000 characters of body in total.

- The search term is 2–200 characters, taken literally. Three or more characters use SQLite FTS5 trigram; two characters, or an environment without trigram support, use a LIKE search over the latest 2,000 indexed items.
- Only the gateway writes, to its own `state_dir/knowledge.sqlite3`, the request ID, a redacted request summary, step status, `failure_kind` and the request source. Logs, output files and original references are not search material.
- Restricted sentences are replaced with a marker, and private references and secrets are masked. Material whose format cannot be determined is excluded, and the current masking rules are also applied at search time.
- At startup it processes past requests 25 at a time and continues from a checkpoint. A newly completed request stays a single item even if reprocessed. An indexing failure does not change request completion and is retried after 30 seconds or at the next completion.
- The `local recall` row of `labhq doctor` shows the index count, the last success time and whether the LIKE fallback is in use. Automatic injection of search results into prompts, reflection and a registry do not exist yet.

### Request lifetime and PI review

**Partial-failure marker** (#1220): a request can end `done` although some steps or branches failed. If any step recorded a failed or skipped result, the status label becomes `완료 · 일부 실패 N` (done · N failed), and `labhq status` (`일부 실패한 단계: s2, s3`) and the completion line of `labhq watch` say which steps. The web request list and detail status and the PDF report header use the same label. The request summary (`/api/requests`) carries `failed_steps`. It is neither a new status nor a new outcome value: it is computed on the spot from the step results the request already holds, so it is not stored. Only steps that recorded a result are counted, so a request that left plan steps unused on purpose (`plan_only`, a solo route that keeps the team plan, a plan approved with the evidence checkpoint off) does not get it, and neither does a `failed` or `cancelled` one. The limitations section of a research report names the branch beside the step id (`B1 (갈래 B): 단계 실패 …`). No notification pop-up is raised.

Even if only the runner stops or restarts, the gateway stores the request as `interrupted` and opens one resume card. The shutdown evidence also stays in task cancellations during shutdown and in restart-recovery results.

A follow-up question also stays as `interrupted` and can accept questions again. A consultation query ends its wait with the interruption reason and a retry notice, and a result edit resumes automatically when the editor runner reconnects. The asyncio cancellation of a task cancel or gateway shutdown is propagated as is.

`labhq request mark-interrupted <id>` corrects only a failed request whose stored shutdown evidence (exit code, cancellation and shutdown-in-progress marks, or a gateway signal record within 10 seconds before the failure) matches, and opens a resume card. The previous state is kept in an event, and it refuses when there is no evidence.

The order is run and report writing → **Report review pending** → PI **Review done**. A failure, cancellation or decline is also finished after you check the result and the reason. On the CLI use `labhq review-done <request_id>`. `status` shows review pending, and `send` and `watch` end after printing the report. The gateway keeps the execution result and stores the PI confirmation time (`pi_reviewed_at`), the audit record and a `request.reviewed` event together. Pressing it again does not record a duplicate.

### Solo handling

Among ordinary requests, the CSO hands off to solo only a lookup, a table, one QC, one figure, or a literature list that one staff member can finish in about 30 minutes and one turn. The plan's DAG remains visible as a fallback, and if the solo answer or output is empty or fails, it continues as `solo failed → team`. The research lane, HPC, controlled data and several dependent steps are always team. You can force the team with **To the team** on the web or `--team` on the CLI, and `--agent` and `--team` cannot be used together.

Without `orchestrator.solo_agent`, the feature is off. For example, connect the roster's `solo` staff member as below. With `solo_review: true`, a solo result also goes through one science review; the default is `false`.

```yaml
# agents/core/solo.yaml
id: solo
name: Solo handler
role: Handle a small request in one turn
engine: codex
model: gpt-6-astra
effort: ultra
```

```yaml
# config/labhq.yaml
orchestrator:
  solo_agent: solo
  solo_review: false
```
