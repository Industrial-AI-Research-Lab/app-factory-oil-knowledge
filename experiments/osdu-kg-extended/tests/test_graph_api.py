"""API stream failures must never masquerade as successful empty results."""

import unittest

from graph_api import parse_sse, validate_target, cypher_literal


class GraphApiTests(unittest.TestCase):
    def test_multiline_sse(self):
        response = 'event: result\ndata: {\ndata: "data":[{"n":2}],\ndata: "metadata":[]\ndata: }\n\n'
        self.assertEqual(parse_sse(response), [{"n": 2}])

    def test_successful_write_has_metadata_only(self):
        response = 'event: result\ndata: {"metadata":["Query internal execution time: 1 ms"]}\n\n'
        self.assertEqual(parse_sse(response), [])

    def test_rejects_error_and_incomplete_stream(self):
        for response in ('event: error\ndata: {"message":"bad query"}\n\n',
                         'event: result\ndata: {"data":[', '<html>login</html>'):
            with self.assertRaises(ValueError):
                parse_sse(response)

    def test_source_graph_is_protected(self):
        for name in ('osdu-volve', '', 'x` DELETE n', 'graph-demo-schools'):
            with self.assertRaises(ValueError):
                validate_target(name)
        self.assertEqual(validate_target('osdu-volve-extended'), 'osdu-volve-extended')

    def test_cypher_values_are_escaped(self):
        self.assertEqual(cypher_literal("a'b\\c"), '"a\'b\\\\c"')
        self.assertEqual(cypher_literal({"a": 2}), '{a:2}')
        with self.assertRaises(ValueError):
            cypher_literal({"bad-key": 1})


if __name__ == "__main__":
    unittest.main()
