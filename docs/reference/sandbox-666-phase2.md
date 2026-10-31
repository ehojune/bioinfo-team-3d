# Process boundary wiring (#666)

Lookup-only integration notes. Actual protection and authenticated cleanup remain **UNVERIFIED**.

| Entry | Behavior |
| --- | --- |
| `Runner(settings)` / ordinary `RunContext` | Boundary gate off; existing policy, hooks, MCP and CLI path unchanged |
| `Runner(settings, process_boundary=True)` | Refuse before workspace preparation, recruitment or adapter execution; zero cost, `boundary_unsupported` result |
| Direct adapter with `ProcessBoundaryGate(required=True)` | Same refusal before reading staff environment, preparing files or spawning a child |
| `runner.process_boundary.observe_lifecycle(events, **expectations)` | Explicit offline observer API; caller supplies synthetic keys, replay store and collector; failures/cancellation propagate |

The opt-in is an embedding Python API, not a config, environment or task-metadata switch. Both runner entry points check it; the runner passes the same gate to the adapter. Existing approval and environment-binding gates are not relaxed.

`protected_launch()` currently returns unsupported without starting a process. Errors produce a fixed refusal without exception details. Even a success-shaped receipt cannot fall through to the ordinary CLI transport. No unsealed diagnostic, model, provider, credential broker or supervisor is started by this wiring.

The producer signer and observer are exercised together offline. Valid HMACs and an empty synthetic termination inventory remain `synthetic_only`; they never populate runtime cleanup evidence or grant capability. The observer owns replay rejection, including after incomplete, failed or cancelled collection.

Activation still requires a separately reviewed protected engine transport, sealed storage authority, trusted identity/keys and independently authenticated lifecycle/cleanup. This PR provides refusal and observer seams only. It does not implement those missing services or prove OS isolation.
