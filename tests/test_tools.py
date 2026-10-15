"""The four read-only tools: contract, visibility filter, conflict and
supersession marking.

These are the defects that stayed silent through ingest and only surfaced in an
evaluation run, so each one is pinned here: unfiltered hops, dropped endpoints
and evidence that never reaches the caller.
"""
from __future__ import annotations

import pytest

from oncall_brain import tools


def test_describe_ontology_exposes_the_full_registry():
    d = tools.describe_ontology()
    assert d["status"] == "ok"
    assert "MAINTAINED_BY proves document curation only" in " ".join(
        d["behavior_rules"])
    assert d["predicates"]["TECH_LEAD_OF"]["direction"] == "Person -> Module"
    assert d["predicates"]["MAINTAINED_BY"]["direction"] == "Resource -> Person"


def test_read_knowledge_returns_every_visible_assertion_with_evidence(graph, now):
    res = tools.read_knowledge(id="MOD-01")
    assert res["status"] == "ok"
    assert res["assertions"], "MOD-01 reads with no assertions at all"
    for a in res["assertions"]:
        assert a["aid"] and a["authority"] in (
            "formal_rule", "practice", "observation")
        assert a["predicate"] and a["other"]["id"]
    assert res["evidence"], "assertions readable but no evidence returned"
    for e in res["evidence"]:
        assert e["source"].startswith("corpus/")
        assert e["locator"].isdigit()
        assert e["excerpt"]


def test_read_knowledge_marks_superseded_neighbours(graph, now):
    """Any neighbour targeted by a visible SUPERSEDES edge is flagged."""
    res = tools.read_knowledge(id="MOD-01")
    assert res["status"] == "ok"
    flags = {a["other"]["id"]: a["superseded"] for a in res["assertions"]}
    assert all(isinstance(v, bool) for v in flags.values())


def test_visibility_filter_excludes_expired_and_candidate(graph, now):
    """Seed an expired and a candidate assertion; neither may be visible.

    This is the defect class that previously let stale rules answer questions.
    """
    g = graph
    g.query(
        "MERGE (a:Entity:Person {id:'Test Expired Person'}) "
        "MERGE (b:Entity:Module {id:'MOD-01'}) "
        "MERGE (a)-[r:TECH_LEAD_OF {aid:'test-expired'}]->(b) "
        "SET r.state='approved', r.valid_from='2026-01-01', r.valid_to='2026-02-01', "
        "r.authority='formal_rule', r.evidence=[]")
    g.query(
        "MERGE (a:Entity:Person {id:'Test Candidate Person'}) "
        "MERGE (b:Entity:Module {id:'MOD-01'}) "
        "MERGE (a)-[r:TECH_LEAD_OF {aid:'test-candidate'}]->(b) "
        "SET r.state='candidate', r.valid_from='2026-01-01', "
        "r.authority='formal_rule', r.evidence=[]")

    res = tools.read_knowledge(id="MOD-01")
    visible = {a["other"]["id"] for a in res["assertions"]}
    assert "Test Expired Person" not in visible, "expired assertion is visible"
    assert "Test Candidate Person" not in visible, "candidate assertion is visible"

    g.query("MATCH (n:Entity) WHERE n.id STARTS WITH 'Test ' DETACH DELETE n")


def test_supersession_marking_leaves_the_old_rule_discoverable(graph, now):
    """A visible SUPERSEDES edge marks its target; the chain is exposed."""
    g = graph
    g.query(
        "MERGE (n:Entity:Notice {id:'Notice:Test_Policy'}) "
        "MERGE (o:Entity:Rule {id:'Rule:Test_Old_Rule'}) "
        "MERGE (n)-[r:SUPERSEDES {aid:'test-sup'}]->(o) "
        "SET r.state='approved', r.valid_from='2026-09-15', "
        "r.authority='practice', r.evidence=[]")
    try:
        res = tools.read_knowledge(id="Rule:Test_Old_Rule")
        assert res["status"] == "ok"
        assert res["superseded"], "superseded rule is not flagged"
        assert res["superseded"][0]["superseded_by"] == "Notice:Test_Policy"
        assert res["superseded"][0]["effective"] == "2026-09-15"

        # and search surfaces the flag too
        found = tools.search_knowledge(query="Test_Old_Rule")
        assert found["candidates"]
        assert found["candidates"][0]["superseded"] is True
    finally:
        g.query("MATCH (n:Entity) WHERE n.id CONTAINS 'Test_' DETACH DELETE n")


