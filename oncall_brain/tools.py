"""Four read-only tools (read-only MCP contract): describe / search / read / query_relations.

All Cypher is generated server-side from templates -- the model only chooses
predicates, direction and hop count, never writes Cypher. Every hop applies the
visibility predicate:

    r.state='approved' AND r.valid_from <= $now AND (r.valid_to IS NULL OR r.valid_to > $now)

An entity targeted by a visible SUPERSEDES edge is marked superseded and kept
out of current answers, but its replacement chain is exposed.
"""
from __future__ import annotations

import datetime
import os
import json
import re

from . import schema as S
from .db import g

VISIBLE = ("r.state = 'approved' AND r.valid_from <= $now "
           "AND (r.valid_to IS NULL OR r.valid_to > $now)")

# A claim condition key that qualifies *what* the value is about (`violation`,
# `violation_type`, `metric`, ...) rather than *when/where* it applies
# (`environment`, `region`, ...). Values under different qualifiers are a scale
# quoted per subject, not competing answers in one slot. See _qualifiers.
_CONDITION_QUALIFIERS = re.compile(r"(?:^|_)(?:violation|violation_type|offense|item|metric|measure|category|level|severity|grade|tier|class|case|reason|kind|type)$",
                                   re.I)

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
        "r.aid, r.authority, r.valid_from, r.valid_to, r.condition, r.condition_key, r.penalty, r.evidence "),
    "superseded_targets": (
        "MATCH (n)-[r:SUPERSEDES]->(o) WHERE " + VISIBLE + " "
        "RETURN o.id, n.id, r.aid, r.valid_from"),
    "traverse": (
        "MATCH p = (s:Entity {id: $id})"  # pattern injected below
        "(x:Entity) WHERE all(rel IN relationships(p) WHERE " + VISIBLE.replace("r.", "rel.") + ") "
        "RETURN [n IN nodes(p) | n.id], [n IN nodes(p) | n.kind], [n IN nodes(p) | n.name], "
        "[rel IN relationships(p) | type(rel)], [rel IN relationships(p) | rel.aid], "
        "[rel IN relationships(p) | rel.authority], [rel IN relationships(p) | rel.evidence], "
        "[rel IN relationships(p) | rel.valid_from], "
        "[rel IN relationships(p) | rel.condition], [rel IN relationships(p) | rel.condition_key] "
        "LIMIT $limit"),
    "evidence_by_id": (
        "MATCH (e:Evidence) WHERE e.id IN $ids "
        "RETURN e.id, e.source, e.locator, e.excerpt, e.authority"),
    "source_search": (
        "MATCH (e:Evidence) WHERE toLower(e.excerpt) CONTAINS toLower($q) "
        "RETURN e.id, e.source, e.locator, e.excerpt, e.authority "
        "ORDER BY e.source, e.locator LIMIT $limit"),
    "claim_value_kinds": (
        "MATCH (n:Entity) WHERE n.id IN $values OR n.name IN $values "
        "RETURN n.id, n.name, n.kind"),
    "slot_claims": (
        "MATCH (s:Entity)-[r]->(o:Entity) WHERE " + VISIBLE + " "
        "AND r.claims_json IS NOT NULL "
        "AND (s.id IN $ids OR o.id IN $ids OR any(x IN r.claim_subjects WHERE x IN $ids)) "
        "RETURN s.id, o.id, r.aid, r.authority, r.valid_from, r.valid_to, r.claims_json, r.evidence"),
}


def _exclusive_values(left: dict, right: dict) -> bool:
    """Different text alone does not prove that two values are exclusive."""
    pred = left["predicate"]
    if any(c.get("cardinality") in ("many", "multi") for c in (left, right)):
        return False
    if pred in S.PREDICATES and pred not in S.SINGLE_VALUED:
        return False
    def kind(c):
        return str(c.get("value_kind") or (c["value"].split(":", 1)[0]
                   if ":" in c["value"] else "")).lower()
    lk, rk = kind(left), kind(right)
    if lk != rk:
        return False
    if pred in S.SINGLE_VALUED or all(c.get("cardinality") == "one" for c in (left, right)):
        return True
    # Canonical scalar measurements and scalar routing duties are comparable.
    scalar = r"[+-]?\d+(?:\.\d+)?\s*([^\d\s].*)?"
    lm, rm = (re.fullmatch(scalar, c["value"].strip()) for c in (left, right))
    if lm and rm and lm[1] == rm[1]:
        return True
    return bool(re.search(r"(?:^|_)(?:routing|owner|agent)$", pred) and lk == rk)


