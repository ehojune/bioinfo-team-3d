# Wiki exposure history and blocking public publication (#1037)

Lookup-only. Behavior is described in [manual chapter 12](../manual/12-github-reports.en.md); the evidence contract is `labhq/evidence/wiki_contract.py` and the #827 comments. Public protection is still **UNVERIFIED**.

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
| The race between the publication check and the outward write; restart and old-runner integration tests | **not yet** |
| Independent source authentication; masking wiki text and paths | later |

## Limits

- The record says "the wiki tool could be used", not "something was copied". `possible` blocks whether or not anything was copied.
- There is no rule yet for a follow-up request to inherit the exposure of the original. A new request starts as `none`.
