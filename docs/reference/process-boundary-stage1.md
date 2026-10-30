# Process boundary: stage 1

Protected launch is **unsupported**. `protected_launch` never starts a process; caller-supplied assertions, digests, or supervisor objects cannot grant authority. This module is not wired into the daemon or an engine.

`python -m labhq.runner.process_boundary --unsealed-diagnostic` explicitly runs a fixed, non-model `true` probe in bubblewrap with an unsealed host runtime. A zero exit establishes only that this command exited zero. The diagnostic is not a protected launch, isolation proof, or capability enablement. Without the flag the command returns the protected-launch refusal.

## Trust and remaining fixture requirements

Windows administrator, WSL root and an explicitly configured supervisor are trusted. The engine and ordinary source writers are untrusted. A future supervisor must create run-specific copies of allowlisted inputs, runtime and policy in Linux storage under its control, outside writable workspace, output and HOME, with no Windows shared-drive backing. Verification must cover parent replacement, links, writer privileges and bytes throughout execution. Ownership, read-only bind mounts and before/after hashes alone cannot establish a seal. The current sudo-capable fixture does not provide the required separation; a dedicated fixture must deny the engine and ordinary writers control of the supervisor and storage. No implementation currently verifies this contract.

## Receipt v1

The JSON object has `version`, unique `run_id`, `snapshot_digest`, `runtime_digest`, `policy_digest`, `supervisor_identity`, `child_identity`, `namespace_identities`, `preparation`, `launch`, `cleanup`, `seal_status` and `unsupported_reasons`. Unknown fields are null or an empty object, never fabricated. `launch: exited_0` is diagnostic child exit only; cleanup explicitly leaves descendants unverified. A future independent witness must carry the same `run_id`, observer identity, observation and outcome; no witness is currently produced.

The staging diagnostic emits receipt v2: all v1 fields plus `ancestry_observations`, an array of objects with `phase`, `provenance`, and `objects`. An empty array means no samples were obtained. Other probes retain the exact v1 JSON fields. Consumers must explicitly recognize v2 before using its added field.

## Disposable fixture proposal — review only

No command below has been authorized or executed. Preferred target: a newly created disposable Linux VM, never an existing WSL distribution. The operator records VM ID, image digest, host, owner and expiry before provisioning; no production data, credentials, shared folders, Windows interop, host sockets or network access. Use a trusted console and a pre-reviewed Linux image with bubblewrap and Python already installed. Host administrator and guest root are trusted; any access from engine/writer to the host hypervisor or guest root invalidates the test. A separate WSL distro is a smaller alternative only if the operator can demonstrate that Windows interop, `/mnt`, shared mounts, WSL root entry and host APIs are unavailable to both untrusted principals; these conditions are not established here. A VM also requires host/hypervisor and guest isolation review.

| Principal | Required access | Explicitly denied |
| --- | --- | --- |
| Operator / guest root | Provision, launch trusted supervisor, inspect and destroy VM | No untrusted workload runs as root |
| Supervisor (root in disposable guest) | Own code and `/var/lib/labhq-boundary`; stage and remove per-run copies; start/stop child | No writable engine-owned code or storage in its execution path |
| `lh-engine` | Execute fixed synthetic child; write only its separate output | sudo, root, supervisor/store writes, host and other user access |
| `lh-writer` | Attempt adversarial writes as independent principal | sudo, root, supervisor/store writes and engine control |
| Observer (trusted root console) | Record run ID, process tree and cleanup independently | Must not infer results from child receipt |

Proposed guest-root setup, after the operator has verified a fresh disposable VM and unique ID (commands assume Debian-like `useradd`; stop if assumptions fail):

```sh
test "$(id -u)" = 0 || exit 1
test ! -e /var/lib/labhq-boundary || exit 1
! id lh-engine && ! id lh-writer || exit 1
useradd --system --user-group --no-create-home --shell /usr/sbin/nologin lh-engine
useradd --system --user-group --no-create-home --shell /usr/sbin/nologin lh-writer
install -d -o root -g root -m 0700 /var/lib/labhq-boundary
install -d -o lh-engine -g lh-engine -m 0700 /var/tmp/labhq-engine-output
id lh-engine; id lh-writer
namei -l /var/lib/labhq-boundary
findmnt -T /var/lib/labhq-boundary
```

