"""Offline regression: a scale quoted per violation is not a slot conflict.

kb_health() used to report one conflict on
Procedure:Operational_Quality_Compliance_Audits / penalty_points with values
-2 / -2 / -10 / -5 pts. Those are four different violations priced on one
scale (corpus/chats/chat_2026-09-24.md:27 lists three of them in one line),
not four competing answers to one question.
"""
import unittest

from oncall_brain import tools


class PenaltyScaleIsNotAConflictTests(unittest.TestCase):
    """Condition keys that qualify *what* is measured subdivide the slot."""

    def claim(self, value, condition, **changes):
        claim = {"subject": "Procedure:Operational_Quality_Compliance_Audits",
                 "predicate": "penalty_points", "value": value, "condition": condition,
                 "authority": "practice", "valid_from": "2026-01-01", "valid_to": None,
                 "aid": "a-" + value, "evidence": ["ev:1"]}
        claim.update(changes)
        return claim

    def test_different_violations_on_one_scale_do_not_conflict(self):
        """The reported false positive: one line, three prices, zero conflicts."""
        claims = [
            self.claim("-10 pts", {"violation": "closing without user verification"}),
            self.claim("-5 pts", {"violation": "missing logs or SLA response breach > 50%"}),
            self.claim("-2 pts", {"violation": "typos or missing territory"}),
        ]
        self.assertEqual(tools.compare_claims(claims), [])

    def test_violation_and_violation_type_keys_are_both_qualifiers(self):
        """The graph spells the qualifier two ways; both must split the slot."""
        self.assertEqual(tools.compare_claims([
            self.claim("-2 pts", {"violation_type": "failure to populate regional territory metadata"}),
            self.claim("-10 pts", {"violation": "premature closure without user verification"}),
        ]), [])

    def test_same_violation_different_scores_still_conflicts(self):
        """The guard must not swallow the real thing: one violation, two prices."""
        conflicts = tools.compare_claims([
            self.claim("-2 pts", {"violation": "typos or missing territory"}),
            self.claim("-5 pts", {"violation": "typos or missing territory"},
                       authority="observation", aid="chat"),
        ])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["predicate"], "penalty_points")
        self.assertEqual([v["value"] for v in conflicts[0]["values"]], ["-2 pts", "-5 pts"])

    def test_a_qualifier_does_not_split_a_slot_that_has_no_qualifier(self):
        """One unconditional value vs one qualified value: still compared.

        A general "penalty is -5" claim and a "penalty is -2 for typos" claim do
        disagree about the unqualified case, so splitting on the qualifier alone
        would hide it -- that is why the default slot stays in the comparison.
        """
        conflicts = tools.compare_claims([
            self.claim("-5 pts", {}, authority="formal_rule"),
            self.claim("-2 pts", {"violation": "typos"}),
        ])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual([v["value"] for v in conflicts[0]["values"]], ["-5 pts", "-2 pts"])

    def test_non_qualifying_condition_keys_keep_the_old_behaviour(self):
        """Only keys that name a subject of measurement split the slot."""
        conflicts = tools.compare_claims([
            self.claim("-2 pts", {"region": "north"}),
            self.claim("-10 pts", {"region": "north"}),
        ])
        self.assertEqual(len(conflicts), 1)

    def test_the_real_graph_reports_no_penalty_conflict(self):
        """End-to-end against FalkorDB, skipped offline."""
        try:
            conflicts = tools._slot_conflicts(
                ["Procedure:Operational_Quality_Compliance_Audits"], tools.now(), {}, None)[0]
        except Exception:  # no graph in the offline suite
            self.skipTest("no graph connection")
        self.assertEqual([c for c in conflicts if c["predicate"] == "penalty_points"], [])


if __name__ == "__main__":
    unittest.main()
