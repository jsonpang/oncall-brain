"""OnCall Brain graph schema: node labels and the controlled predicate registry.

Per design doc §4: every business node carries :Entity; Evidence nodes do not.
Unregistered predicates are rejected at ingest time and at query time.
"""
from __future__ import annotations

# Business node labels (all additionally labeled :Entity). Evidence is separate.
LABELS = [
    "Person", "Team", "Vendor", "Role", "Module", "Territory", "Resource",
    "Procedure", "WorkStage", "Rule", "Notice", "Ticket",
]

# rtype property values for Resource
RESOURCE_TYPES = ["kb", "portal", "channel", "form", "doc"]

# Controlled predicates: name -> (domain, range, single_valued, meaning)
# domain/range are entity labels (checked at validation, after entity-type
# resolution). "single_valued" flags single-value slots where >1 visible
# assertion is surfaced as a conflict instead of being merged.
PREDICATES = {
    "MEMBER_OF":      ("Person", "Team", False, "team membership; property primary distinguishes main/secondary assignment; does not imply escalation duty"),
    "EMPLOYED_BY":    ("Person", "Vendor", True, "vendor the person belongs to"),
    "HOLDS_ROLE":     ("Person", "Role", False, "time-boxed role assignment; set valid_to on handover/departure"),
    "TECH_LEAD_OF":   ("Person", "Module", False, "module Tier 2 technical owner; the escalation target for module issues"),
    "HAS_RESOURCE":   ("Module", "Resource", False, "module knowledge base / channel / portal resources"),
    "MAINTAINED_BY":  ("Resource", "Person", False, "ONLY proves who curates/reviews the document; NEVER an escalation target"),
    "COVERS_TERRITORY": ("Person", "Territory", False, "field-staff territory assignment"),
    "HAS_STEP":       ("Procedure", "WorkStage", False, "lifecycle step of a procedure"),
    "PRECEDES":       ("WorkStage", "WorkStage", False, "ordering between work stages"),
    "PARALLEL_WITH":  ("WorkStage", "WorkStage", False, "parallel stages"),
    "PERFORMED_BY":   ("Procedure", "Role|Team|Person", False, "who executes the procedure"),
    "RECEIVED_BY":    ("Procedure", "Role|Team|Person", False, "who receives/handles the procedure outcome; needs direct evidence"),
    "USES_RESOURCE":  ("Procedure|WorkStage|Module|Role|Rule", "Resource", False, "entry point / template / form / channel used"),
    "REQUIRES_FIELD": ("Procedure", "Resource", False, "mandatory form field of a procedure"),
    "GOVERNED_BY":    ("Procedure|WorkStage|Resource|Rule", "Rule|Notice|Resource", False, "rule applicable to the procedure/stage/resource; Rule->Resource carries the entry point a rule prescribes"),
    "APPLIES_TO":     ("Rule|Notice", "Module|WorkStage|Territory", False, "scope of a rule or notice"),
    "SUPERSEDES":     ("Notice|Rule", "Rule", False, "after effective_from the old rule leaves current answers"),
    "ABOUT":          ("Ticket", "Module", False, "ticket fact; derivation source for who knows the module"),
    "HANDLED_BY":     ("Ticket", "Person", False, "ticket fact; derivation source for who knows the module"),
}

SINGLE_VALUED = {p for p, (_, _, sv, _) in PREDICATES.items() if sv}

AUTHORITY_LEVELS = ["formal_rule", "practice", "observation"]
AUTH_RANK = {a: i for i, a in enumerate(AUTHORITY_LEVELS)}

STATES = ["candidate", "approved"]

TERRITORIES = [
    "Lakeside Delta", "Northland Metro", "Sunward Metro", "Golden Coast",
    "Northern Plains", "Silver Peak", "Emerald Coast", "Frost River",
    "Riverland Basin", "Pine Ridge", "Iron Bay", "Amber Valley",
]


def domain_labels(predicate: str) -> set[str]:
    dom = PREDICATES[predicate][0]
    return set(dom.split("|"))


def range_labels(predicate: str) -> set[str]:
    rng = PREDICATES[predicate][1]
    return set(rng.split("|"))


def is_registered(predicate: str) -> bool:
    return predicate in PREDICATES


def validate_predicate(predicate: str, subject_label: str, object_label: str) -> str | None:
    """Return None if valid, else a rejection reason."""
    if not is_registered(predicate):
        return f"unregistered predicate '{predicate}'"
    if subject_label not in domain_labels(predicate):
        return (f"domain violation for {predicate}: subject '{subject_label}' "
                f"not in {sorted(domain_labels(predicate))}")
    if object_label not in range_labels(predicate):
        return (f"range violation for {predicate}: object '{object_label}' "
                f"not in {sorted(range_labels(predicate))}")
    return None


def describe() -> dict:
    return {
        "entity_kinds": LABELS + ["Resource(rtype: kb/portal/channel/form/doc)"],
        "predicates": {
            p: {
                "direction": f"{d} -> {r}",
                "single_valued": sv,
                "meaning": m,
            }
            for p, (d, r, sv, m) in PREDICATES.items()
        },
        "authority_levels": {
            "formal_rule": "official document",
            "practice": "confirmed experience (documented or multi-person confirmed)",
            "observation": "chat observation, least authoritative",
        },
        "assertion_states": STATES,
        "answer_states": [
            "applicable", "insufficient_context", "conflict", "superseded",
            "stale", "not_found",
        ],
        "behavior_rules": [
            "IDs may only come from tool results",
            "max 8 tool calls / 6 relation queries per question",
            "MAINTAINED_BY never implies escalation duty",
            "SUPERSEDED rules are excluded from current answers but shown with the replacement chain",
            "no visible assertion means not_found in the current graph: say so, never guess",
        ],
    }
