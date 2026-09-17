from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest

from jarvis_memory_engine import build_context, search
from jarvis_memory_engine.canonical import digest_json


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def record(
    memory_id: str,
    *,
    memory_type: str = "FACT",
    value: str = "alpha",
    lifecycle: str = "CURRENT",
    freshness_class: str = "DURABLE",
    sensitivity: str = "STANDARD_PRIVATE",
    confidence: float = 1.0,
    project_scope: str | None = "sample",
    validated_at_utc: str | None = None,
    aged_after_utc: str | None = None,
    stale_after_utc: str | None = None,
):
    return {
        "memory_id": memory_id,
        "memory_type": memory_type,
        "domain": "demo",
        "project_scope": project_scope,
        "subject_type": "project",
        "subject_id": "alpha",
        "slot_key": "status-" + memory_id[-1],
        "content": {"value": value},
        "content_digest": "0" * 64,
        "source_kind": "synthetic",
        "source_ref": "example",
        "source_digest": None,
        "observed_at_utc": None,
        "recorded_at_utc": "2026-01-01T00:00:00Z",
        "validated_at_utc": validated_at_utc,
        "freshness_class": freshness_class,
        "aged_after_utc": aged_after_utc,
        "stale_after_utc": stale_after_utc,
        "sensitivity": sensitivity,
        "confidence": confidence,
        "supersedes_id": None,
        "lineage_root_id": memory_id,
        "lifecycle": lifecycle,
    }


class RetrievalTests(unittest.TestCase):
    def test_current_only_is_default(self) -> None:
        values = [record("mem_" + "1" * 32), record("mem_" + "2" * 32, lifecycle="SUPERSEDED")]
        self.assertEqual(len(search(values, now=NOW)), 1)

    def test_restricted_local_is_always_excluded(self) -> None:
        values = [record("mem_" + "1" * 32, sensitivity="RESTRICTED_LOCAL")]
        self.assertEqual(search(values, allow_sensitive=True, sensitive_purpose="test"), [])

    def test_sensitive_requires_explicit_purpose(self) -> None:
        values = [record("mem_" + "1" * 32, sensitivity="SENSITIVE")]
        self.assertEqual(search(values), [])
        with self.assertRaises(ValueError):
            search(values, allow_sensitive=True)
        self.assertEqual(
            len(search(values, allow_sensitive=True, sensitive_purpose="unit test")),
            1,
        )

    def test_stale_is_excluded_when_usable_only(self) -> None:
        values = [
            record(
                "mem_" + "1" * 32,
                freshness_class="TIME_BOUND",
                aged_after_utc="2025-12-30T00:00:00Z",
                stale_after_utc="2026-01-01T00:00:00Z",
            )
        ]
        self.assertEqual(search(values, now=NOW), [])
        self.assertEqual(len(search(values, now=NOW, usable_only=False)), 1)

    def test_source_bound_unknown_is_excluded(self) -> None:
        values = [record("mem_" + "1" * 32, freshness_class="SOURCE_BOUND")]
        self.assertEqual(search(values, now=NOW), [])

    def test_query_matching_is_case_insensitive(self) -> None:
        values = [record("mem_" + "1" * 32, value="Alpha Beta")]
        self.assertEqual(len(search(values, query="alpha beta", now=NOW)), 1)

    def test_all_tokens_rank_before_any_token(self) -> None:
        both = record("mem_" + "1" * 32, value="alpha beta")
        one = record("mem_" + "2" * 32, value="alpha only")
        result = search([one, both], query="alpha beta", now=NOW)
        self.assertEqual(result[0]["memory_id"], both["memory_id"])

    def test_rule_priority_breaks_tie(self) -> None:
        fact = record("mem_" + "1" * 32, memory_type="FACT")
        rule = record("mem_" + "2" * 32, memory_type="RULE")
        result = search([fact, rule], query="alpha", now=NOW)
        self.assertEqual(result[0]["memory_type"], "RULE")

    def test_confidence_breaks_remaining_tie(self) -> None:
        low = record("mem_" + "1" * 32, confidence=0.2)
        high = record("mem_" + "2" * 32, confidence=0.9)
        result = search([low, high], query="alpha", now=NOW)
        self.assertEqual(result[0]["confidence"], 0.9)

    def test_filters_do_not_cross_projects(self) -> None:
        one = record("mem_" + "1" * 32, project_scope="one")
        two = record("mem_" + "2" * 32, project_scope="two")
        result = search([one, two], project_scope="one", now=NOW)
        self.assertEqual([item["project_scope"] for item in result], ["one"])

    def test_limit_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            search([], limit=0)
        with self.assertRaises(ValueError):
            search([], limit=501)

    def test_query_type_is_strict(self) -> None:
        with self.assertRaises(TypeError):
            search([], query=123)

    def test_context_record_budget_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            build_context([], max_records=501)

    def test_context_budget_counts_complete_items(self) -> None:
        value = record("mem_" + "1" * 32)
        encoded = json.dumps(
            {**value, "freshness_state": "FRESH"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        too_small = build_context([value], max_utf8_bytes=len(encoded) - 1, now=NOW)
        exact = build_context([value], max_utf8_bytes=len(encoded), now=NOW)
        self.assertEqual(too_small["items"], [])
        self.assertEqual(len(exact["items"]), 1)

    def test_context_is_marked_untrusted(self) -> None:
        result = build_context([record("mem_" + "1" * 32)], now=NOW)
        self.assertTrue(result["untrusted_data"])

    def test_context_revision_is_reproducible(self) -> None:
        result = build_context([record("mem_" + "1" * 32)], now=NOW)
        revision = result.pop("revision")
        self.assertEqual(revision, digest_json(result))


if __name__ == "__main__":
    unittest.main()
