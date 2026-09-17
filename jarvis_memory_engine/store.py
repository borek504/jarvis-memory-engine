from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any, Iterator
import uuid

from .canonical import canonical_json, digest_json, parse_utc, utc_now
from .model import MemoryInput, Scope


SCHEMA_VERSION = 1
PRIVATE_FILE_MODE = 0o600


class MemoryStoreError(RuntimeError):
    """Base error for store operations."""


class ConflictError(MemoryStoreError):
    """The requested transition conflicts with current durable state."""


class NotFoundError(MemoryStoreError):
    """The requested record or current scope head does not exist."""


def _memory_id() -> str:
    return f"mem_{uuid.uuid4().hex}"


def _event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def _utc_text(value: str) -> str:
    return (
        parse_utc(value)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _optional_utc_text(value: str | None) -> str | None:
    return None if value is None else _utc_text(value)


def _scope_values(scope: Scope) -> tuple[str, str, str, str, str]:
    scope.validate()
    return (
        scope.domain.strip(),
        (scope.project_scope or "").strip(),
        scope.subject_type.strip(),
        scope.subject_id.strip(),
        scope.slot_key.strip(),
    )


@contextmanager
def _private_umask() -> Iterator[None]:
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


class MemoryStore:
    """A compact local SQLite memory store.

    Record rows are append-only. Lifecycle changes live in a separate state
    table, and every transition is also recorded as an append-only event.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().absolute()
        self._prepare_path()
        self._initialize()

    def _prepare_path(self) -> None:
        current = Path(self.path.anchor)
        for part in self.path.parts[1:]:
            current = current / part
            try:
                value = os.lstat(current)
            except FileNotFoundError:
                break
            if stat.S_ISLNK(value.st_mode) and (
                current == self.path or value.st_uid != 0
            ):
                raise MemoryStoreError("database path must not contain symbolic links")
        with _private_umask():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            parent = os.lstat(self.path.parent)
            if not stat.S_ISDIR(parent.st_mode):
                raise MemoryStoreError("database parent must be a directory")
            if parent.st_uid != os.geteuid():
                raise MemoryStoreError("database parent must be owned by the current user")
            if stat.S_IMODE(parent.st_mode) & 0o022:
                raise MemoryStoreError(
                    "database parent must not be group- or world-writable"
                )
            if self.path.exists():
                existing = os.lstat(self.path)
                if not stat.S_ISREG(existing.st_mode):
                    raise MemoryStoreError("database path must be a regular file")
                if existing.st_uid != os.geteuid():
                    raise MemoryStoreError("database file must be owned by the current user")
                self.path.chmod(PRIVATE_FILE_MODE)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with _private_umask():
            connection = sqlite3.connect(self.path, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            yield connection
        finally:
            connection.close()
            if self.path.exists() and os.name != "nt":
                self.path.chmod(PRIVATE_FILE_MODE)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_records (
                    memory_id TEXT PRIMARY KEY
                        CHECK(length(memory_id)=36 AND substr(memory_id,1,4)='mem_'),
                    memory_type TEXT NOT NULL CHECK(memory_type IN (
                        'FACT','PREFERENCE','GOAL','RULE','DECISION',
                        'PROJECT_STATE','REFERENCE'
                    )),
                    domain TEXT NOT NULL CHECK(length(domain) BETWEEN 1 AND 64),
                    project_scope TEXT NOT NULL CHECK(length(project_scope) <= 128),
                    subject_type TEXT NOT NULL
                        CHECK(length(subject_type) BETWEEN 1 AND 64),
                    subject_id TEXT NOT NULL
                        CHECK(length(subject_id) BETWEEN 1 AND 256),
                    slot_key TEXT NOT NULL CHECK(length(slot_key) BETWEEN 1 AND 128),
                    content_json TEXT NOT NULL
                        CHECK(json_valid(content_json)
                              AND length(CAST(content_json AS BLOB)) <= 262144),
                    content_digest TEXT NOT NULL
                        CHECK(length(content_digest)=64
                              AND content_digest NOT GLOB '*[^0-9a-f]*'),
                    source_kind TEXT NOT NULL
                        CHECK(length(source_kind) BETWEEN 1 AND 64),
                    source_ref TEXT NOT NULL
                        CHECK(length(source_ref) BETWEEN 1 AND 2048),
                    source_digest TEXT CHECK(
                        source_digest IS NULL OR
                        (length(source_digest)=64
                         AND source_digest NOT GLOB '*[^0-9a-f]*')
                    ),
                    observed_at_utc TEXT,
                    recorded_at_utc TEXT NOT NULL,
                    validated_at_utc TEXT,
                    freshness_class TEXT NOT NULL CHECK(freshness_class IN (
                        'DURABLE','TIME_BOUND','SOURCE_BOUND'
                    )),
                    aged_after_utc TEXT,
                    stale_after_utc TEXT,
                    sensitivity TEXT NOT NULL CHECK(sensitivity IN (
                        'STANDARD_PRIVATE','SENSITIVE','RESTRICTED_LOCAL'
                    )),
                    confidence REAL NOT NULL CHECK(confidence BETWEEN 0.0 AND 1.0),
                    supersedes_id TEXT,
                    lineage_root_id TEXT NOT NULL,
                    CHECK(supersedes_id IS NULL OR supersedes_id <> memory_id),
                    FOREIGN KEY(supersedes_id) REFERENCES memory_records(memory_id),
                    FOREIGN KEY(lineage_root_id) REFERENCES memory_records(memory_id)
                        DEFERRABLE INITIALLY DEFERRED
                );

                CREATE TABLE IF NOT EXISTS memory_states (
                    memory_id TEXT PRIMARY KEY,
                    lifecycle TEXT NOT NULL
                        CHECK(lifecycle IN ('CURRENT','SUPERSEDED','RETRACTED','PURGED')),
                    changed_at_utc TEXT NOT NULL,
                    FOREIGN KEY(memory_id) REFERENCES memory_records(memory_id)
                );

                CREATE TABLE IF NOT EXISTS memory_heads (
                    domain TEXT NOT NULL,
                    project_scope TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    slot_key TEXT NOT NULL,
                    memory_id TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(
                        domain, project_scope, subject_type, subject_id, slot_key
                    ),
                    FOREIGN KEY(memory_id) REFERENCES memory_records(memory_id)
                );

                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id TEXT PRIMARY KEY
                        CHECK(length(event_id)=36 AND substr(event_id,1,4)='evt_'),
                    event_type TEXT NOT NULL
                        CHECK(event_type IN ('CREATE','CORRECT','RETRACT')),
                    memory_id TEXT NOT NULL,
                    previous_memory_id TEXT,
                    reason TEXT CHECK(reason IS NULL OR length(reason) <= 2048),
                    occurred_at_utc TEXT NOT NULL,
                    FOREIGN KEY(memory_id) REFERENCES memory_records(memory_id),
                    FOREIGN KEY(previous_memory_id) REFERENCES memory_records(memory_id)
                );

                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    idempotency_key TEXT PRIMARY KEY
                        CHECK(length(idempotency_key) BETWEEN 1 AND 256),
                    operation_digest TEXT NOT NULL
                        CHECK(length(operation_digest)=64
                              AND operation_digest NOT GLOB '*[^0-9a-f]*'),
                    result_json TEXT NOT NULL CHECK(json_valid(result_json))
                );

                CREATE INDEX IF NOT EXISTS idx_memory_scope
                ON memory_records(
                    domain, project_scope, subject_type, subject_id, slot_key
                );

                CREATE INDEX IF NOT EXISTS idx_memory_lineage
                ON memory_records(lineage_root_id, recorded_at_utc, memory_id);

                CREATE TRIGGER IF NOT EXISTS memory_records_no_update
                BEFORE UPDATE ON memory_records
                BEGIN
                    SELECT RAISE(ABORT, 'memory records are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS memory_records_no_delete
                BEFORE DELETE ON memory_records
                BEGIN
                    SELECT RAISE(ABORT, 'memory records are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS memory_events_no_update
                BEFORE UPDATE ON memory_events
                BEGIN
                    SELECT RAISE(ABORT, 'memory events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS memory_events_no_delete
                BEFORE DELETE ON memory_events
                BEGIN
                    SELECT RAISE(ABORT, 'memory events are append-only');
                END;
                """
            )
            current = connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            if current is None:
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif current["value"] != str(SCHEMA_VERSION):
                raise MemoryStoreError("unsupported database schema version")
            connection.commit()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "memory_id": row["memory_id"],
            "memory_type": row["memory_type"],
            "domain": row["domain"],
            "project_scope": row["project_scope"] or None,
            "subject_type": row["subject_type"],
            "subject_id": row["subject_id"],
            "slot_key": row["slot_key"],
            "content": json.loads(row["content_json"]),
            "content_digest": row["content_digest"],
            "source_kind": row["source_kind"],
            "source_ref": row["source_ref"],
            "source_digest": row["source_digest"],
            "observed_at_utc": row["observed_at_utc"],
            "recorded_at_utc": row["recorded_at_utc"],
            "validated_at_utc": row["validated_at_utc"],
            "freshness_class": row["freshness_class"],
            "aged_after_utc": row["aged_after_utc"],
            "stale_after_utc": row["stale_after_utc"],
            "sensitivity": row["sensitivity"],
            "confidence": row["confidence"],
            "supersedes_id": row["supersedes_id"],
            "lineage_root_id": row["lineage_root_id"],
            "lifecycle": row["lifecycle"],
        }

    @staticmethod
    def _operation_digest(operation: str, value: dict[str, Any]) -> str:
        return digest_json({"operation": operation, "value": value})

    @staticmethod
    def _idempotent_result(
        connection: sqlite3.Connection,
        idempotency_key: str | None,
        operation_digest: str,
    ) -> dict[str, Any] | None:
        if idempotency_key is None:
            return None
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        if len(idempotency_key.strip()) > 256:
            raise ValueError("idempotency_key exceeds 256 characters")
        row = connection.execute(
            "SELECT operation_digest, result_json FROM idempotency_keys "
            "WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        if row["operation_digest"] != operation_digest:
            raise ConflictError("idempotency key was already used for another operation")
        return json.loads(row["result_json"])

    @staticmethod
    def _save_idempotent_result(
        connection: sqlite3.Connection,
        idempotency_key: str | None,
        operation_digest: str,
        result: dict[str, Any],
    ) -> None:
        if idempotency_key is None:
            return
        connection.execute(
            "INSERT INTO idempotency_keys("
            "idempotency_key, operation_digest, result_json) VALUES(?, ?, ?)",
            (idempotency_key, operation_digest, canonical_json(result)),
        )

    @staticmethod
    def _record_query() -> str:
        return (
            "SELECT r.*, s.lifecycle FROM memory_records r "
            "JOIN memory_states s ON s.memory_id = r.memory_id "
        )

    def _insert_record(
        self,
        connection: sqlite3.Connection,
        memory: MemoryInput,
        *,
        memory_id: str,
        recorded_at_utc: str,
        supersedes_id: str | None,
        lineage_root_id: str,
    ) -> None:
        content = dict(memory.content)
        connection.execute(
            """
            INSERT INTO memory_records(
                memory_id, memory_type, domain, project_scope, subject_type,
                subject_id, slot_key, content_json, content_digest, source_kind,
                source_ref, source_digest, observed_at_utc, recorded_at_utc,
                validated_at_utc, freshness_class, aged_after_utc,
                stale_after_utc, sensitivity, confidence, supersedes_id,
                lineage_root_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                memory_id,
                memory.memory_type,
                memory.scope.domain.strip(),
                (memory.scope.project_scope or "").strip(),
                memory.scope.subject_type.strip(),
                memory.scope.subject_id.strip(),
                memory.scope.slot_key.strip(),
                canonical_json(content),
                digest_json(content),
                memory.source_kind.strip(),
                memory.source_ref.strip(),
                memory.source_digest,
                _optional_utc_text(memory.observed_at_utc),
                recorded_at_utc,
                _optional_utc_text(memory.validated_at_utc),
                memory.freshness_class,
                _optional_utc_text(memory.aged_after_utc),
                _optional_utc_text(memory.stale_after_utc),
                memory.sensitivity,
                float(memory.confidence),
                supersedes_id,
                lineage_root_id,
            ),
        )
        connection.execute(
            "INSERT INTO memory_states(memory_id, lifecycle, changed_at_utc) "
            "VALUES(?, 'CURRENT', ?)",
            (memory_id, recorded_at_utc),
        )

    def create(
        self,
        memory: MemoryInput,
        *,
        idempotency_key: str | None = None,
        recorded_at_utc: str | None = None,
    ) -> dict[str, Any]:
        memory.validate()
        scope_values = _scope_values(memory.scope)
        recorded = _utc_text(recorded_at_utc or utc_now())
        operation_value = {
            "memory": self._input_value(memory),
            "recorded_at_utc": recorded_at_utc,
        }
        operation_digest = self._operation_digest("CREATE", operation_value)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_result(
                connection, idempotency_key, operation_digest
            )
            if replay is not None:
                connection.rollback()
                return replay
            if connection.execute(
                "SELECT 1 FROM memory_heads WHERE "
                "domain=? AND project_scope=? AND subject_type=? "
                "AND subject_id=? AND slot_key=?",
                scope_values,
            ).fetchone():
                connection.rollback()
                raise ConflictError("scope already has a current memory")

            memory_id = _memory_id()
            self._insert_record(
                connection,
                memory,
                memory_id=memory_id,
                recorded_at_utc=recorded,
                supersedes_id=None,
                lineage_root_id=memory_id,
            )
            connection.execute(
                "INSERT INTO memory_heads("
                "domain, project_scope, subject_type, subject_id, slot_key, memory_id"
                ") VALUES(?,?,?,?,?,?)",
                (*scope_values, memory_id),
            )
            connection.execute(
                "INSERT INTO memory_events VALUES(?, 'CREATE', ?, NULL, NULL, ?)",
                (_event_id(), memory_id, recorded),
            )
            result = self._get_in_connection(connection, memory_id)
            self._save_idempotent_result(
                connection, idempotency_key, operation_digest, result
            )
            connection.commit()
            return result

    def correct(
        self,
        memory: MemoryInput,
        *,
        expected_current_id: str,
        idempotency_key: str | None = None,
        recorded_at_utc: str | None = None,
    ) -> dict[str, Any]:
        memory.validate()
        if not isinstance(expected_current_id, str) or not expected_current_id:
            raise ValueError("expected_current_id must be non-empty")
        scope_values = _scope_values(memory.scope)
        recorded = _utc_text(recorded_at_utc or utc_now())
        operation_value = {
            "memory": self._input_value(memory),
            "expected_current_id": expected_current_id,
            "recorded_at_utc": recorded_at_utc,
        }
        operation_digest = self._operation_digest("CORRECT", operation_value)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_result(
                connection, idempotency_key, operation_digest
            )
            if replay is not None:
                connection.rollback()
                return replay
            head = connection.execute(
                "SELECT memory_id FROM memory_heads WHERE "
                "domain=? AND project_scope=? AND subject_type=? "
                "AND subject_id=? AND slot_key=?",
                scope_values,
            ).fetchone()
            if head is None:
                connection.rollback()
                raise NotFoundError("scope has no current memory")
            if head["memory_id"] != expected_current_id:
                connection.rollback()
                raise ConflictError("current memory changed")

            previous = self._get_in_connection(connection, expected_current_id)
            memory_id = _memory_id()
            self._insert_record(
                connection,
                memory,
                memory_id=memory_id,
                recorded_at_utc=recorded,
                supersedes_id=expected_current_id,
                lineage_root_id=previous["lineage_root_id"],
            )
            connection.execute(
                "UPDATE memory_states SET lifecycle='SUPERSEDED', changed_at_utc=? "
                "WHERE memory_id=? AND lifecycle='CURRENT'",
                (recorded, expected_current_id),
            )
            connection.execute(
                "UPDATE memory_heads SET memory_id=? WHERE "
                "domain=? AND project_scope=? AND subject_type=? "
                "AND subject_id=? AND slot_key=? AND memory_id=?",
                (memory_id, *scope_values, expected_current_id),
            )
            connection.execute(
                "INSERT INTO memory_events VALUES(?, 'CORRECT', ?, ?, NULL, ?)",
                (_event_id(), memory_id, expected_current_id, recorded),
            )
            result = self._get_in_connection(connection, memory_id)
            self._save_idempotent_result(
                connection, idempotency_key, operation_digest, result
            )
            connection.commit()
            return result

    def retract(
        self,
        scope: Scope,
        *,
        expected_current_id: str,
        reason: str,
        idempotency_key: str | None = None,
        occurred_at_utc: str | None = None,
    ) -> dict[str, Any]:
        scope_values = _scope_values(scope)
        if not isinstance(expected_current_id, str) or not expected_current_id:
            raise ValueError("expected_current_id must be non-empty")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        if len(reason.strip()) > 2048:
            raise ValueError("reason exceeds 2048 characters")
        occurred = _utc_text(occurred_at_utc or utc_now())
        operation_value = {
            "scope": self._scope_value(scope),
            "expected_current_id": expected_current_id,
            "reason": reason.strip(),
            "occurred_at_utc": occurred_at_utc,
        }
        operation_digest = self._operation_digest("RETRACT", operation_value)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_result(
                connection, idempotency_key, operation_digest
            )
            if replay is not None:
                connection.rollback()
                return replay
            head = connection.execute(
                "SELECT memory_id FROM memory_heads WHERE "
                "domain=? AND project_scope=? AND subject_type=? "
                "AND subject_id=? AND slot_key=?",
                scope_values,
            ).fetchone()
            if head is None:
                connection.rollback()
                raise NotFoundError("scope has no current memory")
            if head["memory_id"] != expected_current_id:
                connection.rollback()
                raise ConflictError("current memory changed")

            connection.execute(
                "UPDATE memory_states SET lifecycle='RETRACTED', changed_at_utc=? "
                "WHERE memory_id=? AND lifecycle='CURRENT'",
                (occurred, expected_current_id),
            )
            connection.execute(
                "DELETE FROM memory_heads WHERE "
                "domain=? AND project_scope=? AND subject_type=? "
                "AND subject_id=? AND slot_key=? AND memory_id=?",
                (*scope_values, expected_current_id),
            )
            connection.execute(
                "INSERT INTO memory_events VALUES(?, 'RETRACT', ?, ?, ?, ?)",
                (
                    _event_id(),
                    expected_current_id,
                    expected_current_id,
                    reason.strip(),
                    occurred,
                ),
            )
            result = self._get_in_connection(connection, expected_current_id)
            self._save_idempotent_result(
                connection, idempotency_key, operation_digest, result
            )
            connection.commit()
            return result

    def _get_in_connection(
        self, connection: sqlite3.Connection, memory_id: str
    ) -> dict[str, Any]:
        row = connection.execute(
            self._record_query() + "WHERE r.memory_id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("memory record not found")
        return self._row_to_record(row)

    def get(self, memory_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            return self._get_in_connection(connection, memory_id)

    def current(self, scope: Scope) -> dict[str, Any]:
        scope_values = _scope_values(scope)
        with self._connect() as connection:
            row = connection.execute(
                self._record_query()
                + "JOIN memory_heads h ON h.memory_id = r.memory_id WHERE "
                "h.domain=? AND h.project_scope=? AND h.subject_type=? "
                "AND h.subject_id=? AND h.slot_key=?",
                scope_values,
            ).fetchone()
            if row is None:
                raise NotFoundError("scope has no current memory")
            return self._row_to_record(row)

    def history(self, scope: Scope) -> list[dict[str, Any]]:
        scope_values = _scope_values(scope)
        with self._connect() as connection:
            rows = connection.execute(
                self._record_query()
                + "WHERE r.domain=? AND r.project_scope=? AND r.subject_type=? "
                "AND r.subject_id=? AND r.slot_key=? "
                "ORDER BY r.recorded_at_utc, r.memory_id",
                scope_values,
            ).fetchall()
            return [self._row_to_record(row) for row in rows]

    def records(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                self._record_query()
                + "ORDER BY r.recorded_at_utc DESC, r.memory_id ASC"
            ).fetchall()
            return [self._row_to_record(row) for row in rows]

    def events(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_events "
                "ORDER BY occurred_at_utc, event_id"
            ).fetchall()
            return [dict(row) for row in rows]

    def integrity_check(self) -> dict[str, Any]:
        with self._connect() as connection:
            quick_check = [
                row[0] for row in connection.execute("PRAGMA quick_check").fetchall()
            ]
            foreign_keys = [
                tuple(row)
                for row in connection.execute("PRAGMA foreign_key_check").fetchall()
            ]
            head_state = [
                row[0]
                for row in connection.execute(
                    "SELECT h.memory_id FROM memory_heads h "
                    "JOIN memory_states s ON s.memory_id=h.memory_id "
                    "WHERE s.lifecycle <> 'CURRENT'"
                ).fetchall()
            ]
            current_without_head = [
                row[0]
                for row in connection.execute(
                    "SELECT s.memory_id FROM memory_states s "
                    "LEFT JOIN memory_heads h ON h.memory_id=s.memory_id "
                    "WHERE s.lifecycle='CURRENT' AND h.memory_id IS NULL"
                ).fetchall()
            ]
            head_scope = [
                row[0]
                for row in connection.execute(
                    "SELECT h.memory_id FROM memory_heads h "
                    "JOIN memory_records r ON r.memory_id=h.memory_id WHERE "
                    "h.domain<>r.domain OR h.project_scope<>r.project_scope OR "
                    "h.subject_type<>r.subject_type OR h.subject_id<>r.subject_id OR "
                    "h.slot_key<>r.slot_key"
                ).fetchall()
            ]
            content = []
            for row in connection.execute(
                "SELECT memory_id, content_json, content_digest FROM memory_records"
            ).fetchall():
                try:
                    parsed = json.loads(row["content_json"])
                    valid = (
                        canonical_json(parsed) == row["content_json"]
                        and digest_json(parsed) == row["content_digest"]
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    valid = False
                if not valid:
                    content.append(row["memory_id"])
            required_triggers = {
                "memory_records_no_update",
                "memory_records_no_delete",
                "memory_events_no_update",
                "memory_events_no_delete",
            }
            present_triggers = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ).fetchall()
            }
            schema = sorted(required_triggers - present_triggers)
        violations = {
            "foreign_keys": foreign_keys,
            "head_state": head_state,
            "head_scope": head_scope,
            "current_without_head": current_without_head,
            "content": content,
            "missing_schema_controls": schema,
        }
        return {
            "ok": quick_check == ["ok"] and not any(violations.values()),
            "quick_check": quick_check,
            "violations": violations,
        }

    @staticmethod
    def _scope_value(scope: Scope) -> dict[str, Any]:
        return {
            "domain": scope.domain.strip(),
            "project_scope": (scope.project_scope or "").strip() or None,
            "subject_type": scope.subject_type.strip(),
            "subject_id": scope.subject_id.strip(),
            "slot_key": scope.slot_key.strip(),
        }

    @classmethod
    def _input_value(cls, memory: MemoryInput) -> dict[str, Any]:
        return {
            "memory_type": memory.memory_type,
            "scope": cls._scope_value(memory.scope),
            "content": dict(memory.content),
            "source_kind": memory.source_kind,
            "source_ref": memory.source_ref,
            "freshness_class": memory.freshness_class,
            "sensitivity": memory.sensitivity,
            "confidence": float(memory.confidence),
            "observed_at_utc": memory.observed_at_utc,
            "validated_at_utc": memory.validated_at_utc,
            "aged_after_utc": memory.aged_after_utc,
            "stale_after_utc": memory.stale_after_utc,
            "source_digest": memory.source_digest,
        }
