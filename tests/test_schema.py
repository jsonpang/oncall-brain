"""Schema registry and ingest validation: unregistered predicates never land."""
from __future__ import annotations

import pytest

from oncall_brain import schema as S
from oncall_brain.ingest import llm


def test_every_predicate_has_a_domain_range_and_meaning():
    for p, (dom, rng, single, meaning) in S.PREDICATES.items():
        assert dom and rng, f"{p} missing domain/range"
        assert isinstance(single, bool)
        assert meaning, f"{p} has no meaning text for the ontology tool"


def test_validate_predicate_accepts_good_and_rejects_bad():
    assert S.validate_predicate("TECH_LEAD_OF", "Person", "Module") is None
    assert S.validate_predicate("HAS_RESOURCE", "Module", "Resource") is None
    assert S.validate_predicate("SUPERSEDES", "Notice", "Rule") is None

    wrong_domain = S.validate_predicate("TECH_LEAD_OF", "Module", "Module")
    assert wrong_domain and "domain violation" in wrong_domain

    wrong_range = S.validate_predicate("TECH_LEAD_OF", "Person", "Person")
    assert wrong_range and "range violation" in wrong_range

    unregistered = S.validate_predicate("OWNS", "Team", "Module")
    assert unregistered and "unregistered" in unregistered


def test_maintained_by_is_not_an_escalation_predicate():
    """The non-derivation rule is stated in the registry itself, so the agent
    sees it through describe_ontology rather than a hard-coded warning."""
    assert "NEVER an escalation target" in S.PREDICATES["MAINTAINED_BY"][3]
    assert "escalation target" in S.PREDICATES["TECH_LEAD_OF"][3]


def test_llm_validate_rejects_unregistered_and_out_of_range():
    assert llm.validate({"subject": "A", "object": "B", "predicate": "OWNS",
                         "source": "corpus/chats/chat_2026-08-07.md", "line": 1})
    assert llm.validate({"subject": "MOD-01", "object": "MOD-02",
                         "predicate": "MAINTAINED_BY",
                         "source": "corpus/chats/chat_2026-08-07.md", "line": 1})


def test_llm_validate_rejects_a_line_that_does_not_exist():
    reason = llm.validate({
        "subject": "Colin Marsh", "object": "MOD-01", "predicate": "TECH_LEAD_OF",
        "source": "corpus/chats/chat_2026-08-07.md", "line": 99999})
    assert reason and "out of range" in reason


def test_llm_validate_accepts_a_real_assertion_shape():
    assert llm.validate({
        "subject": "Colin Marsh", "object": "MOD-01", "predicate": "TECH_LEAD_OF",
        "source": "corpus/chats/chat_2026-08-07.md", "line": 20}) is None


def test_canonicalise_merges_spelling_variants_only():
    """'Knowledge Hub MOD-01' and 'Knowledge Hub - MOD-01' are one entity;
    two different module KB pages are not."""
    edges = [
        {"subject": "MOD-01", "object": "Knowledge Hub MOD-01",
         "predicate": "HAS_RESOURCE"},
        {"subject": "MOD-01", "object": "Knowledge Hub - MOD-01",
         "predicate": "HAS_RESOURCE"},
        {"subject": "MOD-02", "object": "Knowledge Hub MOD-02",
         "predicate": "HAS_RESOURCE"},
    ]
    out, aliases = llm.canonicalise(edges, known={"Knowledge Hub MOD-01"})
    objs = {e["object"] for e in out}
    assert "Knowledge Hub MOD-01" in objs          # the known id wins
    assert "Knowledge Hub MOD-02" in objs          # a different page is kept
    assert "Knowledge Hub - MOD-01" not in objs    # variant folded away
    assert aliases.get("Knowledge Hub - MOD-01") == "Knowledge Hub MOD-01"


def test_canonicalise_never_merges_across_prefixes():
    edges = [
        {"subject": "Rule:Step_Timeout", "object": "MOD-01", "predicate": "APPLIES_TO"},
        {"subject": "Notice:Step_Timeout", "object": "MOD-01", "predicate": "APPLIES_TO"},
    ]
    out, _ = llm.canonicalise(edges, known=set())
    assert {e["subject"] for e in out} == {"Rule:Step_Timeout", "Notice:Step_Timeout"}


@pytest.mark.parametrize("bad", ["a" * 3, "", "MOD-99", "Zoe Q. Nobody"])
def test_label_of_never_crashes_on_an_unknown_id(bad):
    from oncall_brain.ingest.labeler import label_of

    assert label_of(bad) in set(S.LABELS)
