# Shadow test cost (#811, draft)

Lookup-only: test orchestration changes; Shadow behavior, fsync and SQLite durability stay unchanged.

| Boundary | Evidence retained |
|---|---|
| Four off/on comparisons | One module-scoped off run yields immutable JSON, then four independent on runs. Each comparison decodes its own value; stores are closed, never shared |
| Isolation | Fresh-process off/import cases, fail-open, privacy/read-scope, restart and worker timing tests remain in the ordinary suite |
| Full/actions removal | Compile, residual-reference scan, state recovery/resume approvals and essential E2E remain. Child execution selects contract nodes; all surviving tests are collected first |
| #804 collection | Remove the script-dependent boundary harness with its script. Mainline CP1 tests remain. Five observer-specific tests move out of mixed core suites with assertions preserved; other core tests remain collectable |

The four comparisons structurally change from 4 off + 4 on lab calls to 1 off + 4 on; this is not a timing claim.
Issue #811 cites prior Windows removal times of 135.16/54.59 s and comparisons of 17–20 s each.
Current CI duration and improvement are **unverified**. Local mock/static timings measure different work.
No live lab, process/thread fixture, real removal, research, bench or PI state was exercised locally.
