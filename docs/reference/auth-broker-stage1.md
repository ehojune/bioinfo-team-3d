# Authentication broker: stage 1

**Prototype only; real protection and CLI compatibility are UNVERIFIED.** No launcher or CLI is connected. Do not use real credentials or enable this code.

The supervisor provides a private run directory. `provider.sock` accepts one JSON frame containing only `run_id` and base64 `body`. `SyntheticBroker` echoes public test data only. `ProcessBroker` executes a trusted upstream in a separate process and returns only a constant acknowledgement or rejection. It discards upstream response content and suppresses child stdout and stderr. `close()` stops the listener, closes the client, kills and reaps an active upstream, and removes the socket.

The unwired fixed transport resolves `api.anthropic.com`, rejects a DNS answer containing a non-global IP, connects to a checked address, validates TLS against that hostname and posts only to `/v1/messages`. Redirects and non-2xx responses fail; response content is discarded. A parent deadline also bounds DNS and TLS. These checks were exercised with fakes, not provider traffic. This response contract does not support provider streaming or CLI compatibility, and does not bound timing or other covert channels.

On 2026-10-30 the disposable Linux fixture passed 37 synthetic tests, including namespace socket access, DNS policy, constant responses and process timeout/cancellation cleanup. The upstream uses `spawn`, avoiding fork in the listener thread; the trusted callable must be importable and the process entry point guarded. Non-finite deadlines are rejected. Unpicklable callables and abrupt upstream exit produce only rejection and leave no listener after close. A separate two-test probe combined this broker with the #1047 diagnostic launcher: positive namespace access and deadline cleanup passed. That launcher reports unsealed and descendant cleanup unverified. No provider/model call was made.

CLI compatibility requires a separately reviewed transport that the CLI actually supports, provider-specific streaming and response handling, and a reflection-safe contract. Hook/MCP preservation, trusted lifecycle and actual isolation remain UNVERIFIED. The prototype must not be enabled.
