# Staging digest diagnostic

- After the callback, the diagnostic re-reads snapshot, runtime and policy; changed or missing payloads fail and trigger cleanup.
- Local mock tests: 35 passed, 1 xfailed. Observations are untrusted and point-in-time; protected launch, independent cleanup and actual protection remain unverified.
- PR #1050 remains draft and on hold. The manual review finding awaits separate ownership.