def _qualifiers(c: dict) -> dict:
    """Condition entries naming *what* a value is about, not when/where it holds.

    `penalty_points` is the case that forces this: "-10 pts for closing without
    user verification" and "-2 pts for typos" (one line,
    corpus/chats/chat_2026-09-24.md:27) are one scale priced per violation, not
    two competing answers. Only keys whose name marks them as a qualifier count
    (see _CONDITION_QUALIFIERS); everything else keeps the previous behaviour, so
    this can only remove comparisons, never invent them.
    """
    return {str(k): str(v) for k, v in (c.get("condition") or {}).items()
            if _CONDITION_QUALIFIERS.search(str(k)) and str(v).strip()}


def _different_measurement_subject(left: dict, right: dict) -> bool:
    """True when two claims price/measure different things, so they cannot clash.

    Both must be qualified: a bare "-5 pts" claim still disagrees with a
    "-2 pts for typos" claim about the unqualified case, so it stays comparable
    against every sub-slot rather than being filed under its own.
    """
    lq, rq = _qualifiers(left), _qualifiers(right)
    return bool(lq) and bool(rq) and lq != rq


def compare_claims(claims: list[dict], context: dict | None = None) -> list[dict]:
    """Compare one semantic slot only where equality conditions and dates overlap."""
    by_slot = {}
    for c in claims:
        cond = c.get("condition") or {}
        if any(k in (context or {}) and str(context[k]) != str(v) for k, v in cond.items()):
            continue
        by_slot.setdefault((c["subject"], c["predicate"]), []).append(c)
    conflicts = []
    for (subject, predicate), values in by_slot.items():
        competing = []
        for i, left in enumerate(values):
            for right in values[i + 1:]:
                if left["value"] == right["value"]:
                    continue
                if _different_measurement_subject(left, right):
                    continue
                if not _exclusive_values(left, right):
                    continue
                lc, rc = left.get("condition") or {}, right.get("condition") or {}
                if any(str(lc[k]) != str(rc[k]) for k in lc.keys() & rc.keys()):
                    continue
                if max(left.get("valid_from") or "", right.get("valid_from") or "") >= min(
                        left.get("valid_to") or "9999-12-31", right.get("valid_to") or "9999-12-31"):
                    continue
                for c in (left, right):
                    if c not in competing:
                        competing.append(c)
        if competing:
            competing.sort(key=lambda c: (S.AUTH_RANK.get(c.get("authority"), 9), c["aid"]))
            conflicts.append({"subject": subject, "predicate": predicate, "values": competing,
                "note": "different visible values in an overlapping semantic slot; surfaced, not resolved"})
    return conflicts


def _slot_conflicts(ids: list[str], t: str, sup: dict, context: dict | None) -> tuple[list, set]:
    claims, evidence_ids = [], set()
    rows = g().query(TEMPLATES["slot_claims"], {"ids": ids, "now": t}).result_set
    for subject, obj, aid, authority, vf, vt, raw, ev in rows:
        if subject in sup or obj in sup:
            continue
        for c in json.loads(raw):
            if c["subject"] not in ids:
                continue
            claims.append({**c, "aid": aid, "authority": authority,
                           "valid_from": vf, "valid_to": vt, "evidence": [c["evidence_id"]]})
    kinds = {}
    if claims:
        values = sorted({c["value"] for c in claims})
        for nid, name, kind in g().query(TEMPLATES["claim_value_kinds"], {"values": values}).result_set:
            if kind:
                kinds[nid] = kinds[name] = kind
    for claim in claims:
        if claim["value"] in kinds:
            claim.setdefault("value_kind", kinds[claim["value"]])
    conflicts = compare_claims(claims, context)
    for conflict in conflicts:
        for value in conflict["values"]:
            evidence_ids.update(value["evidence"])
    return conflicts, evidence_ids


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
def _with_context(rows, before: int = 2, after: int = 8) -> list[dict]:
    """Matching source lines plus the surrounding lines of the same thread."""
    keys = ["id", "source", "locator", "excerpt", "authority"]
    hits = [dict(zip(keys, row)) for row in rows]
    ids = []
    for h in hits:
        n = int(h["locator"])
        ids += [f"ev:{h['source']}:{i}" for i in range(max(1, n - before), n + after + 1)]
    if not ids:
        return hits
    near = g().query(TEMPLATES["evidence_by_id"], {"ids": sorted(set(ids))}).result_set
    out = {h["id"]: h for h in hits}
    for row in near:
        out.setdefault(row[0], dict(zip(keys, row)))
    return sorted(out.values(), key=lambda e: (e["source"], int(e["locator"])))[:24]


