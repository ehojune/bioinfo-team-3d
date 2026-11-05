# #1047 producer nonce

- Synthetic broker probe accepts a caller's canonical UUID and propagates it to the factory, callback, child and receipt; invalid input fails before preparation. Omission generates a fresh UUID. Callers must use a fresh nonce; replay detection is not provided.
- Windows mock tests: 29 passed, 1 xfailed. Actual protection, authenticated ancestry and independent cleanup remain UNVERIFIED.
- PR #1050 remains draft/hold. Manual review P1 awaits a separate ownership claim; final-head CI and review are pending.
