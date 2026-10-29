# Claude Code + WSL2 boundary evidence

Status: **UNVERIFIED**. A mock or skipped test is not evidence that a sandbox
protects an actual engine. Keep the existing command and path gates enabled.

The current test implements evidence classification and checks the W1
`build_launch()` contract. It does **not** execute boundary probes: that
contract currently raises `SandboxUnavailable` and has no executable launch
interface. A successful return deliberately fails the test until real probe
execution is implemented and reviewed. This is a launch blocker, not a
sandbox denial. Run the classification tests in a checkout with pytest:

The test also checks a local loopback listener with a control connection. It is
only a harness component; it is not connected to a sandboxed engine. Listener
silence alone does not prove denial or that a command ran.

```sh
python -m pytest -q -rs tests/test_claude_sandbox_boundary.py
```

To reproduce the current launch refusal, combine this test with the W1 branch
containing its launcher and run the same command. The final test reports a skip
with the refusal reason. The real disposable WSL command cannot be specified until W1
publishes an executable contract and the fixture is identified. Do not replace
it with a mock or execute an arbitrary command outside that contract.

## Required disposable test

Use a dedicated WSL fixture and the W1 launcher contract; do not change the
operator distribution, its configuration, or its lifecycle. Record WSL and
Claude versions, effective policy and configuration sources, launcher revision,
fixture identity, each probe and result. Use synthetic data only. A missing
dependency, unknown effective policy, timeout, ambiguous result, or unexercised
probe is **UNVERIFIED**, not a denial PASS.

| Probe | Evidence needed |
|---|---|
| Sandbox unavailable | Launcher refuses to start or execute, without unsandboxed fallback. |
| Private read | Existing readable synthetic private file cannot be read by Bash; a positive control confirms it exists and is readable outside the sandbox. |
| Environment write | Create, overwrite, rename and replace attempts against a protected synthetic environment fail; check content and identity. |
| Private network | A controlled private/loopback listener observes no connection; test direct, proxy, redirect and DNS paths where supported. A failed request without listener evidence is inconclusive. |
| Allowed writes | Bash writes and reads back unique markers under both `outputs/` and `.tmp/`. |
| Resume and cancel | Repeat denial and allowed-write probes after resume; cancel a running child, verify it terminates, and repeat probes in the next session. |
| Policy conflict | Exercise user/project/CLI conflicts, merged arrays, `filesystem.disabled`, weaker network and socket settings; confirm effective policy and fail closed. |

Record the exact command and observed result for each probe, including skips.
The harness must distinguish a verified denial from a launch error, a missing
target, a timeout, or a command that never ran. Network tests must use only
disposable local endpoints and never contact production services.

## Separate, unsupported claims

The Bash sandbox does not establish a whole-process boundary for Read/Edit/Write,
MCP or hooks. It does not by itself prove a sealed snapshot or isolate Windows
and WSL administrators. Explicitly identify trusted administrators before any
broader claim. Test Windows-path writers, parent/alias replacement, an external
writer changing and restoring content during execution, and receipt-to-snapshot
binding. A matching digest before and after execution does not prove immutability.

Interop needs independent checks of effective settings, mounts, PATH, inherited
environment, file descriptors and sockets; probe direct and child execution,
and repeat after a disposable WSL restart. Test child cleanup and inherited
socket/FD access independently. Until these are exercised under an agreed
whole-process design they remain **UNSUPPORTED**, not a Bash sandbox PASS.