def _source_rows(q: str, limit: int) -> list:
    """Verbatim source matches, official documents first and chat after, so the
    alphabetical order of file names does not hide every Doc behind chats."""
    rows = g().query(TEMPLATES["source_search"], {"q": q, "limit": 200}).result_set
    docs = [r for r in rows if r[4] == "formal_rule"]
    rest = [r for r in rows if r[4] != "formal_rule"]
    half = max(1, limit // 2)
    picked = docs[:half] + rest[:limit - min(half, len(docs))]
    return picked[:limit]


_SYNONYMS = {"tier": "l", "escalate": "escalat", "escalation": "escalat",
             "escalating": "escalat", "message": "chat", "messaging": "chat",
             "ping": "chat", "dm": "chat"}
_QSTOP = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "my", "our", "is",
          "it", "i", "we", "do", "what", "where", "when", "who", "how", "should", "can",
          "if", "this", "that", "with", "they", "me", "be", "are", "not", "which", "does",
          "there", "their", "them", "from", "at", "by", "as", "any", "into", "out", "about"}
_LINES: list[dict] | None = None


def _terms(text: str) -> list[str]:
    text = re.sub(r"\btier\s*(\d)\b", r"l\1", text.casefold())
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9_.-]*[a-z0-9]|[a-z0-9]", text):
        w = _SYNONYMS.get(w, w)
        if w not in _QSTOP and len(w) > 1:
            out.append(w[:7] if w.isalpha() else w)
    return out


def related_source_lines(question: str, k: int = 8) -> list[dict]:
    """Source lines sharing the most informative words with the question
    (IDF-weighted), each with the lines that follow it in the same thread."""
    global _LINES
    import math
    if _LINES is None:
        rows = g().query("MATCH (e:Evidence) RETURN e.id, e.source, e.locator, "
                         "e.excerpt, e.authority").result_set
        _LINES = [dict(zip(["id", "source", "locator", "excerpt", "authority"], r),
                       terms=set(_terms(r[3] or ""))) for r in rows]
    q = set(_terms(question))
    if not q or not _LINES:
        return []
    df = {t: sum(t in e["terms"] for e in _LINES) for t in q}
    n = len(_LINES)
    scored = []
    for e in _LINES:
        hit = q & e["terms"]
        if len(hit) >= 2:
            s = sum(math.log(n / (1 + df[t])) for t in hit)
            scored.append((s, e))
    scored.sort(key=lambda x: -x[0])
    rows = [(e["id"], e["source"], e["locator"], e["excerpt"], e["authority"])
            for _, e in scored[:k]]
    records = [r for r in rows if r[1].endswith(".jsonl")]  # one record per line
    prose = [r for r in rows if not r[1].endswith(".jsonl")]
    out = _with_context(prose, before=1, after=3)
    return out + [dict(zip(["id", "source", "locator", "excerpt", "authority"], r))
                  for r in records]


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
        sources = _with_context(_source_rows(q, int(limit)))
        if sources:
            return {"status": "ok", "answer_status": "applicable", "query": q,
                    "candidates": [], "source_evidence": sources}
        return {"status": "not_found", "answer_status": "not_found", "query": q, "candidates": [],
                "note": "no entity in the current graph matches; do not guess an id"}
    # Exact phrases (error codes, component names) may also appear verbatim in
    # source lines that no entity name carries; surface those lines too.
    sources = _with_context(_source_rows(q, 6))
    return {"status": "ok", "query": q, "candidates": out,
            **({"source_evidence": sources} if sources else {})}


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


