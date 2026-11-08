from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from otomekairo.service.app import OtomeKairoService


STAMP = datetime.fromisoformat("2026-11-07T12:00:00+09:00")


@pytest.fixture
def service(tmp_path):
    service = OtomeKairoService(tmp_path)
    state = service.store.read_state()
    state["personas"][state["selected_persona_id"]]["wake_words"] = ["起動"]
    service.store.write_state(state)
    service._audio_runtime.reload_settings()
    try:
        yield service
    finally:
        service.close_audio_runtime()
        service.close_tts_runtime()


def emit_speech(service, source_kind):
    state = service.store.read_state()
    persona_id = state["selected_persona_id"]
    speech = {
        "persona_id": persona_id,
        "persona_display_name": state["personas"][persona_id]["display_name"],
        "text": "確認が終わりました。",
    }
    if source_kind in {"wake", "background_thinking"}:
        return service._emit_wake_assistant_message_event(
            cycle_id="cycle:activation", trigger_kind=source_kind, client_context={},
            interaction_context=None,
            pipeline={**speech, "speech_payload": {"speech_text": speech["text"]}},
        )
    event_data = {
        "cycle_id": "cycle:activation", "source_kind": source_kind,
        "interaction_ref": "interaction:test", "recipient_person_refs": ["person:test"],
    }
    if source_kind == "conversation":
        response = {**event_data, "speech": speech}
        service._attach_response_audio_delivery(response, source_kind=source_kind)
        return response["speech"]["audio_delivery"]
    _, delivery = service._emit_assistant_message_with_audio(
        event_data={**event_data, **{key: speech[key] for key in ("persona_id", "persona_display_name")}},
        speech_text=speech["text"],
    )
    return delivery


def accepts_reply(runtime):
    return runtime._wake_accepts(explicit_input=False, transcript="ありがとう。", wake_words=["起動"])


@pytest.mark.parametrize("source_kind", ["wake", "background_thinking", "autonomous_run", "capability_result", "conversation"])
@pytest.mark.parametrize("tts_enabled", [False, True])
def test_assistant_speech_accepts_reply_for_180_seconds_without_display_subscribers(service, source_kind, tts_enabled):
    state = service.store.read_state()
    state["avatars"][state["selected_avatar_id"]]["tts"]["enabled"] = tts_enabled
    service.store.write_state(state)
    runtime = service._audio_runtime
    registry = service._event_stream_registry
    with patch("otomekairo.audio.runtime.time.monotonic", return_value=1000.0), patch(
        "otomekairo.audio.runtime.local_now", return_value=STAMP,
    ), patch.object(registry, "send_to_subscribers", wraps=registry.send_to_subscribers) as send:
        assert accepts_reply(runtime) == (False, None)
        delivery = emit_speech(service, source_kind)
        assert delivery["status"] == ("failed" if tts_enabled else "disabled")
        if tts_enabled:
            assert delivery["error_code"] == "tts_target_unavailable"
        assert accepts_reply(runtime) == (True, None)
        assert runtime.snapshot()["normal_activation"] == {
            "state": "active", "active_until": (STAMP + timedelta(seconds=180)).isoformat(),
            "wake_words": ["起動"],
        }
        events = [call.args[1] for call in send.call_args_list]
        assert [event["type"] for event in events] == ["audio_runtime_state", "assistant_message"]
        assert events[0]["data"]["normal_activation"] == runtime.snapshot()["normal_activation"]
        assert events[0]["event_id"] < events[1]["event_id"]
    with patch("otomekairo.audio.runtime.time.monotonic", return_value=1179.999):
        assert accepts_reply(runtime) == (True, None)
    with patch("otomekairo.audio.runtime.time.monotonic", return_value=1180.0):
        assert accepts_reply(runtime) == (False, None)
        assert runtime.snapshot()["normal_activation"]["state"] == "waiting"


def test_later_assistant_speech_extends_reply_window(service):
    with patch("otomekairo.audio.runtime.time.monotonic", return_value=1000.0), patch(
        "otomekairo.audio.runtime.local_now", return_value=STAMP,
    ):
        emit_speech(service, "wake")
    later = STAMP + timedelta(seconds=120)
    with patch("otomekairo.audio.runtime.time.monotonic", return_value=1120.0), patch(
        "otomekairo.audio.runtime.local_now", return_value=later,
    ):
        emit_speech(service, "background_thinking")
        assert service._audio_runtime.snapshot()["normal_activation"]["active_until"] == (
            later + timedelta(seconds=180)
        ).isoformat()
    with patch("otomekairo.audio.runtime.time.monotonic", return_value=1180.0):
        assert accepts_reply(service._audio_runtime) == (True, None)
    with patch("otomekairo.audio.runtime.time.monotonic", return_value=1300.0):
        assert accepts_reply(service._audio_runtime) == (False, None)


def test_background_cycle_without_speech_keeps_reply_window_unchanged(service):
    with patch("otomekairo.audio.runtime.time.monotonic", return_value=1000.0), patch(
        "otomekairo.audio.runtime.local_now", return_value=STAMP,
    ):
        before = service._audio_runtime.snapshot()["normal_activation"]
        result = service._emit_wake_assistant_message_event(
            cycle_id="cycle:noop", trigger_kind="background_thinking", client_context={},
            interaction_context=None, pipeline={"speech_payload": None},
        )
        assert result is None
        assert service._audio_runtime.snapshot()["normal_activation"] == before
        assert accepts_reply(service._audio_runtime) == (False, None)


def test_startup_waits_for_configured_wake_word_and_identifies_mismatch(service):
    runtime = service._audio_runtime
    assert runtime.snapshot()["normal_activation"] == {
        "state": "waiting", "active_until": None, "wake_words": ["起動"],
    }
    assert runtime._wake_accepts(
        explicit_input=False, transcript="起動、おはよう。", wake_words=["起動"],
    ) == (True, "起動")
    assert accepts_reply(runtime) == (False, None)


def test_empty_wake_words_report_continuous_activation(service):
    state = service.store.read_state()
    state["personas"][state["selected_persona_id"]]["wake_words"] = []
    service.store.write_state(state)
    runtime = service._audio_runtime
    runtime.reload_settings()
    assert runtime.snapshot()["normal_activation"] == {
        "state": "active", "active_until": None, "wake_words": [],
    }
    assert runtime._wake_accepts(
        explicit_input=False, transcript="おはよう。", wake_words=[],
    ) == (True, None)
