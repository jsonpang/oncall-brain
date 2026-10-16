"""Evidence-backed procedure layers. Only visible assertions are considered.

Parallel components are contracted before Kahn sorting. A layer is a topological
frontier, not evidence that every pair in it is explicitly parallel. Unknown
steps are appended without assigning them an execution position.
"""
from . import tools


def _members(procedure_id=None):
    return tools.g().query(
        "MATCH (p:Entity)-[r:HAS_STEP]->(s:Entity) WHERE p.kind = 'Procedure' AND "
        + tools.VISIBLE + (" AND p.id = $id" if procedure_id is not None else "")
        + " RETURN p.id, p.name, s.id, s.name, r.aid, r.evidence",
        {"now": tools.now(), "id": procedure_id}).result_set


def _ordering(ids):
    if not ids:
        return []
    return tools.g().query(
        "MATCH (a:Entity)-[r]->(b:Entity) WHERE type(r) IN ['PRECEDES', 'PARALLEL_WITH'] AND "
        + tools.VISIBLE + " AND a.id IN $ids AND b.id IN $ids "
        "RETURN a.id, b.id, type(r), r.aid, r.evidence",
        {"now": tools.now(), "ids": sorted(ids)}).result_set


def list_procedures() -> list[dict]:
    procedures = {}
    for pid, name, sid, *_ in _members():
        procedures.setdefault(pid, {"id": pid, "name": name, "steps": set()})['steps'].add(sid)
    ids = {s for p in procedures.values() for s in p['steps']}
    edges = _ordering(ids)
    result = []
    for p in procedures.values():
        own = [(a, b, pred) for a, b, pred, *_ in edges if a in p['steps'] and b in p['steps']]
        result.append({"id": p['id'], "name": p['name'], "step_count": len(p['steps']),
                       "has_order": bool(own),
                       "precedence_count": len({(a, b) for a, b, pred in own if pred == 'PRECEDES'})})
    return sorted(result, key=lambda p: (-p['precedence_count'], p['id']))


def procedure_steps(procedure_id: str) -> dict:
    root = tools.g().query(
        "MATCH (p:Entity {id: $id}) WHERE p.kind = 'Procedure' RETURN p.id, p.name",
        {"id": procedure_id}).result_set
    if not root:
        return {"status": "not_found", "procedure": None, "steps": [], "layers": []}
    steps, evidence_ids = {}, set()

    def assertion(aid, ev, **extra):
        ev = [ev] if isinstance(ev, str) else list(ev or [])
        evidence_ids.update(ev)
        return {"aid": aid, "evidence": ev, **extra}

    for _, _, sid, name, aid, ev in _members(procedure_id):
        step = steps.setdefault(sid, {"id": sid, "name": name, "assertions": [], "performers": []})
        value = assertion(aid, ev, predicate='HAS_STEP')
        if value not in step['assertions']:
            step['assertions'].append(value)
    relations = []
    for a, b, pred, aid, ev in _ordering(steps):
        value = assertion(aid, ev, source=a, target=b, predicate=pred)
        relations.append(value)
        for sid in {a, b}:
            steps[sid]['assertions'].append(value)
    metadata = {k: [] for k in ('rules', 'resources', 'performers', 'required_fields')}
    keys = {'GOVERNED_BY': 'rules', 'USES_RESOURCE': 'resources',
            'PERFORMED_BY': 'performers', 'REQUIRES_FIELD': 'required_fields'}
    rows = tools.g().query(
        "MATCH (s:Entity)-[r]->(o:Entity) WHERE s.id IN $ids AND " + tools.VISIBLE
        + " AND type(r) IN ['GOVERNED_BY', 'USES_RESOURCE', 'PERFORMED_BY', 'REQUIRES_FIELD'] "
        "RETURN s.id, type(r), o.id, o.name, o.kind, r.aid, r.evidence",
        {"ids": [procedure_id, *steps], "now": tools.now()}).result_set
    for subject, pred, oid, name, kind, aid, ev in rows:
        item = assertion(aid, ev, id=oid, name=name, kind=kind, subject=subject)
        if subject == procedure_id:
            metadata[keys[pred]].append(item)
        elif pred == 'PERFORMED_BY':
            steps[subject]['performers'].append(item)

    parent = {s: s for s in steps}

    def find(s):
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s

    for r in relations:
        if r['predicate'] == 'PARALLEL_WITH':
            parent[find(r['target'])] = find(r['source'])
    groups = {}
    for s in sorted(steps):
        groups.setdefault(find(s), []).append(s)
    adj = {g: set() for g in groups}
    touched = set()
    for r in relations:
        a, b = find(r['source']), find(r['target'])
        touched.update((a, b))
        if r['predicate'] == 'PRECEDES':
            adj[a].add(b)
    indegree = {g: 0 for g in groups}
    for targets in adj.values():
        for b in targets:
            indegree[b] += 1
    layers = []

    def layer(gs, status):
        members = sorted(s for g in gs for s in groups[g])
        layers.append({"number": len(layers) + 1, "status": status,
                       "label": '顺序未知' if status == 'unknown' else status,
                       "parallel_groups": [groups[g] for g in sorted(gs) if len(groups[g]) > 1],
                       "steps": [steps[s] for s in members]})

    remaining = set(touched)
    while remaining:
        ready = sorted(g for g in remaining if indegree[g] == 0)
        if not ready:
            break
        layer(ready, 'ordered')
        remaining.difference_update(ready)
        for g in ready:
            for b in adj[g]:
                indegree[b] -= 1
    cyclic = set()
    for g in remaining:
        pending, seen = list(adj[g]), set()
        while pending:
            node = pending.pop()
            if node == g:
                cyclic.add(g)
                break
            if node not in seen:
                seen.add(node)
                pending.extend(adj[node])
    if cyclic:
        layer(cyclic, 'cycle')
    blocked = remaining - cyclic
    if blocked:
        layer(blocked, 'blocked')
    unknown = set(groups) - touched
    if unknown:
        layer(unknown, 'unknown')
    return {"status": 'cycle' if cyclic else 'ok',
            "procedure": {"id": root[0][0], "name": root[0][1]},
            "steps": [s for l in layers for s in l['steps']], "layers": layers,
            "cycle_steps": sorted(s for g in cyclic for s in groups[g]),
            "blocked_steps": sorted(s for g in blocked for s in groups[g]),
            "precedence_count": len({(r['source'], r['target']) for r in relations if r['predicate'] == 'PRECEDES'}),
            "relations": relations, **metadata, "evidence": tools._evidence(sorted(evidence_ids))}
