import json
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest

from observation_fixtures import IMAGE, STAMP
from otomekairo.llm.contracts import LLMError
from otomekairo.service.app import OtomeKairoService
from otomekairo.memory.utils import parse_iso
from otomekairo.observations.models import Measurement


@pytest.fixture
def service(tmp_path):
    with patch.object(OtomeKairoService, '_now_iso', return_value=STAMP):
        service = OtomeKairoService(tmp_path)
    state = service.store.read_state()
    for model in state['model_presets'].values():
        model['model'] = 'mock-test'
    for memory in state['memory_sets'].values():
        memory['embedding'].update(model='mock-embedding', embedding_dimension=3)
    state.update(mcp_servers={}, agent_skill_sources={}, periodic_thought_topics=[])
    state['wake_policy']['observations'] = []
    service.store.write_state(state)
    service._now_iso = lambda: STAMP
    token = service.acquire_console_access_token()['console_access_token']
    return service, token


def converse(service, token, *, images=(IMAGE,)):
    return service.handle_conversation(token, {'message_id': 'chat_message:images', 'text': 'この画像について説明して。',
        'images': list(images), 'interaction_context': {'interaction_ref': 'interaction:observation-test',
            'speaker_ref': 'person:test', 'participants': [{'person_ref': 'person:test', 'display_name': '評価者'}]}})


def assert_media_not_saved(service):
    with service.store._memory_db() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            for row in conn.execute('SELECT * FROM "' + table.replace('"', '""') + '"'):
                for value in row:
                    if isinstance(value, str):
                        assert 'data:image' not in value
                        assert 'YWJj' not in value


def test_full_conversation_passes_same_image_to_interpretation_and_decision(service):
    service, token = service
    seen = []
    interpret, decide = service.llm.generate_input_interpretation, service.llm.generate_decision
    def interpretation(**kwargs):
        context = kwargs['observations']
        seen.append(('input', context.snapshot.snapshot_id, context.image_parts()))
        return interpret(**kwargs)
    def decision(**kwargs):
        context = kwargs['context'].observations
        seen.append(('decision', context.snapshot.snapshot_id, context.image_parts()))
        return decide(**kwargs)
    with patch.object(type(service.llm), 'generate_input_interpretation', side_effect=interpretation), patch.object(
            type(service.llm), 'generate_decision', side_effect=decision):
        result = converse(service, token)
    assert result['result_kind'] != 'internal_failure', result
    trace = service.store.get_cycle_trace(result['cycle_id'])
    assert len(trace['input_trace']['observation_snapshot']['input_material_observations']) == 1
    assert len(trace['input_trace']['input_interpretation']['observation_interpretations']) == 1
    assert [row[0] for row in seen] == ['input', 'decision']
    assert seen[0][1:] == seen[1][1:]
    assert len([p for p in seen[0][2] if p['type'] == 'image_url']) == 1
    records = service.store.list_recent_visual_observation_records(memory_set_id=service.store.read_state()['selected_memory_set_id'])
    assert len(records) == 1
    assert len({r['visual_observation_id'] for r in records}) == 1
    assert_media_not_saved(service)


@pytest.mark.parametrize("offset_seconds", [-60, 60])
def test_clock_skewed_capture_reaches_interpretation_decision_and_saved_record(service, offset_seconds):
    service, token = service
    captured_at = (service._parse_iso(STAMP) + timedelta(seconds=offset_seconds)).isoformat()
    seen = []
    interpret, decide = service.llm.generate_input_interpretation, service.llm.generate_decision

    def capture(**kwargs):
        service._collect_capture_images(
            state=kwargs["state"], acquisition=kwargs["acquisition"],
            capability_response={"captured_at": captured_at, "images": [IMAGE], "client_context": {
                "vision_source_id": "vision_source:desktop", "source_kind": "desktop", "source_label": "画面",
            }}, target={"source_kind": "desktop", "source_label": "画面", "vision_source_id": "vision_source:desktop"},
        )
        return kwargs["client_context"]

    def interpretation(**kwargs):
        context = kwargs["observations"]
        seen.append((context.observations[0].observed_at, context.image_parts()))
        return interpret(**kwargs)

    def decision(**kwargs):
        context = kwargs["context"].observations
        seen.append((context.observations[0].observed_at, context.image_parts()))
        return decide(**kwargs)

    with patch.object(service, "_run_wake_policy_observations", side_effect=capture), patch.object(
        type(service.llm), "generate_input_interpretation", side_effect=interpretation,
    ), patch.object(type(service.llm), "generate_decision", side_effect=decision):
        result = converse(service, token, images=())
    assert result["result_kind"] != "internal_failure", result
    assert len(seen) == 2
    assert seen[0] == seen[1]
    assert seen[0][0] == captured_at
    records = service.store.list_recent_visual_observation_records(memory_set_id=service.store.read_state()["selected_memory_set_id"])
    assert len(records) == 1
    assert parse_iso(records[0]["observed_at"]) == parse_iso(captured_at)
    assert_media_not_saved(service)


