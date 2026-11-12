> Historical: the experimental implementation described below was removed in #1389. APIs, tests and file paths are no longer current. Actual OS protection remains UNVERIFIED.

# Authentication broker: stage 1

**Prototype only; real protection and CLI compatibility are UNVERIFIED.** No launcher or CLI is connected. Do not use real credentials or enable this code.

The supervisor provides a private run directory. `provider.sock` accepts one JSON frame containing only `run_id` and base64 `body`. `SyntheticBroker` echoes public test data only. `ProcessBroker` executes a trusted upstream in a separate process and returns only a constant acknowledgement or rejection. It discards upstream response content and suppresses child stdout and stderr. `close()` stops the listener, closes the client, kills and reaps an active upstream, and removes the socket.

The unwired fixed transport resolves `api.anthropic.com`, rejects a DNS answer containing a non-global IP, connects to a checked address, validates TLS against that hostname and posts only to `/v1/messages`. Redirects and non-2xx responses fail; response content is discarded. A parent deadline also bounds DNS and TLS. These checks were exercised with fakes, not provider traffic. This response contract does not support provider streaming or CLI compatibility, and does not bound timing or other covert channels.

On 2026-10-30 the disposable Linux fixture passed 37 synthetic tests, including namespace socket access, DNS policy, constant responses and process timeout/cancellation cleanup. The upstream uses `spawn`, avoiding fork in the listener thread; the trusted callable must be importable and the process entry point guarded. Non-finite deadlines are rejected. Unpicklable callables and abrupt upstream exit produce only rejection and leave no listener after close. A separate two-test probe combined this broker with the #1047 diagnostic launcher: positive namespace access and deadline cleanup passed. That launcher reports unsealed and descendant cleanup unverified. No provider/model call was made.

CLI compatibility requires a separately reviewed transport that the CLI actually supports, provider-specific streaming and response handling, and a reflection-safe contract. Hook/MCP preservation, trusted lifecycle and actual isolation remain UNVERIFIED. The prototype must not be enabled.

## Main integration, 2026-11-05

Main `df1c64c3` is integrated without wiring the broker into production. Both Windows inventory entries survive the merge. The disposable Linux fixture passed 39 broker tests, including two current-launcher integrations: acknowledgement and upstream timeout. Both verify authenticated synthetic lifecycle frames against the observer API and leave receipts unsealed, identities untrusted and descendants unverified; timeout also proves the direct upstream PID is gone. These events describe the namespace child, not a complete broker/descendant inventory.

The [seal adapter](process-boundary-seal-fixtures.md) accepts only separately supplied synthetic evidence and expectations. The broker does not supply that evidence, protected storage, trusted observer or request-deadline ownership. Its constant acknowledgement also cannot implement provider streaming. Code-only merge can preserve these limitations; enabling protection or CLI use requires separate reviewed implementations and execution approval.
