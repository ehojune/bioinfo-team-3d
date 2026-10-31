# Boundary probe, stage 1 (incomplete)

Run `python scripts/boundary_probe/probe.py --launcher-json '[...]'`. The JSON array is a launcher argv prefix; Python and the child command follow it. Use only a disposable test launcher. An empty array is an unisolated negative control, never evidence of protection.

All nonempty launcher prefixes and alternate Python executables are unsupported before any case launches: no current launcher provides trusted descendant lifecycle ownership. Direct execution remains a synthetic negative control, not launcher validation.

Results use `pass`, `fail`, `inconclusive`, `unsupported`, or `launch_error`. A protection pass requires a successful allowed-work control and a child response proving that the test ran. Unexpected errors, missing prerequisites and launch failures are not passes. The JSON excludes subprocess output and fixture contents. `protection_verified` remains false: individual fixture results do not establish real isolation.

Implemented fixtures: allowed work, external read/write, symlink/hardlink, environment canary, loopback socket and inherited descriptor (POSIX only). The listener and files contain synthetic data. The socket case counts only an explicit permission denial as a pass; connection errors are inconclusive.

Parent replacement and external-writer fixtures use a run-ID-bound child ready/continue barrier and a host mutation witness. The child must read the original bytes after the host sees changed bytes; a denial, missing response, timeout or failed host mutation is inconclusive. This tests a synthetic arrangement, not sealed storage. Symlink creation may be unavailable on Windows.

Mutation fixtures run only with the current Python interpreter and no launcher prefix. Other configurations are unsupported before launch until a trusted descendant lifecycle backend can clean up and join the process tree.

The detached-child case is unsupported before launch for every current launcher, including direct execution: none provides trusted descendant lifecycle ownership. A future authenticated lifecycle backend is required before enabling it; no flag or receipt alone establishes that ownership or protection.

Both Windows-to-WSL and WSL-to-Windows interop cases are unsupported before launch, even when `--interop-distro` is supplied. No current launcher owns and joins WSL descendants after timeout. Enabling these probes requires a trusted lifecycle backend and positive controls in a suitable disposable environment.

`receipt_diagnostic(receipt, run_id)` checks receipt v1 shape and matching run ID; unsealed and claimed sealed receipts remain unsupported. It does not authenticate a supervisor.

`python scripts/boundary_probe/integration_probe.py --boundary-module <path> --broker-module <path>` loads the two supplied modules directly. The unsealed diagnostic checks host-side broker readiness, run-ID match, allowed response, wrong run and extra destination rejection, literal synthetic-secret reflection rejection, and reconnect after cleanup. Namespace-child success and cleanup status are producer-reported, not independently authenticated. The modules must be trusted test code; the fake upstream and credential are fixture-only. A diagnostic pass establishes neither arbitrary request policy nor general secret non-disclosure. Source revisions may change while integration is under development; repeat against final heads.

`--broker-kind process` selects the actual `ProcessBroker` with a fake upstream. Response checks compare the complete frame to the fixed public acknowledgement or rejection, including a fake-secret upstream result; this does not test the real transport or all log channels.

The diagnostic hashes each supplied source before and after execution; changes or unreadable sources make it inconclusive. Supply both `--expected-boundary-sha256` and `--expected-broker-sha256` to label matching inputs pinned; otherwise they are provisional. Invalid JSON/base64, missing and extra fields, and wrong run must each be rejected, followed by an allowed request proving the listener still works. These bounded synthetic controls do not prove general protocol security.

The harness compiles the bytes it hashed. ProcessBroker also imports its module in a spawned process; its default bytecode cache must be absent before and after the run, and bytecode writes are disabled for that child. An existing cache makes the result inconclusive; the harness does not remove producer files.

The control and tool-use cases call real labhq functions with run-bound synthetic state and verify markers from the host. Their scope is `direct_function_only`: a pass does not establish Claude hook invocation or launcher integration. Missing imports or witnesses are inconclusive. An Ubuntu system Python without the package dependencies produced an inconclusive tool-use result.

The built-in job MCP positive control starts the real server with disposable config and lists its tools over stdio, without invoking a tool or making a network request. This `stdio_list_only` result does not establish engine integration or functional broker access.

A fixture file the child cannot read (for example the socket port or the descriptor number) is a setup failure: the child exits 20 and the case is `inconclusive`, never a denial `pass`.

Pending: positive interop controls in a suitable disposable environment, authenticated proof of detached-child cleanup, actual hook invocation, MCP operation inside the namespace and launcher-driven adversarial probes. Engine compatibility and protection are UNVERIFIED. Do not use this harness to enable the capability or claim protection.

The offline seal fixture adapter models separate producer and observer MACs, externally supplied expectations and a one-use run/nonce set. Its accepted verdict is `synthetic_only`: keys, observer, ledger and resource inventory are test inputs, not authenticated OS observations. Replay state is in-memory and not a production replay store. The pinned producer emits only unsealed diagnostics and has no authenticated observer or sealed-storage interface. Actual producer authentication, independent whole-tree cleanup and protection remain UNVERIFIED.

## Offline lifecycle event v1

Producer events are JSON objects with exactly `version` (integer 1), `run_id`, `nonce`, `sequence` (integer), `kind` (`launch` or `termination`), `identity` (`pid` and `birth_id`), and `auth` (`key_id`, `algorithm`, `tag`). Strings are nonempty; PID is a positive integer. Launch is sequence 1, termination is sequence 2, and both share run, nonce and identity. A missing, duplicate, reordered, mismatched or extra field fails closed. `birth_id` is an opaque process-incarnation identifier, not PID alone.

For this synthetic v1 contract, `algorithm` is `hmac-sha256`; `tag` is lowercase hex HMAC-SHA256 over UTF-8 JSON of the event without `auth`, serialized with sorted keys, separators `(',', ':')`, and ASCII escaping. The verifier selects a preconfigured key by `key_id`; keys are never carried in events. The fixture key is public test data and grants no trust. `tests/fixtures/process_boundary_seal/lifecycle-v1.json` is the byte-level interoperability vector.

Observer inventory is separately collected, ordered and run/nonce bound; producer events alone cannot prove cleanup. Production authentication, protected replay storage and independent OS process/resource collection require a separately trusted implementation. This offline contract does not establish protection.
