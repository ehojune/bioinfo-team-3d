# Disposable fixture plan review

Run `python scripts/boundary_fixture_plan.py plan.json`. The tool reads that one JSON file, reports all detected issues and exits 0 only when its schema passes. Unknown fields and values need review. It does not execute strings, inspect or create a VM, install anything, or delete resources. `plan_valid` is a document check; `protection` remains `UNVERIFIED`. An independent review must establish the actual identities, authority, observations and teardown before provisioning.

Example for review only; replace the illustrative digest, identities and expiry:

```json
{
  "version": 1,
  "fixture_id": "review-vm",
  "image_digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "owner": "reviewer",
  "expires_at": "2999-01-01T00:00:00Z",
  "principals": {"supervisor": "supervisor", "engine": "engine", "writer": "writer"},
  "isolation": {"storage": "supervisor_controlled_linux", "shared_paths": false, "interop": false, "network": false},
  "teardown": ["vm", "run_storage", "credentials"],
  "observations": ["identity", "writer_denial", "parent_replacement", "links", "bytes_during_run", "network_denial", "interop_denial", "cleanup"]
}
```

Names are declarations, not proof of distinct privileges. The supervisor must control Linux-only storage; the engine and ordinary writer must not control it. Observations require independent evidence, including throughout-run byte protection. This schema does not establish those facts or authorize a real fixture. See [#1047 trust contract](https://github.com/ehojune/bioinfo-team-3d/issues/1047#issuecomment-6512892609).
