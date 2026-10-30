# Process boundary: stage 1

Protected launch is **unsupported**. `protected_launch` never starts a process; caller-supplied assertions, digests, or supervisor objects cannot grant authority. This module is not wired into the daemon or an engine.

`python -m labhq.runner.process_boundary --unsealed-diagnostic` explicitly runs a fixed, non-model `true` probe in bubblewrap with an unsealed host runtime. A zero exit establishes only that this command exited zero. The diagnostic is not a protected launch, isolation proof, or capability enablement. Without the flag the command returns the protected-launch refusal.

## Trust and remaining fixture requirements

Windows administrator, WSL root and an explicitly configured supervisor are trusted. The engine and ordinary source writers are untrusted. A future supervisor must create run-specific copies of allowlisted inputs, runtime and policy in Linux storage under its control, outside writable workspace, output and HOME, with no Windows shared-drive backing. Verification must cover parent replacement, links, writer privileges and bytes throughout execution. Ownership, read-only bind mounts and before/after hashes alone cannot establish a seal. The current sudo-capable fixture does not provide the required separation; a dedicated fixture must deny the engine and ordinary writers control of the supervisor and storage. No implementation currently verifies this contract.

## Receipt v1

The JSON object has `version`, unique `run_id`, `snapshot_digest`, `runtime_digest`, `policy_digest`, `supervisor_identity`, `child_identity`, `namespace_identities`, `preparation`, `launch`, `cleanup`, `seal_status` and `unsupported_reasons`. Unknown fields are null or an empty object, never fabricated. `launch: exited_0` is diagnostic child exit only; cleanup explicitly leaves descendants unverified. A future independent witness must carry the same `run_id`, observer identity, observation and outcome; no witness is currently produced. A future broker integration must bind a run-scoped AF_UNIX endpoint to that same run and policy; no socket is passed by this diagnostic.
