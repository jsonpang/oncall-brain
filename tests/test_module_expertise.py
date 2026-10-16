"""Offline regression tests for module expertise and who-question routing."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from oncall_brain import tools
from oncall_brain.fast import WHO


def ticket_row(ticket, person):
    return [ticket, "Ticket", ticket, "Module", "Queue", person, "Person", person,
            "about-" + ticket, "formal_rule", [],
            "handled-" + ticket + person, "formal_rule", []]


class ModuleExpertiseTests(unittest.TestCase):
    def setUp(self):
        # Lower-count handler first; duplicate and shared tickets exercise both
        # unique ticket counts and the single ABOUT path per ticket.
        rows = [ticket_row("TCK-1", "Colin"),
                ticket_row("TCK-1", "Frank"),
                ticket_row("TCK-1", "Frank"),
                ticket_row("TCK-2", "Frank")]
        owners = [("TECH_LEAD_OF", "Colin", "Colin", "lead-first"),
                  ("TECH_LEAD_OF", "Colin", "Colin", "lead-duplicate"),
                  ("MAINTAINED_BY", "Colin", "Colin", "maintainer"),
                  ("TECH_LEAD_OF", "Robin", "Robin", "other-lead")]
        graph = Mock()
        graph.query.side_effect = [SimpleNamespace(result_set=rows),
                                   SimpleNamespace(result_set=owners),
                                   SimpleNamespace(result_set=[])]
        with patch.object(tools, "g", return_value=graph):
            self.result = tools.module_expertise("MOD-01")
        self.assertGreaterEqual(graph.query.call_count, 2)

    def test_handlers_ranked_by_distinct_ticket_count(self):
        self.assertEqual([(h["id"], h["count"]) for h in self.result["handlers"]],
                         [("Frank", 2), ("Colin", 1)])
        self.assertEqual(self.result["handlers"][0]["tickets"], ["TCK-1", "TCK-2"])
        self.assertFalse(self.result["handlers"][0]["is_owner"])
        self.assertTrue(self.result["handlers"][1]["is_owner"])

    def test_about_path_appears_once_per_ticket(self):
        about = [p for p in self.result["paths"] if p["edges"] == ["ABOUT"]]
        self.assertCountEqual([p["nodes"][0]["id"] for p in about], ["TCK-1", "TCK-2"])
        self.assertEqual(self.result["tickets"], 2)

    def test_owners_deduplicated_by_predicate_and_id(self):
        self.assertCountEqual([(o["predicate"], o["id"]) for o in self.result["owners"]],
                              [("TECH_LEAD_OF", "Colin"), ("MAINTAINED_BY", "Colin"),
                               ("TECH_LEAD_OF", "Robin")])


class WhoRoutingTests(unittest.TestCase):
    def test_who_questions_match(self):
        for question in ("我该找谁", "Who should I ask"):
            with self.subTest(question=question):
                self.assertIsNotNone(WHO.search(question))

    def test_deadline_question_does_not_match(self):
        self.assertIsNone(WHO.search("What is the deadline"))


if __name__ == "__main__":
    unittest.main()
