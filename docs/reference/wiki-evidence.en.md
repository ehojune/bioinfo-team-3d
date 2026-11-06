# Wiki exposure history and blocking public publication (#1037)

Lookup-only. Behavior is described in [manual chapter 12](../manual/12-github-reports.en.md); the evidence contract is in the #827 comments. The draft of the evidence-level check (the rule that keeps `wiki_summary` from being promoted to direct reading of the original) is not in this PR and is on branch `codex-worker1/1037-lane`: it changes the engine result contract's hash and prompt rules, so it is handled separately. Public protection is still **UNVERIFIED**.

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
| Restart and old-runner integration tests; a follow-up request inheriting the exposure | **not yet** |
| The evidence-level check (no promotion of `wiki_summary`) and the result-contract prompt rules, independent source authentication, masking wiki text and paths | later |

## Limits

- The record says "the wiki tool could be used", not "something was copied". `possible` blocks whether or not anything was copied.
- The result-storage choice card still offers publishing for a blocked request and the upload is refused when attempted (the card display is not changed yet).
- There is no rule yet for a follow-up request to inherit the exposure of the original. A new request starts as `none`.
