# HPC broker routes (#1307)

Lookup-only implementation and verification notes. No real HPC access was used.

| Route | Contract |
|---|---|
| `POST /jobs/submit` | Submission only; any `op` field is rejected |
| `POST /jobs/scheduler/status` | Token identity; running writable task; owned runner-submitted job, including completed jobs |
| `POST /jobs/scheduler/queue` | Same identity/task checks; returns only owned runner-submitted jobs |
| `POST /jobs/scheduler/cancel` | Same checks; owned nonterminal runner-submitted job |

Ownership means the same agent and either the same task or a nonempty matching request. Array children inherit the tracked parent's scope. Local jobs and caller-reported legacy records do not grant HPC ownership. The route chooses the operation; a body `op` cannot override it. Scheduler replies pass through the existing site redactor.

For each command, allow shell queue wait + all reconnect attempts/backoff + execution. Status can run two commands, each reconnecting independently. With default reconnect settings and a 60-second command timeout, the runner may take `2 × (60 + 90 + 15 + 60) = 450` seconds. The old tool waited `345 + 30 = 375`; it now waits `450 + 30 = 480`. Staff CLI timeouts also use the profile's session configuration. Missing profiles still fail at the tool boundary.

`tests/test_hpc_broker_routes_1307.py` covers authentication, forged identity, ownership, arrays, completed jobs, queue filtering, legacy-route rejection, redaction and a simulated two-command status clock. Existing wiring, scheduler, failure, submission and request tests passed locally: **357 passed, 6 skipped**. Skips retain platform restrictions. The local environment lacks pytest-timeout, producing one configuration warning; hosted CI remains required.

Review [P2 4496682580](https://github.com/ehojune/bioinfo-team-3d/pull/1318#discussion_r4496682580): queue ownership filtering now runs on the event loop after the scheduler worker returns, so concurrent submissions cannot mutate the job registry during a worker-thread iteration. A synchronized submission regression fails before the fix and passes after it; all route/wiring checks pass.

The pre-existing transfer redaction test still calls the retired submit/op dispatch; its focused run fails at line 621 because the old call is now rejected before scheduler access. Its migration requires adding `tests/test_hpc_transfer_1308.py` to this assignment before editing it. [Scope request](https://github.com/ehojune/bioinfo-team-3d/issues/1307#issuecomment-6693367241). Merge remains held pending that migration, final-head CI and actual review.
