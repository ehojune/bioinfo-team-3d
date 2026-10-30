# Supervisor/seal acceptance fixtures

Lookup-only evidence for [#1063](https://github.com/ehojune/bioinfo-team-3d/issues/1063), parent #666. Acceptance is **synthetic contract only**; every verdict keeps `protection=unverified`. No process, socket, link or protected launch is created.

| Artifact | Purpose |
|---|---|
| `tests/fixtures/process_boundary_seal/cases.json` | v3 positive control, independent pins and 28 rejected cases |
| `tests/fixtures/process_boundary_seal/controls.json` | 53 negative lifecycle controls with required reason codes |
| `tests/fixtures/process_boundary_seal/validator.py` | Pure validator, independent of W1 producer and W3 harness |
| `tests/test_process_boundary_seal_contract.py` | Phase, ancestry, identity, malformed-input and receipt mutations |

## v3 identity and ancestry

- Fixed topology: `source:tool.py` (file) → `parent:source` (directory) → `parent:root` (directory, independently pinned trust anchor).
- Every object carries `parent_ref` and `parent_identity`. Only the root has two explicit nulls. Missing fields fail closed.
- `parent_identity` contains the complete referenced object's identity fields (`object_id`, `kind`, `symlink`, `nlink`); graph fields are separate, not recursively embedded. Equality includes value types. File identity also requires `sha256` and one link.
- Pins and observations must satisfy the exact edge map, distinct identities and directory parents. Missing/extra edges, wrong parents, self-edges, cycles and detached subtrees reject, even if pins and observations agree on a malformed graph.
- Every required phase supplies the full object graph and must equal the independent pins. A coherent replacement root and rewired children still fail against the original anchor.
- IDs/hashes and external ledger commitments may be rebound independently for a new run. The finite synthetic root does not prove real filesystem ancestry.

Required phases: monitor start → seal → execution start → normal exit → whole-tree cleanup → final snapshot → monitor stop. Sequence and monotonic timestamps must be complete. Execution/exit/cleanup bind the pinned child. Monitor endpoints cover all objects with zero event loss. A mutation event or changed identity/link/relationship at any observed phase rejects even when the final graph and hashes are restored.

| Evidence | Outcome |
|---|---|
| Full v3 normal read/output control | Accepted, `synthetic_only` |
| Missing witness, child authority, run/nonce/child mismatch | Rejected, `unverified` |
| Source/ancestor replacement, symlink/hardlink, ancestry change | Rejected, `unverified` |
| Timeout, supervisor failure, monitor gap/loss, descendant/socket remnant | Rejected, `unverified` |
| Untrusted supervisor | Rejected, `unsupported` |
| `synthetic-v1`, `synthetic-v2` or any adapter other than `synthetic-v3` | Rejected, `unsupported`, `producer_adapter_unavailable` |
| Any receipt passed to `adapt_producer` | Rejected, `unsupported`, `producer_adapter_unavailable` |

## v3 lifecycle controls

| Control | Required evidence |
|---|---|
| Provenance | Distinct observer/writer/child/broker/supervisor; observer sequence, wall time and pinned clock |
| Ledger | Outside-run append-only store, observer-only append access, no rewrite/delete; event hash chain matches independent run/nonce commitments |
| Seal | Complete handle inventory, no retained writable handles; write/rename/link/inherited-FD attempts denied |
| Broker | Broker-only credentials/network, no direct auth; run/child-bound `model:synthetic` token valid through execution and expiring by request deadline |
| Work/deadline | Pinned source read, completed run-scoped output hash; independent-wall request deadline bound to supervisor |
| Failure | Broker death/timeout/transport failure, observer death, supervisor interruption, expiry or mutation rejects; reason-coded stop and independent cleanup still required |
| Cleanup | Independent all-owned-resource inventory; no descendants, sockets, mounts, handles, broker endpoints or active tokens |

Each negative control rejects with its reason code against both the original ledger and independent re-recording. The same fixture's normal read/output control accepts. Expected labels never determine verdicts.

## Future adapter boundary

`validate(evidence, trusted)` assumes independent provenance only in this fixture. Hash chains model tamper/replay detection; they do not authenticate observers or enforce storage access. `adapt_producer` remains unsupported; `protected_launch` is unchanged.

A future adapter needs authenticated external observer/ledger inputs and supervisor request-deadline, stop-reason and cleanup evidence. Real OS seal, writable-handle revocation, continuous ancestry monitoring and independent resource inventory remain **UNVERIFIED**. Child receipts or endpoint hashes cannot replace them; execution approval is separate.

Validation: `python -m pytest tests/test_process_boundary_seal_contract.py -q`. Windows inventory retains all cases. Final-head CI/review and draft/hold release are tracked in PR #1064; coordinated #666 approval remains required.
