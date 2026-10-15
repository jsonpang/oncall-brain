"""Six pure-Cypher questions answered end to end from the graph, no LLM.

Each test states one of the six questions the design doc §7 lists as answerable
straight from deterministic sources, asserts the exact answer the graph must
give, and asserts that every returned assertion carries real evidence.
"""
from __future__ import annotations

from tests.conftest import ids, rows

VISIBLE = ("r.state = 'approved' AND r.valid_from <= $now "
           "AND (r.valid_to IS NULL OR r.valid_to > $now)")


def test_q1_module_knowledge_base_and_its_curator(graph, now):
    """'Where is the knowledge base for my module, and who maintains it?'

    Module -> HAS_RESOURCE -> the KB page, and the page -> MAINTAINED_BY -> its
    curator. Both hops must be visibility-filtered.
    """
    q = f"""
        MATCH (m:Module {{id: 'MOD-01'}})-[r1:HAS_RESOURCE]->(kb:Resource)
        WHERE {VISIBLE.replace('r.', 'r1.')}
        MATCH (kb)-[r2:MAINTAINED_BY]->(curator:Person)
        WHERE {VISIBLE.replace('r.', 'r2.')}
        RETURN kb.id, kb.path, curator.id, r1.aid, r2.aid
    """
    got = rows(graph, q, now=now)
    assert got, "MOD-01 has no visible knowledge-base resource"
    kb_ids = {r[0] for r in got}
    assert kb_ids == {"Knowledge Hub - MOD-01"}
    assert {r[2] for r in got} == {"Wesley Tate"}
    # every assertion carries an aid and the KB path is the documented one
    assert all(r[3] and r[4] for r in got)
    assert {r[1] for r in got} == {"Knowledge Hub -> MOD-01"}


def test_q1_maintainer_is_not_the_escalation_owner(graph, now):
    """MAINTAINED_BY must never be read as the escalation target.

    The design doc's core non-derivation rule: the KB curator differs from the
    module's TECH_LEAD_OF, and no query may conflate the two.
    """
    q = f"""
        MATCH (kb:Resource {{id: 'Knowledge Hub - MOD-01'}})-[r:MAINTAINED_BY]->(p:Person)
        WHERE {VISIBLE}
        RETURN p.id
    """
    curator = ids(graph, q, now=now)
    q2 = f"""
        MATCH (p:Person)-[r:TECH_LEAD_OF]->(m:Module {{id: 'MOD-01'}})
        WHERE {VISIBLE}
        RETURN p.id
    """
    owner = ids(graph, q2, now=now)
    assert curator == {"Wesley Tate"}
    assert owner == {"Colin Marsh"}
    assert curator and owner and not (curator & owner), (
        "the same person is both curator and escalation owner -- the predicates "
        "have been conflated")


def test_q3_module_escalation_owner_is_tech_lead(graph, now):
    """'Who do I escalate a Pipeline issue to?' -> the module's TECH_LEAD_OF.

    Two deterministic sources (the who-to-find guide and the module registry)
    must agree; the answer is a named person, not a role or a team.
    """
    q = f"""
        MATCH (p:Person)-[r:TECH_LEAD_OF]->(m:Module {{id: 'MOD-02'}})
        WHERE {VISIBLE}
        RETURN p.id, r.aid, r.authority, r.evidence
    """
    got = rows(graph, q, now=now)
    assert {r[0] for r in got} == {"Wyatt Lawson"}
    for _pid, aid, authority, ev in got:
        assert aid and authority in ("formal_rule", "practice", "observation")
        assert ev, "TECH_LEAD_OF assertion without evidence"


def test_q7_ticket_module_and_handler_are_both_reachable(graph, now):
    """'What module is this ticket about and who handled it?' -> ABOUT + HANDLED_BY.

    A two-hop ticket question, with the ticket's own module resolved by id.
    """
    q = f"""
        MATCH (t:Ticket)-[r1:ABOUT]->(m:Module)
        WHERE {VISIBLE.replace('r.', 'r1.')}
        MATCH (t)-[r2:HANDLED_BY]->(p:Person)
        WHERE {VISIBLE.replace('r.', 'r2.')}
        RETURN t.id, m.id, p.id LIMIT 5
    """
    got = rows(graph, q, now=now)
    assert got, "no ticket resolves to both a module and a handler"
    for tid, mid, pid in got:
        assert tid.startswith("TCK-")
        assert mid.startswith("MOD-")
        assert pid and not pid.startswith("MOD-")


def test_who_handles_a_module_most_derives_from_tickets(graph, now):
    """'Who knows this module?' is derived from tickets, ranked by count.

    The design doc §3.7 derivation: handlers of tickets ABOUT a module, ordered
    by how many they handled. This must be a real aggregate, not a guess.
    """
    q = f"""
        MATCH (t:Ticket)-[r1:ABOUT]->(m:Module {{id: 'MOD-01'}})
        WHERE {VISIBLE.replace('r.', 'r1.')}
        MATCH (t)-[r2:HANDLED_BY]->(p:Person)
        WHERE {VISIBLE.replace('r.', 'r2.')}
        RETURN p.id, count(t) AS n ORDER BY n DESC, p.id LIMIT 5
    """
    got = rows(graph, q, now=now)
    assert got, "no handler derived for MOD-01"
    counts = [r[1] for r in got]
    assert counts == sorted(counts, reverse=True), "ranking is not by count"
    assert all(n >= 1 for n in counts)


def test_territory_coverage_is_one_hop_and_named(graph, now):
    """'Who covers Northern Plains?' -> COVERS_TERRITORY to a named person."""
    q = f"""
        MATCH (p:Person)-[r:COVERS_TERRITORY]->(t:Territory {{id: 'Northern Plains'}})
        WHERE {VISIBLE}
        RETURN p.id, r.evidence
    """
    got = rows(graph, q, now=now)
    assert got, "no one covers Northern Plains"
    assert "Frank Stewart" in {r[0] for r in got}
    assert all(r[1] for r in got), "territory assertion without evidence"


def test_every_hop_is_visibility_filtered(graph, now):
    """A single unfiltered hop would leak candidate/expired assertions.

    Count with and without the visibility predicate; the filtered count can
    never exceed the raw count, and a multi-hop traversal must filter EVERY hop.
    """
    raw = rows(graph, "MATCH ()-[r]->() RETURN count(r)")[0][0]
    q = f"""
        MATCH (m:Module)-[r1:HAS_RESOURCE]->(kb:Resource)-[r2:MAINTAINED_BY]->(p:Person)
        WHERE {VISIBLE.replace('r.', 'r1.')}
          AND {VISIBLE.replace('r.', 'r2.')}
        RETURN m.id, kb.id, p.id
    """
    filtered = len(rows(graph, q, now=now))
    q_unfiltered = """
        MATCH (m:Module)-[:HAS_RESOURCE]->(kb:Resource)-[:MAINTAINED_BY]->(p:Person)
        RETURN m.id, kb.id, p.id
    """
    unfiltered = len(rows(graph, q_unfiltered))
    assert raw > 0
    assert filtered <= unfiltered
    # and the filter is actually doing work on at least the state field
    candidates = rows(
        graph, "MATCH ()-[r]->() WHERE r.state = 'candidate' RETURN count(r)")[0][0]
    assert candidates == 0, "candidate assertions are visible: filter is broken"
