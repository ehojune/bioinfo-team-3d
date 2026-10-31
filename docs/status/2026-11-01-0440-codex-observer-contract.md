# Observer contract checkpoint

- Lifecycle event v1 and vector: commit f54642c3. Synthetic HMAC keys are public fixtures.
- Offline observer prototype checks event shape, order and MAC, consumes a SQLite replay key and signs separately supplied snapshots. The caller must protect the database and keys.
- The injected collector is a fixture, not an independently authenticated OS collector. A streaming source can interleave collection with events, but source and collector provenance and actual cleanup are not established. No process tree or resource backend exists. Actual protection remains UNVERIFIED.
- Windows targeted test: 10 passed. New test requires Windows inventory registration, outside rev21 claim.
