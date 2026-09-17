from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

from jarvis_memory_engine import (
    ConflictError,
    MemoryInput,
    MemoryStore,
    NotFoundError,
    Scope,
)


T0 = "2026-01-01T00:00:00Z"
T1 = "2026-01-02T00:00:00Z"


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="memory-engine-test-")
        self.path = Path(self.temp.name) / "memory.sqlite3"
        self.store = MemoryStore(self.path)
        self.scope = Scope("demo", "project", "alpha", "status", "sample")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def memory(self, value="ready", *, scope=None):
        return MemoryInput(
            memory_type="PROJECT_STATE",
            scope=scope or self.scope,
            content={"status": value},
            source_kind="user_input",
            source_ref="synthetic-example",
        )

    def test_create_and_read_current(self) -> None:
        created = self.store.create(self.memory(), recorded_at_utc=T0)
        self.assertEqual(self.store.current(self.scope), created)
        self.assertEqual(created["lifecycle"], "CURRENT")
        self.assertEqual(len(created["content_digest"]), 64)

    @unittest.skipIf(os.name == "nt", "POSIX file mode")
    def test_database_file_is_private(self) -> None:
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_duplicate_scope_is_rejected(self) -> None:
        self.store.create(self.memory(), recorded_at_utc=T0)
        with self.assertRaises(ConflictError):
            self.store.create(self.memory("again"), recorded_at_utc=T1)

    def test_create_idempotency_replays_result(self) -> None:
        first = self.store.create(
            self.memory(), idempotency_key="create-1", recorded_at_utc=T0
        )
        second = self.store.create(
            self.memory(), idempotency_key="create-1", recorded_at_utc=T0
        )
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.records()), 1)

    def test_idempotency_key_cannot_be_reused_for_other_input(self) -> None:
        self.store.create(self.memory(), idempotency_key="create-1")
        with self.assertRaises(ConflictError):
            self.store.create(self.memory("different"), idempotency_key="create-1")

    def test_idempotency_key_size_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create(self.memory(), idempotency_key="x" * 257)

    def test_correction_is_compare_and_swap(self) -> None:
        first = self.store.create(self.memory(), recorded_at_utc=T0)
        second = self.store.correct(
            self.memory("updated"),
            expected_current_id=first["memory_id"],
            recorded_at_utc=T1,
        )
        self.assertEqual(second["supersedes_id"], first["memory_id"])
        self.assertEqual(self.store.get(first["memory_id"])["lifecycle"], "SUPERSEDED")
        self.assertEqual(self.store.current(self.scope)["memory_id"], second["memory_id"])

    def test_stale_correction_is_rejected(self) -> None:
        first = self.store.create(self.memory())
        self.store.correct(
            self.memory("updated"), expected_current_id=first["memory_id"]
        )
        with self.assertRaises(ConflictError):
            self.store.correct(
                self.memory("stale"), expected_current_id=first["memory_id"]
            )

    def test_history_is_immutable_and_ordered(self) -> None:
        first = self.store.create(self.memory(), recorded_at_utc=T0)
        second = self.store.correct(
            self.memory("updated"),
            expected_current_id=first["memory_id"],
            recorded_at_utc=T1,
        )
        history = self.store.history(self.scope)
        self.assertEqual(
            [item["memory_id"] for item in history],
            [first["memory_id"], second["memory_id"]],
        )

    def test_retraction_does_not_fall_back(self) -> None:
        first = self.store.create(self.memory(), recorded_at_utc=T0)
        second = self.store.correct(
            self.memory("updated"),
            expected_current_id=first["memory_id"],
            recorded_at_utc=T1,
        )
        retracted = self.store.retract(
            self.scope,
            expected_current_id=second["memory_id"],
            reason="synthetic correction",
        )
        self.assertEqual(retracted["lifecycle"], "RETRACTED")
        with self.assertRaises(NotFoundError):
            self.store.current(self.scope)
        self.assertEqual(self.store.get(first["memory_id"])["lifecycle"], "SUPERSEDED")

    def test_retraction_is_idempotent(self) -> None:
        created = self.store.create(self.memory())
        first = self.store.retract(
            self.scope,
            expected_current_id=created["memory_id"],
            reason="synthetic correction",
            idempotency_key="retract-1",
        )
        second = self.store.retract(
            self.scope,
            expected_current_id=created["memory_id"],
            reason="synthetic correction",
            idempotency_key="retract-1",
        )
        self.assertEqual(first, second)

    def test_project_scopes_are_isolated(self) -> None:
        other = Scope("demo", "project", "alpha", "status", "other")
        one = self.store.create(self.memory())
        two = self.store.create(self.memory(scope=other))
        self.assertNotEqual(one["memory_id"], two["memory_id"])

    def test_state_persists_across_instances(self) -> None:
        created = self.store.create(self.memory())
        reopened = MemoryStore(self.path)
        self.assertEqual(reopened.current(self.scope)["memory_id"], created["memory_id"])

    def test_record_rows_reject_direct_update(self) -> None:
        created = self.store.create(self.memory())
        connection = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE memory_records SET content_json='{}' WHERE memory_id=?",
                    (created["memory_id"],),
                )
        finally:
            connection.close()

    def test_events_are_append_only_audit_evidence(self) -> None:
        first = self.store.create(self.memory())
        second = self.store.correct(
            self.memory("updated"), expected_current_id=first["memory_id"]
        )
        self.store.retract(
            self.scope,
            expected_current_id=second["memory_id"],
            reason="synthetic correction",
        )
        self.assertEqual(
            [event["event_type"] for event in self.store.events()],
            ["CREATE", "CORRECT", "RETRACT"],
        )

    def test_unknown_record_is_not_found(self) -> None:
        with self.assertRaises(NotFoundError):
            self.store.get("mem_" + "0" * 32)

    def test_integrity_check_passes_for_valid_store(self) -> None:
        self.store.create(self.memory())
        result = self.store.integrity_check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["quick_check"], ["ok"])

    def test_integrity_check_detects_head_state_conflict(self) -> None:
        created = self.store.create(self.memory())
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "UPDATE memory_states SET lifecycle='SUPERSEDED' WHERE memory_id=?",
                (created["memory_id"],),
            )
            connection.commit()
        finally:
            connection.close()
        result = self.store.integrity_check()
        self.assertFalse(result["ok"])
        self.assertEqual(result["violations"]["head_state"], [created["memory_id"]])

    def test_integrity_check_detects_content_tamper(self) -> None:
        created = self.store.create(self.memory())
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("DROP TRIGGER memory_records_no_update")
            connection.execute(
                "UPDATE memory_records SET content_json=? WHERE memory_id=?",
                ('{"status":"tampered"}', created["memory_id"]),
            )
            connection.commit()
        finally:
            connection.close()
        result = self.store.integrity_check()
        self.assertFalse(result["ok"])
        self.assertEqual(result["violations"]["content"], [created["memory_id"]])

    def test_invalid_explicit_timestamp_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create(self.memory(), recorded_at_utc="not-a-time")

    def test_explicit_timestamp_is_normalized_to_utc(self) -> None:
        created = self.store.create(
            self.memory(), recorded_at_utc="2026-01-01T02:00:00+02:00"
        )
        self.assertEqual(created["recorded_at_utc"], "2026-01-01T00:00:00.000000Z")

    @unittest.skipIf(os.name == "nt", "POSIX symbolic links")
    def test_user_owned_symlink_component_is_rejected(self) -> None:
        target = Path(self.temp.name) / "target"
        target.mkdir()
        link = Path(self.temp.name) / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            MemoryStore(link / "memory.sqlite3")

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_writable_database_parent_is_rejected(self) -> None:
        parent = Path(self.temp.name) / "unsafe"
        parent.mkdir(mode=0o700)
        parent.chmod(0o777)
        try:
            with self.assertRaises(RuntimeError):
                MemoryStore(parent / "memory.sqlite3")
        finally:
            parent.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