The operator must also verify inherited groups, ACLs, capabilities, sudo/polkit, mount propagation, parent ownership, links and all routes to root or host, as well as supervisor executable and dependencies. The commands alone do not provide a sealed service or sandbox. Run-specific directories are created and exclusively controlled by the supervisor, retired only after child termination and observation; artifacts retained for audit contain synthetic data only. Do not run a protected launch until an independently reviewed service implements those controls, including continuous integrity and safe path resolution.

After a future service exists, hold a fixed synthetic child running under one UUID. From a separate `lh-writer` process attempt file creation, modification, rename of store and parent, symlink and hard-link replacement; independently observe failed syscalls and unchanged supervisor-held objects throughout the run. Test the same from `lh-engine`, including `/mnt`, `/init`, host/interop sockets and namespace escape paths. A denied attempt alone is not proof of completeness. A separate trusted observer records that UUID, identities, process/cgroup membership, termination and empty membership after normal exit, timeout and forced supervisor failure; independently verify no socket or run directory remains. Bind each observation and timestamp to that UUID, not merely to a child receipt. Missing checks mean UNVERIFIED.

Teardown: trusted operator first stops the supervisor and confirms all child processes and mounts are gone, exports only reviewed synthetic evidence, then destroys the **identified disposable VM and its virtual disks** using the host's VM manager. Verify the VM ID and disk paths before deletion. On partial setup or failed cleanup, disconnect and quarantine the VM, preserve logs, and destroy only after owner review; do not reuse it or apply cleanup commands to a host/WSL filesystem. Host administrator owns recovery and audit. No VM-specific deletion command is supplied until platform and target are selected.

## Local staging diagnostic

`DiagnosticStager` and `diagnostic_staging_probe` exercise fixed synthetic staging, digest comparison and cleanup without a child or external authority. The run ID joins staging and cleanup. Injected implementations are untrusted test doubles: success or a returned digest never permits `protected_launch`, and independent cleanup and actual storage security remain unverified.

The diagnostic samples `source:tool.py -> parent:source -> parent:root` before and after its callback. Each entry includes its identity, `parent_ref`, and the complete referenced parent identity; the root has null parent fields. Samples are labeled `stager_untrusted`; the built-in stager uses local lstat, while an injected stager's method is unverified. Authentication, continuous monitoring, and ancestors above the temporary root are unknown. `validate_synthetic_ancestry(pins, phases)` checks this fixed graph against separately supplied pins and every supplied phase; it does not authenticate pins, prove phase completeness or authorize launch. A missing observer or failed observation fails the diagnostic.

A real supervisor must pin every ancestor to an independently trusted mount/root anchor, resolve and hold objects safely against concurrent replacement, authenticate independent witnesses and their complete ordered monitoring interval, and verify storage authority and cleanup. If any prerequisite is unavailable, fail unsupported. The temporary root and matching endpoint samples do not establish filesystem ancestry or absence of intervening mutation.

## Synthetic broker probe

`diagnostic_broker_probe(factory, timeout=10, on_ready=None)` makes a private temporary directory and passes it with the receipt run ID to a trusted synthetic broker factory. Its `start()` must create `provider.sock`; the callback observes the run ID and socket before the fixed child starts. Callback failure aborts launch. Only that socket is mounted at `/run/labhq/provider.sock`; the child sends the fixed newline JSON request `{"run_id": ..., "body": "cHJvYmU="}`. The factory must use fake credentials and a fake upstream. The supervisor calls `close()` on failure and exit, recording whether the socket disappeared. The callback and receipt do not substitute for independent witnesses or verify descendant cleanup. Real engine compatibility and protected launch remain unsupported.

The child subprocess timeout records `BROKER_TIMEOUT` in `unsupported_reasons` and marks launch failed. This timeout does not bound broker startup or cleanup.
