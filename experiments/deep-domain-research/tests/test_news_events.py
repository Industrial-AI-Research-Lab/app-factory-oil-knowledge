from copy import deepcopy

from test_news_commands import NewsFixture


class NewsEventTests(NewsFixture):
    def two_publications(self, identical=True):
        second = self.publication | {
            "publication_id": "paper-b",
            "url": "https://example.test/b",
            "text": self.publication["text"]
            if identical
            else "Интеллектуальное управление насосом добычи.",
        }
        self.write_package(
            "news", "news", {"a.json": self.publication, "b.json": second}
        )
        search = self.call(**self.search_request())
        analysis = self.analysis()
        analysis["publications"].append(
            analysis["publications"][0]
            | {
                "publication_id": "paper-b",
                "evidence": [{"quote": second["text"]}],
            }
        )
        return second, search, analysis

    def test_reprints_keep_two_sources_and_count_one_event(self):
        second, search, analysis = self.two_publications()
        event = analysis["events"][0]
        event["publication_ids"].append("paper-b")
        event["evidence"].append({"publication_id": "paper-b", "quote": second["text"]})
        event["merge_reason"] = "Точное совпадение исходного текста"
        result = self.call(
            operation="finalize",
            candidates_ref=search["reference"],
            initial_analysis=analysis,
            analysis=analysis,
            critique={"correction_cycle": 0, "notes": []},
        )["result"]
        self.assertEqual(result["statistics"]["publication_count"], 2)
        self.assertEqual(result["statistics"]["event_count"], 1)
        self.assertEqual(result["statistics"]["source_count"], len(result["sources"]))
        self.assertEqual(len(result["exact_duplicate_groups"]), 1)

    def test_similar_titles_do_not_merge_distinct_technologies(self):
        second, search, analysis = self.two_publications(identical=False)
        event = deepcopy(analysis["events"][0])
        event.update(
            {
                "event_id": "event-b",
                "technology": "Управление насосом",
                "publication_ids": ["paper-b"],
                "evidence": [{"publication_id": "paper-b", "quote": second["text"]}],
            }
        )
        analysis["events"].append(event)
        result = self.call(
            operation="finalize",
            candidates_ref=search["reference"],
            initial_analysis=analysis,
            analysis=analysis,
            critique={"correction_cycle": 0, "notes": []},
        )["result"]
        self.assertEqual(result["statistics"]["event_count"], 2)
        self.assertEqual(result["exact_duplicate_groups"], [])

    def test_one_publication_can_support_two_distinct_events(self):
        self.publication["text"] += " Завтра начнут испытания новой технологии."
        self.write_package("news", "news", {"paper.json": self.publication})
        request = self.finalize_request()
        event = deepcopy(request["analysis"]["events"][0])
        event.update(
            {"event_id": "event-b", "description": "Анонс испытаний", "time": "Завтра"}
        )
        request["analysis"]["events"].append(event)
        result = self.call(**request)["result"]
        self.assertEqual(result["statistics"]["event_count"], 2)
        self.assertEqual(result["publications"][0]["event_ids"], ["event-a", "event-b"])

    def test_duplicate_articles_cannot_double_count_identical_event(self):
        second, search, analysis = self.two_publications()
        event = deepcopy(analysis["events"][0])
        event.update(
            {
                "event_id": "event-b",
                "publication_ids": ["paper-b"],
                "evidence": [{"publication_id": "paper-b", "quote": second["text"]}],
            }
        )
        analysis["events"].append(event)
        error = self.call(
            expected_error="DUPLICATE_EVENT",
            operation="finalize",
            candidates_ref=search["reference"],
            initial_analysis=analysis,
            analysis=analysis,
            critique={"correction_cycle": 0, "notes": []},
        )
        self.assertEqual(error["code"], "DUPLICATE_EVENT")
