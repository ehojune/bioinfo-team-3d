# Supervisor/seal acceptance fixtures

Lookup-only contract evidence for [#1063](https://github.com/ehojune/bioinfo-team-3d/issues/1063), parent #666. No processes, sockets, links or protected launch are created. Acceptance means **synthetic contract only**; every verdict keeps `protection=unverified`.

| Artifact | Purpose |
|---|---|
| `tests/fixtures/process_boundary_seal/cases.json` | Positive observations, independent launch expectations and negative mutations |
| `tests/fixtures/process_boundary_seal/validator.py` | Pure validator; no dependency on W1 producer or W3 harness |
| `tests/test_process_boundary_seal_contract.py` | Corpus, identity mutations, missing phases, malformed observations and receipt non-authority |

Required witness observations, in order: monitor start → sealed source/ancestor identities → execution start → normal exit → whole-tree descendant/socket cleanup → final identities → monitor stop. Sequence and monotonic timestamps must be complete. Both monitor endpoints cover every pinned object with zero event loss. A mutation event rejects the run even when endpoint hashes match. Source/ancestor identities and link metadata must match caller-owned pins.

| Evidence | Expected status / reason |
|---|---|
| Full positive control | `synthetic_only`, accepted contract |
| Missing witness; child receipt as authority or witness | `unverified`, independent evidence missing/refused |
| Run/nonce mismatch; source/parent replacement; symlink/hardlink | `unverified`, binding/identity failure |
| Transient mutation restored before final hash | `unverified`, `mutation_observed` |
| Unknown supervisor executable identity | `unsupported`, `untrusted_supervisor` |
| Normal exit with remaining descendant/socket | `unverified`, cleanup incomplete |
| Timeout/supervisor failure; monitor gap/loss; malformed observations | `unverified`, execution/coverage incomplete |
| `protected_launch` receipt or any real producer | `unsupported`, `producer_adapter_unavailable` |

## Future adapter boundary

`validate(evidence, trusted)` assumes independent provenance **only within this synthetic fixture**. Observer names and `origin` fields do not authenticate a real witness. `adapt_producer(receipt)` always returns unsupported. No existing `protected_launch` capability changes.

A future adapter must authenticate an external witness outside both child and supervisor control; independently bind the launch run/nonce and pinned supervisor executable; enumerate and pin every source ancestor; verify stable filesystem identities and link metadata; establish continuous observation before sealing through cleanup/final snapshot; detect gaps, overflow, transient changes and all descendant/listener remnants. It must fail closed when the platform cannot supply these observations. Endpoint hashing or a child-written receipt cannot supply that evidence. Real integration needs separate implementation, review and approved trials.

Validation: `python -m pytest tests/test_process_boundary_seal_contract.py -q`.

Windows inventory is outside this claim: add `tests/test_process_boundary_seal_contract.py` to sorted `known_tests` and add the same exact key to `boundary_cohorts` in `config/ci/windows-boundary-v1.json` (reason: synthetic supervisor/seal security contract). This retains all cases on Windows. Until a scoped owner registers it, discovery/CI is blocked; no CI PASS is implied.
