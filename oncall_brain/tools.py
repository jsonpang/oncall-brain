"""Four read-only tools (read-only MCP contract): describe / search / read / query_relations.

All Cypher is generated server-side from templates -- the model only chooses
predicates, direction and hop count, never writes Cypher. Every hop applies the
visibility predicate from design doc §4.3:

    r.state='approved' AND r.valid_from <= $now AND (r.valid_to IS NULL OR r.valid_to > $now)

An entity targeted by a visible SUPERSEDES edge is marked superseded and kept
out of current answers, but its replacement chain is exposed.
"""
from __future__ import annotations

import datetime
import os

from . import schema as S
from .db import g

VISIBLE = ("r.state = 'approved' AND r.valid_from <= $now "
           "AND (r.valid_to IS NULL OR r.valid_to > $now)")

TEMPLATES = {
    "fulltext_search": (
        "CALL db.idx.fulltext.queryNodes('Entity', $q) "
        "YIELD node, score RETURN node.id, node.kind, node.name, score "
        "ORDER BY score DESC LIMIT $limit"),
    "fulltext_search_kind": (
        "CALL db.idx.fulltext.queryNodes('Entity', $q) "
        "YIELD node, score WITH node, score WHERE node.kind = $kind "
        "RETURN node.id, node.kind, node.name, score "
        "ORDER BY score DESC LIMIT $limit"),
    "id_prefix": (
        "MATCH (n:Entity) WHERE toLower(n.id) CONTAINS toLower($q) "
        "RETURN n.id, n.kind, n.name, 0.0 "
        "ORDER BY n.id LIMIT $limit"),
    "id_prefix_kind": (
        "MATCH (n:Entity) WHERE toLower(n.id) CONTAINS toLower($q) "
        "AND n.kind = $kind RETURN n.id, n.kind, n.name, 0.0 "
        "ORDER BY n.id LIMIT $limit"),
    "read_entity": "MATCH (n:Entity {id: $id}) RETURN properties(n)",
    "read_neighbors": (
        "MATCH (n:Entity {id: $id})-[r]-(m:Entity) WHERE " + VISIBLE + " "
        "RETURN type(r), startNode(r).id = $id, m.id, m.kind, m.name, "
        "r.aid, r.authority, r.valid_from, r.valid_to, r.condition, r.condition_key, r.penalty "),
    "superseded_targets": (
        "MATCH (n)-[r:SUPERSEDES]->(o) WHERE " + VISIBLE + " "
        "RETURN o.id, n.id, r.aid, r.valid_from"),
    "traverse": (
        "MATCH p = (s:Entity {id: $id})"  # pattern injected below
        "(x:Entity) WHERE all(rel IN relationships(p) WHERE " + VISIBLE.replace("r.", "rel.") + ") "
        "RETURN [n IN nodes(p) | n.id], [n IN nodes(p) | n.kind], [n IN nodes(p) | n.name], "
        "[rel IN relationships(p) | type(rel)], [rel IN relationships(p) | rel.aid], "
        "[rel IN relationships(p) | rel.authority], [rel IN relationships(p) | rel.evidence], "
        "[rel IN relationships(p) | rel.valid_from] "
        "LIMIT $limit"),
    "evidence_by_id": (
        "MATCH (e:Evidence) WHERE e.id IN $ids "
        "RETURN e.id, e.source, e.locator, e.excerpt, e.authority"),
}


def now() -> str:
    return (os.environ.get("ONCALL_NOW")
            or datetime.date.today().isoformat())


def _visible_asof(t: str) -> str:
    return VISIBLE


def _superseded(t: str) -> dict[str, list[dict]]:
    """Map of superseded entity id -> list of replacement records."""
    out: dict[str, list[dict]] = {}
    for oid, nid, aid, vf in g().query(TEMPLATES["superseded_targets"],
                                       {"now": t}).result_set:
        out.setdefault(oid, []).append(
            {"superseded_by": nid, "aid": aid, "effective": vf})
    return out


