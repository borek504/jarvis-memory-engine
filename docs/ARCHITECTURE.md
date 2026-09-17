# Architecture

The Memory engine is built around a local SQLite source of truth with deterministic serialization, immutable versioned records, explicit lifecycle transitions, freshness semantics, idempotent writes, scoped retrieval, integrity checks, and optional transport adapters.

## Core principles

- **Local-first canonical state** — the database is authoritative; transports are caches or delivery mechanisms, not a second source of truth.
- **Immutable versions** — corrections create a new version instead of silently rewriting history.
- **Explicit lifecycle** — CURRENT, SUPERSEDED, RETRACTED, and PURGED are distinct states.
- **Freshness-aware retrieval** — durable, time-bound, source-bound, and historical information are treated differently.
- **Human-governed writes** — automated workflows may propose or apply policy-authorized deltas, but ambiguity and conflicts must fail closed or require review.
- **Deterministic context** — canonical JSON and stable ordering make revisions reproducible.
- **Privacy by default** — restricted data is never exported as ordinary context.

## Public release boundary

This repository intentionally excludes any real user Memory database, real context snapshot, production credentials, service-account material, account identifiers, remote object IDs, private logs, and private handoff material.
