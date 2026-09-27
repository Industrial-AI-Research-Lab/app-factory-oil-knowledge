"""Retry and resume behavior for the extended graph loader."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graph_api import GraphHttpError
from load import _query, load_package


class SequenceClient:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = 0

    def query(self, graph, cypher, timeout):
        self.calls += 1
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class ResumeClient:
    def __init__(self):
        self.queries = []

    def query(self, graph, cypher, timeout):
        self.queries.append(cypher)
        if cypher == "MATCH (n) RETURN count(n) AS count":
            return [{"count": 1}]
        if "RETURN m.managedBy" in cypher:
            return [
                {
                    "managedBy": "extended-graph-data",
                    "schemaVersion": 1,
                    "status": "loading",
                    "packageSha256": None,
                    "expectedNodes": None,
                    "expectedEdges": None,
                }
            ]
        if cypher.startswith("CALL db.indexes()"):
            return [{"label": "Record", "properties": ["uid"]}]
        return []


class LoadTests(unittest.TestCase):
    def test_mixed_retry_failures_raise_the_last_exception(self):
        outcomes = [GraphHttpError(429, "busy", "0")] * 3
        outcomes.extend(ConnectionResetError("connection lost") for _ in range(5))
        client = SequenceClient(outcomes)

        with (
            patch("load.time.sleep", return_value=None),
            self.assertRaisesRegex(ConnectionResetError, "connection lost"),
        ):
            _query(client, "graph", "RETURN 1")

        self.assertEqual(client.calls, 6)

    def test_resume_does_not_recreate_existing_record_uid_index(self):
        package = {"targetGraph": "osdu-volve-extended", "nodes": [], "edges": []}
        client = ResumeClient()
        with tempfile.TemporaryDirectory() as directory:
            package_path = Path(directory) / "package.json"
            package_path.write_text(json.dumps(package), encoding="utf-8")
            with patch("load.time.sleep", return_value=None):
                load_package(client, package_path)

        self.assertTrue(
            any(query.startswith("CALL db.indexes()") for query in client.queries)
        )
        self.assertFalse(
            any(query.startswith("CREATE INDEX") for query in client.queries)
        )


if __name__ == "__main__":
    unittest.main()