def _evidence(ids) -> list[dict]:
    ids = [i for i in ids if i]
    if not ids:
        return []
    rows = g().query(TEMPLATES["evidence_by_id"], {"ids": list(ids)}).result_set
    recs = [dict(zip(["id", "source", "locator", "excerpt", "authority"], r)) for r in rows]
    return sorted(recs, key=lambda e: (e["source"], int(e["locator"])
                                       if str(e["locator"]).isdigit() else 0))


def _eval_condition(cond: str | None, key: str | None, ctx: dict | None) -> str:
    """Design doc §3.5: rule applicability given a context."""
    if not cond:
        return "applicable"
    k = cond.split("=", 1)[0]
    if k not in (ctx or {}):
        return "insufficient_context"
    if key and key not in (ctx or {}):
        return "insufficient_context"
    want = cond.split("=", 1)[1] if "=" in cond else None
    return "applicable" if want is None or str(ctx[k]) == want else "inapplicable"


# --------------------------------------------------------------------------
# 1. describe_ontology
# --------------------------------------------------------------------------
def describe_ontology() -> dict:
    return {
        "status": "ok",
        "release_time": now(),
        "entity_kinds": S.LABELS + ["Resource(rtype: kb/portal/channel/form/doc)"],
        "predicates": {
            p: {"direction": f"{d} -> {r}", "single_valued": sv, "meaning": m}
            for p, (d, r, sv, m) in S.PREDICATES.items()},
        "authority_levels": {
            "formal_rule": "official document (Doc 01-10)",
            "practice": "confirmed practice / announced notice (chat, multi-person)",
            "observation": "single chat remark, least authoritative"},
        "answer_states": ["applicable", "insufficient_context", "conflict",
                          "superseded", "stale", "not_found"],
        "behavior_rules": [
            "IDs may only come from tool results; never invent an id",
            "MAINTAINED_BY proves document curation only -- never an escalation target",
            "TECH_LEAD_OF is the module Tier 2 escalation owner",
            "same slot with several visible values = conflict; surface all ranked by authority, do not pick one",
            "a SUPERSEDES target leaves current answers but is shown with its replacement chain",
            "no visible assertion = not_found in this graph: say so and suggest who to ask"],
        "budget": {"max_tool_calls": 8, "max_relation_queries": 6, "max_hops": 3},
    }


# --------------------------------------------------------------------------
# 2. search_knowledge
# --------------------------------------------------------------------------
def search_knowledge(query: str, kind: str | None = None, limit: int = 12) -> dict:
    """Entity candidates by fulltext name match, with a substring fallback."""
    q = (query or "").strip()
    if not q:
        return {"status": "error", "error": "empty query"}
    rows = []
    for key in ("fulltext_search_kind" if kind else "fulltext_search",):
        try:
            rows = g().query(TEMPLATES[key], {"q": q, "kind": kind,
                                              "limit": int(limit)}).result_set
        except Exception:
            rows = []
    if not rows:
        key = "id_prefix_kind" if kind else "id_prefix"
        rows = g().query(TEMPLATES[key], {"q": q, "kind": kind,
                                          "limit": int(limit)}).result_set
    sup = _superseded(now())
    out = [{"id": i, "kind": k, "name": n, "score": float(s),
            "superseded": i in sup,
            "superseded_by": [r["superseded_by"] for r in sup.get(i, [])]}
           for i, k, n, s in rows]
    if not out:
        return {"status": "not_found", "query": q, "candidates": [],
                "note": "no entity in the current graph matches; do not guess an id"}
    return {"status": "ok", "query": q, "candidates": out}


