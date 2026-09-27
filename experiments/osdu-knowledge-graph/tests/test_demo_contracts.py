import sys
import unittest
from pathlib import Path


DEMO = Path(__file__).resolve().parents[1] / "demo"
if str(DEMO) not in sys.path:
    sys.path.insert(0, str(DEMO))

from run_osdu_demo import (
    CONTROLS,
    SCENARIOS,
    _parse_output,
    _require_scenario_output,
)


def _gold(scenario_id: str):
    for sid, _question, expected in SCENARIOS:
        if sid == scenario_id:
            return expected
    raise AssertionError(f"unknown scenario {scenario_id}")


class DemoContractTests(unittest.TestCase):
    def test_e2e_scenarios_cover_q1_to_q10_and_free(self):
        self.assertEqual(
            [row[0] for row in SCENARIOS],
            [control.id for control in CONTROLS] + ["FREE"],
        )
        self.assertEqual(
            [row[1] for row in SCENARIOS if row[0] != "FREE"],
            [control.question for control in CONTROLS],
        )

    def test_control_output_accepts_name_set_in_flexible_shapes(self):
        expected = {"wellbores": ["A", "B"]}
        _require_scenario_output("Q1", {"wellbores": ["B", "A"]}, expected)
        _require_scenario_output(
            "Q1",
            [{"name": "A"}, {"name": "B", "osduId": "x"}],
            expected,
        )
        _require_scenario_output("Q1", "Wellbores: A and B", expected)
        with self.assertRaisesRegex(Exception, "output mismatch"):
            _require_scenario_output(
                "Q1", {"wellbores": ["A", "unexpected"]}, expected
            )

    def test_rejects_tool_arg_dump_as_answer(self):
        with self.assertRaisesRegex(Exception, "tool arguments/Cypher"):
            _require_scenario_output(
                "Q7",
                {
                    "graphName": "osdu-volve",
                    "query": 'MATCH (log:WellLog {name: "NO_15_9-F-4_KLOGH_NEW.las"}) '
                    "RETURN log",
                },
                _gold("Q7"),
            )

    def test_q1_accepts_unicode_hyphens_and_spaces(self):
        _require_scenario_output(
            "Q1",
            (
                "Скважина 15/9\u201119 имеет wellbore-ы:\n"
                "- 15/9\u201119\u202fS\n"
                "- 15/9\u201119\u202fSR\n"
                "- 15/9\u201119\u202fA\n"
                "- 15/9\u201119\u202fSR2\n"
                "- 15/9\u201119\u202fB\n"
            ),
            _gold("Q1"),
        )

    def test_q1_rejects_sr_hidden_inside_sr2(self):
        with self.assertRaisesRegex(Exception, "output mismatch"):
            _require_scenario_output(
                "Q1",
                "Wellbores: 15/9-19 A, 15/9-19 B, 15/9-19 S, 15/9-19 SR2",
                _gold("Q1"),
            )

    def test_q1_accepts_facts_even_if_json_dump_is_appended(self):
        dump = {
            "graphName": "osdu-volve",
            "query": "MATCH (wb:Wellbore) RETURN wb.name",
        }
        _require_scenario_output(
            "Q1",
            dump,
            _gold("Q1"),
            original=(
                "Wellbores: 15/9-19 A, 15/9-19 B, 15/9-19 S, "
                "15/9-19 SR, 15/9-19 SR2\n"
                '{"graphName": "osdu-volve", '
                '"query": "MATCH (wb:Wellbore) RETURN wb.name"}'
            ),
        )

    def test_q2_accepts_both_log_names_case_insensitively(self):
        _require_scenario_output(
            "Q2",
            (
                "Well logs: 15_9-19_SR_CPI.las and "
                "STAT1990__30-1__15-9-19_SR__COMPOSITE__1.las"
            ),
            _gold("Q2"),
        )

    def test_q2_rejects_missing_composite_log(self):
        with self.assertRaisesRegex(Exception, "output mismatch"):
            _require_scenario_output(
                "Q2",
                "Well logs: 15_9-19_SR_CPI.las",
                _gold("Q2"),
            )

    def test_q3_accepts_well_and_wellbore(self):
        _require_scenario_output(
            "Q3",
            "Лог относится к скважине 15/9-19 S и стволу 15/9-19 SR.",
            _gold("Q3"),
        )

    def test_q3_rejects_s_hidden_inside_sr(self):
        with self.assertRaisesRegex(Exception, "output mismatch"):
            _require_scenario_output(
                "Q3",
                "Лог относится к стволу 15/9-19 SR.",
                _gold("Q3"),
            )

    def test_q4_accepts_wellbore_osdu_id(self):
        _require_scenario_output(
            "Q4",
            "OSDU ID: osdu:master-data--Wellbore:NPD-2105",
            _gold("Q4"),
        )

    def test_q4_rejects_wrong_npd(self):
        with self.assertRaisesRegex(Exception, "output mismatch"):
            _require_scenario_output(
                "Q4",
                "OSDU ID: osdu:master-data--Wellbore:NPD-3145",
                _gold("Q4"),
            )

    def test_q5_accepts_well_osdu_id(self):
        _require_scenario_output(
            "Q5",
            "osdu:master-data--Well:15/9-19",
            _gold("Q5"),
        )

    def test_q5_rejects_wellbore_id_for_well(self):
        with self.assertRaisesRegex(Exception, "output mismatch"):
            _require_scenario_output(
                "Q5",
                "osdu:master-data--Wellbore:NPD-2105",
                _gold("Q5"),
            )

    def test_q6_accepts_all_wellbores(self):
        _require_scenario_output(
            "Q6",
            "Wellbores: 15/9-F-1, 15/9-F-1 A, 15/9-F-1 B, 15/9-F-1 C",
            _gold("Q6"),
        )

    def test_q6_rejects_base_name_hidden_inside_suffixed_name(self):
        with self.assertRaisesRegex(Exception, "output mismatch"):
            _require_scenario_output(
                "Q6",
                "Wellbores: 15/9-F-1 A, 15/9-F-1 B, 15/9-F-1 C",
                _gold("Q6"),
            )

    def test_q7_accepts_same_well_and_wellbore_name(self):
        _require_scenario_output("Q7", _gold("Q7"), _gold("Q7"))
        _require_scenario_output(
            "Q7",
            "Well and wellbore are both 15/9-F-4.",
            _gold("Q7"),
        )

    def test_q8_accepts_wellbore_a_osdu_id(self):
        _require_scenario_output(
            "Q8",
            "osdu:master-data--Wellbore:NPD-3145",
            _gold("Q8"),
        )

    def test_q9_accepts_parenthesized_suffix_letters(self):
        _require_scenario_output(
            "Q9",
            (
                "Скважина 15/9-F-15 имеет пять wellbore-ов:\n"
                "- 15/9-F-15 (A)\n"
                "- 15/9-F-15 (B)\n"
                "- 15/9-F-15 (C)\n"
                "- 15/9-F-15 (D)\n"
                "- 15/9-F-15 (основной ствол)\n"
            ),
            _gold("Q9"),
        )

    def test_q10_accepts_bound_label_counts(self):
        expected = _gold("Q10")
        _require_scenario_output("Q10", "Well 11, Wellbore 27, WellLog 28", expected)
        _require_scenario_output("Q10", expected, expected)
        _require_scenario_output(
            "Q10",
            "В графе osdu-volve содержится 11 узлов `Well`, 27 узлов `Wellbore` и 28 узлов `WellLog`.",
            expected,
        )
        _require_scenario_output(
            "Q10",
            (
                "В графе osdu-volve присутствуют:\n"
                "- 11 узлов типа **Well**\n"
                "- 27 узлов типа **Wellbore**\n"
                "- 28 узлов типа **WellLog**.\n"
            ),
            expected,
        )

    def test_q10_rejects_well_hidden_inside_wellbore_and_swapped_counts(self):
        expected = _gold("Q10")
        with self.assertRaisesRegex(Exception, "output mismatch"):
            _require_scenario_output("Q10", "Wellbore 27, WellLog 28", expected)
        with self.assertRaisesRegex(Exception, "output mismatch"):
            _require_scenario_output(
                "Q10",
                "Wellbore 11, Well 27, WellLog 28",
                expected,
            )
        with self.assertRaisesRegex(Exception, "output mismatch"):
            _require_scenario_output(
                "Q10",
                (
                    "В графе osdu-volve присутствуют:\n"
                    "- **Well** — 8 316 узлов\n"
                    "- **Wellbore** — 8 316 узлов\n"
                    "- **WellLog** — 8 316 узлов.\n"
                ),
                expected,
            )
        _require_scenario_output(
            "Q10",
            {
                "graphName": "osdu-volve",
                "query": "MATCH (w:Well) RETURN count(w)",
            },
            expected,
            original=(
                "Well 11, Wellbore 27, WellLog 28\n"
                '{"graphName": "osdu-volve", '
                '"query": "MATCH (w:Well) RETURN count(w)"}'
            ),
        )

    def test_free_question_accepts_natural_language_missing_data(self):
        _require_scenario_output(
            "FREE",
            {
                "answer": None,
                "insufficientData": True,
                "missing": "drilling cost",
            },
            None,
        )
        _require_scenario_output(
            "FREE",
            "В графе нет свойства стоимости бурения для этого wellbore.",
            None,
        )
        _require_scenario_output(
            "FREE",
            "В графе нет стоимости бурения для этого wellbore.",
            None,
        )
        _require_scenario_output(
            "FREE",
            "Стоимость бурения не хранится в типе Wellbore",
            None,
        )
        _require_scenario_output(
            "FREE",
            (
                "Тип **Wellbore** в графе osdu-volve не содержит свойства, "
                "которое бы хранило информацию о стоимости бурения. "
                "Поэтому из имеющихся данных невозможно определить стоимость "
                "бурения для скважины 15/9-19 SR (NPD-2105)."
            ),
            None,
        )
        with self.assertRaisesRegex(Exception, "insufficient data"):
            _require_scenario_output(
                "FREE",
                {"answer": "1000 USD", "insufficientData": False},
                None,
            )
        with self.assertRaisesRegex(Exception, "insufficient data"):
            _require_scenario_output("FREE", "Стоимость бурения 1000 USD", None)
        with self.assertRaisesRegex(Exception, "insufficient data"):
            _require_scenario_output(
                "FREE",
                "Нет, стоимость бурения 1000 USD",
                None,
            )
        _require_scenario_output(
            "FREE",
            (
                "В графе нет свойства стоимости бурения.\n"
                '{"graphName": "osdu-volve", '
                '"query": "MATCH (w:Wellbore) RETURN w LIMIT 1"}'
            ),
            None,
        )
        _require_scenario_output(
            "FREE",
            "No cost property is stored for this wellbore.",
            None,
        )
        _require_scenario_output(
            "FREE",
            "I could not find a drilling cost in this graph.",
            None,
        )
        _require_scenario_output(
            "FREE",
            "The graph does not store drilling cost.",
            None,
        )
        with self.assertRaisesRegex(Exception, "insufficient data"):
            _require_scenario_output(
                "FREE",
                "The drilling cost is 1000 USD",
                None,
            )
        with self.assertRaisesRegex(Exception, "tool arguments/Cypher"):
            _require_scenario_output(
                "FREE",
                {
                    "graphName": "osdu-volve",
                    "query": "MATCH (w:Wellbore) RETURN w LIMIT 1",
                },
                None,
            )

    def test_markdown_json_output_is_parsed(self):
        self.assertEqual(
            _parse_output('```json\n{"osduId":"value"}\n```'),
            {"osduId": "value"},
        )

    def test_json_object_is_extracted_from_trailing_text(self):
        self.assertEqual(
            _parse_output('Reasoning...\n{"wellbores":["15/9-19 A"]}'),
            {"wellbores": ["15/9-19 A"]},
        )

    def test_plain_text_output_is_kept(self):
        self.assertEqual(
            _parse_output("Wellbores: 15/9-19 A, 15/9-19 B"),
            "Wellbores: 15/9-19 A, 15/9-19 B",
        )


if __name__ == "__main__":
    unittest.main()
