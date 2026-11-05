# #1048 r9: broker integration

Integrated main `df1c64c3` into PR #1052; resolved the Windows inventory conflict by retaining both registrations. Runtime broker code remains unchanged and unwired. Added two tests combining the actual ProcessBroker with the current diagnostic launcher and observer event verifier.

| Verification | Result |
|---|---|
| Existing disposable Linux fixture, temporary source/dependency copies | 39 passed, including acknowledgement and upstream timeout integrations |
| Windows broker/launcher/observer/seal/preview tests | 747 passed, 39 skipped, 1 xfailed |
| Windows inventory validation | Passed |
| Final-head CI and actual review | Pending; tracked in PR #1052 |

Synthetic lifecycle frames validate; timeout reaps the direct upstream PID and closes the socket/listener. The receipt remains unsealed with descendants unverified. Namespace-child events do not inventory the broker or its descendants. The seal adapter still requires separate synthetic evidence and expectations; the broker supplies no trusted observer, protected storage or supervisor deadline ownership. Streaming/CLI compatibility, OS protection, hook/MCP preservation and independent cleanup remain UNVERIFIED. No real credentials, provider/model calls, provisioning, deployment, research, HPC or capability enablement.

Keep draft/hold pending lead review. Recommendation: code-only merge is separable from later protection/CLI gates after final CI and actual review pass; do not enable the prototype.

Procedure exception: the first pre-edit check was refused because it ran outside the registered checkout, but inventory/test edits followed. A subsequent check in the registered checkout passed before further work; the edits are within r9 scope. The assignment was not changed.
