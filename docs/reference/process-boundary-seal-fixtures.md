# Supervisor/seal acceptance fixtures

Lookup-only evidence for [#1063](https://github.com/ehojune/bioinfo-team-3d/issues/1063), parent #666. Acceptance is **synthetic contract only**; every verdict keeps `protection=unverified`. No process, socket, link or protected launch is created.

| Artifact | Purpose |
|---|---|
| `tests/fixtures/process_boundary_seal/cases.json` | v2 positive control, independent pins and 28 rejected cases |
| `tests/fixtures/process_boundary_seal/validator.py` | Pure validator, independent of W1 producer and W3 harness |
| `tests/test_process_boundary_seal_contract.py` | Phase, ancestry, identity, malformed-input and receipt mutations |

## v2 identity and ancestry

- Fixed topology: `source:tool.py` (file) → `parent:source` (directory) → `parent:root` (directory, independently pinned trust anchor).
- Every object carries `parent_ref` and `parent_identity`. Only the root has two explicit nulls. Missing fields fail closed.
- `parent_identity` contains the complete referenced object's identity fields (`object_id`, `kind`, `symlink`, `nlink`); graph fields are separate, not recursively embedded. Equality includes value types. File identity also requires `sha256` and one link.
- Pins and observations must satisfy the exact edge map, distinct identities and directory parents. Missing/extra edges, wrong parents, self-edges, cycles and detached subtrees reject, even if pins and observations agree on a malformed graph.
- Every required phase supplies the full object graph and must equal the independent pins. A coherent replacement root and rewired children still fail against the original anchor.
- IDs/hashes may be rebound independently for a new run. The finite synthetic root does not prove real filesystem ancestry.

Required phases: monitor start → seal → execution start → normal exit → whole-tree cleanup → final snapshot → monitor stop. Sequence and monotonic timestamps must be complete. Execution/exit/cleanup bind the pinned child. Monitor endpoints cover all objects with zero event loss. A mutation event or changed identity/link/relationship at any observed phase rejects even when the final graph and hashes are restored.

| Evidence | Outcome |
|---|---|
| Full v2 positive control | Accepted, `synthetic_only` |
| Missing witness, child authority, run/nonce/child mismatch | Rejected, `unverified` |
| Source/ancestor replacement, symlink/hardlink, ancestry change | Rejected, `unverified` |
| Timeout, supervisor failure, monitor gap/loss, descendant/socket remnant | Rejected, `unverified` |
| Untrusted supervisor, old adapter or real producer | Rejected, `unsupported` |

## Future adapter boundary

`validate(evidence, trusted)` assumes independent provenance only in this fixture. Labels do not authenticate a witness. `adapt_producer` remains unsupported; `protected_launch` is unchanged.

A future producer must authenticate an authority-separated witness; independently bind run/nonce, supervisor executable and child creation identity; pin every ancestor to an independently trusted mount/root anchor; verify directory relationships, stable identity and links continuously through cleanup. Missing ancestry, authentication, event coverage or independent descendant/listener cleanup must fail unsupported. Child receipts and endpoint hashes cannot supply that proof. Real supervisor, seal, monitoring and cleanup remain **UNVERIFIED**.

Validation: `python -m pytest tests/test_process_boundary_seal_contract.py -q`. Windows inventory retains all cases. Final-head CI/review and draft/hold release are tracked in PR #1064; coordinated #666 approval remains required.
