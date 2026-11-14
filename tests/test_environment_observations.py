from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import threading
import uuid

import pytest

from otomekairo.observations import (
    EnvironmentRuntime, MediaBundle, ObservationError, ObservationSnapshot,
    validate_environment_config,
)
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.common import ServiceError
from otomekairo.store.file_store import FileStore


STAMP = "2026-10-28T10:00:00+09:00"
LATER = "2026-10-28T10:00:30+09:00"


@pytest.fixture
def identifier_store(tmp_path):
    return FileStore(tmp_path)


@pytest.fixture
def definition():
    return {"image_max_age_seconds": 300, "places": [{"place_ref": "place:room", "display_name": "作業部屋"}],
            "sensor_sources": [{"sensor_source_id": "sensor_source:room", "display_name": "室内センサー",
                                "producer_client_id": "sensor-client", "enabled": True, "place_ref": "place:room",
                                "source_owner": "self", "max_age_seconds": 60,
                                "channels": [
                                    {"channel_id": "temperature", "display_name": "温度", "kind": "measurement", "value_type": "number", "unit": "degC"},
                                    {"channel_id": "presence", "display_name": "検知状態", "kind": "measurement", "value_type": "boolean", "unit": None},
                                    {"channel_id": "motion", "display_name": "最終反応", "kind": "event", "value_type": None, "unit": None}]}]}


def sample(channel="temperature", value=0, *, occurred_at=STAMP):
    return {"sample_id": str(uuid.uuid4()), "channel_id": channel, "occurred_at": occurred_at,
            "status": "succeeded", "payload": {"kind": "measurement", "value": value,
                                                   "unit": "degC" if channel == "temperature" else None},
            "error_code": None}


def submit(runtime, *samples, received_at=LATER):
    return runtime.submit({"client_id": "sensor-client", "sensor_source_id": "sensor_source:room",
                           "samples": list(samples)}, received_at=received_at)


def test_latest_samples_keep_zero_false_and_stable_identity(definition, identifier_store):
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    zero, false = sample(), sample("presence", False)
    response = submit(runtime, zero, false)
    observations, reports = runtime.snapshot(LATER)
    assert [o.payload.value for o in observations] == [0, False]
    assert len(reports) == 1 and reports[0].status == "not_observed"
    duplicate = submit(runtime, zero, false, received_at="2026-10-28T10:00:40+09:00")
    assert [r["acceptance"] for r in duplicate["results"]] == ["duplicate", "duplicate"]
    assert [r["observation_id"] for r in response["results"]] == [r["observation_id"] for r in duplicate["results"]]
    assert runtime.snapshot(LATER)[0][0].received_at == LATER
    restarted = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    assert not restarted.snapshot(LATER)[0]
    assert submit(restarted, zero)["results"][0]["observation_id"] == response["results"][0]["observation_id"]
    assert runtime.snapshot("2026-10-28T10:01:00+09:00")[0] == ()


def test_failed_channel_does_not_resurrect_previous_value(definition, identifier_store):
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    submit(runtime, sample())
    failed = sample(occurred_at=LATER)
    failed.update(status="failed", payload=None, error_code="acquisition_failed")
    submit(runtime, failed)
    observations, reports = runtime.snapshot(LATER)
    assert observations == ()
    assert reports[0].status == "failed"
    recovery = sample(value=1, occurred_at="2026-10-28T10:00:31+09:00")
    submit(runtime, recovery, received_at="2026-10-28T10:00:32+09:00")
    assert runtime.snapshot("2026-10-28T10:00:32+09:00")[0][0].payload.value == 1


@pytest.mark.parametrize("value,unit", [(True, "degC"), (float("nan"), "degC"), (float("inf"), "degC"),
                                         (10**1000, "degC"), (1, "%"), (None, "degC")])
def test_invalid_measurements_are_explicit_failures(definition, value, unit, identifier_store):
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    invalid = sample(value=value)
    invalid["payload"]["unit"] = unit
    with pytest.raises(ObservationError) as caught:
        submit(runtime, invalid)
    assert caught.value.code == "invalid_sensor_value"
    assert runtime.snapshot(LATER)[0] == ()


