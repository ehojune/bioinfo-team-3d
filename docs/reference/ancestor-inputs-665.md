# Ancestor inputs (#665, PR #791)

Lookup-only evidence for the [manual](../manual.md).

| Boundary | Contract |
|---|---|
| Identity | Dispatch includes the successful result's task id. The manifest's matching run must be successful, under the same request/step; newer per-run identity fields are also checked. The first failed attempt cannot stand in for a successful retry |
| Native writes | Codex snapshots use the existing `:read-only` profile with only `outputs/` and `.tmp/` writable, on POSIX/Windows and resume. User config, extra write roots and extra CLI args cannot widen it. Later turns retain protection when `inputs/` already exists |
| Partial failure | Clear only inventory-owned copies, including a previous snapshot. Keep source and unknown files. If a rejected partial snapshot cannot be cleared, stop before staff execution |
| Hash admission | For a step declaring outputs, collection hashes through `max(output_hash_max_bytes, min(input_hash_max_file_bytes, input_hash_max_total_bytes))`. No collection setting is silently changed. Copy still checks both input byte caps, recorded digest and protected paths/links |

Tests use bounded synthetic text files, fake adapters and command/profile construction; worker threads are mocked.
Existing cap/growth/missing assertions remain; fixtures bound collection/input caps together and declare the mock `read_only_inputs=[]` field explicitly.
Native sandbox execution, full supported-platform integration and scientific use remain CI/authorized-run boundaries.
