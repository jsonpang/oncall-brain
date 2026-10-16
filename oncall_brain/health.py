"""Read-only knowledge-base health check (/api/health contract).

Six checks, each answered by one or two read-only queries against the same
visibility predicate the tools use (state/validity filtered, superseded
entities excluded from the assertion-based checks):

  single_points      one person is TECH_LEAD_OF >= 2 modules
  owner_vs_handlers  official tech lead vs the people on the module's tickets
  chat_only_rules    Rule/Notice backed by chat transcripts only
  conflicts          mutual exclusive values in one semantic slot
  superseded         old rule -> replacement, with its effective date

Nothing here writes, and every number can be re-derived from the graph.
"""
from __future__ import annotations

import datetime

from . import schema as S
# `g` is imported through tools (not db) so a single patch of tools.g covers
# both these queries and the conflict/supersession helpers they reuse.
from .tools import VISIBLE, _slot_conflicts, _superseded, g, now

CHAT_PREFIX = "corpus/chats/"


def _vis(var: str = "r") -> str:
    """The shared visibility predicate, bound to a chosen relationship alias."""
    return VISIBLE.replace("r.", var + ".")


def _rows(cypher: str, **params) -> list:
    return g().query(cypher, params).result_set


def _sources(ids: list[str]) -> dict[str, str]:
    """Evidence id -> "source:locator", for citing where a claim came from."""
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    rows = _rows("MATCH (e:Evidence) WHERE e.id IN $ids "
                 "RETURN e.id, e.source, e.locator", ids=ids)
    return {r[0]: f"{r[1]}:{r[2]}" for r in rows if r[1]}


def _totals() -> dict:
    def scalar(q):
        return int(_rows(q)[0][0])
    return {
        "entities": scalar("MATCH (n:Entity) RETURN count(n)"),
        # Stored assertions of every state; EVIDENCE edges are graph plumbing.
        "assertions": scalar("MATCH ()-[r]->() WHERE type(r) <> 'EVIDENCE' "
                             "RETURN count(r)"),
        "tickets": scalar("MATCH (n:Entity:Ticket) RETURN count(n)"),
    }


def _single_points(t: str) -> list[dict]:
    rows = _rows(
        "MATCH (p:Entity:Person)-[r:TECH_LEAD_OF]->(m:Entity:Module) "
        "WHERE " + _vis() + " RETURN p.id, p.name, m.id, m.name", now=t)
    people: dict[str, dict] = {}
    for pid, pname, mid, mname in rows:
        entry = people.setdefault(pid, {"person": pname, "modules": {}, "count": 0})
        entry["modules"][mid] = mname
    out = []
    for pid, entry in people.items():
        if len(entry["modules"]) < 2:
            continue
        out.append({"person": entry["person"],
                    "modules": [{"id": m, "name": entry["modules"][m]}
                                for m in sorted(entry["modules"])],
                    "count": len(entry["modules"])})
    out.sort(key=lambda r: (-r["count"], r["person"]))
    return out


def _owner_vs_handlers(t: str) -> list[dict]:
    owners: dict[str, dict] = {}
    for mid, mname, pid, pname in _rows(
            "MATCH (p:Entity)-[r:TECH_LEAD_OF]->(m:Entity:Module) "
            "WHERE " + _vis() + " RETURN m.id, m.name, p.id, p.name", now=t):
        entry = owners.setdefault(mid, {"module_id": mid, "module_name": mname,
                                        "owners": [], "owner_ids": set(),
                                        "handlers": {}})
        entry["module_name"] = mname
        if pid not in entry["owner_ids"]:
            entry["owner_ids"].add(pid)
            entry["owners"].append(pname)
    # Ticket -ABOUT-> Module joined with Ticket -HANDLED_BY-> Person: the same
    # two hops module_expertise walks, once for every module.
    for mid, pid, pname, kid in _rows(
            "MATCH (k:Entity)-[a:ABOUT]->(m:Entity:Module), "
            "(k)-[h:HANDLED_BY]->(p:Entity) "
            "WHERE " + _vis("a") + " AND " + _vis("h") + " "
            "RETURN m.id, p.id, p.name, k.id", now=t):
        entry = owners.setdefault(mid, {"module_id": mid, "module_name": mid,
                                        "owners": [], "owner_ids": set(),
                                        "handlers": {}})
        handler = entry["handlers"].setdefault(pid, {"name": pname, "tickets": set()})
        handler["name"] = pname
        handler["tickets"].add(kid)
    out = []
    for mid in sorted(owners):
        entry = owners[mid]
        handlers = sorted(({"name": h["name"], "count": len(h["tickets"])}
                           for h in entry["handlers"].values()),
                          key=lambda h: (-h["count"], h["name"]))
        owner_ids = entry["owner_ids"]
        out.append({"module_id": mid, "module_name": entry["module_name"],
                    "owners": sorted(entry["owners"]), "handlers": handlers,
                    # owner_handled: the tech lead appears on the module's own
                    # tickets. False means the official owner is a paper owner.
                    "owner_handled": bool(owner_ids & entry["handlers"].keys())})
    return out


