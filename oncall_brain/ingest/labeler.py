"""ID -> entity-label resolution used during ingest validation.

Entities are aligned by exact ID (person full name, MOD code, channel name...).
Names never auto-merge; a later duplicate ID from a different source aligns to
the same entity only when the ID string is identical.
"""
from __future__ import annotations

import re

from .. import schema as S

MODULE_IDS = {}
_ROSTER_NAMES = None
_DOC08_LINES = None


def _load_doc08():
    global _DOC08_LINES, MODULE_IDS
    if _DOC08_LINES is None:
        from . import evidence as EV
        _DOC08_LINES = EV.lines("corpus/docs/08-module-knowledge-base-registry.md")
        for ln in _DOC08_LINES:
            m = re.match(r"\|\s*(MOD-\d+)\s*\|\s*([^|]+?)\s*\|", ln)
            if m and m.group(1) != "Module ID":
                MODULE_IDS[m.group(1)] = m.group(2).strip()


def roster_names() -> set[str]:
    global _ROSTER_NAMES
    if _ROSTER_NAMES is None:
        import json
        from . import evidence as EV
        people = json.loads((EV.ROOT / "corpus/org/contacts_roster.json").read_text(encoding="utf-8"))
        _ROSTER_NAMES = {p["name"] for p in people}
    return _ROSTER_NAMES


def module_name(mid: str) -> str | None:
    _load_doc08()
    return MODULE_IDS.get(mid)


KNOWN_TEAMS = {
    "Field & Enablement Team",
    "Operations Governance & QA Group",
    "Frontline Support Team (L1)",
    "Operations Engineering Team (L2)",
    "Tier 2 Ops Engineering",
    "Field & Enablement",
}

KNOWN_RESOURCES = {
    "Channel-General-OpsL1L2",
    "Channel-CICD-L1-L2-Collaboration",
    "Channel-OpsSupervision",
    "Channel-TopSeverity-Escalations",
    "Sentinel Desk",
    "Sentinel Desk Portal",
    "Unified Gateway SSO",
    "Unified Gateway Portal",
    "Internal Operations Reporting Portal",
    "Non-Ticket Issue Register",
    "Non-Ticket Issue Ledger",
    "Production Incident Registration Form",
    "PulseVPN Renewal Request Form",
    "Knowledge Hub",
}


def label_of(nid: str) -> str:
    """Best-effort label for an entity ID (used for domain/range validation)."""
    _load_doc08()
    if nid in KNOWN_TEAMS:
        return "Team"
    if nid in KNOWN_RESOURCES:
        return "Resource"
    if nid in MODULE_IDS:
        return "Module"
    if nid.startswith("MOD-"):
        return "Module"
    if nid in S.TERRITORIES:
        return "Territory"
    if nid.startswith("TCK-"):
        return "Ticket"
    if nid.startswith("Resource:"):
        return "Resource"
    if nid.startswith("Rule:") or nid.startswith("Notice:"):
        return "Rule" if nid.startswith("Rule:") else "Notice"
    if nid.startswith("Procedure:"):
        return "Procedure"
    if nid.startswith("Stage:"):
        return "WorkStage"
    if nid.startswith("Role:") or nid.startswith("Team Lead - "):
        return "Role"
    if nid.startswith("Team:"):
        return "Team"
    if nid in ("Synthetix Solutions", "Strata Technologies", "Northwind Field Services"):
        return "Vendor"
    if re.match(r"^(Channel|Sentinel|Unified|Internal Ops|Internal Operations|"
                r"Non-Ticket|Knowledge Hub|Production Incident|PulseVPN)", nid):
        return "Resource"
    if re.match(r"^[A-Z][a-z]+( [A-Z][a-z()/-]+)+$", nid):
        # Person-like (two+ capitalized words) — Person, Team, Role or Territory
        if nid in roster_names():
            return "Person"
        if nid.endswith(" Team") or nid.endswith(" Group") or "Team (" in nid:
            return "Team"
        if nid in S.TERRITORIES:
            return "Territory"
        if nid.startswith("Tier ") or nid.endswith(" Lead") or " Lead" in nid or nid.endswith(" Officer"):
            return "Role"
        return "Person"  # default for personal names
    return "Person"
