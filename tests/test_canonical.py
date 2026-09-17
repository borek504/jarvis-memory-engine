from __future__ import annotations

from datetime import timezone
import unittest

from jarvis_memory_engine.canonical import (
    canonical_bytes,
    canonical_json,
    digest_json,
    normalize_text,
    parse_utc,
)


class CanonicalTests(unittest.TestCase):
    def test_object_order_is_deterministic(self) -> None:
        self.assertEqual(canonical_json({"b": 2, "a": 1}), '{"a":1,"b":2}')

    def test_unicode_is_preserved(self) -> None:
        self.assertEqual(canonical_json({"value": "zażółć"}), '{"value":"zażółć"}')

    def test_digest_is_order_independent(self) -> None:
        self.assertEqual(digest_json({"b": 2, "a": 1}), digest_json({"a": 1, "b": 2}))

    def test_bytes_are_utf8(self) -> None:
        self.assertEqual(canonical_bytes({"value": "é"}), b'{"value":"\xc3\xa9"}')

    def test_non_finite_number_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            canonical_json({"value": float("nan")})

    def test_non_string_object_key_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            canonical_json({1: "value"})

    def test_unsupported_value_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            canonical_json({"value": object()})

    def test_text_normalizes_to_nfc(self) -> None:
        self.assertEqual(normalize_text("e\u0301"), "é")

    def test_timezone_offset_normalizes_for_comparison(self) -> None:
        value = parse_utc("2026-01-01T02:00:00+02:00")
        self.assertEqual(value.tzinfo, timezone.utc)
        self.assertEqual(value.hour, 0)

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_utc("2026-01-01T00:00:00")


if __name__ == "__main__":
    unittest.main()
