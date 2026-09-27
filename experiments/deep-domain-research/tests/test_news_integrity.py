import json
from pathlib import Path
from unittest.mock import patch

from command_io import CommandError
from news_inputs import load_inputs
from news_storage import read_result
from test_news_commands import NewsFixture


class NewsIntegrityTest(NewsFixture):
    def replace_after_read(self, target, replacement):
        original_read = Path.read_bytes
        replaced = False

        def read_bytes(path):
            nonlocal replaced
            payload = original_read(path)
            if path == target and not replaced:
                replaced = True
                path.write_bytes(json.dumps(replacement).encode())
            return payload

        return patch.object(Path, "read_bytes", read_bytes)

    def test_result_decodes_only_the_bytes_checked_by_reference(self):
        prepared = self.call()
        reference = prepared["reference"]
        path = self.results / reference["path"]
        replacement = prepared["brief"] | {"query": "Подменённая постановка"}
        with self.replace_after_read(path, replacement):
            result = read_result(
                reference,
                self.request,
                self.results,
                prepared["brief"]["inputs"],
                "brief",
            )
        self.assertEqual(result, prepared["brief"])
        self.assertEqual(json.loads(path.read_bytes())["query"], replacement["query"])

    def test_publication_changed_after_manifest_check_is_rejected(self):
        path = self.inputs / "news/paper.json"
        replacement = self.publication | {"text": "Непроверенный новый текст"}
        with self.replace_after_read(path, replacement):
            with self.assertRaises(CommandError) as raised:
                load_inputs(self.request, self.inputs)
        self.assertEqual(raised.exception.error["code"], "CHECKSUM_MISMATCH")
        self.assertEqual(json.loads(path.read_bytes()), replacement)

    def test_dictionary_changed_after_manifest_check_is_rejected(self):
        path = self.inputs / "terms/terms.json"
        replacement = {
            "version": "1",
            "terms": [{"id": "injection", "name": "Подменённый термин"}],
        }
        with self.replace_after_read(path, replacement):
            with self.assertRaises(CommandError) as raised:
                load_inputs(self.request, self.inputs)
        self.assertEqual(raised.exception.error["code"], "CHECKSUM_MISMATCH")
        self.assertEqual(json.loads(path.read_bytes()), replacement)

    def test_taxonomy_changed_after_manifest_check_is_rejected(self):
        path = self.inputs / "terms/news-taxonomy.json"
        replacement = {
            "version": "1",
            "categories": [{"id": "water", "name": "Подменённая категория"}],
        }
        with self.replace_after_read(path, replacement):
            with self.assertRaises(CommandError) as raised:
                load_inputs(self.request, self.inputs)
        self.assertEqual(raised.exception.error["code"], "CHECKSUM_MISMATCH")
        self.assertEqual(json.loads(path.read_bytes()), replacement)


if __name__ == "__main__":
    import unittest

    unittest.main()
