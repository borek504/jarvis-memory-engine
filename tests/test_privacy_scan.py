from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tools.privacy_scan import scan_tree


class PrivacyScanTests(unittest.TestCase):
    def test_repository_tree_is_clean(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(scan_tree(root), [])

    def test_database_artifact_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ("live" + ".sqlite3")
            path.write_bytes(b"synthetic")
            self.assertTrue(scan_tree(Path(directory)))

    def test_email_address_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_text("person" + chr(64) + "example.com", encoding="utf-8")
            self.assertTrue(scan_tree(Path(directory)))

    def test_private_key_marker_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_text(
                "-----BEGIN " + "PRIVATE" + " KEY-----", encoding="utf-8"
            )
            self.assertTrue(scan_tree(Path(directory)))

    def test_token_signature_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_text("sk" + "-proj-" + "A" * 24, encoding="utf-8")
            self.assertTrue(scan_tree(Path(directory)))


if __name__ == "__main__":
    unittest.main()
