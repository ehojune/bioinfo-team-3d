# HPC broker routes (#1307)

Lookup-only implementation and verification notes. No real HPC access was used.

| Route | Contract |
|---|---|
| `POST /jobs/submit` | Submission only; any `op` field is rejected |
| `POST /jobs/scheduler/status` | Token identity; running writable task; owned runner-submitted job, including completed jobs |
| `POST /jobs/scheduler/queue` | Same identity/task checks; returns only owned runner-submitted jobs |
| `POST /jobs/scheduler/cancel` | Same checks; owned nonterminal runner-submitted job |

Ownership means the same agent and either the same task or a nonempty matching request. Array children inherit the tracked parent's scope. Local jobs and caller-reported legacy records do not grant HPC ownership. The route chooses the operation; a body `op` cannot override it. Scheduler replies pass through the existing site redactor.

Review P1 4496811746 exposed ambiguous submissions without a recorded job ID. Queue now reports an uncertainty error when the scheduler returns a job without a runner submission record, keeping its ID and details private. It does not adopt that job. Submission errors require PI reconciliation before resubmitting, even after an empty queue. This also covers jobs that finished before the query. Existing untracked account jobs can therefore block a queue read until reconciliation. Timeout and unparseable-ID regressions exercise submission, broker queue and MCP error propagation.

For each command, allow shell queue wait + all reconnect attempts/backoff + execution. Status can run two commands, each reconnecting independently. With default reconnect settings and a 60-second command timeout, the runner may take `2 × (60 + 90 + 15 + 60) = 450` seconds. The old tool waited `345 + 30 = 375`; it now waits `450 + 30 = 480`. Staff CLI timeouts also use the profile's session configuration. Missing profiles still fail at the tool boundary.

`tests/test_hpc_broker_routes_1307.py` covers authentication, forged identity, ownership, arrays, completed jobs, queue filtering, legacy-route rejection, redaction and a simulated two-command status clock. Existing wiring, scheduler, failure, submission and request tests passed locally: **357 passed, 6 skipped**. Skips retain platform restrictions. The local environment lacks pytest-timeout, producing one configuration warning; hosted CI remains required.

Review [P2 4496682580](https://github.com/ehojune/bioinfo-team-3d/pull/1318#discussion_r4496682580): queue ownership filtering now runs on the event loop after the scheduler worker returns, so concurrent submissions cannot mutate the job registry during a worker-thread iteration. A synchronized submission regression fails before the fix and passes after it; all route/wiring checks pass.

Rev2 migrated the transfer redaction test to `_on_scheduler_request` and made its ownership stub accept the `include_terminal` keyword. The original site-removal and error-reason assertions remain intact. Its focused failure was reproduced before editing; transfer, broker-route and session-wiring tests now report **144 passed, 10 skipped**. Local dependency deprecation warnings remain. [Scope approval](https://github.com/ehojune/bioinfo-team-3d/issues/1307#issuecomment-6693681765). Merge remains held pending final-head CI and actual review.
