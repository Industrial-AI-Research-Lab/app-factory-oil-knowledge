from copy import deepcopy

from test_news_commands import NewsFixture


class NewsBoundaryTests(NewsFixture):
    def test_publication_date_boundaries_are_inclusive(self):
        start = self.publication | {
            "publication_id": "start",
            "published_date": "2025-01-01",
        }
        end = self.publication | {
            "publication_id": "end",
            "published_date": "2025-12-31",
        }
        outside = self.publication | {
            "publication_id": "outside",
            "published_date": "2026-01-01",
        }
        self.write_package(
            "news", "news", {"a.json": start, "b.json": end, "c.json": outside}
        )
        result = self.call(**self.search_request())
        self.assertEqual(
            {row["publication_id"] for row in result["candidates"]}, {"start", "end"}
        )
        self.assertEqual(result["audit"][2]["status"], "exclude_period")

    def test_empty_period_produces_zero_events_with_archive_boundary(self):
        self.request["brief"]["period"] = {"start": "2024-01-01", "end": "2024-12-31"}
        search = self.call(**self.search_request())
        analysis = {"publications": [], "events": []}
        result = self.call(
            operation="finalize",
            candidates_ref=search["reference"],
            initial_analysis=analysis,
            analysis=analysis,
            critique={"correction_cycle": 0, "notes": []},
        )["result"]
        self.assertEqual(result["statistics"]["event_count"], 0)
        self.assertEqual(result["statistics"]["publication_count"], 0)
        self.assertEqual(result["brief"]["archive_period"]["start"], "2025-06-01")
        self.assertTrue(result["limitations"])

    def test_missing_source_metadata_cannot_support_a_candidate(self):
        self.publication["url"] = None
        self.publication["source"] = " "
        self.write_package("news", "news", {"paper.json": self.publication})
        result = self.call(**self.search_request())
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["audit"][0]["status"], "insufficient_evidence")

    def test_missing_file_remains_a_structured_error(self):
        (self.inputs / "news/paper.json").unlink()
        self.assertEqual(
            self.call(expected_error="MISSING_FILE")["code"], "MISSING_FILE"
        )

    def test_unknown_terminology_id_is_rejected(self):
        request = self.search_request()
        request["terminology_result"]["expansions"][0]["term_id"] = "unknown"
        self.assertEqual(
            self.call(expected_error="UNKNOWN_ID", **request)["code"], "UNKNOWN_ID"
        )

    def test_approval_for_another_brief_is_rejected(self):
        request = self.search_request()
        request["approval"]["brief_sha256"] = "0" * 64
        self.assertEqual(
            self.call(expected_error="VERSION_MISMATCH", **request)["code"],
            "VERSION_MISMATCH",
        )

    def test_fabricated_source_quote_is_rejected(self):
        request = self.finalize_request()
        request["analysis"]["events"][0]["evidence"][0][
            "quote"
        ] = "Не существующая цитата"
        self.assertEqual(
            self.call(expected_error="INVALID_EVIDENCE", **request)["code"],
            "INVALID_EVIDENCE",
        )

    def test_two_correction_cycles_are_rejected(self):
        request = self.finalize_request()
        request["critique"]["correction_cycle"] = 2
        self.assertEqual(
            self.call(expected_error="INVALID_INPUT", **request)["code"],
            "INVALID_INPUT",
        )

    def test_unresolved_criticism_remains_visible_after_one_correction(self):
        request = self.finalize_request()
        request["analysis"] = deepcopy(request["initial_analysis"])
        request["analysis"]["events"][0]["limitations"] = ["Нет протокола испытаний"]
        request["critique"] = {
            "correction_cycle": 1,
            "notes": [
                {
                    "id": "review-one",
                    "publication_ids": ["paper-a"],
                    "event_ids": ["event-a"],
                    "issue": "Отсутствует протокол испытаний",
                    "action": "Указать границу данных",
                    "resolution": "unresolved",
                    "resolution_reason": "Архив не содержит протокола",
                }
            ],
        }
        result = self.call(**request)["result"]
        self.assertIn("Отсутствует протокол испытаний", result["limitations"])
        self.assertNotEqual(
            result["analysis_sha256"], result["initial_analysis_sha256"]
        )
        self.assertEqual(result["critique"]["correction_cycle"], 1)

    def test_changed_result_cannot_overwrite_previous_result(self):
        request = self.finalize_request()
        reference = self.call(**request)["reference"]
        original = (self.results / reference["path"]).read_bytes()
        request["analysis"]["events"][0]["description"] = "Другое описание"
        self.assertEqual(
            self.call(expected_error="RESULT_CONFLICT", **request)["code"],
            "RESULT_CONFLICT",
        )
        self.assertEqual((self.results / reference["path"]).read_bytes(), original)
