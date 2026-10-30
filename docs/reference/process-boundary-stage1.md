# Process boundary: stage 1

Protected launch is **unsupported**. `protected_launch` never starts a process; caller-supplied assertions, digests, or supervisor objects cannot grant authority. This module is not wired into the daemon or an engine.

`python -m labhq.runner.process_boundary --unsealed-diagnostic` explicitly runs a fixed, non-model `true` probe in bubblewrap with an unsealed host runtime. A zero exit establishes only that this command exited zero. The diagnostic is not a protected launch, isolation proof, or capability enablement. Without the flag the command returns the protected-launch refusal.

## Trust and remaining fixture requirements

Windows administrator, WSL root and an explicitly configured supervisor are trusted. The engine and ordinary source writers are untrusted. A future supervisor must create run-specific copies of allowlisted inputs, runtime and policy in Linux storage under its control, outside writable workspace, output and HOME, with no Windows shared-drive backing. Verification must cover parent replacement, links, writer privileges and bytes throughout execution. Ownership, read-only bind mounts and before/after hashes alone cannot establish a seal. The current sudo-capable fixture does not provide the required separation; a dedicated fixture must deny the engine and ordinary writers control of the supervisor and storage. No implementation currently verifies this contract.

## Receipt v1

The JSON object has `version`, unique `run_id`, `snapshot_digest`, `runtime_digest`, `policy_digest`, `supervisor_identity`, `child_identity`, `namespace_identities`, `preparation`, `launch`, `cleanup`, `seal_status` and `unsupported_reasons`. Unknown fields are null or an empty object, never fabricated. `launch: exited_0` is diagnostic child exit only; cleanup explicitly leaves descendants unverified. A future independent witness must carry the same `run_id`, observer identity, observation and outcome; no witness is currently produced.

## Synthetic broker probe

`diagnostic_broker_probe(factory, timeout=10, on_ready=None)` makes a private temporary directory and passes it with the receipt run ID to a trusted synthetic broker factory. Its `start()` must create `provider.sock`; the callback observes the run ID and socket before the fixed child starts. Callback failure aborts launch. Only that socket is mounted at `/run/labhq/provider.sock`; the child sends the fixed newline JSON request `{"run_id": ..., "body": "cHJvYmU="}`. The factory must use fake credentials and a fake upstream. The supervisor calls `close()` on failure and exit, recording whether the socket disappeared. The callback and receipt do not substitute for independent witnesses or verify descendant cleanup. Real engine compatibility and protected launch remain unsupported.
