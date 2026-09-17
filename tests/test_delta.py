from __future__ import annotations

import json
import unittest

from jarvis_memory_engine import DeltaValidationError, validate_delta_bytes


def envelope():
    return {
        "protocol_version": "jarvis-memory-delta-v1",
        "delta_id": "delta_" + "1" * 32,
        "created_at_utc": "2026-01-01T00:00:00Z",
        "domain": "demo",
        "project_scope": "sample",
        "candidates": [
            {
                "candidate_id": "cand_" + "2" * 32,
                "action": "CREATE",
                "request": {
                    "domain": "demo",
                    "project_scope": "sample",
                    "content": {"value": "synthetic"},
                },
            }
        ],
    }


def raw(value) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


class DeltaTests(unittest.TestCase):
    def test_valid_delta_returns_canonical_identity(self) -> None:
        result = validate_delta_bytes(raw(envelope()))
        self.assertEqual(len(result.sha256), 64)
        self.assertTrue(result.filename.endswith(".json"))
        self.assertEqual(json.loads(result.canonical_bytes), envelope())

    def test_root_fields_are_exact(self) -> None:
        value = envelope()
        value["extra"] = True
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(raw(value))

    def test_protocol_is_strict(self) -> None:
        value = envelope()
        value["protocol_version"] = "other"
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(raw(value))

    def test_delta_id_is_strict(self) -> None:
        value = envelope()
        value["delta_id"] = "delta_invalid"
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(raw(value))

    def test_candidate_count_is_bounded(self) -> None:
        value = envelope()
        value["candidates"] = []
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(raw(value))

    def test_duplicate_candidate_id_is_rejected(self) -> None:
        value = envelope()
        value["candidates"].append(dict(value["candidates"][0]))
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(raw(value))

    def test_cross_domain_candidate_is_rejected(self) -> None:
        value = envelope()
        value["candidates"][0]["request"]["domain"] = "other"
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(raw(value))

    def test_missing_scope_binding_is_rejected(self) -> None:
        value = envelope()
        del value["candidates"][0]["request"]["project_scope"]
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(raw(value))

    def test_authority_override_is_rejected_at_any_depth(self) -> None:
        value = envelope()
        value["candidates"][0]["request"]["content"]["write_authority"] = "self"
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(raw(value))

    def test_secret_like_field_is_rejected(self) -> None:
        value = envelope()
        value["candidates"][0]["request"]["content"]["token"] = "synthetic"
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(raw(value))

    def test_private_key_marker_is_rejected(self) -> None:
        value = envelope()
        value["candidates"][0]["request"]["content"]["value"] = (
            "-----BEGIN " + "PRIVATE" + " KEY-----"
        )
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(raw(value))

    def test_token_signature_is_rejected(self) -> None:
        value = envelope()
        value["candidates"][0]["request"]["content"]["value"] = (
            "gh" + "p_" + "A" * 24
        )
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(raw(value))

    def test_duplicate_json_key_is_reported(self) -> None:
        duplicate = (
            '{"protocol_version":"jarvis-memory-delta-v1",'
            '"protocol_version":"jarvis-memory-delta-v1"}'
        ).encode()
        with self.assertRaisesRegex(DeltaValidationError, "duplicate JSON key"):
            validate_delta_bytes(duplicate)

    def test_invalid_utf8_is_rejected(self) -> None:
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(b"\xff")

    def test_non_finite_json_number_is_rejected(self) -> None:
        value = raw(envelope()).replace(b'"synthetic"', b'NaN')
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(value)

    def test_non_string_action_is_rejected(self) -> None:
        value = envelope()
        value["candidates"][0]["action"] = []
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(raw(value))

    def test_size_limit_is_enforced_before_parse(self) -> None:
        with self.assertRaises(DeltaValidationError):
            validate_delta_bytes(b"x" * (64 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
