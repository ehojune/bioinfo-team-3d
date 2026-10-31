# #1047 process boundary — PR #1050

Protected launch remains unsupported. Standalone unsealed namespace, staging and synthetic broker diagnostics retain their receipts and CLI failure status. An opt-in lifecycle stream now emits run/nonce/identity-bound launch and termination events under the offline observer contract [f54642c3](https://github.com/ehojune/bioinfo-team-3d/commit/f54642c3). Existing broker calls and v1 JSON remain unchanged.

- Tests: `python -X utf8 -m pytest tests/test_process_boundary.py tests/test_text_encoding.py -q` — **85 passed, 1 xfailed** on Windows. The existing strict xfail covers the unavailable authenticated ancestry witness.
- New coverage: exact observer HMAC vectors, identity/run binding, delivery failures, child exit/timeout/reaping failure, one-use stream and receipt compatibility. New process paths use mocks; no live probe was run.
- Keep **draft/hold** for coordinated review. Final-head CI and Codex review are tracked in the PR. No merge, engine/daemon integration, provisioning, provider or HPC execution.
- Fixture HMACs and injected identities are not trusted authority. Actual protection, independent cleanup and production replay protection remain **UNVERIFIED**. See [reference](../reference/process-boundary-stage1.md).

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
