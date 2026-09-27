import copy
import json
import unittest
from pathlib import Path

from ontology_checks import validate_ontology

INPUTS = Path(__file__).resolve().parents[1] / "inputs"


class OntologyTest(unittest.TestCase):
    def setUp(self):
        root = INPUTS / "terminology"
        self.terms = json.loads((root / "terms.json").read_text(encoding="utf-8"))
        self.taxonomy = json.loads(
            (root / "news-taxonomy.json").read_text(encoding="utf-8")
        )

    def validate(self):
        return validate_ontology(self.terms, self.taxonomy, INPUTS)

    def test_delivered_ontology_and_quotes(self):
        terms, categories = self.validate()
        self.assertEqual(len(terms), len(self.terms["terms"]))
        self.assertEqual(len(categories), len(self.taxonomy["categories"]))

    def test_duplicate_concept_rejected(self):
        self.terms["terms"].append(copy.deepcopy(self.terms["terms"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate ID"):
            self.validate()

    def test_unresolved_relation_rejected(self):
        self.terms["terms"][0]["relations"].append(
            {"target_id": "missing-concept", "type": "uses", "reason": "fixture"}
        )
        with self.assertRaisesRegex(ValueError, "relation target"):
            self.validate()

    def test_invented_evidence_rejected(self):
        self.terms["terms"][0]["evidence"][0]["quote"] = "INVENTED-B03-EVIDENCE-9bca"
        with self.assertRaisesRegex(ValueError, "quote does not match"):
            self.validate()

    def test_null_evidence_and_keyword_rejected_cleanly(self):
        for field in ("evidence", "keywords"):
            with self.subTest(field=field):
                self.setUp()
                self.terms["terms"][0][field] = [None]
                with self.assertRaisesRegex(ValueError, "object required"):
                    self.validate()

    def test_unobserved_keyword_rejected(self):
        term = self.terms["terms"][0]
        term["keywords"] = [
            term["evidence"][0]
            | {
                "text": "INVENTED-OBSERVED-KEYWORD-9bca",
                "kind": "source_phrase",
                "context": "Incorrectly claiming a search proposal was observed",
            }
        ]
        with self.assertRaisesRegex(ValueError, "keyword is absent"):
            self.validate()

    def test_abbreviation_inside_unrelated_word_is_not_evidence(self):
        term = next(t for t in self.terms["terms"] if t["id"] == "capillary-number")
        term["keywords"] = [
            {
                "source_id": "h359",
                "quote": "influenced",
                "text": "Nc",
                "kind": "abbreviation",
                "context": "Substring nc inside influenced is not the symbol Nc",
            }
        ]
        with self.assertRaisesRegex(ValueError, "keyword is absent"):
            self.validate()

    def test_parent_cycle_rejected(self):
        first, second = self.terms["terms"][:2]
        first["parent_id"], second["parent_id"] = second["id"], first["id"]
        with self.assertRaisesRegex(ValueError, "parent cycle"):
            self.validate()

    def test_stale_case2_category_rejected(self):
        self.taxonomy["case2_category_ids"] = ["missing-category"]
        with self.assertRaisesRegex(ValueError, "Unknown case2"):
            self.validate()

    def test_changed_concept_requires_old_migration_id(self):
        self.terms["migration"][0].update(status="clarified", old_id=None)
        with self.assertRaisesRegex(ValueError, "Migration old ID"):
            self.validate()

    def test_source_must_stay_inside_inputs(self):
        self.terms["sources"][0]["path"] = "../../outside.txt"
        with self.assertRaisesRegex(ValueError, "Source escapes"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