@pytest.mark.parametrize("change", [
    None,
    {},
    {"pattern": "other", "previous_value": 24, "delta": 1, "elapsed_seconds": 10},
    {"pattern": "initial", "previous_value": 24, "delta": 1, "elapsed_seconds": 10},
    {"pattern": "steady", "previous_value": 24, "delta": 99, "elapsed_seconds": 10},
    {"pattern": "rapid_change", "previous_value": 24, "delta": 1, "elapsed_seconds": 0},
    {"pattern": "rapid_change", "previous_value": 24, "delta": 1, "elapsed_seconds": True},
    {"pattern": "rapid_change", "previous_value": float("inf"), "delta": 1, "elapsed_seconds": 10},
])
def test_invalid_numeric_change_rejects_entire_report(definition, identifier_store, change):
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    reading = sample(value=25)
    reading["payload"]["change"] = change
    with pytest.raises(ObservationError) as caught:
        submit(runtime, reading, sample("presence", True))
    assert caught.value.code == "invalid_sensor_value"
    assert runtime.snapshot(LATER)[0] == ()


def test_boolean_measurement_does_not_accept_numeric_change(definition, identifier_store):
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    reading = sample("presence", False)
    reading["payload"]["change"] = {"pattern": "initial", "previous_value": None, "delta": None, "elapsed_seconds": None}
    with pytest.raises(ObservationError) as caught:
        submit(runtime, reading)
    assert caught.value.code == "invalid_sensor_value"


def test_conflicting_batch_is_atomic(definition, identifier_store):
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    initial = sample()
    submit(runtime, initial)
    conflicting = deepcopy(initial)
    conflicting["payload"]["value"] = 4
    with pytest.raises(ObservationError) as caught:
        submit(runtime, sample("presence", True), conflicting)
    assert caught.value.code == "sensor_sample_conflict"
    observations, reports = runtime.snapshot(LATER)
    assert len(observations) == 1
    assert reports[0].channel_id == "presence" and reports[0].status == "not_observed"
    with pytest.raises(ObservationError) as caught:
        submit(runtime, sample())
    assert caught.value.code == "sensor_sample_out_of_order"


def test_failed_batch_does_not_reserve_other_observation_identities(definition, identifier_store):
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    initial = sample()
    submit(runtime, initial)
    restarted = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    presence = sample("presence", True)
    conflicting = deepcopy(initial)
    conflicting["payload"]["value"] = 4
    with pytest.raises(ObservationError) as caught:
        submit(restarted, presence, conflicting)
    assert caught.value.code == "sensor_sample_conflict"
    assert restarted.snapshot(LATER)[0] == ()
    with identifier_store._memory_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM internal_identities").fetchone()[0] == 1
    presence["payload"]["value"] = False
    assert submit(restarted, presence)["results"][0]["acceptance"] == "accepted"


def test_event_report_keeps_monitoring_and_occurrence_separate(definition, identifier_store):
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    event = sample("motion")
    event["payload"] = {"kind": "event", "monitoring_started_at": "2026-10-28T09:00:00+09:00", "last_occurred_at": None}
    submit(runtime, event)
    assert runtime.snapshot(LATER)[0][0].payload.last_occurred_at is None
    new_event = deepcopy(event)
    new_event.update(sample_id=str(uuid.uuid4()), occurred_at=LATER)
    new_event["payload"]["last_occurred_at"] = STAMP
    submit(runtime, new_event)
    failed = deepcopy(new_event)
    failed.update(sample_id=str(uuid.uuid4()), occurred_at="2026-10-28T10:00:31+09:00",
                  status="failed", payload=None, error_code="source_unavailable")
    submit(runtime, failed, received_at="2026-10-28T10:00:32+09:00")
    reversed_event = deepcopy(event)
    reversed_event.update(sample_id=str(uuid.uuid4()), occurred_at="2026-10-28T10:00:33+09:00")
    with pytest.raises(ObservationError) as caught:
        submit(runtime, reversed_event, received_at="2026-10-28T10:00:34+09:00")
    assert caught.value.code == "invalid_observation_time"


