import json
import sys
import unittest
from pathlib import Path


DEMO = Path(__file__).resolve().parents[1] / "demo"
if str(DEMO) not in sys.path:
    sys.path.insert(0, str(DEMO))

from osdu_critic_rules import ACCEPT, REVISE, critic_decision, is_user_publishable


class CriticContractTests(unittest.TestCase):
    def test_revises_query_json_dump(self):
        self.assertEqual(
            critic_decision(
                {
                    "graphName": "osdu-volve",
                    "query": "MATCH (wb:Wellbore) RETURN wb",
                    "params": {},
                }
            ),
            REVISE,
        )

    def test_revises_schema_arg_dump(self):
        self.assertEqual(
            critic_decision(
                {
                    "graphName": "osdu-volve",
                    "label": "Well",
                    "sampleSize": 100,
                }
            ),
            REVISE,
        )

    def test_revises_mixed_text_and_json(self):
        text = (
            "Need to inspect Well schema.\n"
            '{"graphName": "osdu-volve", "label": "Well", "sampleSize": 100}'
        )
        self.assertEqual(critic_decision(text), REVISE)

    def test_revises_match_plan(self):
        self.assertEqual(
            critic_decision("MATCH (wb:Wellbore) WHERE wb.name = $name RETURN wb"),
            REVISE,
        )

    def test_accepts_counts_that_quote_match_in_prose(self):
        text = (
            "В графе osdu-volve: Well 11, Wellbore 27, WellLog 28. "
            "These came from MATCH (n:Well) RETURN count(n)."
        )
        self.assertEqual(critic_decision(text), ACCEPT)
        self.assertTrue(is_user_publishable(text))

    def test_accepts_plain_language_absence(self):
        self.assertEqual(
            critic_decision(
                "У типа Wellbore в графе нет свойства стоимости. "
                "В схеме есть name, osduId, facilityID, sequenceNumber, kind."
            ),
            ACCEPT,
        )

    def test_accepts_plain_language_neighbors(self):
        self.assertEqual(
            critic_decision("Wellbores: 15/9-19 A, 15/9-19 B, 15/9-19 S."),
            ACCEPT,
        )

    def test_dump_is_revised_even_if_journal_has_schema(self):
        journal = json.dumps(
            {"properties": [{"property": "name"}, {"property": "osduId"}]}
        )
        self.assertEqual(
            critic_decision(
                {"graphName": "osdu-volve", "query": "MATCH (n) RETURN n"},
                journal=journal,
            ),
            REVISE,
        )

    def test_revises_english_protocol(self):
        self.assertEqual(
            critic_decision("I could not complete this without a free-form answer."),
            REVISE,
        )

    def test_revises_clarification_request(self):
        self.assertEqual(
            critic_decision(
                "Could you please clarify what information you'd like to retrieve "
                "from the osdu-volve graph?"
            ),
            REVISE,
        )
        self.assertFalse(
            is_user_publishable(
                "Could you please clarify what information you'd like to retrieve "
                "from the osdu-volve graph?"
            )
        )

    def test_revise_marker_is_not_publishable(self):
        text = (
            "REVISE\n"
            "Call query_graph_readonly for Wellbore neighbors. Do not print tool JSON."
        )
        self.assertEqual(critic_decision(text), REVISE)
        self.assertFalse(is_user_publishable(text))

    def test_accepted_plain_language_is_publishable(self):
        text = "Wellbores: 15/9-19 A, 15/9-19 B, 15/9-19 S."
        self.assertEqual(critic_decision(text), ACCEPT)
        self.assertTrue(is_user_publishable(text))

    def test_revises_identical_label_counts(self):
        text = (
            "В графе osdu-volve:\n"
            "- Well — 8 316 узлов\n"
            "- Wellbore — 8 316 узлов\n"
            "- WellLog — 8 316 узлов"
        )
        self.assertEqual(critic_decision(text), REVISE)
        self.assertFalse(is_user_publishable(text))

    def test_accepts_distinct_label_counts(self):
        text = "Well 11, Wellbore 27, WellLog 28."
        self.assertEqual(critic_decision(text), ACCEPT)
        self.assertTrue(is_user_publishable(text))

    def test_dump_is_not_publishable(self):
        self.assertFalse(
            is_user_publishable(
                '{"graphName": "osdu-volve", "query": "MATCH (n) RETURN n"}'
            )
        )


if __name__ == "__main__":
    unittest.main()
