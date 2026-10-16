"""MCP入力の生成と検証を確認する。自然文の精度は評価しない。"""
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


@pytest.fixture(params=["decision", "followup", "step"])
def generation(request):
    view = _view()
    text = "  雨筋のように見える線がある。\n降雨は確認できない。  "
    result_text = "架空のツールから取得した結果。"
    person_text = "本人による過去の報告。"
    memory_text = "本人が以前に伝えた内容。"
    result_context = {"source_capability_id": "mcp.call_tool", "allowed_followup_capability_ids": ["mcp.call_tool"],
                      "result_kind": "success", "result_summary": result_text}
    recent_turns = [{"role": "person", "message": person_text}]
    current = _current_input()
    if request.param == "followup":
        current = replace(current, sender_kind="system", sender_ref=None,
                          source_kind="capability_result", response_target_refs=(), text="能力の実行結果。")
    mcp_request = {"capability_id": "mcp.call_tool", "input": {
        "mcp_server_id": "elyth", "tool_name": "publish", "arguments": {"body": text},
    }}
    client = LLMClient()
    if request.param == "step":
        candidate = {
            'action': {'kind': 'capability_request', 'capability_request': mcp_request, 'speech': None},
            'transition': {'kind': 'continue', 'next_run_at': None},
            'run_update': {'current_step_summary': '投稿する。', 'history_summary': '観測を受け取った。'},
        }
        context = AutonomousStepContext(
            run={'run_id': 'autonomous_run:test'}, current_input=current,
            recent_turns=recent_turns, time_context={}, foreground_world_state=None, activity_context=None,
            ongoing_action_summary=None, capability_decision_view=view, last_result_context=result_context,
            affect_context={}, observation_context={'visual_summary_text': text},
        )
        def generate():
            return client.generate_autonomous_step(model_config={'model': 'provider/test'},
                persona_context=_persona_context(), context=context)
        expected_sources = [text, result_text, person_text]
    else:
        candidate = _capability_decision('mcp.call_tool', mcp_request['input'])
        context = replace(_decision_context(view), current_input=current, input_text=current.text,
            trigger_kind='capability_result' if request.param == 'followup' else 'user_message',
            recent_turns=recent_turns, current_visual_observations=[{'visual_summary_text': text}],
            capability_result_context=result_context, recall_pack={'event_evidence': [{
                'kind': 'conversation_input', 'role': 'person', 'text': memory_text,
            }]})
        def generate():
            return client.generate_decision(model_config={'model': 'provider/test'},
                persona_context=_persona_context(), context=context)
        expected_sources = [text, result_text, person_text, memory_text]
    return generate, candidate, view, expected_sources


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def test_mcp_generation_keeps_arguments_and_source_context_in_one_completion(monkeypatch, generation):
    generate, candidate, _, expected_sources = generation
    before = deepcopy(candidate)
    completion = Mock(return_value=_wire_response(candidate))
    monkeypatch.setattr('otomekairo.llm.client.complete_text', completion)
    assert generate() == before
    assert candidate == before
    completion.assert_called_once()
    assert completion.call_args.kwargs['response_format']['json_schema']['name'] in {'decision', 'autonomous_step'}
    # JSONの入力資料がそのまま届くことを確認し、文章の意味を判定するテストにはしない。
    payloads = [json.loads(message['content'].splitlines()[1])
                for message in completion.call_args.kwargs['messages'] if message['role'] == 'user']
    actual_sources = list(_strings(payloads))
    for source in expected_sources:
        assert source in actual_sources


def test_malformed_json_repairs_once_without_a_separate_review(monkeypatch, generation):
    generate, candidate, _, _ = generation
    completion = Mock(side_effect=['{invalid', _wire_response(candidate)])
    monkeypatch.setattr('otomekairo.llm.client.complete_text', completion)
    assert generate() == candidate
    assert completion.call_count == 2


def test_invalid_arguments_are_repaired_before_returning(monkeypatch, generation):
    generate, candidate, _, _ = generation
    invalid = deepcopy(candidate)
    mcp_request = invalid['action']['capability_request'] if 'action' in invalid else invalid['capability_request']
    mcp_request['input']['arguments']['body'] = 123
    completion = Mock(side_effect=[_wire_response(invalid), _wire_response(candidate)])
    monkeypatch.setattr('otomekairo.llm.client.complete_text', completion)
    assert generate() == candidate
    assert completion.call_count == 2


@pytest.mark.parametrize('failure', ['invalid_arguments', 'unavailable'])
def test_invalid_or_unavailable_mcp_request_fails_after_one_repair(monkeypatch, generation, failure):
    generate, candidate, view, _ = generation
    invalid = deepcopy(candidate)
    if failure == 'unavailable':
        view[0]['available'] = False
        view[0]['unavailable_reason'] = 'no_binding'
    else:
        mcp_request = invalid['action']['capability_request'] if 'action' in invalid else invalid['capability_request']
        mcp_request['input']['arguments']['body'] = 123
    completion = Mock(return_value=_wire_response(invalid))
    monkeypatch.setattr('otomekairo.llm.client.complete_text', completion)
    with pytest.raises(LLMError):
        generate()
    assert completion.call_count == 2


def test_generation_failure_propagates_without_additional_calls(monkeypatch, generation):
    generate, _, _, _ = generation
    failure = LLMError('provider failed')
    completion = Mock(side_effect=failure)
    monkeypatch.setattr('otomekairo.llm.client.complete_text', completion)
    with pytest.raises(LLMError) as raised:
        generate()
    assert raised.value is failure
    completion.assert_called_once()
