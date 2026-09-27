import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
if str(DEMO) not in sys.path:
    sys.path.insert(0, str(DEMO))

from bootstrap_osdu_demo import (
    ALLOWED_ANALYST_TOOLS,
    ANALYST_ID,
    CRITIC_ID,
    DEFAULT_MODEL,
    load_osdu_demo_bundle,
    _require_readonly_bundle,
)


class AssetTests(unittest.TestCase):
    def test_bundle_contains_readonly_analyst_and_toolless_critic(self):
        bundle = load_osdu_demo_bundle()
        _require_readonly_bundle(bundle)
        by_id = {row["_id"]: row for row in bundle["items"]["agents"]}
        self.assertEqual(set(by_id), {ANALYST_ID, CRITIC_ID})
        analyst = by_id[ANALYST_ID]
        critic = by_id[CRITIC_ID]
        self.assertEqual(set(analyst["allowed_mcp_tools"]), ALLOWED_ANALYST_TOOLS)
        self.assertEqual(critic["allowed_mcp_tools"], [])
        self.assertEqual(analyst["model"], DEFAULT_MODEL)
        self.assertEqual(critic["model"], DEFAULT_MODEL)
        self.assertNotIn("osdu-graph-ro.query_graph", analyst["allowed_mcp_tools"])
        self.assertNotIn("osdu-graph-ro.delete_graph", analyst["allowed_mcp_tools"])
        self.assertNotIn(
            "osdu-graph-ro.get_relationship_schema", analyst["allowed_mcp_tools"]
        )
        workflow = bundle["items"]["workflows"][0]
        self.assertEqual(workflow["_id"], "osdu_graph_demo")
        node_ids = {node["id"] for node in workflow["nodes"]}
        self.assertEqual(
            node_ids,
            {"start", "osdu_analyst", "osdu_critic", "osdu_critic_gate", "end"},
        )
        gate = next(node for node in workflow["nodes"] if node["id"] == "osdu_critic_gate")
        self.assertEqual(gate["type"], "validator")
        self.assertEqual(gate.get("max_reject_retries"), 5)
        self.assertEqual(gate["checks"][0]["key"], "osdu_user_answer.raw_output")
        critic_node = next(node for node in workflow["nodes"] if node["id"] == "osdu_critic")
        analyst_node = next(node for node in workflow["nodes"] if node["id"] == "osdu_analyst")
        self.assertIn("osdu_user_answer", critic_node["writes"])
        self.assertIn("user_prompt", critic_node["reads"])
        self.assertNotIn("conversation_history", critic_node["reads"])
        self.assertIn("user_prompt", analyst_node["reads"])
        edge_pairs = {
            (edge["from"], edge["to"], edge.get("condition"))
            for edge in workflow["edges"]
        }
        self.assertIn(("osdu_analyst", "osdu_critic", None), edge_pairs)
        self.assertIn(("osdu_critic", "osdu_critic_gate", None), edge_pairs)
        self.assertIn(("osdu_critic_gate", "osdu_analyst", "rejected"), edge_pairs)
        self.assertIn(("osdu_critic_gate", "end", "approved"), edge_pairs)

    def test_committed_bundle_declares_gpt_oss_for_both_agents(self):
        raw = json.loads(
            (ROOT / "config" / "osdu-demo-bundle.json").read_text(encoding="utf-8")
        )
        models = [row["model"] for row in raw["items"]["agents"]]
        self.assertEqual(models, [DEFAULT_MODEL, DEFAULT_MODEL])
        self.assertEqual(raw["items"]["agents"][0]["system_prompt"], "")
        self.assertEqual(raw["items"]["agents"][1]["system_prompt"], "")

    def test_loaded_prompts_have_no_inner_double_quotes(self):
        bundle = load_osdu_demo_bundle()
        for agent in bundle["items"]["agents"]:
            prompt = agent["system_prompt"]
            self.assertNotIn('"', prompt, agent["_id"])
            self.assertGreater(len(prompt), 200)
        analyst = next(
            row for row in bundle["items"]["agents"] if row["_id"] == ANALYST_ID
        )
        critic = next(
            row for row in bundle["items"]["agents"] if row["_id"] == CRITIC_ID
        )
        self.assertIn("query_graph_readonly", analyst["system_prompt"])
        self.assertIn("Do not call get_node_schema", analyst["system_prompt"])
        self.assertIn("Do not inventory every node", analyst["system_prompt"])
        self.assertIn("Never call get_graph_schema", analyst["system_prompt"])
        self.assertIn("grouped by label", analyst["system_prompt"])
        self.assertNotIn("MATCH (n) RETURN", analyst["system_prompt"])
        self.assertNotIn("OPTIONAL MATCH", analyst["system_prompt"])
        self.assertIn("cartesian product", critic["system_prompt"])
        self.assertIn("from user_prompt", analyst["system_prompt"])
        self.assertIn("user_prompt", critic["system_prompt"])
        self.assertIn("Do not reverse either hop", analyst["system_prompt"])
        self.assertIn("Do not call query_graph_readonly", analyst["system_prompt"])
        self.assertIn("starts with REVISE", analyst["system_prompt"])
        self.assertIn("does not answer the question", critic["system_prompt"])
        self.assertIn("asks the user to clarify", critic["system_prompt"])
        self.assertIn("user-facing answer", critic["system_prompt"])
        self.assertIn("REVISE", critic["system_prompt"])
        self.assertIn("language of the question", critic["system_prompt"])
        self.assertIn("not stored on that type", critic["system_prompt"])
        self.assertIn("Do not tell the analyst to search other nodes", critic["system_prompt"])
        self.assertIn("ignore the hunt", analyst["system_prompt"])
        self.assertIn("do not call any more tools", analyst["system_prompt"])
        self.assertIn("empty message after the schema tool", analyst["system_prompt"])
        self.assertIn("Do not inspect another label", analyst["system_prompt"])
        self.assertNotIn("do not send work back", critic["system_prompt"].lower())
        self.assertNotIn("drilling cost", analyst["system_prompt"])
        json.loads(json.dumps(bundle))

    def test_critic_gate_rejects_revise_and_dumps_and_accepts_plain_text(self):
        import jsonschema

        bundle = load_osdu_demo_bundle()
        gate = next(
            node
            for node in bundle["items"]["workflows"][0]["nodes"]
            if node["id"] == "osdu_critic_gate"
        )
        schema = gate["checks"][0]["json_schema"]
        jsonschema.validate(
            "У скважины 15/9-19 есть wellbore 15/9-19 A и 15/9-19 SR.",
            schema,
        )
        jsonschema.validate(
            "Well 11, Wellbore 27, WellLog 28. "
            "These counts came from MATCH (n:Well) RETURN count(n).",
            schema,
        )
        jsonschema.validate(
            "I could not find a drilling cost in this graph.",
            schema,
        )
        jsonschema.validate(
            "11, 27, 28",
            schema,
        )
        rejected = (
            "REVISE\nCall query_graph_readonly for Wellbore neighbors.",
            '{"graphName": "osdu-volve", "label": "Wellbore", "sampleSize": 10}',
            "Need schema.\n"
            '{"graphName": "osdu-volve", "query": "MATCH (w:Well) RETURN w", "params": {}}',
            "MATCH (w:Well {name: $wellName}) RETURN w",
            "I could not complete this without a free-form answer.",
            "Could you please clarify what information you would like to retrieve.",
        )
        for text in rejected:
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(text, schema)

    def test_cursor_config_uses_separate_readonly_endpoint(self):
        cursor = json.loads(
            (ROOT / "config" / "osdu-mcp.cursor.json").read_text(encoding="utf-8")
        )
        server = cursor["mcpServers"]["osdu-graph-ro"]
        self.assertEqual(server["url"], "http://127.0.0.1:8081/")
        self.assertEqual(server["transport"], "streamable-http")

    def test_compose_extension_enforces_strict_readonly(self):
        text = (ROOT / "compose" / "docker-compose.osdu.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('FALKORDB_DEFAULT_READONLY: "true"', text)
        self.assertIn('FALKORDB_STRICT_READONLY: "true"', text)
        self.assertNotIn("8080:8080", text)

    def test_committed_assets_do_not_contain_credentials(self):
        secret_patterns = (
            re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
            re.compile(r"\bBearer\s+[A-Za-z0-9._-]{8,}"),
            re.compile(r"mongodb(?:\+srv)?://[^/\s]+:[^@\s]+@"),
        )
        for path in ROOT.rglob("*"):
            if (
                not path.is_file()
                or ".git" in path.parts
                or path.suffix in {".pyc", ".log"}
                or "data" in path.parts
                or "build" in path.parts
            ):
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in secret_patterns:
                self.assertIsNone(pattern.search(text), str(path))


if __name__ == "__main__":
    unittest.main()
