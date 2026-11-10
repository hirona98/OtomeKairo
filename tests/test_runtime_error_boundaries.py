from copy import deepcopy
from dataclasses import replace
import threading
from unittest.mock import Mock

import pytest

from observation_fixtures import text_payloads
from otomekairo.audio.runtime import QueuedUtterance
from otomekairo.audio.segmenter import SegmentedUtterance
from otomekairo.llm.prompts import build_decision_messages
from otomekairo.service.app import OtomeKairoService
from test_decision_contract import _decision_context, _persona_context


@pytest.mark.parametrize("state_type", ["visual_context", "environment"])
@pytest.mark.parametrize("comparison_scope", ["full", "outward_speech"])
def test_world_state_summary_preserves_the_canonical_id_in_decision_input(state_type, comparison_scope):
    service = OtomeKairoService.__new__(OtomeKairoService)
    record = {
        "world_state_id": "world_state:45", "state_type": state_type,
        "scope_type": "world", "scope_key": "world", "summary_text": "今回の観測状態。",
        "source_kind": "wake", "source_ref": "cycle:41", "confidence": 0.8, "salience": 0.5,
        "observed_at": "2026-11-09T23:55:41+09:00", "updated_at": "2026-11-09T23:55:41+09:00",
        "source_observation_refs": ["visual_observation:42"],
    }
    summaries = service._summarize_foreground_world_states([record], current_time="2026-11-09T23:55:52+09:00")
    context = replace(_decision_context([]), foreground_world_state=summaries, comparison_scope=comparison_scope)
    messages = build_decision_messages(persona_context=_persona_context(), context=context)
    surroundings = next(value["surroundings"] for value in text_payloads(messages)
                        if isinstance(value, dict) and "surroundings" in value)
    assert surroundings["state_summaries"][0]["state_ref"] == record["world_state_id"]
    assert surroundings["state_summaries"][0]["source_observation_refs"] == record["source_observation_refs"]


class ControlSocket:
    def __init__(self, fail_type=None):
        self.fail_type = fail_type
        self.closed = threading.Event()

    def send_json(self, payload):
        if payload["type"] == self.fail_type:
            raise OSError("WebSocket connection is closed.")

    def close(self):
        self.closed.set()


@pytest.fixture
def audio_runtime(tmp_path, monkeypatch):
    service = OtomeKairoService(tmp_path)
    runtime = service._audio_runtime
    monkeypatch.setattr(runtime, "_publish_state", Mock())
    monkeypatch.setattr(runtime, "_evaluate_pause_reason_locked", lambda connection: None)
    monkeypatch.setattr(runtime, "_evaluate_conversation_input_blocked_reason_locked", lambda: None)
    try:
        yield runtime
    finally:
        service.close_audio_runtime()
        service.close_tts_runtime()


def activate(runtime, socket):
    session_id = runtime.register_connection(socket, endpoint_source="local_microphone")
    with runtime._condition:
        connection = runtime._connections[session_id]
        connection.lease_generation = 1
        runtime._active_connection = connection
    return connection


@pytest.mark.parametrize("operation", ["pause", "resume", "revoke", "reload"])
def test_failed_control_delivery_ends_the_connection_and_releases_its_lease(audio_runtime, monkeypatch, operation):
    runtime = audio_runtime
    socket = ControlSocket("audio_resumed" if operation == "resume" else "audio_paused")
    connection = activate(runtime, socket)
    with runtime._condition:
        if operation == "pause":
            runtime._set_paused_locked("queue_full")
        elif operation == "resume":
            runtime._paused_reason = "queue_full"
            runtime._refresh_pause_state_locked()
        elif operation == "revoke":
            runtime._revoke_active_locked("input_source_changed")
        else:
            settings = deepcopy(runtime._settings)
            settings["microphone_settings"]["input_source"] = "console_microphone"
            monkeypatch.setattr(runtime, "_read_audio_settings", lambda: settings)
            runtime.reload_settings()
    assert socket.closed.is_set()
    assert runtime._active_connection is None
    assert connection.lease_generation is None
    assert connection.session_id not in runtime._connections
    assert runtime._worker_thread.is_alive()


def test_resume_failure_in_worker_finally_does_not_stop_the_next_utterance(audio_runtime, monkeypatch):
    runtime = audio_runtime
    socket = ControlSocket("audio_resumed")
    activate(runtime, socket)
    worker = runtime._worker_thread
    second_done = threading.Event()

    def process(item):
        if item.utterance_seq == 1:
            runtime._paused_reason = "queue_full"
        else:
            second_done.set()

    monkeypatch.setattr(runtime, "_process_normal_utterance", process)

    def enqueue(sequence):
        with runtime._condition:
            runtime._waiting.append(QueuedUtterance(
                sequence, "local_microphone", "test", 1, runtime._settings_generation,
                runtime._work_generation, False, SegmentedUtterance(b"", b"", 0, False),
            ))
            runtime._condition.notify_all()

    enqueue(1)
    assert socket.closed.wait(3)
    assert worker.is_alive()
    activate(runtime, ControlSocket())
    enqueue(2)
    assert second_done.wait(3)
    assert runtime._worker_thread is worker
    assert worker.is_alive()


def test_lease_timeout_on_a_closed_socket_keeps_the_watchdog_running(audio_runtime):
    runtime = audio_runtime
    socket = ControlSocket("audio_error")
    connection = activate(runtime, socket)
    with runtime._condition:
        connection.last_heartbeat_monotonic = 0
    assert socket.closed.wait(3)
    assert runtime._watchdog_thread.is_alive()
    assert runtime._active_connection is None
