import hashlib
import json

from test_news_commands import NewsFixture


class NewsInputErrorsTest(NewsFixture):
    def test_non_object_briefs_have_structured_errors(self):
        for value in (None, [], "", 0, {}):
            with self.subTest(value=value):
                error = self.call(expected_error="INVALID_INPUT", brief=value)
                self.assertEqual(error["code"], "INVALID_INPUT")

    def test_period_rejects_invalid_dates_and_reversed_bounds(self):
        for start in (None, [], {}, "", 0, "20250101", "2025-02-29", "2026-01-01"):
            with self.subTest(start=start):
                brief = self.request["brief"] | {
                    "period": {"start": start, "end": "2025-12-31"}
                }
                self.assertEqual(
                    self.call(expected_error="INVALID_INPUT", brief=brief)["code"],
                    "INVALID_INPUT",
                )

    def test_arrays_reject_null_wrong_types_and_empty_required_values(self):
        for field, value in (
            ("inclusions", None),
            ("inclusions", []),
            ("exclusions", {}),
            ("output_formats", "json"),
            ("output_formats", [""]),
        ):
            with self.subTest(field=field, value=value):
                brief = self.request["brief"] | {field: value}
                self.assertEqual(
                    self.call(expected_error="INVALID_INPUT", brief=brief)["code"],
                    "INVALID_INPUT",
                )

    def test_reference_cannot_cross_project_or_run(self):
        request = self.search_request()
        error = self.call(
            expected_error="INVALID_PATH", **(request | {"project_id": "p2"})
        )
        self.assertEqual(error["code"], "INVALID_PATH")

    def test_reference_checksum_change_is_rejected(self):
        request = self.search_request()
        request["brief_ref"]["sha256"] = "0" * 64
        self.assertEqual(
            self.call(expected_error="CHECKSUM_MISMATCH", **request)["code"],
            "CHECKSUM_MISMATCH",
        )

    def test_changed_manifest_does_not_reuse_previous_brief(self):
        request = self.search_request()
        self.publication["title"] = "Обновлённый заголовок"
        self.write_package("news", "news", {"paper.json": self.publication})
        self.assertEqual(
            self.call(expected_error="VERSION_MISMATCH", **request)["code"],
            "VERSION_MISMATCH",
        )

    def test_malformed_publication_json_is_not_zero_news(self):
        root = self.inputs / "news"
        (root / "paper.json").write_bytes(b"{broken")
        manifest = json.loads((root / "manifest.json").read_bytes())
        manifest["files"][0]["sha256"] = hashlib.sha256(b"{broken").hexdigest()
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(
            self.call(expected_error="INVALID_JSON")["code"], "INVALID_JSON"
        )

    def test_duplicate_publication_identifiers_are_rejected(self):
        self.write_package(
            "news", "news", {"a.json": self.publication, "b.json": self.publication}
        )
        self.assertEqual(
            self.call(expected_error="DUPLICATE_ID")["code"], "DUPLICATE_ID"
        )
