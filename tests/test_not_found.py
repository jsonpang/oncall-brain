"""Negative cases: the graph must report not_found rather than fabricate.

Design doc §3.5 / answer card traps D1 (unknown component) and D2 (unrecorded
error code). The tools return a `not_found` status with an explicit note, and
nothing in the graph may resolve to an owner, module or runbook for an entity
that the corpus does not contain.
"""
from __future__ import annotations

import pytest

from oncall_brain import tools


def test_unknown_component_resolves_to_nothing(graph, now):
    """Trap D1: 'IoT-Edge-Gateway-Sync' is not in the corpus at all.

    Every lookup -- search, read and traversal -- must come back not_found.
    """
    for q in ("IoT-Edge-Gateway-Sync", "IoT", "Edge Gateway Sync"):
        res = tools.search_knowledge(query=q)
        assert res["status"] == "not_found", f"{q!r} unexpectedly matched"
        assert res["candidates"] == []
        assert "do not guess" in res["note"]

    res = tools.read_knowledge(id="IoT-Edge-Gateway-Sync")
    assert res["status"] == "not_found"
    assert "do not assume it exists" in res["note"]

    res = tools.query_relations(start_id="IoT-Edge-Gateway-Sync",
                                predicates=["HAS_RESOURCE", "TECH_LEAD_OF"])
    assert res["status"] == "not_found"
    assert res["paths"] == []


def test_unrecorded_error_code_has_no_runbook(graph, now):
    """Trap D2: 'ERR_PULSE_RUNNER_0x8F9C' has no runbook or FAQ entry.

    No entity in the graph may carry that code in its id, and a traversal from
    the module it was reported on must not produce a runbook for it.
    """
    for q in ("ERR_PULSE_RUNNER_0x8F9C", "ERR_PULSE_RUNNER", "0x8F9C"):
        assert tools.search_knowledge(query=q)["status"] == "not_found"

    # the module the failure was reported on exists and has a KB, but that KB
    # is not an entry for this code: a search for the code must still fail
    assert tools.search_knowledge(query="MOD-01")["status"] == "ok"
    res = tools.query_relations(start_id="MOD-01",
                                predicates=["HAS_RESOURCE"])
    assert res["status"] == "ok"
    kb_names = [n["name"] for p in res["paths"] for n in p["nodes"]]
    assert not any("0x8F9C" in str(n) or "ERR_PULSE" in str(n) for n in kb_names)


def test_not_found_for_a_person_who_does_not_exist(graph, now):
    """A plausible-looking name that is not in the roster must not resolve."""
    res = tools.search_knowledge(query="Nobody Unknownperson")
    assert res["status"] == "not_found"


def test_traversal_of_a_real_module_never_invents_a_target(graph, now):
    """A module with no RECEIVED_BY / PERFORMED_BY yields not_found, not a guess.

    Modules do not hold those predicates (schema domain is Procedure), so the
    traversal must decline rather than return something adjacent.
    """
    res = tools.query_relations(start_id="MOD-01",
                                predicates=["PERFORMED_BY", "RECEIVED_BY"])
    assert res["status"] == "not_found"
    assert "not_found" in res["note"]


def test_unregistered_predicate_is_rejected_before_querying(graph, now):
    """The predicate registry is enforced: an unknown predicate never reaches
    the graph, so a typo cannot silently become a full scan."""
    res = tools.query_relations(start_id="MOD-01", predicates=["OWNS"])
    assert res["status"] == "error"
    assert "unregistered" in res["error"]
    assert "registered" in res  # the caller gets the real list back

    res = tools.query_relations(start_id="MOD-01", predicates=[])
    assert res["status"] == "error"


def test_empty_and_bad_arguments_do_not_fabricate(graph, now):
    res = tools.search_knowledge(query="   ")
    assert res["status"] == "error"

    res = tools.query_relations(start_id="MOD-01", predicates=["HAS_RESOURCE"],
                                direction="sideways")
    assert res["status"] == "error"


def test_superseded_target_is_flagged_not_silently_dropped(graph, now):
    """A superseded rule leaves current answers but stays discoverable.

    With no notice in the deterministic graph there is nothing superseded; the
    tool must report that honestly instead of inventing a chain.
    """
    res = tools.read_knowledge(id="MOD-01")
    assert res["status"] == "ok"
    assert isinstance(res["superseded"], list)
    assert res["superseded"] == []


@pytest.mark.parametrize("missing", [
    "IoT-Edge-Gateway-Sync",
    "ERR_PULSE_RUNNER_0x8F9C",
    "Totally Unnamed Service",
])
def test_nothing_in_the_graph_claims_to_own_a_missing_entity(graph, now, missing):
    """No invented ownership: no visible edge may point at a missing entity."""
    rows_ = graph.query(
        "MATCH (n:Entity) WHERE toLower(n.id) CONTAINS toLower($q) RETURN n.id",
        {"q": missing[:8]}).result_set
    assert rows_ == []
