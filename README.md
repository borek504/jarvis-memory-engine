# jarvis-memory-engine

A local-first, privacy-conscious memory engine for AI agents.

The public project focuses on a small set of durable primitives:

- immutable, versioned memory records;
- explicit `CURRENT`, `SUPERSEDED`, and `RETRACTED` lifecycle transitions;
- compare-and-swap style corrections;
- idempotent writes;
- deterministic canonical JSON and SHA-256 digests;
- freshness-aware retrieval;
- sensitivity-aware context construction;
- validated `jarvis-memory-delta-v1` envelopes for sync workflows;
- SQLite as the canonical local source of truth.

This is a **sanitized public implementation** of the Memory subsystem contracts. It contains no live user database, private context snapshot, credentials, account identifiers, user-specific absolute paths, private handoffs, or raw transcripts.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
```

Minimal example:

```python
from pathlib import Path
from jarvis_memory_engine import MemoryInput, MemoryStore, Scope

store = MemoryStore(Path("memory.sqlite3"))

scope = Scope(
    domain="demo",
    project_scope="sample",
    subject_type="project",
    subject_id="alpha",
    slot_key="status",
)

created = store.create(
    MemoryInput(
        memory_type="PROJECT_STATE",
        scope=scope,
        content={"status": "ready"},
        source_kind="user_input",
        source_ref="example",
    )
)

print(created)
print(store.current(scope))
```

## Design

The engine treats memory as durable state rather than a chat transcript.

A stable authority slot is identified by:

`domain + project_scope + subject_type + subject_id + slot_key`

Corrections create a new immutable record and atomically move the head. Retractions remove the current head without silently falling back to an older version.

`PURGED` is reserved for separately verified maintenance/erasure workflows. The compact v0.1 public API does not claim physical erasure; deleting SQLite bytes safely requires deployment-specific backup and storage handling.

Freshness classes:

- `DURABLE`
- `TIME_BOUND`
- `SOURCE_BOUND`
- `HISTORICAL`

Sensitivity classes:

- `STANDARD_PRIVATE`
- `SENSITIVE`
- `RESTRICTED_LOCAL`

Context retrieval excludes restricted-local data and excludes sensitive data unless explicitly requested.

## Sync delta contract

`jarvis-memory-delta-v1` is a bounded JSON envelope intended for controlled cross-process or cross-device workflows. The public validator enforces:

- max 64 KiB;
- max 32 candidates;
- strictly formatted envelope and candidate IDs;
- one domain/project scope per delta;
- no caller-supplied authority override;
- rejection of secret-like fields and private-key material.

Transport is intentionally not mandatory. The database remains canonical.

## Security and privacy

See [SECURITY.md](SECURITY.md) and [PRIVACY.md](PRIVACY.md).

Do not use real personal data in examples, tests, issues, or pull requests.

## Status

`v0.1.0` — public alpha/reference implementation.

The production/private system has additional operational hardening and deployment-specific controls that are intentionally outside this repository.

## License

MIT.
