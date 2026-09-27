"""Preflight validation protects the graph from ambiguous source joins."""

import unittest

from prepare import add_node, validate_package


class PrepareTests(unittest.TestCase):
    def test_conflicting_ids_are_rejected(self):
        nodes = {}
        add_node(nodes, "Well", {"uid": "one", "name": "A"})
        with self.assertRaises(ValueError):
            add_node(nodes, "Well", {"uid": "one", "name": "B"})

    def test_missing_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_package({"nodes": [{"label": "Well", "properties": {"uid": "one"}}],
                              "edges": [{"source": "one", "target": "missing", "type": "BELONGS_TO_WELL"}]})

    def test_duplicate_edge_is_rejected(self):
        edge = {"source": "one", "target": "one", "type": "FROM_DATASET"}
        with self.assertRaises(ValueError):
            validate_package({"nodes": [{"label": "Dataset", "properties": {"uid": "one"}}],
                              "edges": [edge, edge]})

    def test_bad_latitude_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_package({"nodes": [{"label": "Wellbore", "properties": {
                "uid": "one", "latitude": 190, "longitude": 1}}], "edges": []})


if __name__ == "__main__":
    unittest.main()
