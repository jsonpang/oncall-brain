"""Deterministic (no LLM) extraction from structured corpus sources:

- corpus/org/contacts_roster.json         -> Person, Vendor, EMPLOYED_BY, MEMBER_OF
- corpus/org/teams_and_responsibilities.md -> Team, TeamLead(Role), COVERS_TERRITORY (field staff)
- corpus/org/routing_and_points_of_contact.md -> TECH_LEAD_OF (core module escalation)
- corpus/org/organizational_hierarchy.md   -> Team membership (managers)
- corpus/docs/08-module-knowledge-base-registry.md -> Module, TECH_LEAD_OF, HAS_RESOURCE(kb)
- corpus/tickets/tickets.jsonl            -> Ticket, ABOUT, HANDLED_BY

Every assertion carries evidence (file:line + excerpt). Chats and the free-text
docs are extracted by the LLM pass (llm.py); this file never guesses.
"""
from __future__ import annotations

import json

from .. import schema as S
from . import evidence as EV

CHATS = "corpus/chats"


def _month_floor(date: str) -> str:
    return date[:7] + "-01"


def _payload():
    """Yield op dicts (node / evidence / edge) to be batch-written."""
    yield from _roster()
    yield from _teams()
    yield from _routing()
    yield from _hierarchy()
    yield from _doc08()
    yield from _tickets()


def _person(name: str):
    return {"op": "node", "label": "Person", "id": name,
            "props": {"name": name, "kind": "Person"}}


def _vendor(v: dict):
    return {"op": "node", "label": "Vendor", "id": v["name"],
            "props": {"name": v["name"], "code": v["code"], "kind": "Vendor"}}


VENDORS = [
    {"name": "Synthetix Solutions", "code": "SYN"},
    {"name": "Strata Technologies", "code": "STR"},
    {"name": "Northwind Field Services", "code": "NFS"},
]

TEAMS = [
    {"id": "Field & Enablement Team", "lead": "Arthur Vance"},
    {"id": "Operations Governance & QA Group", "lead": "Gregory Stone"},
    {"id": "Frontline Support Team (L1)", "lead": "Julian Thorne"},
    {"id": "Operations Engineering Team (L2)", "lead": "Marcus Sterling"},
]


def _roster():
    src = "corpus/org/contacts_roster.json"
    raw = (EV.ROOT / "corpus/org/contacts_roster.json").read_text(encoding="utf-8")
    people = json.loads(raw)
    for v in VENDORS:
        yield _vendor(v)
    # find the line each person's record starts on for evidence
    lines = raw.splitlines()
    starts = {}
    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith('{"name"'):
            nm = json.loads(ln)["name"]
            starts[nm] = i
    n = 0
    for p in people:
        name = p["name"]
        if name not in starts:  # multi-line records not present; skip safely
            continue
        n += 1
        yield _person(name)
        # vendor
        vmap = {v["name"]: v for v in VENDORS}
        vn = p.get("vendor")
        if vn in vmap:
            yield _ev(src, starts[name], "formal_rule", json.dumps(p, ensure_ascii=False))
            yield _edge(name, "EMPLOYED_BY", vn, src, starts[name],
                        authority="formal_rule", valid_from="2026-01-01")
        # team membership via functional role text
        role = (p.get("role") or "") + " " + (p.get("notes") or "")
        team = None
        if "Field" in role or p.get("domain") == "Field Domain":
            team = "Field & Enablement Team"
        elif "QA" in role or "Governance" in role:
            team = "Operations Governance & QA Group"
        elif role.startswith("L1") or "Frontline" in role:
            team = "Frontline Support Team (L1)"
        elif role.startswith("L2"):
            team = "Operations Engineering Team (L2)"
        if team:
            yield _ev(src, starts[name], "formal_rule", json.dumps(p, ensure_ascii=False))
            yield _edge(name, "MEMBER_OF", team, src, starts[name],
                        authority="formal_rule", valid_from="2026-01-01")


def _ev(source, line_no, authority, excerpt=None):
    rec = EV.build(source, line_no, authority, excerpt)
    if rec is None:
        raise ValueError(f"evidence build failed {source}:{line_no}")
    return {"op": "evidence", **rec}


def _edge(subject, predicate, obj, source, line_no, *, authority,
          valid_from="2026-01-01", valid_to=None, **props):
    rec = EV.build(source, line_no, authority)
    assert rec is not None, f"no evidence at {source}:{line_no}"
    ev_id = rec["id"]
    aid = f"det:{predicate}:{subject}->{obj}:{source}:{line_no}"
    return {"op": "edge", "subject": subject, "predicate": predicate, "object": obj,
            "aid": aid, "authority": authority, "evidence": [ev_id],
            "valid_from": valid_from, "valid_to": valid_to, "props": props}


