# Wiki exposure history and blocking public publication (#1037)

Lookup-only. Behavior is described in [manual chapter 12](../manual/12-github-reports.en.md); the evidence contract is in the #827 comments. The draft of the evidence-level check (the rule that keeps `wiki_summary` from being promoted to direct reading of the original) is not in this PR and is on branch `codex-worker1/1037-lane`: it changes the engine result contract's hash and prompt rules, so it is handled separately. Public protection is still **UNVERIFIED**.

## What was decided

| Item | Value |
|---|---|
| Record | `wiki_exposure` on the request record (`labhq/evidence/wiki_exposure.py`) |
| `none` | The request was made by a gateway that records this, and no task has been sent to a staff member who could be given the wiki tool |
| `possible` | A task was sent to such a staff member. It never goes back to `none` |
| missing or any other value | **unknown**: an older request with no record, or one that cannot be read |
| Public publication | Allowed only for `none`. `possible` and unknown are blocked |

## How it is bound

- Just before it sends a task, the gateway's `dispatch` (`_bind_wiki_exposure`) sets the task's `meta["wiki_tools"]` from the `wiki_tools` in the roster that runner sent. When the wiki is possible or unknown it raises the request to `possible`. The write comes before delivery.
- A roster without the `wiki_tools` key (an older runner), or without that staff member, is unknown: the request is raised to `possible` and the task gets no permission.
- The runner attaches `labhq_wiki` only to a dispatch whose `meta["wiki_tools"]` is true (`_mcp_servers(..., wiki_bound=True)`). A roster sent earlier cannot authorize a different tool set later.
- A request with no history is not written to by a dispatch (it stays unknown).

## What this stage blocks and what it does not

| Path | State |
|---|---|
| Step progress publishing to GitHub (`ResultStorage.progress_step`) | blocked |
| Report, upload and round publishing, the pipeline contribution in `ProjectReporter` | **not yet** |
| CP2 | **not yet** |
| The race inside step progress publishing (the exposure rises mid-publication) | blocked: checked again right before each file is written |
| The race in the other publishing paths; restart and old-runner integration tests | **not yet** |
| The evidence-level check (no promotion of `wiki_summary`) and the result-contract prompt rules, independent source authentication, masking wiki text and paths | later |

## Limits

- The record says "the wiki tool could be used", not "something was copied". `possible` blocks whether or not anything was copied.
- There is no rule yet for a follow-up request to inherit the exposure of the original. A new request starts as `none`.
