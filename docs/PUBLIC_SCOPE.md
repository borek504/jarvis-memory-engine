# Public scope

The public v0.1 repository contains a compact, synthetic implementation of the Memory contracts plus tests and documentation. It is intentionally not a dump of the private production runtime.

Included: deterministic serialization, local SQLite storage, immutable version history, lifecycle transitions, freshness-aware retrieval, sensitivity-aware context building, bounded sync-delta validation, tests, and synthetic examples.

Excluded: real Memory state, private deployment topology, credentials, live transport configuration, private runtime logs, account identifiers, and private handoffs.
