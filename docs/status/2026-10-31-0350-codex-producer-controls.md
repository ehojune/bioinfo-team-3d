# Producer synthetic controls — #1047

- Mock controls check refusal of missing or forged authority and broker start, child, timeout and cleanup failures. Protected launch remains unsupported.
- `python -m pytest tests/test_process_boundary.py -q`: 21 passed, 2 xfailed. The strict xfails expose missing authenticated ancestry and distinct broker timeout reason codes; they are not protection evidence.
- Follow-up needs an authenticated run-bound observer verifier and stable structured failure codes, including distinct timeout and cleanup outcomes. Diagnostic ancestry is explicitly untrusted; cleanup and descendant termination remain independently unverified.
- PR #1050 remains draft/hold. No provisioning, live boundary experiment or engine/provider/model execution was performed.
