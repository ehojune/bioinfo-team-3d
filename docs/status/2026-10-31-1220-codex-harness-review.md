# Boundary harness review fixes

- Synthetic diagnostic supplies a fresh UUID and requires both ready and receipt to match it.
- The entire diagnostic runs in a bounded subprocess. Deadline expiry reports inconclusive and direct worker termination; descendant termination remains unverified.
- Worker environment excludes host secrets and uses a temporary home. Windows targeted test: 37 passed, 1 xfailed. The xfail records missing authenticated observer evidence.
- Producer integration on the updated API, actual protection and cleanup remain unverified. PR #1051 remains draft/hold.