def test_capture_clock_skew_keeps_expiry_and_other_observation_times_strict():
    from observation_fixtures import image_context
    context = image_context(origin_kind="vision_capture")
    try:
        observation = context.observations[0]
        ahead = (parse_iso(STAMP) + timedelta(seconds=60)).isoformat()
        future = replace(observation, observed_at=ahead)
        assert future.is_fresh(STAMP)
        too_far = (parse_iso(STAMP) + timedelta(seconds=60, microseconds=1)).isoformat()
        assert not replace(future, observed_at=too_far).is_fresh(STAMP)
        assert not observation.is_fresh(observation.expires_at)
        assert not replace(future, origin_kind="conversation_attachment").is_fresh(STAMP)
        assert not replace(future, origin_kind="sensor_report", payload=Measurement(20, "degC")).is_fresh(STAMP)
    finally:
        context.snapshot.media_bundle.close()


def test_observations_survive_downstream_failure_and_media_are_closed(service):
    service, token = service
    acquisitions = []
    create = service._new_observation_acquisition
    def capture(state):
        value = create(state)
        acquisitions.append(value)
        return value
    with patch.object(service, '_new_observation_acquisition', side_effect=capture), patch.object(
            type(service.llm), 'generate_decision', side_effect=LLMError('fixture downstream failure')):
        result = converse(service, token)
    assert result['result_kind'] == 'internal_failure'
    records = service.store.list_recent_visual_observation_records(memory_set_id=service.store.read_state()['selected_memory_set_id'])
    assert len(records) == 1
    assert 'closed=True' in repr(acquisitions[0].media)
    assert_media_not_saved(service)


def test_sensor_ingestion_does_not_generate_and_gate_skip_keeps_deduplicated_observation(service):
    service, token = service
    definition = {'image_max_age_seconds': 300, 'places': [], 'sensor_sources': [{
        'sensor_source_id': 'sensor_source:test', 'display_name': '温湿度計', 'producer_client_id': 'test-client',
        'enabled': True, 'place_ref': None, 'source_owner': 'self', 'max_age_seconds': 60,
        'channels': [{'channel_id': 'temperature', 'display_name': '温度', 'kind': 'measurement', 'value_type': 'number', 'unit': 'degC'}]}]}
    service.replace_environment_editor_state(token, definition)
    with patch.object(type(service.llm), 'generate_input_interpretation', wraps=service.llm.generate_input_interpretation) as interpret, patch.object(
            type(service.llm), 'generate_decision', wraps=service.llm.generate_decision) as decision, patch.object(
            service, '_has_autonomous_initiative_context', return_value=False):
        report = service.submit_sensor_observations(token, {'client_id': 'test-client', 'sensor_source_id': 'sensor_source:test',
            'samples': [{'sample_id': '00000000-0000-4000-8000-000000000001', 'channel_id': 'temperature',
                         'occurred_at': STAMP, 'status': 'succeeded', 'payload': {'kind': 'measurement', 'value': 0, 'unit': 'degC'}, 'error_code': None}]})
        interpret.assert_not_called()
        for _ in range(2):
            result = service.trigger_background_thinking_once(token)
            assert result['result_kind'] == 'noop', result
            trace = service.store.get_cycle_trace(result['cycle_id'])
            assert trace['input_trace']['observation_snapshot']['environment_observations'][0]['payload']['value'] == 0
        assert interpret.call_count == 2
        decision.assert_not_called()
    with service.store._memory_db() as conn:
        rows = list(conn.execute("SELECT payload_json FROM events WHERE kind='sensor_observation'"))
    assert len(rows) == 1
    assert json.loads(rows[0][0])['observation']['observation_id'] == report['results'][0]['observation_id']


def test_multiple_sources_record_each_image_before_decision(service):
    from otomekairo.llm.contexts import CurrentInput
    service, token = service
    state = service.store.read_state()
    acquisition = service._new_observation_acquisition(state)
    target = {'source': 'conversation_attachment', 'image_count': 2}
    service._collect_material_images(acquisition=acquisition, images=[IMAGE, IMAGE], target=target,
                                     origin_kind='conversation_attachment', observed_at=STAMP)
    try:
        context, result = service._interpret_observation_inputs(state=state, acquisition=acquisition,
            current_input=CurrentInput('system', None, 'wake', (), None, '取得資料を確認する。'),
            recent_turns=[], client_context={}, cycle_id='cycle:multiple')
        assert len(result['observation_interpretations']) == 2
        records = service.store.list_recent_visual_observation_records(memory_set_id=state['selected_memory_set_id'])
        assert {r['visual_observation_id'] for r in records} == {o.observation_id for o in context.observations}
    finally:
        acquisition.close()


def test_self_activity_filters_observation_candidates_states_and_signals(service):
    service, _ = service
    candidates = [
        {'factor_ref': 'observation:1', 'kind': 'observation', 'source': 'observation_snapshot', 'summary_text': '周囲の観測', 'metadata': {}},
        {'factor_ref': 'current_input:wake', 'kind': 'current_input', 'source': 'current_input', 'summary_text': '外界の依頼', 'metadata': {}},
    ]
    workspace = service._self_activity_workspace(candidates, current_input_text='自身の活動を考える。')
    assert [item['factor_ref'] for item in workspace['workspace_candidates']] == ['current_input:wake']
    states = [{'state_type': 'environment', 'observation_source_ref': 'sensor_source:test', 'summary_text': '観測値'},
              {'state_type': 'visual_context', 'summary_text': '視覚説明'},
              {'state_type': 'schedule', 'summary_text': '自身の予定'}]
    assert service._self_activity_world_state(states) == [states[2]]
    isolated = service._self_activity_foreground_signal_summary({'observations': [{'summary_text': '観測説明'}]}, world_state_summary=[])
    assert 'observations' not in isolated
