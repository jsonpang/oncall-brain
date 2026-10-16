"""Offline tests for the knowledge-base health check.

The suite runs without a graph: `tools.g` is replaced by a fake whose
`query()` dispatches on the shape of the Cypher, so the aggregation logic in
health.py (which is where the bugs would be) is exercised directly.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from oncall_brain import health, tools

NOW = "2026-10-16"


class FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class FakeGraph:
    """Answers the handful of queries health.py issues, keyed by content."""

    def __init__(self, *, tech_leads, ticket_rows, rule_rows, evidence,
                 entities, claims=(), superseded=(), employed_by=()):
        self.tech_leads = tech_leads          # (person, module, module_name)
        self.ticket_rows = ticket_rows        # (module, person, person_name, ticket)
        self.rule_rows = rule_rows            # (rule_id, rule_name, [evidence ids])
        self.evidence = evidence              # evidence id -> (source, locator)
        self.entities = entities              # [entity ids]
        self.claims = claims                  # (subject, obj, claims_json, aid, ...)
        self.superseded = superseded          # (old, new, aid, effective)
        self.employed_by = employed_by        # (subject, obj, obj_name)
        self.seen = []

    def query(self, cypher, params=None):
        self.seen.append(cypher)
        if "MATCH (n:Entity) RETURN count(n)" in cypher:
            return FakeResult([[len(self.entities)]])
        if "type(r) <> 'EVIDENCE' RETURN count(r)" in cypher:
            return FakeResult([[sum(map(len, (self.tech_leads, self.ticket_rows,
                                              self.rule_rows)))]])
        if "(n:Entity:Ticket) RETURN count(n)" in cypher:
            return FakeResult([[len({r[3] for r in self.ticket_rows})]])
        if "TECH_LEAD_OF]->(m:Entity:Module)" in cypher and "RETURN p.id" in cypher:
            # single_points wants p.id, p.name, m.id, m.name
            return FakeResult([[p, p, m, mn] for p, m, mn in self.tech_leads])
        if "TECH_LEAD_OF]->(m:Entity:Module)" in cypher:
            # owner_vs_handlers wants m.id, m.name, p.id, p.name
            return FakeResult([[m, mn, p, p] for p, m, mn in self.tech_leads])
        if "ABOUT" in cypher:
            return FakeResult([list(r) for r in self.ticket_rows])
        if "n.kind IN ['Rule', 'Notice']" in cypher:
            return FakeResult([list(r) for r in self.rule_rows])
        if "MATCH (e:Evidence) WHERE e.id IN $ids" in cypher:
            ids = (params or {}).get("ids", [])
            return FakeResult([[i, *self.evidence[i]] for i in ids if i in self.evidence])
        if "MATCH (n:Entity) RETURN n.id" in cypher:
            return FakeResult([[i] for i in self.entities])
        if "slot_claims" in cypher or "r.claims_json IS NOT NULL" in cypher:
            return FakeResult([list(r) for r in self.claims])
        if "claim_value_kinds" in cypher or "n.id IN $values" in cypher:
            return FakeResult([])
        if "SUPERSEDES" in cypher:
            return FakeResult([list(r) for r in self.superseded])
        if "EMPLOYED_BY" in cypher:
            return FakeResult([list(r) for r in self.employed_by])
        raise AssertionError(f"unexpected query: {cypher}")


def graph(**kw):
    return FakeGraph(tech_leads=kw.get("tech_leads", []),
                     ticket_rows=kw.get("ticket_rows", []),
                     rule_rows=kw.get("rule_rows", []),
                     evidence=kw.get("evidence", {}),
                     entities=kw.get("entities", []),
                     claims=kw.get("claims", []),
                     superseded=kw.get("superseded", []),
                     employed_by=kw.get("employed_by", []))


def run(**kw) -> dict:
    """kb_health() against a fake graph, with `now` pinned.

    tools.g is patched as well: _slot_conflicts / _superseded resolve `g` in
    the tools namespace, so patching health.g alone would still hit the graph.
    """
    fake = graph(**kw)
    with patch.object(health, "g", return_value=fake), \
            patch.object(tools, "g", return_value=fake), \
            patch.object(health, "now", return_value=NOW):
        return health.kb_health()


class SinglePointsTests(unittest.TestCase):
    """A person who is TECH_LEAD_OF >= 2 modules is a single point of failure."""

    def test_only_people_with_two_or_more_modules_are_reported(self):
        h = run(tech_leads=[("Arthur Vance", "MOD-11", "Continuous Testing Engine"),
                            ("Arthur Vance", "MOD-12", "Continuous Testing Agent"),
                            ("Arthur Vance", "MOD-18", "On-Site Field Support"),
                            ("Colin Marsh", "MOD-01", "Pipeline Orchestration")],
                entities=["MOD-01", "MOD-11", "MOD-12", "MOD-18"])
        self.assertEqual(h["single_points"], [{
            "person": "Arthur Vance", "count": 3,
            "modules": [{"id": "MOD-11", "name": "Continuous Testing Engine"},
                        {"id": "MOD-12", "name": "Continuous Testing Agent"},
                        {"id": "MOD-18", "name": "On-Site Field Support"}]}])

    def test_sorted_by_count_descending_then_name(self):
        h = run(tech_leads=[("Zoe Owner", "MOD-01", "A"), ("Zoe Owner", "MOD-02", "B"),
                            ("Anna Owner", "MOD-03", "C"), ("Anna Owner", "MOD-04", "D"),
                            ("Anna Owner", "MOD-05", "E")],
                entities=["MOD-01", "MOD-02", "MOD-03", "MOD-04", "MOD-05"])
        self.assertEqual([p["person"] for p in h["single_points"]],
                         ["Anna Owner", "Zoe Owner"])
        self.assertEqual([p["count"] for p in h["single_points"]], [3, 2])

    def test_the_same_module_twice_counts_once(self):
        """A duplicate assertion must not inflate the count (real graph has them)."""
        h = run(tech_leads=[("Dup Owner", "MOD-07", "Requirements Management"),
                            ("Dup Owner", "MOD-07", "Requirements Management")],
                entities=["MOD-07"])
        self.assertEqual(h["single_points"], [])


class OwnerVsHandlersTests(unittest.TestCase):
    """Official TECH_LEAD_OF owner vs people who actually handled the tickets."""

    def test_owner_absent_from_handlers_is_flagged(self):
        """MOD-01: Colin Marsh owns it, three other people handled its tickets."""
        h = run(tech_leads=[("Colin Marsh", "MOD-01", "Pipeline Orchestration Engine")],
                ticket_rows=[("MOD-01", "Frank Stewart", "Frank Stewart", "T-1"),
                             ("MOD-01", "Frank Stewart", "Frank Stewart", "T-2"),
                             ("MOD-01", "Robert Coleman", "Robert Coleman", "T-2"),
                             ("MOD-01", "Gordon Archer", "Gordon Archer", "T-3")],
                entities=["MOD-01"])
        self.assertEqual(h["owner_vs_handlers"], [{
            "module_id": "MOD-01", "module_name": "Pipeline Orchestration Engine",
            "owners": ["Colin Marsh"], "owner_handled": False,
            "handlers": [{"name": "Frank Stewart", "count": 2},
                         {"name": "Gordon Archer", "count": 1},
                         {"name": "Robert Coleman", "count": 1}]}])

    def test_owner_who_handled_own_tickets_is_not_flagged(self):
        h = run(tech_leads=[("Alan Cox", "MOD-06", "ZeroTrust SDP")],
                ticket_rows=[("MOD-06", "Alan Cox", "Alan Cox", "T-1"),
                             ("MOD-06", "Sean Porter", "Sean Porter", "T-2")],
                entities=["MOD-06"])
        row = h["owner_vs_handlers"][0]
        self.assertTrue(row["owner_handled"])
        self.assertEqual(row["handlers"][0]["name"], "Alan Cox")

    def test_module_with_no_tickets_is_left_out(self):
        """No ticket history says nothing about owner vs practice: only modules with handlers."""
        h = run(tech_leads=[("Seth Miller", "MOD-05", "Dev Workspace")],
                ticket_rows=[("MOD-02", "Zachary Price", "Zachary Price", "T-1")],
                entities=["MOD-02", "MOD-05"])
        by_id = {r["module_id"]: r for r in h["owner_vs_handlers"]}
        self.assertEqual(sorted(by_id), ["MOD-02"])
        self.assertEqual(by_id["MOD-02"]["owners"], [])  # handler-only module

    def test_one_ticket_counted_once_per_person(self):
        h = run(tech_leads=[("Own Er", "MOD-09", "M")],
                ticket_rows=[("MOD-09", "Han Dler", "Han Dler", "T-1"),
                             ("MOD-09", "Han Dler", "Han Dler", "T-1")],
                entities=["MOD-09"])
        self.assertEqual(h["owner_vs_handlers"][0]["handlers"],
                         [{"name": "Han Dler", "count": 1}])


class OtherSectionsTests(unittest.TestCase):
    def test_chat_only_rule_is_reported_with_its_sources(self):
        h = run(rule_rows=[("Rule:Timeout", "Timeout", ["ev:chat:1"])],
                evidence={"ev:chat:1": ("corpus/chats/chat_2026-08-10.md", "38")},
                entities=["Rule:Timeout"])
        self.assertEqual(h["chat_only_rules"], [{
            "id": "Rule:Timeout", "name": "Timeout",
            "sources": ["corpus/chats/chat_2026-08-10.md:38"]}])

    def test_rule_with_any_doc_evidence_is_not_chat_only(self):
        h = run(rule_rows=[("Rule:Mixed", "Mixed", ["ev:chat:1", "ev:doc:1"])],
                evidence={"ev:chat:1": ("corpus/chats/c.md", "1"),
                          "ev:doc:1": ("corpus/docs/d.md", "2")},
                entities=["Rule:Mixed"])
        self.assertEqual(h["chat_only_rules"], [])

    def test_superseded_pairs_carry_effective_dates(self):
        h = run(superseded=[("Rule:PulseVPN_Automatic_Token_Extension",
                             "Rule:PulseVPN_Token_Renewal", "aid-1", "2026-10-08")],
                entities=["Rule:PulseVPN_Token_Renewal"])
        self.assertEqual(h["superseded"], [
            {"old": "Rule:PulseVPN_Automatic_Token_Extension",
             "new": "Rule:PulseVPN_Token_Renewal", "effective": "2026-10-08"}])

    def test_single_valued_slot_with_two_values_is_a_conflict(self):
        """EMPLOYED_BY is single-valued: two visible vendors must conflict."""
        h = run(entities=["Test Conflict Person"],
                employed_by=[("Test Conflict Person", "Synthetix Solutions",
                              "Synthetix Solutions"),
                             ("Test Conflict Person", "Strata Technologies",
                              "Strata Technologies")])
        self.assertEqual(h["conflicts"], [
            {"subject": "Test Conflict Person", "predicate": "EMPLOYED_BY",
             "values": ["Strata Technologies", "Synthetix Solutions"]}])

    def test_totals_and_timestamp(self):
        h = run(tech_leads=[("A", "MOD-01", "M")],
                ticket_rows=[("MOD-01", "B", "B", "T-1")],
                rule_rows=[("Rule:X", "X", [])], entities=["MOD-01", "Rule:X"])
        self.assertEqual(h["totals"], {"entities": 2, "assertions": 3, "tickets": 1})
        self.assertTrue(h["generated_at"].startswith("20"))

    def test_claim_conflicts_come_from_the_shared_detector(self):
        """Two competing claim values in one slot, via tools._slot_conflicts."""
        def claim(value):
            return json.dumps([{"subject": "Procedure:Audits",
                                "predicate": "penalty_points", "value": value,
                                "condition": {}, "evidence_id": "ev:1"}])
        rows = [("Procedure:Audits", "other", "aid-1", "formal_rule", "2026-01-01",
                 None, claim("-2 pts"), ["ev:1"]),
                ("Procedure:Audits", "other", "aid-2", "practice", "2026-01-01",
                 None, claim("-10 pts"), ["ev:1"])]
        h = run(entities=["Procedure:Audits"], claims=rows)
        conflicts = [c for c in h["conflicts"] if c["predicate"] == "penalty_points"]
        self.assertEqual(len(conflicts), 1)
        # authority-ranked, so the formal_rule value comes first
        self.assertEqual(conflicts[0]["values"], ["-2 pts", "-10 pts"])

    def test_payload_has_every_contract_key(self):
        h = run()
        self.assertEqual(list(h), ["generated_at", "totals", "single_points",
                                   "owner_vs_handlers", "chat_only_rules",
                                   "conflicts", "superseded"])


if __name__ == "__main__":
    unittest.main()