def _chat_only_rules(t: str) -> list[dict]:
    """Rules whose every visible assertion is evidenced by chat lines only."""
    per_rule: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for nid, nname, ev in _rows(
            "MATCH (n:Entity)-[r]-(m:Entity) WHERE n.kind IN ['Rule', 'Notice'] "
            "AND " + _vis() + " RETURN n.id, n.name, r.evidence", now=t):
        names.setdefault(nid, nname)
        per_rule.setdefault(nid, []).extend(ev or [])
    out = []
    for nid, ev_ids in per_rule.items():
        if not ev_ids:
            continue  # no evidence at all: unverifiable, not "chat-only"
        sources = _sources(ev_ids)
        if not sources:
            continue
        if not all(v.startswith(CHAT_PREFIX) for v in sources.values()):
            continue
        out.append({"id": nid, "name": names.get(nid, nid),
                    "sources": sorted(set(sources.values()))})
    out.sort(key=lambda r: r["id"])
    return out


def _conflicts(t: str, sup: dict[str, list[dict]]) -> list[dict]:
    ids = [r[0] for r in _rows("MATCH (n:Entity) RETURN n.id")]
    out = []
    for conflict in _slot_conflicts(ids, t, sup, None)[0]:
        values = [v["value"] for v in conflict["values"]]
        out.append({"subject": conflict["subject"], "predicate": conflict["predicate"],
                    "values": values})
    # Single-valued slots where the claim extractor wrote nothing still have to
    # be caught structurally: two visible objects in one single-valued slot.
    seen = {(c["subject"], c["predicate"]) for c in out}
    for predicate in sorted(S.SINGLE_VALUED):
        rows = _rows("MATCH (s:Entity)-[r:" + predicate + "]->(o:Entity) WHERE "
                     + _vis() + " RETURN s.id, o.id, o.name", now=t)
        by_subject: dict[str, dict[str, str]] = {}
        for sid, oid, oname in rows:
            by_subject.setdefault(sid, {})[oid] = oname or oid
        for sid, values in by_subject.items():
            if len(values) < 2 or (sid, predicate) in seen:
                continue
            out.append({"subject": sid, "predicate": predicate,
                        "values": sorted(values.values())})
    return out


def _superseded_list(t: str) -> list[dict]:
    out = []
    for old, records in _superseded(t).items():
        for rec in records:
            out.append({"old": old, "new": rec["superseded_by"],
                        "effective": rec["effective"]})
    out.sort(key=lambda r: (r["effective"] or "", r["old"], r["new"]))
    return out


def kb_health() -> dict:
    """The /api/health payload. Read-only; safe to call on every page load."""
    t = now()
    sup = _superseded(t)
    return {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "totals": _totals(),
        "single_points": _single_points(t),
        # Modules with no ticket history say nothing about owner vs practice.
        "owner_vs_handlers": [m for m in _owner_vs_handlers(t) if m["handlers"]],
        "chat_only_rules": _chat_only_rules(t),
        "conflicts": _conflicts(t, sup),
        "superseded": _superseded_list(t),
    }
