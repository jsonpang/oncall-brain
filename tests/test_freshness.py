import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from oncall_brain import tools


def test_demo_guards_before_connection():
    from oncall_brain import freshness as f
    with patch.object(tools, 'g') as connect:
        for action in (lambda: f.clone_demo_graph('prod', 'prod'),
                       lambda: f.reset_demo('prod'),
                       lambda: f.add_notice('prod', 'Notice:x', 'x', 'x', 'old', '2026-10-16')):
            with pytest.raises(ValueError):
                action()
        connect.assert_not_called()


def test_clone_and_reset_target_only_demo():
    from oncall_brain import freshness as f
    base = Mock(name='base')
    base.name = 'prod'
    with patch.object(tools, 'g', return_value=base):
        f.clone_demo_graph('prod', 'fresh_demo')
        base.execute_command.assert_called_once_with('GRAPH.COPY', 'prod', 'fresh_demo')
        f.reset_demo('fresh_demo')
        base.execute_command.assert_called_with('GRAPH.DELETE', 'fresh_demo')


class FakeGraph:
    name = 'fresh_demo'

    def __init__(self):
        self.writes = []

    def query(self, query, params=None):
        if 'CREATE (n:Entity:Notice' in query:
            self.writes.append((query, params))
            return SimpleNamespace(result_set=[['Notice:new']])
        if 'kind: \'Notice\'' in query:
            return SimpleNamespace(result_set=[['practice']])
        if 'RETURN n.kind' in query:
            return SimpleNamespace(result_set=[['Rule']])
        if 'RETURN type(r)' in query:
            return SimpleNamespace(result_set=[['APPLIES_TO', True, 'Stage:Step',
                {'condition': 'tier=default', 'claims_json': '[]'}],
                ['GOVERNED_BY', False, 'Procedure:Run', {'claims_json': '[]'}]])
        if 'RETURN n.id' in query:
            return SimpleNamespace(result_set=[])
        raise AssertionError(query)


def test_notice_atomic_context_and_new_evidence():
    from oncall_brain import freshness as f
    graph = FakeGraph()
    with patch.object(tools, 'g', return_value=graph):
        result = f.add_notice('fresh_demo', 'Notice:new', 'Timeout 30 minutes',
                              'Default timeout is 30 minutes.', 'Rule:old', '2026-10-16')
    assert result['copied_relations'] == 2
    assert len(graph.writes) == 1
    query, params = graph.writes[0]
    assert '(n)-[c0:APPLIES_TO]->(m0)' in query
    assert '(m1)-[c1:GOVERNED_BY]->(n)' in query
    assert params['edge']['state'] == 'approved'
    assert params['edge']['authority'] == 'practice'
    assert params['edge']['evidence'] == [result['evidence_id']]
    assert params['p0']['condition'] == 'tier=default'
    assert '120' not in params['p0']['claims_json']


def test_future_and_invalid_dates():
    from oncall_brain import freshness as f
    with patch.object(tools, 'g') as connect:
        with pytest.raises(ValueError):
            f.add_notice('fresh_demo', 'Notice:x', 'x', 'x', 'old', 'yesterday')
        connect.assert_not_called()


def test_routes_disabled_and_validation():
    from web.app import app
    client = app.test_client()
    with patch.dict(os.environ, {'DEMO_GRAPH': 'prod'}):
        assert client.post('/api/demo/notice', json={}).status_code == 403
        assert client.post('/api/demo/reset').status_code == 403
    with patch.dict(os.environ, {'DEMO_GRAPH': 'fresh_demo'}):
        assert client.post('/api/demo/notice', json={}).status_code == 400
        with patch('oncall_brain.freshness.add_notice', return_value={'status': 'ok'}) as add:
            assert client.post('/api/demo/notice', json=dict(name='new', text='30m',
                supersedes_id='Rule:old', effective='2026-10-16')).status_code == 200
            assert add.call_args.args[0] == 'fresh_demo'


def test_replacement_inherits_scope_from_ancestor():
    from oncall_brain import freshness as f
    graph = FakeGraph()
    original = graph.query

    def query(cypher, params=None):
        if 'RETURN type(r)' in cypher and params['id'] == 'Rule:old':
            return SimpleNamespace(result_set=[['SUPERSEDES', True, 'Rule:ancestor', {}]])
        return original(cypher, params)

    graph.query = query
    with patch.object(tools, 'g', return_value=graph):
        result = f.add_notice('fresh_demo', 'Notice:new', '30 minutes', '30 minutes',
                              'Rule:old', '2026-10-16')
    assert result['copied_relations'] == 2


@pytest.mark.parametrize('case', ['missing', 'duplicate', 'authority'])
def test_rejected_notices_do_not_write(case):
    from oncall_brain import freshness as f
    graph = FakeGraph()
    original = graph.query

    def query(cypher, params=None):
        if case == 'missing' and 'RETURN n.kind' in cypher:
            return SimpleNamespace(result_set=[])
        if case == 'duplicate' and 'RETURN n.id' in cypher:
            return SimpleNamespace(result_set=[['Notice:new']])
        if case == 'authority' and "kind: 'Notice'" in cypher:
            return SimpleNamespace(result_set=[])
        return original(cypher, params)

    graph.query = query
    with patch.object(tools, 'g', return_value=graph), pytest.raises(ValueError):
        f.add_notice('fresh_demo', 'Notice:new', '30 minutes', '30 minutes',
                     'Rule:old', '2026-10-16')
    assert not graph.writes


def test_future_notice_uses_existing_visibility_predicate():
    from oncall_brain import freshness as f
    graph = FakeGraph()
    with patch.object(tools, 'g', return_value=graph):
        f.add_notice('fresh_demo', 'Notice:new', '30 minutes', '30 minutes',
                     'Rule:old', '2099-01-01')
    edge = graph.writes[0][1]['edge']

    def query(cypher, params=None):
        assert tools.VISIBLE in cypher
        visible = edge['state'] == 'approved' and edge['valid_from'] <= params['now']
        return SimpleNamespace(result_set=[['Rule:old', 'Notice:new', edge['aid'],
                                           edge['valid_from']]] if visible else [])

    graph.query = query
    with patch.object(tools, 'g', return_value=graph):
        assert tools._superseded('2026-10-16') == {}
        assert tools._superseded('2099-01-01')['Rule:old'][0]['superseded_by'] == 'Notice:new'


def test_reset_route_and_page():
    from web.app import app
    with patch.dict(os.environ, {'DEMO_GRAPH': 'fresh_demo'}), \
            patch('oncall_brain.freshness.reset_demo', return_value={'deleted': 'fresh_demo'}) as reset:
        client = app.test_client()
        assert client.post('/api/demo/reset').json == {'deleted': 'fresh_demo'}
        reset.assert_called_once_with('fresh_demo')
        page = client.get('/demo')
        assert page.status_code == 200
        assert b'oncall-lang' in page.data
