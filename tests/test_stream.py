import json
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from oncall_brain import agent, fast
from web.app import app


def response(message):
    return NS(choices=[NS(message=message)])


def test_fast_steps_arrive_before_composition():
    steps = []
    def compose(**kwargs):
        assert [s['tool'] for s in steps] == ['source_lines', 'decide (fulltext)', 'compose']
        return response(NS(content='Status: not_found\n## Answer\nNo evidence.'))
    client = NS(chat=NS(completions=NS(create=compose)))
    with patch.object(fast.tools, 'related_source_lines', return_value=[{'id': 'e', 'source': 'x', 'locator': '1'}]), patch.object(fast, '_candidates', return_value=[]), patch.object(fast, '_jev_pick', return_value=([], 'fulltext')), patch.object(fast, '_openai_client', return_value=client):
        result = fast.ask_fast('unknown', on_step=steps.append)
    assert [step['tool'] for step in steps] == ['source_lines', 'decide (fulltext)', 'compose']
    assert len(result['trace']) == 2
    assert all({'tool', 'args', 'args_str', 'summary', 'elapsed'} <= s.keys() for s in steps)
    assert [s['elapsed'] for s in steps] == sorted(s['elapsed'] for s in steps)


def test_agent_calls_on_step_for_tools_and_composition():
    steps = []
    call = NS(id='one', function=NS(name='describe_ontology', arguments='{}'))
    messages = iter([NS(tool_calls=[call]), NS(tool_calls=[], content='Status: not_found\nNo evidence.')])
    client = NS(chat=NS(completions=NS(create=lambda **kw: response(next(messages)))))
    with patch.object(agent, '_openai_client', return_value=client), patch.object(agent.tools, 'related_source_lines', return_value=[]), patch.object(agent.tools, 'describe_ontology', return_value={'status': 'ok'}):
        result = agent.ask('unknown', on_step=steps.append)
    assert [s['tool'] for s in steps if s['tool'] != 'compose'] == ['describe_ontology']
    assert any(s['tool'] == 'compose' for s in steps)
    assert result['tool_calls'] == 1


@pytest.mark.parametrize("mode,target", [("fast", "web.app.fast.ask_fast"), ("deep", "web.app.agent.ask")])
def test_stream_yields_before_worker_finishes_and_matches_regular_json(mode, target):
    release = Event()
    def fake(question, trace, lang, on_step=None):
        if on_step:
            on_step({'tool': 'read_knowledge', 'args': {'id': 'M'}, 'summary': {}, 'elapsed': 0.1})
            assert release.wait(3), 'stream buffered until worker finished'
            on_step({'tool': 'compose', 'args': {}, 'summary': {}, 'elapsed': 0.2})
        return {'question': question, 'answer': 'Status: not_found\nNothing.', 'trace': [], 'tool_results': []}
    with patch(target, side_effect=fake):
        client = app.test_client()
        stream = client.post('/api/ask/stream', json={'question': 'unknown', 'mode': mode}, buffered=False)
        try:
            assert stream.mimetype == 'text/event-stream'
            iterator = iter(stream.response)
            first = next(iterator).decode()
            assert first.startswith('event: step\n')
            release.set()
            frames = [first] + [chunk.decode() for chunk in iterator]
            assert [f.splitlines()[0] for f in frames] == ['event: step', 'event: step', 'event: done']
            done = json.loads(frames[-1].split('data: ', 1)[1])
            regular = client.post('/api/ask', json={'question': 'unknown', 'mode': mode}).json
            assert done == regular
        finally:
            release.set()
            stream.close()


def test_stream_errors_are_sanitized_and_validation_is_shared():
    client = app.test_client()
    for body in ({}, [], {'question': ' '}, {'question': 123}):
        assert client.post('/api/ask/stream', json=body).status_code == 400
    with patch('web.app.fast.ask_fast', side_effect=RuntimeError('secret credential')):
        response = client.post('/api/ask/stream', json={'question': 'unknown'})
        body = response.get_data(as_text=True)
    assert body.startswith('event: error\n')
    assert 'secret credential' not in body


def test_fast_publishes_every_record_and_decision_without_changing_trace():
    steps = []
    module = {'id': 'M', 'name': 'Module', 'kind': 'Module'}
    neighbor = {'id': 'N', 'name': 'Neighbor', 'kind': 'Resource'}
    def read(entity_id):
        return {'status': 'ok', 'entity': module if entity_id == 'M' else neighbor,
                'assertions': [{'aid': 'a1', 'other': neighbor}] if entity_id == 'M' else []}
    client = NS(chat=NS(completions=NS(create=lambda **kw: response(NS(content='Status: not_found\nNo evidence.')))))
    with patch.object(fast.tools, 'related_source_lines', return_value=[]), patch.object(fast, '_candidates', return_value=[module]), patch.object(fast, '_jev_pick', side_effect=[([module], 'jev'), ([neighbor], 'jev')]), patch.object(fast.tools, 'read_knowledge', side_effect=read), patch.object(fast.tools, 'module_expertise', return_value={'status': 'ok', 'handlers': []}), patch.object(fast, '_openai_client', return_value=client):
        result = fast.ask_fast('Who owns Module?', on_step=steps.append)
    assert [s['tool'] for s in steps] == ['decide (jev)', 'read_knowledge', 'decide hop (jev)', 'read_knowledge', 'module_expertise', 'compose']
    assert [s['tool'] for s in steps[:-1]] == [s['tool'] for s in result['trace']]
    assert all('elapsed' not in entry for entry in result['trace'])
    steps[0]['args']['candidates'].clear()
    assert result['trace'][0]['args']['candidates'] == ['M']