# --------------------------------------------------------------------------
# 3. read_knowledge
# --------------------------------------------------------------------------
def _rel_evidence(subj: str, aid: str, predicate: str, obj: str,
                  outgoing: bool | None = None) -> list[str]:
    """Evidence ids carried on one relationship (stored as a property).

    `outgoing` disambiguates undirected matches from read_knowledge; when None
    either direction is accepted.
    """
    pats = []
    if outgoing is not False:
        pats.append("(a:Entity {id: $s})-[r]->(b:Entity {id: $o})")
    if outgoing is not True:
        pats.append("(a:Entity {id: $o})-[r]->(b:Entity {id: $s})")
    out: list[str] = []
    for pat in pats:
        rows = g().query(f"MATCH {pat} WHERE r.aid = $aid RETURN r.evidence",
                         {"s": subj, "o": obj, "aid": aid}).result_set
        for r in rows:
            out.extend(r[0] or [])
    return out


def read_knowledge(id: str, context: dict | None = None) -> dict:
    """Node properties + every visible assertion + its evidence.

    `other.name` is filled in for every neighbour, so the caller never has to
    guess what an id refers to from the id string alone.
    """
    t = now()
    row = g().query(TEMPLATES["read_entity"], {"id": id}).result_set
    if not row:
        return {"status": "not_found", "id": id,
                "note": f"{id} is not in the current graph; do not assume it exists"}
    props = dict(row[0][0])
    rows = g().query(TEMPLATES["read_neighbors"], {"id": id, "now": t}).result_set
    sup = _superseded(t)
    out = {
        "status": "ok", "id": id, "entity": props,
        "superseded": [{"superseded_by": r["superseded_by"], "aid": r["aid"],
                        "effective": r["effective"]} for r in sup.get(id, [])],
        "assertions": [], "evidence": [],
    }
    props_cond = props.get("condition")
    if props_cond:
        out["applicability"] = _eval_condition(
            props_cond, props.get("condition_key"), context)
    ev: set[str] = set()
    for (typ, outgoing, mid, mkind, mname, aid, auth, vf, vt,
         cond, ckey, penalty) in rows:
                rec = {"predicate": typ, "direction": "out" if outgoing else "in",
                       "other": {"id": mid, "kind": mkind, "name": mname},
                       "aid": aid, "authority": auth,
                       "valid_from": vf, "valid_to": vt,
                       "superseded": mid in sup}
                if cond:
                    rec["condition"] = cond
                    rec["applicability"] = _eval_condition(cond, ckey, context)
                if penalty:
                    rec["penalty"] = penalty
                out["assertions"].append(rec)
                ev.update(_rel_evidence(id, aid, typ, mid, outgoing))
    out["assertions"].sort(key=lambda a: (S.AUTH_RANK.get(a["authority"], 9),
                                          a["predicate"], a["other"]["id"]))
    out["evidence"] = _evidence(ev)
    # conflict detection over single-valued slots (design doc §3.6)
    by_slot: dict[tuple[str, str], list[dict]] = {}
    for a in out["assertions"]:
        if a["predicate"] in S.SINGLE_VALUED and a["direction"] == "out":
            by_slot.setdefault((a["predicate"], "out"), []).append(a)
    conflicts = []
    for (pred, _), vals in by_slot.items():
        if len(vals) > 1:
            conflicts.append({
                "predicate": pred,
                "values": [{"value": v["other"]["id"], "authority": v["authority"],
                            "aid": v["aid"]} for v in vals],
                "note": "multiple visible values for a single-valued slot; surfaced, not resolved"})
    if conflicts:
        out["conflicts"] = conflicts
    return out


# --------------------------------------------------------------------------
# 4. query_relations
# --------------------------------------------------------------------------
_DIRECTIONS = {"out": "-[rel:%s*1..%d]->", "in": "<-[rel:%s*1..%d]-",
               "both": "-[rel:%s*1..%d]-"}


