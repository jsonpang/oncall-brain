"""Isolated, reversible notice injection; production graphs are read-only here."""
from __future__ import annotations

import datetime
import json
import re

from falkordb.graph import Graph
from . import tools


def _demo(name):
    if not isinstance(name, str) or not re.fullmatch(r'[\w-]+_demo', name):
        raise ValueError('graph must be a named _demo graph')
    return name


def _graph(name):
    base = tools.g()
    return base if base.name == name else Graph(base.client, name)


def clone_demo_graph(src, dst):
    _demo(dst)
    if not isinstance(src, str) or not src or src == dst:
        raise ValueError('source and destination must be distinct')
    # GRAPH.COPY refuses an existing destination; never overwrite a demo either.
    tools.g().execute_command('GRAPH.COPY', src, dst)
    return {'status': 'ok', 'source': src, 'graph': dst}


def reset_demo(graph):
    _demo(graph)
    tools.g().execute_command('GRAPH.DELETE', graph)
    return {'status': 'ok', 'deleted': graph}


def add_notice(graph, notice_id, name, text, supersedes_id, effective,
               source='corpus/notices/live-demo.md'):
    _demo(graph)
    for value in (notice_id, name, text, supersedes_id, effective, source):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('notice fields must be nonempty strings')
    if not notice_id.startswith('Notice:') or notice_id == supersedes_id:
        raise ValueError('a distinct Notice: id is required')
    if datetime.date.fromisoformat(effective).isoformat() != effective:
        raise ValueError('effective must be YYYY-MM-DD')
    target = _graph(graph)
    kinds = target.query('MATCH (n:Entity {id: $id}) RETURN n.kind',
                         {'id': supersedes_id}).result_set
    if not kinds or kinds[0][0] not in ('Rule', 'Notice'):
        raise ValueError('supersedes_id must identify an existing Rule or Notice')
    eid = f'ev:{source}:{notice_id}'
    if target.query('MATCH (n) WHERE n.id IN $ids RETURN n.id',
                    {'ids': [notice_id, eid]}).result_set:
        raise ValueError('notice or evidence id already exists')
    auth = target.query("MATCH (n:Entity {kind: 'Notice'})-[r:SUPERSEDES]->() "
                        "WHERE r.state = 'approved' RETURN r.authority ORDER BY n.id LIMIT 1").result_set
    if not auth or not auth[0][0]:
        raise ValueError('no approved existing notice authority available')
    authority = auth[0][0]
    # A replacement can carry only SUPERSEDES: recover its inherited scope too.
    pending, seen, contexts = [supersedes_id], set(), {}
    while pending:
        old = pending.pop()
        if old in seen:
            continue
        seen.add(old)
        rows = target.query('MATCH (n:Entity {id: $id})-[r]-(m:Entity) WHERE ' + tools.VISIBLE +
            ' RETURN type(r), startNode(r).id = $id, m.id, properties(r)',
            {'id': old, 'now': effective}).result_set
        for typ, outgoing, other, props in rows:
            if typ == 'SUPERSEDES' and outgoing:
                pending.append(other)
            elif typ in ('APPLIES_TO', 'GOVERNED_BY'):
                contexts.setdefault((typ, outgoing, other), props)
    edge = dict(aid=f'demo:{notice_id}:supersedes', authority=authority,
                state='approved', valid_from=effective, evidence=[eid],
                claims_json='[]', claim_subjects=[])
    params = dict(old=supersedes_id, nid=notice_id, eid=eid, name=name,
                  text=text, source=source, authority=authority, edge=edge)
    # All writes in one graph query: no half-created notice on an error.
    query = 'MATCH (old:Entity {id: $old}) '
    for i, (_, _, other) in enumerate(contexts):
        query += f'MATCH (m{i}:Entity {{id: $m{i}}}) '
        params[f'm{i}'] = other
    query += ('CREATE (n:Entity:Notice {id: $nid, kind: \'Notice\', name: $name, text: $text}) '
              'CREATE (e:Evidence {id: $eid, source: $source, locator: "1", '
              'excerpt: $text, authority: $authority}) '
              'CREATE (n)-[s:SUPERSEDES]->(old) SET s = $edge ')
    for i, ((typ, outgoing, _), props) in enumerate(contexts.items()):
        # Preserve applicability, never copy old values, expiry, penalties or evidence.
        copied = {k: props[k] for k in ('condition', 'condition_key') if k in props}
        copied.update(edge, aid=f'demo:{notice_id}:context:{i}')
        params[f'p{i}'] = copied
        pattern = f'(n)-[c{i}:{typ}]->(m{i})' if outgoing else f'(m{i})-[c{i}:{typ}]->(n)'
        query += f'CREATE {pattern} SET c{i} = $p{i} '
    rows = target.query(query + 'RETURN n.id', params).result_set
    if not rows:
        raise ValueError('target or context disappeared; no notice written')
    return dict(status='ok', graph=graph, notice_id=notice_id, evidence_id=eid,
                supersedes_id=supersedes_id, effective=effective,
                authority=authority, copied_relations=len(contexts))
