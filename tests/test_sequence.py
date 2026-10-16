"""Offline sequence tests; graph access is always mocked."""
from types import SimpleNamespace
from unittest.mock import patch

from oncall_brain import tools


class Graph:
    def __init__(self, edges=(), steps=('a', 'b', 'c', 'z'), exists=True):
        self.edges, self.steps, self.exists = edges, steps, exists

    def query(self, query, params=None):
        assert tools.VISIBLE in query or 'Evidence' in query or 'RETURN p.id, p.name' in query
        if 'HAS_STEP' in query:
            rows = [['p', 'Process', s, s, 'has-'+s, ['e']] for s in self.steps]
        elif 'PRECEDES' in query:
            rows = [[a, b, pred, 'order', ['e']] for a, b, pred in self.edges]
        elif 'RETURN p.id, p.name' in query:
            rows = [['p', 'Process']] if self.exists else []
        elif 'Evidence' in query:
            rows = [['e', 'corpus/test.md', '4', '<source>', 'official']]
        else:
            rows = [['p', 'GOVERNED_BY', 'rule', 'Rule', 'Rule', 'r', ['e']],
                    ['a', 'PERFORMED_BY', 'person', 'Person', 'Person', 'who', ['e']]]
            rows = [r for r in rows if r[0] in params['ids']]
        return SimpleNamespace(result_set=rows)


def run(edges=(), **kwargs):
    from oncall_brain.sequence import procedure_steps
    with patch.object(tools, 'g', return_value=Graph(edges, **kwargs)):
        return procedure_steps('p')


def test_parallel_merge_topology_unknown_and_provenance():
    result = run([('a', 'b', 'PRECEDES'), ('b', 'c', 'PARALLEL_WITH')])
    assert [[s['id'] for s in layer['steps']] for layer in result['layers']] == [['a'], ['b', 'c'], ['z']]
    assert result['layers'][-1]['status'] == 'unknown'
    assert result['steps'][0]['assertions'][0]['aid'] == 'has-a'
    assert result['evidence'][0]['excerpt'] == '<source>'
    assert result['rules'][0]['id'] == 'rule'
    assert result['steps'][0]['performers'][0]['id'] == 'person'


def test_cycle_distinguishes_blocked_descendant_and_keeps_all_steps():
    result = run([('a', 'b', 'PRECEDES'), ('b', 'a', 'PRECEDES'), ('b', 'c', 'PRECEDES')])
    assert result['status'] == 'cycle'
    assert result['cycle_steps'] == ['a', 'b']
    assert result['blocked_steps'] == ['c']
    assert sum(len(l['steps']) for l in result['layers']) == 4


def test_parallel_precedence_contradiction_and_self_loop():
    assert run([('a', 'b', 'PARALLEL_WITH'), ('a', 'b', 'PRECEDES')])['cycle_steps'] == ['a', 'b']
    assert run([('a', 'a', 'PRECEDES')])['cycle_steps'] == ['a']


def test_parallel_component_waits_for_every_predecessor():
    result = run([('a', 'b', 'PRECEDES'), ('b', 'c', 'PARALLEL_WITH'),
                  ('z', 'c', 'PRECEDES')])
    assert [[s['id'] for s in l['steps']] for l in result['layers']] == [['a', 'z'], ['b', 'c']]
    assert result['layers'][1]['parallel_groups'] == [['b', 'c']]


def test_query_parameters_visibility_and_procedure_scope():
    from oncall_brain.sequence import procedure_steps
    fake = Graph()
    original = fake.query
    calls = []

    def query(cypher, params=None):
        calls.append((cypher, params))
        return original(cypher, params)

    fake.query = query
    pid = "p' OR true"
    with patch.object(tools, 'g', return_value=fake):
        procedure_steps(pid)
    assert all(pid not in q for q, _ in calls)
    assert calls[0][1]['id'] == pid
    for q, params in calls:
        if 'HAS_STEP' in q:
            assert 'p.id = $id' in q
            assert params['id'] == pid
        if 'PRECEDES' in q:
            assert 'a.id IN $ids AND b.id IN $ids' in q


def test_unknown_empty_missing_and_duplicate_assertions():
    assert all(l['status'] == 'unknown' for l in run()['layers'])
    assert run(steps=[])['layers'] == []
    assert run(steps=[], exists=False)['status'] == 'not_found'
    result = run([('a', 'b', 'PRECEDES')]*2, steps=('a', 'a', 'b'))
    assert len(result['steps']) == 2
    assert result['precedence_count'] == 1


def test_list_and_routes_and_safe_errors():
    from oncall_brain.sequence import list_procedures
    from web.app import app
    with patch.object(tools, 'g', return_value=Graph([('a', 'b', 'PRECEDES')])):
        assert list_procedures()[0]['step_count'] == 4
        assert list_procedures()[0]['has_order'] is True
        client = app.test_client()
        assert client.get('/api/procedures').status_code == 200
        assert client.get('/api/procedure/p/steps').json['procedure']['id'] == 'p'
    with patch.object(tools, 'g', return_value=Graph(steps=[], exists=False)):
        assert client.get('/api/procedure/missing/steps').status_code == 404
    with patch.object(tools, 'g', side_effect=RuntimeError('secret')):
        response = client.get('/api/procedures')
        assert response.status_code == 503
        assert b'secret' not in response.data
