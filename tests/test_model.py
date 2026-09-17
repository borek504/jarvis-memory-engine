from __future__ import annotations

from datetime import datetime, timezone
import unittest

from jarvis_memory_engine.model import MemoryInput, Scope, freshness_state


T0 = "2026-01-01T00:00:00Z"
T1 = "2026-01-02T00:00:00Z"
T2 = "2026-01-03T00:00:00Z"


def sample(**overrides):
    values = {
        "memory_type": "FACT",
        "scope": Scope("demo", "project", "alpha", "status", "sample"),
        "content": {"value": "ready"},
        "source_kind": "user_input",
        "source_ref": "synthetic-example",
    }
    values.update(overrides)
    return MemoryInput(**values)


class ModelTests(unittest.TestCase):
    def test_valid_input(self) -> None:
        sample().validate()

    def test_empty_scope_value_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            sample(scope=Scope("", "project", "alpha", "status")).validate()

    def test_event_cannot_be_a_head(self) -> None:
        with self.assertRaises(ValueError):
            sample(memory_type="EVENT").validate()

    def test_historical_cannot_be_a_head(self) -> None:
        with self.assertRaises(ValueError):
            sample(freshness_class="HISTORICAL").validate()

    def test_time_bound_requires_all_boundaries(self) -> None:
        with self.assertRaises(ValueError):
            sample(freshness_class="TIME_BOUND", observed_at_utc=T0).validate()

    def test_stale_boundary_cannot_precede_aged_boundary(self) -> None:
        with self.assertRaises(ValueError):
            sample(
                freshness_class="TIME_BOUND",
                observed_at_utc=T0,
                aged_after_utc=T2,
                stale_after_utc=T1,
            ).validate()

    def test_aged_boundary_cannot_precede_observation(self) -> None:
        with self.assertRaises(ValueError):
            sample(
                freshness_class="TIME_BOUND",
                observed_at_utc=T1,
                aged_after_utc=T0,
                stale_after_utc=T2,
            ).validate()

    def test_content_size_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            sample(content={"value": "x" * (256 * 1024)}).validate()

    def test_scope_size_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            sample(scope=Scope("x" * 65, "project", "alpha", "status")).validate()

    def test_boolean_confidence_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            sample(confidence=True).validate()

    def test_source_digest_is_strict(self) -> None:
        with self.assertRaises(ValueError):
            sample(source_digest="not-a-digest").validate()

    def test_durable_is_fresh(self) -> None:
        self.assertEqual(
            freshness_state(
                freshness_class="DURABLE",
                lifecycle="CURRENT",
                aged_after_utc=None,
                stale_after_utc=None,
                validated_at_utc=None,
            ),
            "FRESH",
        )

    def test_time_bound_transitions(self) -> None:
        self.assertEqual(
            freshness_state(
                freshness_class="TIME_BOUND",
                lifecycle="CURRENT",
                aged_after_utc=T1,
                stale_after_utc=T2,
                validated_at_utc=None,
                now=datetime(2026, 1, 2, 12, tzinfo=timezone.utc),
            ),
            "AGED",
        )

    def test_source_bound_without_validation_is_unknown(self) -> None:
        self.assertEqual(
            freshness_state(
                freshness_class="SOURCE_BOUND",
                lifecycle="CURRENT",
                aged_after_utc=None,
                stale_after_utc=None,
                validated_at_utc=None,
            ),
            "UNKNOWN",
        )

    def test_non_current_is_not_applicable(self) -> None:
        self.assertEqual(
            freshness_state(
                freshness_class="DURABLE",
                lifecycle="SUPERSEDED",
                aged_after_utc=None,
                stale_after_utc=None,
                validated_at_utc=None,
            ),
            "NOT_APPLICABLE",
        )


if __name__ == "__main__":
    unittest.main()
