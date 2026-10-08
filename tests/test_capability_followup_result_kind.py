from unittest.mock import Mock

import pytest

from otomekairo.service.input.trace_build import ServiceInputTraceBuildMixin
from otomekairo.service.input.trace_persist import ServiceInputTracePersistMixin


class ResultBoundary(ServiceInputTracePersistMixin, ServiceInputTraceBuildMixin):
    def __init__(self):
        self._now_iso = Mock(return_value='2026-10-05T21:00:00+09:00')
        self._apply_pending_intent_candidate = Mock(return_value=None)
        self._persist_cycle_success = Mock(return_value=[])
        self._register_interaction_participants = Mock()
        self._emit_input_success_logs = Mock()
        self._skipped_memory_trace = Mock(return_value={})
        self._update_cycle_trace_memory_trace = Mock()
        self._emit_memory_trace_logs = Mock()


@pytest.mark.parametrize('trigger_kind', ['capability_result', 'background_thinking'])
@pytest.mark.parametrize('new_request,speech,expected', [
    (None, None, 'noop'),
    ({'request_id': 'request:next', 'capability_id': 'mcp.call_tool'}, None, 'capability_request'),
    (None, {'speech_text': '確認を終えました。'}, 'speech'),
    ({'request_id': 'request:next', 'capability_id': 'mcp.call_tool'},
     {'speech_text': '追加の確認へ進みます。'}, 'speech'),
])
def test_current_result_uses_only_newly_dispatched_request_and_keeps_source_provenance(
        trigger_kind, new_request, speech, expected):
    boundary = ResultBoundary()
    source = {'request_id': 'request:source', 'capability_id': 'mcp.call_tool'}
    pipeline = {
        'decision': {'kind': 'noop' if new_request is None and speech is None else expected},
        'speech_payload': speech, 'capability_request_summary': new_request,
        'recall_hint': {}, 'recall_pack': {}, 'time_context': {}, 'affect_context': {},
        'persona_id': 'persona:test', 'persona_display_name': 'テスト人格',
    }
    response = boundary._complete_input_success(
        cycle_id='cycle:followup', started_at='2026-10-05T21:00:00+09:00',
        state={'selected_memory_set_id': 'memory_set:test'}, runtime_summary={},
        input_text='保存済みの能力結果。', client_context={}, interaction_context=None,
        pipeline=pipeline, trigger_kind=trigger_kind, input_event_kind='capability_result',
        input_event_role='system', consolidate_memory=False,
        capability_request_summary=source)
    persisted = boundary._persist_cycle_success.call_args.kwargs
    assert response['result_kind'] == persisted['result_kind'] == expected
    assert response['capability_request'] == new_request
    assert persisted['capability_request_summary'] == source
    assert persisted['followup_capability_request_summary'] == new_request
