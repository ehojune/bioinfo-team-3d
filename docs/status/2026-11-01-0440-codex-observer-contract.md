# Observer contract checkpoint

- Lifecycle event v1 and vector: commit f54642c3. Synthetic HMAC keys are public fixtures.
- Offline observer prototype checks event shape, order and MAC, consumes a SQLite replay key and signs separately supplied snapshots. The caller must protect the database and keys.
- The injected collector is a fixture, not an independently authenticated OS collector. A streaming source can interleave collection with events, but source and collector provenance and actual cleanup are not established. No process tree or resource backend exists. Actual protection remains UNVERIFIED.
- Rev22 registers the observer test in the Windows inventory. Offline snapshots require matching source labels, ordered sequence, an asserted complete inventory and the launch identity. These are untrusted assertions, not collector authentication or proof of completeness.
- Bounded execution needs an owned process supervisor; synchronous injected collectors may hang. Protected keys, replay storage and actual cleanup remain UNVERIFIED.