def test_configuration_revision_rejects_racing_report(definition, monkeypatch, identifier_store):
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    original = runtime._payload
    def changing(payload, channel, occurred):
        changed = deepcopy(definition)
        changed["sensor_sources"][0]["place_ref"] = None
        runtime.configure(changed)
        return original(payload, channel, occurred)
    monkeypatch.setattr(runtime, "_payload", changing)
    with pytest.raises(ObservationError) as caught:
        submit(runtime, sample())
    assert caught.value.code == "sensor_source_changed"
    assert runtime.snapshot(LATER)[0] == ()


def test_snapshot_is_immutable_and_trace_contains_no_media(definition, identifier_store):
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    submit(runtime, sample())
    observations, reports = runtime.snapshot(LATER)
    media = MediaBundle()
    media.add("media:test", "data:image/png;base64,YWJj")
    snapshot = ObservationSnapshot("snapshot:test", LATER, "memory_set:test", observations, (), reports, media)
    with pytest.raises(FrozenInstanceError):
        snapshot.snapshot_at = STAMP
    serialized = json.dumps(snapshot.to_trace_payload())
    assert "YWJj" not in serialized and "data:image" not in serialized
    assert "YWJj" not in repr(media) and "YWJj" not in repr(snapshot)
    media.close()
    with pytest.raises(ObservationError):
        media.image_parts(("media:test",))
    newer = sample(value=7, occurred_at=LATER)
    submit(runtime, newer)
    assert snapshot.environment_observations[0].payload.value == 0


def test_environment_api_persists_config_and_invalidates_changed_source(tmp_path, definition, identifier_store):
    service = OtomeKairoService.__new__(OtomeKairoService)
    service._config_update_lock = threading.RLock()
    service._runtime_state_lock = threading.RLock()
    service._observation_history = {}
    service.store = FileStore(tmp_path)
    service._now_iso = lambda: LATER
    state = service.store.read_state()
    service._environment_runtime = EnvironmentRuntime(state["environment_settings"], resolve_ids=identifier_store.resolve_observation_ids)
    token = service.acquire_console_access_token()["console_access_token"]
    assert service.replace_environment_editor_state(token, definition) == definition
    assert FileStore(tmp_path).read_state()["environment_settings"] == definition
    service.submit_sensor_observations(token, {"client_id": "sensor-client", "sensor_source_id": "sensor_source:room",
                                              "samples": [sample()]})
    changed = deepcopy(definition)
    changed["sensor_sources"][0]["place_ref"] = None
    service.replace_environment_editor_state(token, changed)
    assert service._environment_runtime.snapshot(LATER)[0] == ()
    with pytest.raises(ServiceError) as caught:
        service.get_environment_editor_state("invalid")
    assert caught.value.error_code == "invalid_token"


@pytest.mark.parametrize("field,value", [("source_owner", []), ("place_ref", {}), ("max_age_seconds", True)])
def test_invalid_source_config_is_rejected(definition, field, value):
    definition["sensor_sources"][0][field] = value
    with pytest.raises(ObservationError) as caught:
        validate_environment_config(definition, camera_sources={})
    assert caught.value.code == "invalid_environment_config"


