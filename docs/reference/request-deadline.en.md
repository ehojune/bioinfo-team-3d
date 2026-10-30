# Request wall-clock limit (#877)

Lookup-only. The behaviour is in `enforce_request_deadlines` and `_count_pi_wait` in `labhq/gateway/server.py`.

With `orchestrator.request_wall_clock_s` (seconds) set, the gateway counts the wall-clock time of a whole request itself. It is separate from the per-step `runner.task_timeout_s`: however many steps staff go through, their sum cannot pass the limit.

| Item | Detail |
|---|---|
| Setting | 0 is off (default). To turn it on use 60 or more, at most 7 days (604800). Below 60 is refused as minutes written as seconds. |
| Fixed at | When the request is accepted, `limit_s`, `held_s` and `deadline_at` are written to `wall_clock` in the request record. A later config edit does not change a request already accepted. A request with the limit off keeps `limit_s: 0`. |
| Counted | From acceptance until the request enters `review_pending`. Tool-card waits in that span count: staff are alive. |
| Not counted | Time in `waiting_pi` (a PI decision card) is added to the deadline as `held_s`, settled from the real status transitions, so the 15 s watch interval does not decide the amount. Review and publishing after `review_pending`, and `interrupted` requests, are never cut. |
| Cancel | An active request past `created_at + limit_s + held_s` is cancelled through the existing `cancel_request` with `cancel_reason: request_deadline`. It shows as "cancelled by time limit", and the report says labhq cancelled it at the request time limit, not the PI. |
| Receipt | `deadline_receipt` holds `limit_s`, `held_s`, `deadline_at`, `fired_at`, the tasks cancelled, the tasks that did not report a result, and the termination verdict. |

## Termination verdict
- `confirmed`: every cancelled task reported a result after the cancel. The runner ends the process tree before it reports.
- `unconfirmed`: some task did not report. It is not assumed stopped; the task list is written down.
- `no_running_tasks`: there was nothing to cancel.

## Restart
- The same `created_at` and the recorded `held_s` stay. The time the gateway was down is not subtracted.
- A restart turns active requests into `interrupted`. Resuming applies the same deadline, and a deadline already past cancels at once.
- A `waiting_pi` interval a restart left open is not added.
- If the gateway stops in the middle of a cancel, the next start finishes it in `finish_interrupted_cancels` and writes the receipt.

## Limits and what is unchecked
- The watch looks every 15 s, so a deadline can be judged up to that late.
- Cutting a live model call and its child processes at the deadline is checked only by unit tests of task state. It needs a separate isolated-instance check.
- The external Windows Job owner path (proof c2) is an outer limit apart from this setting; this setting does not replace it.
- No real request with the limit on has been run yet.

Verified by: `tests/test_active_request_cancel.py`