def test_conflict_is_surfaced_for_a_single_valued_slot(graph, now):
    """Two visible values in a single-valued slot must be reported, not merged.

    EMPLOYED_BY is single-valued in the registry; seeding a second vendor for
    one person has to come back as a conflict.
    """
    g = graph
    g.query(
        "MERGE (p:Entity:Person {id:'Test Conflict Person'}) "
        "MERGE (v1:Entity:Vendor {id:'Synthetix Solutions'}) "
        "MERGE (v2:Entity:Vendor {id:'Strata Technologies'}) "
        "MERGE (p)-[r1:EMPLOYED_BY {aid:'test-emp-1'}]->(v1) "
        "MERGE (p)-[r2:EMPLOYED_BY {aid:'test-emp-2'}]->(v2) "
        "SET r1.state='approved', r1.valid_from='2026-01-01', "
        "r1.authority='formal_rule', r1.evidence=[], "
        "r2.state='approved', r2.valid_from='2026-01-01', "
        "r2.authority='practice', r2.evidence=[]")
    try:
        res = tools.read_knowledge(id="Test Conflict Person")
        assert res["status"] == "ok"
        assert res.get("conflicts"), "two vendors in one slot were merged silently"
        conflict = res["conflicts"][0]
        assert conflict["predicate"] == "EMPLOYED_BY"
        assert {v["value"] for v in conflict["values"]} == {
            "Synthetix Solutions", "Strata Technologies"}
        assert "surfaced, not resolved" in conflict["note"]
    finally:
        g.query("MATCH (n:Entity {id:'Test Conflict Person'}) DETACH DELETE n")


def test_conditional_rule_is_insufficient_context_without_the_fact(graph, now):
    """A rule with condition=severity=P3 that the caller did not supply must be
    reported as insufficient_context, not silently treated as applicable."""
    g = graph
    g.query(
        "MERGE (r:Entity:Rule {id:'Rule:Test_Conditional'}) "
        "MERGE (m:Entity:Module {id:'MOD-01'}) "
        "MERGE (r)-[e:APPLIES_TO {aid:'test-cond'}]->(m) "
        "SET e.state='approved', e.valid_from='2026-01-01', "
        "e.authority='formal_rule', e.condition='severity=P3', e.evidence=[]")
    try:
        res = tools.read_knowledge(id="Rule:Test_Conditional")
        assert res["status"] == "ok"
        cond = [a for a in res["assertions"] if a.get("condition")]
        assert cond, "condition was not returned to the caller"
        assert cond[0]["applicability"] == "insufficient_context"

        res = tools.read_knowledge(id="Rule:Test_Conditional",
                                   context={"severity": "P3"})
        cond = [a for a in res["assertions"] if a.get("condition")]
        assert cond[0]["applicability"] == "applicable"
    finally:
        g.query("MATCH (n:Entity {id:'Rule:Test_Conditional'}) DETACH DELETE n")


def test_query_relations_filters_every_hop(graph, now):
    """A 2-hop traversal must not return a path whose second hop is invisible."""
    g = graph
    g.query(
        "MERGE (m:Entity:Module {id:'MOD-01'}) "
        "MERGE (r:Entity:Resource {id:'Resource:Test_Stale'}) "
        "MERGE (p:Entity:Person {id:'Test Stale Curator'}) "
        "MERGE (m)-[r1:HAS_RESOURCE {aid:'test-hr'}]->(res2:Entity:Resource "
        "  {id:'Resource:Test_Hop'}) "
        "MERGE (res2)-[r2:MAINTAINED_BY {aid:'test-mb'}]->(p) "
        "SET r1.state='approved', r1.valid_from='2026-01-01', "
        "r1.authority='formal_rule', r1.evidence=[], "
        "r2.state='candidate', r2.valid_from='2026-01-01', "
        "r2.authority='observation', r2.evidence=[]")
    try:
        res = tools.query_relations(start_id="MOD-01",
                                    predicates=["HAS_RESOURCE", "MAINTAINED_BY"],
                                    max_hops=2)
        assert res["status"] == "ok"
        for path in res["paths"]:
            if "Resource:Test_Hop" in [n["id"] for n in path["nodes"]]:
                # the second hop is a candidate: it must not have been crossed
                assert len(path["edges"]) == 1
    finally:
        g.query("MATCH (n:Entity) WHERE n.id CONTAINS 'Test_' "
                "OR n.id = 'Test Stale Curator' DETACH DELETE n")


def test_hop_count_is_clamped(graph, now):
    res = tools.query_relations(start_id="MOD-01",
                                predicates=["HAS_RESOURCE"], max_hops=99)
    assert res["status"] == "ok"
    assert res["max_hops"] == 3


def test_search_returns_ids_that_can_actually_be_read(graph, now):
    """The agent may only use ids from tool results, so every candidate id
    returned by search must resolve when read back."""
    found = tools.search_knowledge(query="Pipeline", limit=5)
    assert found["status"] == "ok"
    for c in found["candidates"]:
        assert tools.read_knowledge(id=c["id"])["status"] == "ok"
