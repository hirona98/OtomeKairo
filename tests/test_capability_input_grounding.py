from copy import deepcopy
from dataclasses import replace
import json
from unittest.mock import Mock

import pytest

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import AutonomousStepContext
from otomekairo.llm.contracts import LLMError
from test_decision_contract import (
    _capability_decision, _current_input, _decision_context, _persona_context, _wire_response,
)


def _view():
    return [{"id": "mcp.call_tool", "available": True, "unavailable_reason": None,
             "mcp_servers": [{"mcp_server_id": "elyth", "available": True, "tools": [{
                 "name": "publish", "input_schema": {"type": "object", "required": ["body"],
                     "properties": {"body": {"type": "string"}}, "additionalProperties": False},
             }]}]}]


def _request(text):
    return {"mcp_server_id": "elyth", "tool_name": "publish", "arguments": {"body": text}}


def _context():
    return replace(_decision_context(_view()), visual_observation_context={
        "visual_summary_text": "空中の線が雨筋のように見える。現在の降雨は確認できない。",
    })


@pytest.mark.parametrize('repaired', [True, False])
def test_decision_repairs_mcp_content_before_returning_or_fails(monkeypatch, repaired):
    draft = _capability_decision('mcp.call_tool', _request('現在、公園では雨が降っている。'))
    safe = _capability_decision('mcp.call_tool', _request('空中に雨筋のように見える線がある。'))
    reject = {'outcome': 'reconsider', 'reason_summary': '雨筋のように見えるという観測を降雨の確定へ強めている。'}
    allow = {'outcome': 'allow', 'reason_summary': '観測で確認した確かさを保っている。'}
    completion = Mock(side_effect=[_wire_response(draft), json.dumps(reject),
                                   _wire_response(safe if repaired else draft), json.dumps(allow if repaired else reject)])
    monkeypatch.setattr('otomekairo.llm.client.complete_text', completion)
    client = LLMClient()
    monkeypatch.setattr(LLMClient, 'generate_future_action_alignment_review', Mock(return_value={
        'outcome': 'aligned', 'reason_summary': '今回の操作で完了する。'}))
    def generate():
        return client.generate_decision(model_config={'model': 'provider/test'},
            persona_context=_persona_context(), context=_context())
    if repaired:
        assert generate()['capability_request']['input'] == safe['capability_request']['input']
    else:
        with pytest.raises(LLMError, match='事実表現'):
            generate()
    assert completion.call_count == 4
    assert completion.call_args_list[1].kwargs['response_format']['json_schema']['name'] == 'capability_input_grounding_review'
    source = completion.call_args_list[1].kwargs['messages'][-1]['content']
    assert '現在の降雨は確認できない' in source
    assert '雨筋のように見える' in source


def test_grounding_reviewer_failure_does_not_regenerate_unchecked_request(monkeypatch):
    draft = _capability_decision('mcp.call_tool', _request('水面が見える。'))
    completion = Mock(side_effect=[_wire_response(draft), LLMError('provider failed')])
    monkeypatch.setattr('otomekairo.llm.client.complete_text', completion)
    with pytest.raises(RuntimeError, match='根拠審査'):
        LLMClient().generate_decision(model_config={'model': 'provider/test'},
            persona_context=_persona_context(), context=_context())
    assert completion.call_count == 2


def test_autonomous_step_uses_the_same_mcp_content_grounding(monkeypatch):
    request = {'capability_id': 'mcp.call_tool', 'input': _request('今、雨が降っている。')}
    draft = {'action': {'kind': 'capability_request', 'capability_request': request, 'speech': None},
             'transition': {'kind': 'continue', 'next_run_at': None},
             'run_update': {'current_step_summary': '投稿する。', 'history_summary': '観測した。'}}
    safe = deepcopy(draft)
    safe['action']['capability_request']['input'] = _request('雨筋のように見える線がある。')
    completion = Mock(side_effect=[_wire_response(draft), json.dumps({
        'outcome': 'reconsider', 'reason_summary': '降雨の確定は観測されていない。'}),
        _wire_response(safe), json.dumps({'outcome': 'allow', 'reason_summary': '確かさを保っている。'})])
    monkeypatch.setattr('otomekairo.llm.client.complete_text', completion)
    context = AutonomousStepContext(run={'run_id': 'autonomous_run:test'}, current_input=_current_input(),
        recent_turns=[], time_context={}, foreground_world_state=None, activity_context=None,
        ongoing_action_summary=None, capability_decision_view=_view(), last_result_context=None, affect_context={},
        observation_context={'visual_summary_text': '雨筋のように見えるが降雨は未確認。'})
    result = LLMClient().generate_autonomous_step(model_config={'model': 'provider/test'},
        persona_context=_persona_context(), context=context)
    assert result == safe
    assert completion.call_count == 4
