"""Impact analysis: who and what is affected when a module fails.

Provides multi-hop aggregation (/api/impact/<module_id>):
  - owners (TECH_LEAD_OF) and their teams (MEMBER_OF)
  - resources and SOPs (HAS_RESOURCE)
  - applicable rules (APPLIES_TO)
  - historical ticket volume, affected regional territories and user tenants
    (parsed from ticket excerpts, with note '来自工单原文，不是图关系'),
    and actual handlers (reusing module_expertise)
  - territory leads (COVERS_TERRITORY reverse lookup) indicating who to notify
  - provenance (aid / evidence) for every item
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import tools


SOURCE_NOTE = "来自工单原文，不是图关系"


def _vis(var: str = "r") -> str:
    """Visibility predicate bound to relationship alias."""
    return tools.VISIBLE.replace("r.", var + ".")


def get_all_modules() -> list[dict[str, str]]:
    """Return all known modules sorted by ID for dropdown selection."""
    rows = tools.g().query(
        "MATCH (m:Entity) WHERE m.kind = 'Module' RETURN m.id, m.name ORDER BY m.id"
    ).result_set
    return [{"id": r[0], "name": r[1] or r[0]} for r in rows]


def resolve_territory_id(raw_territory: str, graph_territories: dict[str, str]) -> str | None:
    """Match raw ticket regional_territory string to canonical graph Territory entity ID."""
    if not raw_territory:
        return None
    raw = raw_territory.strip()
    if raw in graph_territories:
        return raw

    # Normalize common naming variations: e.g. "Sunward Metro Territory" -> "Sunward Metro",
    # "Northland Metropolitan Territory" -> "Northland Metro"
    cleaned = re.sub(r"\s+Territory$", "", raw).strip()
    cleaned = cleaned.replace("Metropolitan", "Metro")
    if cleaned in graph_territories:
        return cleaned

    for gid in graph_territories:
        if gid.lower() == cleaned.lower() or gid.lower() in raw.lower() or raw.lower() in gid.lower():
            return gid

    return None


def module_impact(module_id: str) -> dict[str, Any]:
    """Perform multi-hop impact analysis for a given module."""
    t = tools.now()
    all_ev_ids: set[str] = set()

    # 1. Lookup target module
    mod_rows = tools.g().query(
        "MATCH (m:Entity {id: $id}) RETURN m.id, m.name, m.kind",
        {"id": module_id}
    ).result_set
    if not mod_rows:
        # Try case-insensitive fallback if exact match wasn't found
        mod_rows = tools.g().query(
            "MATCH (m:Entity) WHERE toUpper(m.id) = toUpper($id) RETURN m.id, m.name, m.kind",
            {"id": module_id}
        ).result_set

    if not mod_rows:
        return {
            "status": "not_found",
            "module_id": module_id,
            "error": f"Module '{module_id}' not found",
            "owners": [],
            "resources": [],
            "rules": [],
            "tickets_count": 0,
            "handlers": [],
            "affected_territories": [],
            "affected_tenants": [],
            "notify_leads": [],
            "evidence": []
        }

    mod_id, mod_name, mod_kind = mod_rows[0]

    # 2. Owners (TECH_LEAD_OF) & Member of Teams (MEMBER_OF)
    owner_rows = tools.g().query(
        "MATCH (p:Entity)-[r:TECH_LEAD_OF]->(m:Entity {id: $id}) WHERE " + _vis("r") + " "
        "RETURN p.id, p.name, p.kind, r.aid, r.evidence, r.authority",
        {"id": mod_id, "now": t}
    ).result_set

    owners_map: dict[str, dict[str, Any]] = {}
    for pid, pname, pkind, raid, rev, rauth in owner_rows:
        if pid not in owners_map:
            owners_map[pid] = {
                "id": pid,
                "name": pname or pid,
                "kind": pkind or "Person",
                "aids": [],
                "evidence_ids": [],
                "authorities": [],
                "teams": []
            }
        if raid and raid not in owners_map[pid]["aids"]:
            owners_map[pid]["aids"].append(raid)
        if rauth and rauth not in owners_map[pid]["authorities"]:
            owners_map[pid]["authorities"].append(rauth)
        if rev:
            for evid in rev:
                if evid not in owners_map[pid]["evidence_ids"]:
                    owners_map[pid]["evidence_ids"].append(evid)
                    all_ev_ids.add(evid)

    # Resolve teams for each owner
    for pid in owners_map:
        team_rows = tools.g().query(
            "MATCH (p:Entity {id: $pid})-[r:MEMBER_OF]->(tm:Entity) WHERE " + _vis("r") + " "
            "RETURN tm.id, tm.name, tm.kind, r.aid, r.evidence, r.authority",
            {"pid": pid, "now": t}
        ).result_set
        teams_map: dict[str, dict[str, Any]] = {}
        for tmid, tmname, tmkind, raid, rev, rauth in team_rows:
            if tmid not in teams_map:
                teams_map[tmid] = {
                    "id": tmid,
                    "name": tmname or tmid,
                    "kind": tmkind or "Team",
                    "aids": [],
                    "evidence_ids": [],
                    "authorities": []
                }
            if raid and raid not in teams_map[tmid]["aids"]:
                teams_map[tmid]["aids"].append(raid)
            if rauth and rauth not in teams_map[tmid]["authorities"]:
                teams_map[tmid]["authorities"].append(rauth)
            if rev:
                for evid in rev:
                    if evid not in teams_map[tmid]["evidence_ids"]:
                        teams_map[tmid]["evidence_ids"].append(evid)
                        all_ev_ids.add(evid)
        owners_map[pid]["teams"] = list(teams_map.values())

    # 3. Resources (HAS_RESOURCE)
    res_rows = tools.g().query(
        "MATCH (m:Entity {id: $id})-[r:HAS_RESOURCE]->(res:Entity) WHERE " + _vis("r") + " "
        "RETURN res.id, res.name, res.kind, r.aid, r.evidence, r.authority",
        {"id": mod_id, "now": t}
    ).result_set
    resources_map: dict[str, dict[str, Any]] = {}
    for resid, resname, reskind, raid, rev, rauth in res_rows:
        if resid not in resources_map:
            resources_map[resid] = {
                "id": resid,
                "name": resname or resid,
                "kind": reskind or "Resource",
                "aids": [],
                "evidence_ids": [],
                "authorities": []
            }
        if raid and raid not in resources_map[resid]["aids"]:
            resources_map[resid]["aids"].append(raid)
        if rauth and rauth not in resources_map[resid]["authorities"]:
            resources_map[resid]["authorities"].append(rauth)
        if rev:
            for evid in rev:
                if evid not in resources_map[resid]["evidence_ids"]:
                    resources_map[resid]["evidence_ids"].append(evid)
                    all_ev_ids.add(evid)

    # 4. Rules (APPLIES_TO)
    rule_rows = tools.g().query(
        "MATCH (rule:Entity)-[r:APPLIES_TO]->(m:Entity {id: $id}) WHERE " + _vis("r") + " "
        "RETURN rule.id, rule.name, rule.kind, r.aid, r.evidence, r.authority",
        {"id": mod_id, "now": t}
    ).result_set
    rules_map: dict[str, dict[str, Any]] = {}
    for rid, rname, rkind, raid, rev, rauth in rule_rows:
        if rid not in rules_map:
            rules_map[rid] = {
                "id": rid,
                "name": rname or rid,
                "kind": rkind or "Rule",
                "aids": [],
                "evidence_ids": [],
                "authorities": []
            }
        if raid and raid not in rules_map[rid]["aids"]:
            rules_map[rid]["aids"].append(raid)
        if rauth and rauth not in rules_map[rid]["authorities"]:
            rules_map[rid]["authorities"].append(rauth)
        if rev:
            for evid in rev:
                if evid not in rules_map[rid]["evidence_ids"]:
                    rules_map[rid]["evidence_ids"].append(evid)
                    all_ev_ids.add(evid)

    # 5. Tickets, Handlers and raw ticket evidence
    exp = tools.module_expertise(mod_id)
    handlers = exp.get("handlers", [])
    tickets_count = exp.get("tickets", 0)

    # Also capture ABOUT edges to ensure all related ticket IDs are collected
    about_rows = tools.g().query(
        "MATCH (k:Entity)-[r:ABOUT]->(m:Entity {id: $id}) WHERE " + _vis("r") + " "
        "RETURN k.id, r.aid, r.evidence, r.authority",
        {"id": mod_id, "now": t}
    ).result_set
    ticket_ids = set()
    for kid, raid, rev, rauth in about_rows:
        ticket_ids.add(kid)
        if rev:
            for evid in rev:
                all_ev_ids.add(evid)

    for ev in exp.get("evidence", []):
        if isinstance(ev, dict) and "id" in ev:
            all_ev_ids.add(ev["id"])

    # Fetch initial evidence set
    evidence_items = tools._evidence(list(all_ev_ids))

    # 6. Parse regional_territory and user_tenant from ticket excerpts
    territory_counts: dict[str, dict[str, Any]] = {}
    tenant_counts: dict[str, dict[str, Any]] = {}

    for ev in evidence_items:
        if ev.get("source") == "corpus/tickets/tickets.jsonl" and ev.get("excerpt"):
            try:
                data = json.loads(ev["excerpt"])
                tid = data.get("ticket_id")
                if tid and ticket_ids and tid not in ticket_ids:
                    continue
                reg_terr = data.get("regional_territory")
                tenant = data.get("user_tenant")
                evid = ev["id"]

                if reg_terr:
                    entry = territory_counts.setdefault(reg_terr, {"count": 0, "tickets": set(), "evidence_ids": set()})
                    entry["count"] += 1
                    if tid:
                        entry["tickets"].add(tid)
                    entry["evidence_ids"].add(evid)

                if tenant:
                    entry = tenant_counts.setdefault(tenant, {"count": 0, "tickets": set(), "evidence_ids": set()})
                    entry["count"] += 1
                    if tid:
                        entry["tickets"].add(tid)
                    entry["evidence_ids"].add(evid)
            except Exception:
                pass

    # 7. Reverse lookup: Territory leads (COVERS_TERRITORY)
    graph_terr_rows = tools.g().query(
        "MATCH (t:Entity) WHERE t.kind = 'Territory' RETURN t.id, t.name"
    ).result_set
    graph_territories = {r[0]: r[1] or r[0] for r in graph_terr_rows}

    affected_territories = []
    for raw_t, info in territory_counts.items():
        gid = resolve_territory_id(raw_t, graph_territories)
        affected_territories.append({
            "territory": raw_t,
            "canonical_id": gid,
            "count": info["count"],
            "tickets": sorted(info["tickets"]),
            "evidence_ids": sorted(info["evidence_ids"]),
            "source_note": SOURCE_NOTE
        })
    affected_territories.sort(key=lambda x: (-x["count"], x["territory"]))

    affected_tenants = []
    for raw_ten, info in tenant_counts.items():
        affected_tenants.append({
            "tenant": raw_ten,
            "count": info["count"],
            "tickets": sorted(info["tickets"]),
            "evidence_ids": sorted(info["evidence_ids"]),
            "source_note": SOURCE_NOTE
        })
    affected_tenants.sort(key=lambda x: (-x["count"], x["tenant"]))

    # Find who covers each affected territory
    notify_leads_map: dict[str, dict[str, Any]] = {}
    for terr in affected_territories:
        gid = terr["canonical_id"]
        if not gid:
            continue
        leads_rows = tools.g().query(
            "MATCH (p:Entity)-[r:COVERS_TERRITORY]->(t:Entity {id: $tid}) WHERE " + _vis("r") + " "
            "RETURN p.id, p.name, p.kind, r.aid, r.evidence, r.authority",
            {"tid": gid, "now": t}
        ).result_set

        for pid, pname, pkind, raid, rev, rauth in leads_rows:
            if pid not in notify_leads_map:
                notify_leads_map[pid] = {
                    "id": pid,
                    "name": pname or pid,
                    "kind": pkind or "Person",
                    "territories": [],
                    "aids": [],
                    "evidence_ids": [],
                    "authorities": []
                }
            if terr["territory"] not in notify_leads_map[pid]["territories"]:
                notify_leads_map[pid]["territories"].append(terr["territory"])
            if raid and raid not in notify_leads_map[pid]["aids"]:
                notify_leads_map[pid]["aids"].append(raid)
            if rauth and rauth not in notify_leads_map[pid]["authorities"]:
                notify_leads_map[pid]["authorities"].append(rauth)
            if rev:
                for evid in rev:
                    if evid not in notify_leads_map[pid]["evidence_ids"]:
                        notify_leads_map[pid]["evidence_ids"].append(evid)
                        all_ev_ids.add(evid)

    # Refresh evidence set if new evidence IDs were accumulated
    if len(all_ev_ids) > len(evidence_items):
        evidence_items = tools._evidence(list(all_ev_ids))

    notify_leads = sorted(notify_leads_map.values(), key=lambda x: (-len(x["territories"]), x["name"]))

    return {
        "status": "ok",
        "module": {
            "id": mod_id,
            "name": mod_name,
            "kind": mod_kind
        },
        "owners": list(owners_map.values()),
        "resources": list(resources_map.values()),
        "rules": list(rules_map.values()),
        "tickets_count": tickets_count,
        "handlers": handlers,
        "affected_territories": affected_territories,
        "affected_tenants": affected_tenants,
        "notify_leads": notify_leads,
        "evidence": evidence_items,
        "release_time": t
    }