def test_event_timestamps_can_build_multimodal_prompt_without_double_conversion(definition, identifier_store):
    from otomekairo.llm.contexts import CurrentInput, PersonaContext
    from otomekairo.llm.prompts import build_input_interpretation_messages
    from otomekairo.observations.acquisition import ObservationAcquisition
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    event = sample('motion')
    event['payload'] = {'kind': 'event', 'monitoring_started_at': STAMP, 'last_occurred_at': None}
    submit(runtime, event)
    observations, reports = runtime.snapshot(LATER)
    acquisition = ObservationAcquisition('memory_set:test', 300, identifier_store.allocate_id)
    context = acquisition.freeze(snapshot_at=LATER, sensor_observations=observations, sensor_reports=reports)
    try:
        messages = build_input_interpretation_messages(persona_context=PersonaContext('テスト', '観測を確認する。', None, ''),
            current_input=CurrentInput('system', None, 'wake', (), None, '観測を確認する。'),
            recent_turns=[], current_time=LATER, observations=context)
        payload = json.loads(messages[-2]['content'].splitlines()[1])
        assert payload['surroundings']['observations'][0]['payload']['last_occurred_time_text'] is None
        assert '2026年' in payload['surroundings']['observations'][0]['payload']['monitoring_started_time_text']
    finally:
        acquisition.close()


def test_sensor_event_identity_survives_clone_and_delete(tmp_path, definition, identifier_store):
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    submit(runtime, sample())
    observation = runtime.snapshot(LATER)[0][0].to_trace_payload()
    store = FileStore(tmp_path)
    source = store.read_state()['selected_memory_set_id']
    event = {'event_id': 'event:sensor-source', 'cycle_id': 'cycle:source', 'memory_set_id': source,
             'kind': 'sensor_observation', 'role': None, 'text': None, 'created_at': LATER, 'observation': observation}
    store.persist_observations(visual_records=[], sensor_events=[event])
    store.clone_memory_set_records(source_memory_set_id=source, target_memory_set_id='memory_set:copy')
    store.persist_observations(visual_records=[], sensor_events=[{**event, 'event_id': 'event:sensor-copy', 'cycle_id': 'cycle:copy', 'memory_set_id': 'memory_set:copy'}])
    with store._memory_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='sensor_observation'").fetchone()[0] == 2
    store.delete_memory_set_records('memory_set:copy')
    with store._memory_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='sensor_observation'").fetchone()[0] == 1


def test_inspection_never_resurrects_stored_sensor_state_after_restart_or_failed_report(definition, identifier_store):
    from types import SimpleNamespace
    runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    report = sample()
    observation_id = submit(runtime, report)['results'][0]['observation_id']
    record = {'observation_payload': report['payload'], 'source_observation_ids': [observation_id]}
    service = OtomeKairoService.__new__(OtomeKairoService)
    service._environment_runtime = runtime
    service.store = SimpleNamespace(list_world_states=lambda **kwargs: [record])
    state = {'selected_memory_set_id': 'memory_set:test'}
    assert service._list_current_world_states(state=state, current_time=LATER) == [record]
    failed = sample(occurred_at=LATER)
    failed.update(status='failed', payload=None, error_code='acquisition_failed')
    submit(runtime, failed)
    assert service._list_current_world_states(state=state, current_time=LATER) == []
    service._environment_runtime = EnvironmentRuntime(definition, resolve_ids=identifier_store.resolve_observation_ids)
    assert service._list_current_world_states(state=state, current_time=LATER) == []


@pytest.mark.parametrize('method,path,service_method', [
    ('GET', '/api/config/environment/editor-state', 'get_environment_editor_state'),
    ('PUT', '/api/config/environment/editor-state', 'replace_environment_editor_state'),
    ('POST', '/api/observations/sensors', 'submit_sensor_observations'),
])
def test_observation_http_routes_preserve_authentication_and_body(method, path, service_method):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from otomekairo.http_server import OtomeKairoHandler
    handler = object.__new__(OtomeKairoHandler)
    handler.path, handler.headers = path, {'Authorization': 'Bearer fixture-token'}
    call = Mock(return_value={'accepted': True})
    handler.server = SimpleNamespace(service=SimpleNamespace(**{service_method: call}))
    handler._read_json_body = Mock(return_value={'fixture': 0})
    handler._write_success = Mock()
    handler._dispatch(method)
    expected = ('fixture-token',) if method == 'GET' else ('fixture-token', {'fixture': 0})
    call.assert_called_once_with(*expected)
    handler._write_success.assert_called_once_with(200, {'accepted': True})
