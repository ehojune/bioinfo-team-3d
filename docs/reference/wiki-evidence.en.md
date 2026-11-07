# Wiki exposure history and blocking public publication (#1037)

Lookup-only. Behavior is described in [manual chapter 12](../manual/12-github-reports.en.md); the evidence contract is in the #827 comments. The evidence-level check (the rule that keeps `wiki_summary` from being promoted to direct reading of the original) revives the W1 draft (`e2a36148`); the result schema hash changes on purpose. Public protection is still **UNVERIFIED**.

## What was decided

| Item | Value |
|---|---|
| Record | `wiki_exposure` on the request record (`labhq/evidence/wiki_exposure.py`) |
| `none` | The request was made by a gateway that records this, and no task has been sent to a staff member who could be given the wiki tool |
| `possible` | A task was sent to such a staff member. It never goes back to `none` |
| missing or any other value | **unknown**: no record, or one that cannot be read. Except that numeric `created_at` and `updated_at` (bumped on every save) both before the `labhq_wiki` release (PR #1040 merge, 2026-10-29T21:01:56Z) count as `none`, since the request was never touched while the tool existed. Created before but updated after it (a resume, for one), or with a time missing, it is unknown |
| Public publication | Allowed only for `none`. `possible` and unknown are blocked |

## How it is bound

- Just before it sends a task, the gateway's `dispatch` (`_bind_wiki_exposure`) sets the task's `meta["wiki_tools"]` from the `wiki_tools` in the roster that runner sent. When the wiki is possible or unknown it raises the request to `possible`. The write comes before delivery.
- A roster without the `wiki_tools` key (an older runner), or without that staff member, is unknown: the request is raised to `possible` and the task gets no permission.
- The runner attaches `labhq_wiki` only to a dispatch whose `meta["wiki_tools"]` is true (`_mcp_servers(..., wiki_bound=True)`). A roster sent earlier cannot authorize a different tool set later.
- A request with no history created after the release is not written to by a dispatch (it stays unknown). One created before it counts as `none`, so a task to a wiki-capable staff member makes it `possible`.

## What this stage blocks and what it does not

| Path | State |
|---|---|
| Step progress publishing to GitHub (`ResultStorage.progress_step`) | blocked |
| `ProjectReporter`: project issue, comments, report, appendix, close; pipeline contribution (before the approval card, and right before the branch, files and PR) | blocked (rechecked right before each write) |
| `RoundRecorder`: the round issue write | blocked (rechecked right before the write) |
| `ResultStorage.upload`: the research-result upload (each document, figure and PDF file) | blocked (rechecked right before each write) |
| The CP2 evidence card | not blocked; one warning line (it is the PI's review, not a publication) |
| The race inside step progress publishing (the exposure rises mid-publication) | blocked: checked again right before each file is written |
| A request opened from another (a CP1 revision, a retry after a model refusal) | blocked: it starts at the parent's level (in its first save) |
| Restart and old-runner | tested: the record is read back from the store (not recomputed), and a request sent to an old runner without the `wiki_tools` key becomes `possible` |
| The recheck right before a new repository is created by the result upload | tested |
| The evidence-level check: a `wiki_summary` row is a literature_claim, tertiary and indirect and cannot be an original (`lineage.original`) (validated in `claims.py`). Without a recorded `full_text` reading on the same claim, a wiki summary alone does not fill `supported` or `partially_supported` (`ledger_errors`). A summary pointing the other way still counts and blocks `supported` (it is not hidden). The report's evidence table writes `wiki summary (freshness)` | blocked |
| The result-contract prompt rules (`RESULT_CONTRACT_FIELD_RULES`) and the result schema (`wiki_summary` in `access_status`, `wiki_freshness`) | done. The result schema hash was updated on purpose (the plan schema, the plan prompt and `VALID_PLAN_SHA` are unchanged) |
| Matching against the audit record when a step result is accepted (`save_salvage` in `orchestrator/cso.py` → `wiki_contract.step_findings`) | wired: a row that cites the wiki is checked against `outputs/wiki_queries.jsonl` in that step's workdir. A locator not in the record, a freshness or origin that differs, an unreadable record, or one broken line makes every wiki row a CP2 refused row (`wiki audit: …`). Rows derived from it and claims resting only on it go with it (fail-closed) |
| Limits | The audit record is a file in the staff member's workspace, so they can rewrite it (this is not independent source authentication). On a runner whose workdir the gateway cannot read (another machine, WSL) it cannot be read and every wiki row is refused |
| Independent source authentication, masking wiki text and paths | later |

## Limits

- The record says "the wiki tool could be used", not "something was copied". `possible` blocks whether or not anything was copied.
- The result-storage choice card still offers publishing for a blocked request and the upload is refused when attempted (the card display is not changed yet).
- A follow-up runs inside the same request and uses the same record. The two paths that open a new request from a parent (the CP1 revision and the retry after a model refusal) inherit the parent's level. Any new code that creates a request another way must follow this rule. A task bound to no request id, or to an unknown one, has nowhere to record.
