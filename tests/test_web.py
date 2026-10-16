"""Read-only integration checks against whatever graph GRAPH_NAME selects.

The graph name is never hardcoded: set GRAPH_NAME (or leave it unset and the
`oncall_brain` default applies). Tests that need real rows skip when that
graph does not exist or is empty, so a fresh checkout passes without data.
"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GRAPH_NAME", "oncall_brain")

from web.app import app, citations_from  # noqa: E402
from oncall_brain import db  # noqa: E402

GRAPH_NAME = db.GRAPH_NAME


def _graph_has_rows() -> bool:
    """True when the configured graph exists and holds at least one entity."""
    try:
        rows = db.g().query("MATCH (n:Entity) RETURN n.id LIMIT 1").result_set
    except Exception:  # noqa: BLE001 - no FalkorDB, or graph not built
        return False
    return bool(rows)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def require_graph(self):
        if not _graph_has_rows():
            self.skipTest(f"graph {GRAPH_NAME} not built")

    def candidate(self):
        result = self.client.get("/api/search?q=Pipeline")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json["candidates"])
        return result.json["candidates"][0]["id"]

    def test_ontology(self):
        result = self.client.get("/api/ontology")
        self.assertEqual(result.status_code, 200)
        self.assertIn("HAS_RESOURCE", result.json["predicates"])

    def test_search_real_graph(self):
        self.require_graph()
        self.candidate()
        self.assertEqual(self.client.get("/api/search?q=zz-no-such-entity-zz").json["status"], "not_found")

    def test_node_real_graph(self):
        self.require_graph()
        node_id = self.candidate()
        result = self.client.get(f"/api/node/{node_id}")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["entity"]["id"], node_id)
        self.assertTrue(result.json["assertions"])
        self.assertTrue(result.json["evidence"])
        evidence = result.json["evidence"][0]
        self.assertTrue(all(k in evidence for k in ("source", "locator", "excerpt", "authority")))

    def test_neighbors_real_graph(self):
        self.require_graph()
        node_id = self.candidate()
        result = self.client.get(f"/api/neighbors/{node_id}")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json["graph"]["edges"])
        ids = {n["id"] for n in result.json["graph"]["nodes"]}
        self.assertIn(node_id, ids)
        for edge in result.json["graph"]["edges"]:
            self.assertIn(edge["source"], ids)
            self.assertIn(edge["target"], ids)
            self.assertIn(edge["authority"], ("formal_rule", "practice", "observation"))

    def test_ask_uses_its_own_tool_results_without_llm(self):
        from oncall_brain import tools
        self.require_graph()
        node_id = self.candidate()
        trace = [{"tool": "read_knowledge", "args": {"id": node_id},
                  "template": ["read_entity"], "status": "ok"}]
        fake = {"answer": "Evidence-backed answer", "trace": trace,
                "tool_results": [tools.read_knowledge(node_id)]}
        for mode, target in (("fast", "web.app.fast.ask_fast"), ("deep", "web.app.agent.ask")):
            with patch(target, return_value=dict(fake)):
                result = self.client.post("/api/ask", json={"question": "Pipeline?", "mode": mode})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json["mode"], mode)
            self.assertEqual(result.json["trace"], trace)
            self.assertTrue(result.json["evidence"])
            self.assertTrue(result.json["graph"]["edges"])
            self.assertNotIn("tool_results", result.json)
            self.assertEqual(result.json["evidence_origin"], "tool_results")
            self.assertIn(result.json["status"], ("applicable", "conflict", "superseded", "insufficient_context"))

    def test_invalid_and_missing_requests(self):
        for body in ({}, {"question": " "}, {"question": 12}, {"question": "x" * 4001}, []):
            self.assertEqual(self.client.post("/api/ask", json=body).status_code, 400)
        self.assertEqual(self.client.get("/api/search").status_code, 400)
        self.assertEqual(self.client.get("/api/node/zz-no-such-node-zz").status_code, 404)
        self.assertEqual(self.client.get("/api/neighbors/zz-no-such-node-zz").status_code, 404)

    def test_dependency_failure_is_sanitized(self):
        with patch("web.app.tools.search_knowledge", side_effect=RuntimeError("private configuration")):
            result = self.client.get("/api/search?q=Pipeline")
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("private configuration", result.get_data(as_text=True))

    def test_citations_with_arrows_inside_assertion_ids(self):
        items = [{"id": "e1", "source": "corpus/chats/example.md", "locator": "23"}]
        answer = "[llm:HAS_RESOURCE:MOD-01->Knowledge Hub:corpus/chats/example.md:23]"
        citations = citations_from(answer, items)
        self.assertEqual(citations[0]["aid"], "llm:HAS_RESOURCE:MOD-01->Knowledge Hub")
        self.assertEqual(citations[0]["evidence_id"], "e1")
        for arrow in ("->", "→"):
            citation = citations_from(f"[aid {arrow} corpus/chats/example.md:23]", items)[0]
            self.assertEqual(citation["evidence_id"], "e1")
        missing = citations_from("[aid -> corpus/docs/missing.md:9]", items)[0]
        self.assertIsNone(missing["evidence_id"])

    def test_page(self):
        result = self.client.get("/")
        self.assertEqual(result.status_code, 200)
        self.assertIn(b"cytoscape@3.30.4", result.data)
        result.close()


if __name__ == "__main__":
    unittest.main()
