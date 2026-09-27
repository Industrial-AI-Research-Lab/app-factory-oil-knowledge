from copy import deepcopy

from test_news_commands import NewsFixture


class NewsReviewRegressions(NewsFixture):
    def test_unclassified_evidence_requires_explicit_limitations(self):
        request = self.finalize_request()
        for field in ("publications", "events"):
            request["analysis"][field][0]["categories"] = []
            request["analysis"][field][0]["limitations"] = ["Категория не установлена"]
        result = self.call(**request)["result"]
        self.assertEqual(result["statistics"]["uncategorized_event_count"], 1)
        self.assertEqual(result["statistics"]["uncategorized_publication_count"], 1)
        self.assertEqual(result["events"][0]["categories"], [])
        for field in ("publications", "events"):
            with self.subTest(field=field):
                invalid = deepcopy(request)
                invalid["analysis"][field][0]["limitations"] = []
                error = self.call(expected_error="INVALID_INPUT", **invalid)
                self.assertEqual(error["code"], "INVALID_INPUT")

    def test_publication_quote_rejects_conflicting_source_attribution(self):
        request = self.finalize_request()
        request["analysis"]["publications"][0]["evidence"][0][
            "publication_id"
        ] = "unknown"
        error = self.call(expected_error="INVALID_EVIDENCE", **request)
        self.assertEqual(error["code"], "INVALID_EVIDENCE")

    def test_identical_event_with_another_id_cannot_inflate_statistics(self):
        request = self.finalize_request()
        repeated = deepcopy(request["analysis"]["events"][0])
        repeated["event_id"] = "another-event-id"
        request["analysis"]["events"].append(repeated)
        error = self.call(expected_error="DUPLICATE_EVENT", **request)
        self.assertEqual(error["code"], "DUPLICATE_EVENT")

    def test_query_can_find_publication_identifier_without_domain_expansion(self):
        self.publication["publication_id"] = "source-1042"
        second = self.publication | {"publication_id": "source-7784"}
        self.write_package(
            "news", "news", {"a.json": self.publication, "b.json": second}
        )
        self.request["brief"]["query"] = "Сопоставь публикацию 1042"
        request = self.search_request()
        request["terminology_result"]["expansions"] = []
        result = self.call(**request)
        self.assertEqual(
            [record["publication_id"] for record in result["candidates"]],
            ["source-1042"],
        )
        self.assertIn("1042", result["candidates"][0]["matches"][0]["matched_terms"])
