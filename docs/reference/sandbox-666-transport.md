# #666 VM-free transport contract

Lookup only. This draft implements synthetic admission, not an OS transport.
`SyntheticTransport` is off by default, has no CLI caller and never launches a child.
VM provisioning, channel ownership, actual isolation and lifetime/cleanup remain **UNVERIFIED**.

| Boundary | Contract |
|---|---|
| Trusted registration | `RunPolicy`: fresh canonical run UUID, fresh 256-bit hex nonce, peer/key IDs, policy digest, fixed runtime ID, deadline, exact env values and symbolic stdio bindings. No arbitrary command/path/destination field |
| Wire | UTF-8 JSON bytes, at most 8 KiB; exact `payload`/`auth` fields, strict types, bounded env/stdio, no duplicate keys. Version 1 only |
| Authentication | Injected trusted `PeerVerifier` validates channel identity outside the wire **and** authentication of domain-separated canonical bytes containing the entire payload and key ID. The returned `AuthenticatedPeer` must match registered and claimed identities |
| Fake verifier | Exists only in tests. Object-identity channel registry plus a public fixture HMAC key. No OS peer credentials, key provisioning or protected channel implementation exists |
| Admission | Schema → authentication → peer/run/nonce binding → durable nonce consumption → operation/runtime/policy/deadline/env/stdio checks → immutable synthetic plan |
| Replay | SQLite unique run UUID, transactional insert before returning a plan. Concurrent instances sharing a ledger have one winner; changing nonce cannot reuse a consumed run UUID |
| Rejection | Malformed/unauthenticated/wrong-peer/wrong-run input does not consume the registered run. An authenticated, bound request consumes it even if later policy checks fail or the reply is lost. Retry requires a newly registered run |
| Restart | Reopening the same intact SQLite file rejects consumed runs. No ledger pruning API. File/parent ACLs, rollback/deletion resistance, reboot/power-loss durability and trusted reconciliation are unverified; a missing/empty replacement database is **not** secure recovery |
| Env/FD | Accept only requested env entries with exact registered values and registered `stdin`/`stdout`/`stderr` roles. Rebuild the plan from registration, never from ambient env or incoming numeric handles. `close_fds=True` is plan data; actual descriptor inheritance is unverified |
| Expiry | Request deadline cannot exceed the registered deadline and must be later than the injected trusted clock, checked after replay commit. Clock integrity, revocation and execution-time rechecks belong to the future trusted service |

Payload fields: `version`, `run_id`, `nonce`, `peer_id`, `policy_digest`,
`operation` (only `prepare`), `runtime_id`, `expires_at` (integer Unix seconds),
`env` (up to 16 names with values), `stdio` (up to three symbolic roles).
`auth` has `key_id` and a 64-character lowercase hex `tag`.
Authentication binds the parsed canonical message, not JSON whitespace/order.

The Python caller, registered env/FD values, verifier, clock and ledger are trusted
dependencies. An engine must never supply them. In-process objects cannot establish
protection from a same-user attacker. A `synthetic_only` plan is not an admission
proof for `ProcessBoundaryGate` or permission to bypass its unsupported result.

Tests cover normal/empty plans, schema/type/size/duplicate-key rejection, tampering
with each payload field, fake channel spoofing, wrong key/run/nonce, expiry,
env/stdio denial, authenticated rejection consumption, restart replay, unavailable
ledger and eight concurrent requests. No process, socket, VM or credential is used.