def query_relations(start_id: str, predicates: list[str], direction: str = "out",
                    max_hops: int = 1, context: dict | None = None,
                    limit: int = 50) -> dict:
    """Traverse from an entity along registered predicates, all hops filtered."""
    if not predicates:
        return {"status": "error", "error": "predicates must be non-empty"}
    bad = [p for p in predicates if not S.is_registered(p)]
    if bad:
        return {"status": "error", "error": f"unregistered predicates {bad}",
                "registered": sorted(S.PREDICATES)}
    if direction not in _DIRECTIONS:
        return {"status": "error",
                "error": f"direction must be one of {sorted(_DIRECTIONS)}"}
    hops = max(1, min(int(max_hops), 3))
    t = now()
    pattern = _DIRECTIONS[direction] % ("|".join(predicates), hops)
    q = TEMPLATES["traverse"].replace("(x:Entity)", pattern + "(x:Entity)")
    rows = g().query(q, {"id": start_id, "now": t, "limit": int(limit)}).result_set
    sup = _superseded(t)
    paths, ev = [], set()
    for ids, kinds, names, types, aids, auths, evidences, vfs in rows:
        paths.append({
            "nodes": [{"id": i, "kind": k, "name": n} for i, k, n in zip(ids, kinds, names)],
            "edges": types, "aids": aids, "authority": auths, "valid_from": vfs,
            "superseded_nodes": [i for i in ids if i in sup],
        })
        for e in evidences or []:
            ev.update(e)
    paths.sort(key=lambda p: (len(p["edges"]), S.AUTH_RANK.get(
        (p["authority"] or ["observation"])[0], 9)))
    out = {
        "status": "ok" if paths else "not_found",
        "start_id": start_id, "predicates": predicates,
        "direction": direction, "max_hops": hops,
        "release_time": t, "paths": paths,
        "evidence": _evidence(ev), "truncated": len(rows) >= int(limit),
    }
    if not paths:
        out["note"] = ("no visible relation path in the current graph for these "
                       "predicates/direction; report not_found rather than guessing")
    return out


TOOL_NAMES = ["describe_ontology", "search_knowledge", "read_knowledge",
              "query_relations"]

TOOLS = {
    "describe_ontology": {
        "fn": describe_ontology,
        "description": "Return entity kinds, registered predicates (direction + meaning), authority levels and behaviour rules. Call first.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "search_knowledge": {
        "fn": search_knowledge,
        "description": "Find entity candidates (id/kind/name) by name. Use it to resolve an id before querying relations.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "name or keyword to search"},
            "kind": {"type": "string", "description": "optional entity kind filter, e.g. Module, Person, Procedure"},
            "limit": {"type": "integer", "description": "max candidates (default 12)"}},
            "required": ["query"]},
    },
    "read_knowledge": {
        "fn": read_knowledge,
        "description": "Read one entity: its properties plus all currently visible assertions with aid, authority, evidence and applicability.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "entity id from a previous tool result"},
            "context": {"type": "object", "description": "conditions for rule applicability, e.g. {\"severity\": \"P3\"}"}},
            "required": ["id"]},
    },
    "query_relations": {
        "fn": query_relations,
        "description": "Traverse 1-3 hops from an entity along registered predicates. Every hop is filtered by state/validity.",
        "parameters": {"type": "object", "properties": {
            "start_id": {"type": "string", "description": "start entity id from a previous tool result"},
            "predicates": {"type": "array", "items": {"type": "string"},
                           "description": "registered predicate names, e.g. [\"HAS_RESOURCE\"]"},
            "direction": {"type": "string", "enum": ["out", "in", "both"], "description": "default out"},
            "max_hops": {"type": "integer", "description": "1-3, default 1"},
            "context": {"type": "object", "description": "conditions for rule applicability"}},
            "required": ["start_id", "predicates"]},
    },
}


def call_tool(name: str, args: dict) -> dict:
    spec = TOOLS.get(name)
    if spec is None:
        return {"status": "error", "error": f"unknown tool '{name}'",
                "available": TOOL_NAMES}
    try:
        return spec["fn"](**(args or {}))
    except TypeError as e:
        return {"status": "error", "error": f"bad arguments for {name}: {e}"}
    except Exception as e:  # noqa: BLE001 - surfaced to the agent, not swallowed
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}
