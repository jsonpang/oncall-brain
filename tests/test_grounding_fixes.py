"""Offline tests for partial grounding, cited conflicts and quoted line prefixes."""
import unittest

from oncall_brain import agent
from oncall_brain.ingest import llm

LOC = "corpus/docs/synthetic.md:7"
SUPPORTED = {("a1", LOC), ("a2", "corpus/chats/c.md:3")}


class GroundingTests(unittest.TestCase):
    def test_one_bad_citation_drops_only_its_line(self):
        answer = ("## Next step\nAsk Cedar [a1 → " + LOC + "].\n"
                  "Ask Birch [a9 → corpus/docs/other.md:1].\n"
                  "Uncited advice.\n## Gaps")
        text, facts = agent._grounded_answer(answer, SUPPORTED)
        self.assertEqual(facts, 1)
        self.assertIn("Cedar", text)
        self.assertNotIn("Birch", text)
        self.assertNotIn("Uncited", text)
        self.assertNotIn("## Gaps", text)

    def test_nothing_supported_means_no_facts(self):
        _, facts = agent._grounded_answer("Ask Birch [a9 → corpus/x.md:1].", SUPPORTED)
        self.assertEqual(facts, 0)

    def test_conflict_needs_two_distinct_cited_lines(self):
        one = "## Conflicts / Superseded\nX [a1 → " + LOC + "] [a1 → " + LOC + "]"
        two = one + "\nY [a2 → corpus/chats/c.md:3]"
        self.assertFalse(agent._cited_conflict(one))
        self.assertTrue(agent._cited_conflict(two))

    def test_supersession_requires_question_overlap(self):
        results = [{"entity": {"name": "Default Step Timeout 120 Minutes"},
                    "superseded": [{"superseded_by": "Rule:T60", "aid": "a1", "effective": "2026-08-20"}]}]
        self.assertTrue(agent._supersession_lines("What is the default step timeout?", results, SUPPORTED))
        self.assertFalse(agent._supersession_lines("Who owns Orchid?", results, SUPPORTED))


class QuotePrefixTests(unittest.TestCase):
    def test_slice_line_number_prefix_is_ignored(self):
        edge = {"source": "corpus/chats/chat_2026-08-20.md", "line": 28,
                "evidence_text": "[28] [09:45] Colin Marsh: Starting today 2026-08-20, the default step timeout"}
        self.assertTrue(llm.quote_matches(edge))


if __name__ == "__main__":
    unittest.main()
