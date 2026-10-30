# Boundary probe, stage 1 (incomplete)

Run `python scripts/boundary_probe/probe.py --launcher-json '[...]'`. The JSON array is a launcher argv prefix; Python and the child command follow it. Use only a disposable test launcher. An empty array is an unisolated negative control, never evidence of protection.

Results use `pass`, `fail`, `inconclusive`, `unsupported`, or `launch_error`. A protection pass requires a successful allowed-work control and a child response proving that the test ran. Unexpected errors, missing prerequisites and launch failures are not passes. The JSON excludes subprocess output and fixture contents. `protection_verified` remains false: individual fixture results do not establish real isolation.

Implemented fixtures: allowed work, external read/write, symlink/hardlink, environment canary, loopback socket and inherited descriptor (POSIX only). The listener and files contain synthetic data. The socket case counts only an explicit permission denial as a pass; connection errors are inconclusive.

Parent replacement and external-writer fixtures use a run-ID-bound child ready/continue barrier and a host mutation witness. The child must read the original bytes after the host sees changed bytes; a denial, missing response, timeout or failed host mutation is inconclusive. This tests a synthetic arrangement, not sealed storage. Symlink creation may be unavailable on Windows.

The detached-child fixture starts a finite synthetic descendant, waits for its ready marker, then signals it after the launcher exits. A matching host-observed marker is a failure. An observed PID exit before the earliest natural timeout is reported separately, but is inconclusive: it does not authenticate the cause or a supervisor cleanup receipt. A missing marker or PID observation is never proof of cleanup.

On Windows, `--interop-distro labhq-666-fixture` opts into synthetic Windows-to-WSL and WSL-to-Windows execution probes. The reverse probe asks WSL Python to execute the Windows Python fixture, which writes a run-bound host marker. A matching marker is a failure even if the child response is lost; absent markers and launch errors are inconclusive. Neither direction exercises a real sandbox. The current fixture has automount and interop disabled; failures in it do not prove protection.

`receipt_diagnostic(receipt, run_id)` checks receipt v1 shape and matching run ID; unsealed and claimed sealed receipts remain unsupported. It does not authenticate a supervisor.

`python scripts/boundary_probe/integration_probe.py --boundary-module <path> --broker-module <path>` loads the two supplied modules directly. The unsealed diagnostic checks host-side broker readiness, run-ID match, allowed response, wrong run and extra destination rejection, literal synthetic-secret reflection rejection, and reconnect after cleanup. Namespace-child success and cleanup status are producer-reported, not independently authenticated. The modules must be trusted test code; the fake upstream and credential are fixture-only. A diagnostic pass establishes neither arbitrary request policy nor general secret non-disclosure. Source revisions may change while integration is under development; repeat against final heads.

`--broker-kind process` selects the actual `ProcessBroker` with a fake upstream. Response checks compare the complete frame to the fixed public acknowledgement or rejection, including a fake-secret upstream result; this does not test the real transport or all log channels.

The control and tool-use cases call real labhq functions with run-bound synthetic state and verify markers from the host. Their scope is `direct_function_only`: a pass does not establish Claude hook invocation or launcher integration. Missing imports or witnesses are inconclusive. An Ubuntu system Python without the package dependencies produced an inconclusive tool-use result.

The built-in job MCP positive control starts the real server with disposable config and lists its tools over stdio, without invoking a tool or making a network request. This `stdio_list_only` result does not establish engine integration or functional broker access.

Pending: positive interop controls in a suitable disposable environment, authenticated proof of detached-child cleanup, actual hook invocation, MCP operation inside the namespace and launcher-driven adversarial probes. Engine compatibility and protection are UNVERIFIED. Do not use this harness to enable the capability or claim protection.
