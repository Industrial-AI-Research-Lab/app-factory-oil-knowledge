"""Pinned-source and connection configuration behavior."""

import hashlib
import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sources
from sources import SOURCES


class SourceTests(unittest.TestCase):
    def test_every_remote_source_has_a_pinned_sha256(self):
        for filename, spec in SOURCES.items():
            with self.subTest(filename=filename):
                self.assertIsInstance(spec, dict)
                self.assertIn("url", spec)
                self.assertRegex(spec.get("sha256", ""), re.compile(r"^[0-9a-f]{64}$"))

    def test_checksum_mismatch_preserves_existing_source(self):
        original = b"known-good"
        expected = hashlib.sha256(original).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            target = raw / "source.json"
            target.write_bytes(original)
            source_specs = {
                target.name: {"url": "https://example.test/source", "sha256": expected}
            }
            with (
                patch.object(sources, "RAW", raw),
                patch.object(sources, "SOURCES", source_specs),
                patch(
                    "sources.urllib.request.urlopen",
                    return_value=io.BytesIO(b"changed"),
                ),
                self.assertRaisesRegex(ValueError, "checksum mismatch"),
            ):
                sources.download(force=True)

            self.assertEqual(target.read_bytes(), original)
            self.assertFalse(target.with_suffix(".json.part").exists())

    def test_database_url_is_required_before_connecting(self):
        with (
            patch.dict(sources.os.environ, {"FALKORDB_PASSWORD": "secret"}, clear=True),
            patch.object(sources, "GraphClient") as client_class,
            self.assertRaisesRegex(ValueError, "FALKORDB_URL"),
        ):
            sources.client_from_env()
        client_class.assert_not_called()

    def test_manifest_records_that_every_remote_source_is_pinned(self):
        payload = b"known-good"
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            raw.mkdir()
            (raw / "source.json").write_bytes(payload)
            source_specs = {
                "source.json": {
                    "url": "https://example.test/source",
                    "sha256": expected,
                }
            }
            with (
                patch.object(sources, "ROOT", root),
                patch.object(sources, "RAW", raw),
                patch.object(sources, "SOURCES", source_specs),
            ):
                manifest = sources.download()

        self.assertIn("pinned SHA-256", manifest["notes"])


if __name__ == "__main__":
    unittest.main()
