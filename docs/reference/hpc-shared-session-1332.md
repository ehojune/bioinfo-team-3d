# HPC shared session (#1332)

Lookup-only implementation evidence. Real HPC with multiple employees remains **UNVERIFIED**.

| Path | Ownership and boundary |
|---|---|
| Employee MCP → authenticated broker → runner | Submit, status, queue and cancel all use the runner, including plain `ssh_host`. Token, writable task and owned-job checks remain in place. Plain SSH still opens one counted login per command; only a profile with `connect_argv` keeps one shell. |
| `Scheduler.session` / `site_session` | One lazy session per profile and process. Runner submission, polling, cancellation and transfer use it. Separate runner processes do not share a shell. |
| `HpcSession.run` | Reentrant FIFO lock; heartbeat cannot overtake queued work. Default queue wait 60 s; configured command timeout and output cap remain. A timeout closes the shell; submission is never retried. |
| Shared status batches | Same request and covered job ids join only an in-flight batch. Completed results are not cached. One cancelled waiter leaves other readers running. Accounting fallback shares the staff call deadline; expired work sends no more commands or logins. |
| Connection admission | An opening connection is shared even after it consumes the last permitted login. Actual reconnect admission stays under the session lock. Lost connections at a limit leave all employees waiting and emit a request-scoped PI feed alert. File recovery waits without consuming attempts. |
| Interactive output | BEGIN, quoted `eval` command and END use one physical input line. Shell prompts and bracketed-paste controls stay outside the captured result. |

`tests/test_hpc_session_wiring_1295.py` exercises three authenticated employee tokens against the fake gateway: concurrent submissions, one shared status batch, cross-owner denial, cancellation, isolated script/result/log recovery and one login. The gateway emits interactive prompt controls. Additional regressions cover FIFO expiry, command timeout, login exhaustion, shared-reader cancellation, per-request isolation, call deadline and repeat window alerts.

W1 owns `hpc_login_budget.py` and `hpc_site.py` (#1295/#1334). This change consumes their admission/error API; it does not alter those files, connect to HPC or establish a cross-runner singleton.

After merging #1334, the related mock suite reports 480 passed, 12 skipped, 3 failed. The stdio scheduler-route fixture restores all ten error-propagation failures. The three remaining cancel tests use a legacy broker stub in `tests/test_hpc_followups.py`, outside rev2; assignment check refuses that path. Preserve the assertions and route that stub through the runner after the lead adds its path. Final-head CI/review and merge remain blocked on that change.
