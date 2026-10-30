# Authentication broker: stage 1

**Prototype only; real protection and CLI compatibility are UNVERIFIED.** No launcher or CLI is connected. Do not use real credentials or enable this code.

The supervisor provides a private run directory. `provider.sock` accepts one JSON frame containing only `run_id` and base64 `body`. `SyntheticBroker` echoes public test data only. `ProcessBroker` executes a trusted upstream in a separate process and returns only a constant acknowledgement or rejection. It discards upstream response content and suppresses child stdout and stderr. `close()` stops the listener, closes the client, kills and reaps an active upstream, and removes the socket.

The unwired fixed transport resolves `api.anthropic.com`, rejects a DNS answer containing a non-global IP, connects to a checked address, validates TLS against that hostname and posts only to `/v1/messages`. Redirects and non-2xx responses fail; response content is discarded. A parent deadline also bounds DNS and TLS. These checks were exercised with fakes, not provider traffic. This response contract does not support provider streaming or CLI compatibility, and does not bound timing or other covert channels.

On 2026-10-30 the disposable Linux fixture passed 30 synthetic tests including namespace socket access, DNS policy, pinned TLS hostname, redirect rejection, constant responses and process timeout/cancellation cleanup. Python 3.12 warned that forking a multithreaded process can deadlock; the deadline kills a stuck child, but a production-safe process launch design and integration remain open. No provider/model call was made. Hook/MCP preservation and actual isolation remain UNVERIFIED.