def _nearby_sources(props: dict, evidence: list[dict], limit: int = 24) -> list[dict]:
    """Source lines an assertion's own evidence line leaves out: the lines just
    after it in the same thread (enumerations, conditions, penalties) and other
    lines naming this entity verbatim."""
    keys = ["id", "source", "locator", "excerpt", "authority"]
    evidence = [e for e in evidence if isinstance(e, dict)]
    have = {e["id"] for e in evidence}
    ids = []
    for e in evidence:
        if str(e["locator"]).isdigit():
            n = int(e["locator"])
            ids += [f"ev:{e['source']}:{i}" for i in range(max(1, n - 1), n + 4)]
    rows = g().query(TEMPLATES["evidence_by_id"], {"ids": sorted(set(ids))}).result_set if ids else []
    near = [dict(zip(keys, r)) for r in rows]
    name = (props.get("name") or "").strip()
    if len(name) >= 8:
        near += _with_context(_source_rows(name, 6), before=1, after=3)
    out, seen = [], set(have)
    for e in sorted(near, key=lambda e: (e["source"], int(e["locator"]))):
        if e["id"] not in seen and (e.get("excerpt") or "").strip():
            seen.add(e["id"])
            out.append(e)
    return out[:limit]


def read_knowledge(id: str, context: dict | None = None) -> dict:
    """Node properties + every visible assertion + its evidence.

    `other.name` is filled in for every neighbour, so the caller never has to
    guess what an id refers to from the id string alone.
    """
    t = now()
    row = g().query(TEMPLATES["read_entity"], {"id": id}).result_set
    if not row:
        return {"status": "not_found", "answer_status": "not_found", "id": id,
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
    for row in rows:
                # Evidence ids come back with the neighbour row; one query per
                # assertion over a remote link turned a read into seconds.
                (typ, outgoing, mid, mkind, mname, aid, auth, vf, vt,
                 cond, ckey, penalty) = row[:12]
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
                rec["evidence"] = (list(row[12] or []) if len(row) > 12
                                   else _rel_evidence(id, aid, typ, mid, outgoing))
                ev.update(rec["evidence"])
    out["assertions"].sort(key=lambda a: (S.AUTH_RANK.get(a["authority"], 9),
                                          a["predicate"], a["other"]["id"]))
    # conflict detection over single-valued slots
    by_slot: dict[tuple[str, str], list[dict]] = {}
    for a in out["assertions"]:
        if a["predicate"] in S.SINGLE_VALUED and a["direction"] == "out":
            by_slot.setdefault((a["predicate"], "out"), []).append(a)
    relation_claims = [dict(a, subject=id, value=a["other"]["id"],
                            condition=dict([a["condition"].split("=", 1)])
                            if "=" in (a.get("condition") or "") else {})
                       for vals in by_slot.values() for a in vals if not a.get("superseded")]
    conflicts = compare_claims(relation_claims, context)
    semantic, extra_ev = _slot_conflicts([id],
                                       t, sup, context)
    conflicts.extend(semantic)
    ev.update(extra_ev)
    out["evidence"] = _evidence(ev)
    nearby = _nearby_sources(props, out["evidence"])
    if nearby:
        out["source_evidence"] = nearby
    if conflicts:
        out["conflicts"] = conflicts
    out["answer_status"] = ("conflict" if conflicts else "superseded" if out["superseded"]
        or any(a["predicate"] == "SUPERSEDES" for a in out["assertions"])
        else "insufficient_context" if out.get("applicability") == "insufficient_context"
        or any(a.get("applicability") == "insufficient_context" for a in out["assertions"])
        else "applicable" if out["assertions"] else "not_found")
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
    for ids, kinds, names, types, aids, auths, evidences, vfs, conditions, keys in rows:
        applicability = [_eval_condition(c, k, context) for c, k in zip(conditions, keys)]
        if "inapplicable" in applicability:
            continue
        paths.append({
            "nodes": [{"id": i, "kind": k, "name": n} for i, k, n in zip(ids, kinds, names)],
            "edges": types, "aids": aids, "authority": auths, "valid_from": vfs,
            "evidence_ids": evidences,
            "conditions": conditions, "applicability": applicability,
            "superseded_nodes": [i for i in ids if i in sup],
        })
        for e in evidences or []:
            ev.update(e)
    paths.sort(key=lambda p: (len(p["edges"]), S.AUTH_RANK.get(
        (p["authority"] or ["observation"])[0], 9)))
    conflicts, extra_ev = _slot_conflicts(
        sorted({start_id} | {n["id"] for p in paths for n in p["nodes"]}), t, sup, context)
    ev.update(extra_ev)
    out = {
        "status": "ok" if paths else "not_found",
        "start_id": start_id, "predicates": predicates,
        "direction": direction, "max_hops": hops,
        "release_time": t, "paths": paths,
        "evidence": _evidence(ev), "truncated": len(rows) >= int(limit),
        "conflicts": conflicts,
    }
    if not paths:
        out["note"] = ("no visible relation path in the current graph for these "
                       "predicates/direction; report not_found rather than guessing")
    out["answer_status"] = ("conflict" if conflicts else "superseded" if any(p["superseded_nodes"] or "SUPERSEDES" in p["edges"] for p in paths)
                            else "insufficient_context" if any("insufficient_context" in p["applicability"] for p in paths)
                            else "applicable" if paths else "not_found")
    return out


def module_expertise(module_id: str, limit: int = 8) -> dict:
    """Who has actually handled this module's tickets, next to who officially owns it.

    Two hops the text alone cannot join: Ticket -ABOUT-> Module and
    Ticket -HANDLED_BY-> Person, ranked by tickets handled.
    """
    t = now()
    vis = lambda v: VISIBLE.replace("r.", v + ".")
    rows = g().query(
        "MATCH (k:Entity)-[a:ABOUT]->(m:Entity {id: $id}), (k)-[h:HANDLED_BY]->(p:Entity) "
        "WHERE " + vis("a") + " AND " + vis("h") + " "
        "RETURN k.id, k.kind, k.name, m.kind, m.name, p.id, p.kind, p.name, "
        "a.aid, a.authority, a.evidence, h.aid, h.authority, h.evidence",
        {"id": module_id, "now": t}).result_set
    owners = g().query(
        "MATCH (p:Entity)-[r:TECH_LEAD_OF|MAINTAINED_BY]-(m:Entity {id: $id}) WHERE " + VISIBLE + " "
        "RETURN type(r), p.id, p.name, r.aid", {"id": module_id, "now": t}).result_set
    paths, ev, people, seen = [], set(), {}, set()
    for kid, kk, kn, mk, mn, pid, pk, pn, aaid, aauth, aev, haid, hauth, hev in rows:
        if kid not in seen:
            seen.add(kid)
            paths.append({"nodes": [{"id": kid, "kind": kk, "name": kn}, {"id": module_id, "kind": mk, "name": mn}],
                          "edges": ["ABOUT"], "aids": [aaid], "authority": [aauth],
                          "evidence_ids": [aev], "superseded_nodes": []})
            ev.update(aev or [])
        paths.append({"nodes": [{"id": kid, "kind": kk, "name": kn}, {"id": pid, "kind": pk, "name": pn}],
                      "edges": ["HANDLED_BY"], "aids": [haid], "authority": [hauth],
                      "evidence_ids": [hev], "superseded_nodes": []})
        ev.update(hev or [])
        people.setdefault(pid, {"id": pid, "name": pn, "tickets": set()})["tickets"].add(kid)
    handlers = sorted(({**v, "tickets": sorted(v["tickets"]), "count": len(v["tickets"])}
                       for v in people.values()), key=lambda v: (-v["count"], v["id"]))[:limit]
    owner_ids = {o[1] for o in owners}
    for h in handlers:
        h["is_owner"] = h["id"] in owner_ids
    return {"status": "ok" if rows else "not_found", "start_id": module_id,
            "direction": "out", "predicates": ["ABOUT", "HANDLED_BY"],
            "owners": list({(p, i): {"predicate": p, "id": i, "name": n, "aid": aid}
                            for p, i, n, aid in reversed(owners)}.values()),
            "handlers": handlers, "tickets": len(seen), "paths": paths,
            "evidence": _evidence(ev), "release_time": t,
            "answer_status": "applicable" if rows else "not_found",
            "note": ("handlers = people on this module's past tickets (history, not ownership); "
                     "owners = official tech lead / maintainer")}


TOOL_NAMES = ["describe_ontology", "search_knowledge", "read_knowledge",
              "query_relations", "module_expertise"]

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
    "module_expertise": {
        "fn": module_expertise,
        "description": "For a Module: who has actually handled its past tickets (Ticket-ABOUT->Module, Ticket-HANDLED_BY->Person), next to its official tech lead/maintainer. Use for 'who should I ask / who knows this module'.",
        "parameters": {"type": "object", "properties": {
            "module_id": {"type": "string", "description": "Module id, e.g. MOD-01"}},
            "required": ["module_id"]},
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
