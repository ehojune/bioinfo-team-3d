# Producer synthetic controls — #1047

- Mock controls check refusal of missing or forged authority and broker start, child, timeout and cleanup failures. Protected launch remains unsupported.
- The broker child timeout records `BROKER_TIMEOUT` and still closes the broker. The remaining strict xfail exposes missing authenticated ancestry; neither mock test proves protection.
- Follow-up needs an authenticated run-bound observer verifier and broader structured failure codes. Diagnostic ancestry is untrusted; cleanup and descendant termination remain independently unverified.
- PR #1050 remains draft/hold. No provisioning, live boundary experiment or engine/provider/model execution was performed.