def _teams():
    import re
    src = "corpus/org/teams_and_responsibilities.md"
    ls = EV.lines(src)
    for t in TEAMS:
        yield {"op": "node", "label": "Team", "id": t["id"],
               "props": {"name": t["id"], "kind": "Team"}}
    # team leads (HOLDS_ROLE is Person->Role; we model lead via Role node)
    for i, ln in enumerate(ls, 1):
        m = re.match(r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*(\d{4}-\d{2}-\d{2})", ln)
        if m and m.group(1) in {t["id"] for t in TEAMS}:
            team, lead, _scope, _mission, eff = [m.group(k).strip() for k in range(1, 6)]
            role_id = f"Team Lead - {team}"
            yield {"op": "node", "label": "Role", "id": role_id,
                   "props": {"name": role_id, "kind": "Role"}}
            yield _person(lead)
            yield _edge(lead, "HOLDS_ROLE", role_id, src, i,
                        authority="formal_rule", valid_from=_month_floor(eff))
    # field staff territories (section 2 table rows)
    for i, ln in enumerate(ls, 1):
        m = re.match(r"\|\s*([A-Z][^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(None|[^|]*?)\s*\|", ln)
        if m and any(t in ln for t in S.TERRITORIES) and "---" not in ln and "Member" not in ln:
            member = m.group(1).split("[")[0].strip()
            for terr in S.TERRITORIES:
                if terr in m.group(2):
                    yield _person(member)
                    yield _edge(member, "COVERS_TERRITORY", terr, src, i,
                                authority="formal_rule", valid_from="2026-07-01")


def _routing():
    import re
    src = "corpus/org/routing_and_points_of_contact.md"
    ls = EV.lines(src)
    mod_alias = {
        "Pipeline Engine": ("MOD-01", "Pipeline Orchestration Engine"),
        "Continuous Deployment (CD)": ("MOD-03", "Continuous Deployment (CD)"),
        "Continuous Integration (CI)": ("MOD-02", "Continuous Integration (CI)"),
        "Dev Workspace / Cloud Desktops": ("MOD-05", "Dev Workspace & Cloud Desktops"),
        "Artifact Repository & Registries": ("MOD-04", "Artifact Repository & Storage"),
    }
    for i, ln in enumerate(ls, 1):
        if "Confirmed" not in ln or "|" not in ln:
            continue
        for alias, (mid, mname) in mod_alias.items():
            if alias in ln:
                m = re.match(r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", ln)
                if not m:
                    continue
                lead = m.group(2).split("[")[0].strip()
                # skip header / separator rows
                if lead in ("Tier 2 Escalation Lead", "---") or "Lead" in lead and "[" not in m.group(2):
                    continue
                yield _person(lead)
                yield _edge(lead, "TECH_LEAD_OF", mid, src, i,
                            authority="formal_rule", valid_from="2026-09-01")
                break


def _hierarchy():
    import re
    src = "corpus/org/organizational_hierarchy.md"
    ls = EV.lines(src)
    for i, ln in enumerate(ls, 1):
        if not ln.startswith("|"):
            continue
        m = re.match(r"\|\s*([^|]+?)\s*\|\s*\d+\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|", ln)
        if not m:
            continue
        lead = m.group(1).strip()
        if lead in ("Operations Lead (Manager)", "---"):
            continue
        cols = [m.group(k).strip() for k in range(2, 6)]
        team_map = [
            ("Operations Governance & QA Group", cols[0]),
            ("Operations Engineering Team (L2)", cols[1]),
            ("Frontline Support Team (L1)", cols[2]),
            ("Field & Enablement Team", cols[3]),
        ]
        for team, cell in team_map:
            for name in re.findall(r"[A-Z][a-z]+(?: [A-Z][a-z]+)+", cell):
                name = name.strip()
                if name in ("Duplicate Name", "Regional", "Role", "formerly",
                            "Duplicate", "Name", "Regional)"):
                    continue
                yield _person(name)
                yield _edge(name, "MEMBER_OF", team, src, i,
                            authority="formal_rule", valid_from="2026-01-01")


def _doc08():
    src = "corpus/docs/08-module-knowledge-base-registry.md"
    ls = EV.lines(src)
    import re
    kb_ids = set()
    for i, ln in enumerate(ls, 1):
        m = re.match(r"\|\s*(MOD-\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", ln)
        if not m:
            continue
        mid, mname, lead, platform = [m.group(k).strip() for k in range(1, 5)]
        if mid in ("Module ID",) or mname.startswith("---"):
            continue
        yield {"op": "node", "label": "Module", "id": mid,
               "props": {"name": mname, "kind": "Module"}}
        # Tier 2 Engineering Lead column == TECH_LEAD_OF
        yield _person(lead)
        yield _edge(lead, "TECH_LEAD_OF", mid, src, i, authority="formal_rule",
                    valid_from="2026-01-01")
        # Primary KB Location == Knowledge Hub => HAS_RESOURCE kb node
        rid = f"Knowledge Hub - {mid}"
        if rid not in kb_ids:
            kb_ids.add(rid)
            yield {"op": "node", "label": "Resource", "id": rid,
                   "props": {"name": f"{mid} Knowledge Hub", "kind": "Resource",
                             "rtype": "kb", "platform": platform,
                             "path": f"Knowledge Hub -> {mid}"}}
        yield _edge(mid, "HAS_RESOURCE", rid, src, i, authority="formal_rule",
                    valid_from="2026-01-01")
        # Review cadence: weekly review by Wesley Tate (line 9) -> MAINTAINED_BY
    # MAINTAINED_BY from the metadata section (weekly review by Wesley Tate)
    for i, ln in enumerate(ls, 1):
        if "Weekly review by Wesley Tate" in ln:
            for rid in kb_ids:
                yield _edge(rid, "MAINTAINED_BY", "Wesley Tate", src, i,
                            authority="formal_rule", valid_from="2026-01-01")
            break


def _tickets():
    import re
    src = "corpus/tickets/tickets.jsonl"
    raw = (EV.ROOT / "corpus/tickets/tickets.jsonl").read_text(encoding="utf-8")
    lines = raw.splitlines()
    # module name -> MOD id map from doc 08 (deterministic source)
    mod_map = {}
    for ln in EV.lines("corpus/docs/08-module-knowledge-base-registry.md"):
        m = re.match(r"\|\s*(MOD-\d+)\s*\|\s*([^|]+?)\s*\|", ln)
        if m and m.group(1) != "Module ID":
            mod_map[m.group(2).strip()] = m.group(1)
    for i, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        t = json.loads(ln)
        tid = t["ticket_id"]
        # evidence excerpt is the ticket line itself (deterministic source)
        rec = EV.build(src, i, "observation")
        assert rec is not None
        yield {"op": "evidence", **rec}
        ev_id = rec["id"]
        yield {"op": "node", "label": "Ticket", "id": tid,
               "props": {"name": tid, "kind": "Ticket", "module_name": t["module_name"],
                         "status": t["ticket_status"], "territory": t["regional_territory"],
                         "vendor": t["vendor"], "date": t["submission_date"]}}
        mid = mod_map.get(t["module_name"])
        if mid:
            aid = f"det:ABOUT:{tid}->{mid}"
            yield {"op": "edge", "subject": tid, "predicate": "ABOUT", "object": mid,
                   "aid": aid, "authority": "observation", "evidence": [ev_id],
                   "valid_from": t["submission_date"], "state": "candidate"}
        handler = t["assigned_l1"].split("[")[0].strip()
        if handler:
            yield _person(handler)
            aid = f"det:HANDLED_BY:{tid}->{handler}"
            yield {"op": "edge", "subject": tid, "predicate": "HANDLED_BY", "object": handler,
                   "aid": aid, "authority": "observation", "evidence": [ev_id],
                   "valid_from": t["submission_date"], "state": "candidate"}


def extract():
    """Run all deterministic extractors; return (payload, rejects)."""
    payload, rejects = [], []
    for item in _payload():
        if item["op"] == "edge":
            reason = _validate_edge(item)
            if reason:
                rejects.append({**item, "reason": reason})
                continue
        payload.append(item)
    return payload, rejects


def _label_of(payload_ids, nid, payload):
    for item in payload:
        if item["op"] == "node" and item["id"] == nid:
            return item["label"]
    # fallback: query the live graph later; here we use known prefixes
    if nid.startswith("MOD-"):
        return "Module"
    if nid in {t["id"] for t in TEAMS}:
        return "Team"
    if nid in {v["name"] for v in VENDORS} or nid in ("SYN", "STR", "NFS"):
        return "Vendor"
    for t in S.TERRITORIES:
        if nid == t:
            return "Territory"
    if nid.startswith("TCK-"):
        return "Ticket"
    if nid.startswith("Role:"):
        return "Role"
    return "Person"


def _validate_edge(item):
    sub, pre, obj = item["subject"], item["predicate"], item["object"]
    if not S.is_registered(pre):
        return f"unregistered predicate {pre}"
    # resolve labels via payload accumulated so far is expensive; use heuristics
    from .labeler import label_of
    sl, ol = label_of(sub), label_of(obj)
    return S.validate_predicate(pre, sl, ol)


if __name__ == "__main__":
    payload, rejects = extract()
    print(f"payload ops: {len(payload)}, rejects: {len(rejects)}")
    for r in rejects[:10]:
        print("REJECT:", r.get("predicate"), r.get("subject"), "->", r.get("object"), "|", r["reason"])
