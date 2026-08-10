import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from otomekairo.audio.models import SpeakerIdentification
from otomekairo.audio.runtime import AUDIO_FORMAT, QueuedUtterance
from otomekairo.audio.segmenter import (
    AudioSegmenter,
    FRAME_BYTES,
    SegmentedUtterance,
)
from otomekairo.service.app import OtomeKairoService


class FakeVad:
    def __init__(self, probabilities: list[float]) -> None:
        self.probabilities = iter(probabilities)

    def reset(self) -> None:
        return

    def float32_samples(self, samples: list[int]) -> list[int]:
        return samples

    def infer(self, samples: list[int]) -> float:
        return next(self.probabilities)


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    def close(self) -> None:
        self.closed = True


class AudioSegmenterTests(unittest.TestCase):
    def test_builds_exact_prebuffer_and_trailing_views(self) -> None:
        # 512 sampleの陽性16回で最短発話を成立させ、末尾500msを切り出す。
        vad = FakeVad([0.9] * 16 + [0.1] * 16)
        segmenter = AudioSegmenter(vad, probability_threshold=0.5)
        utterances = []

        for _ in range(52):
            update = segmenter.feed_frame(bytes(FRAME_BYTES))
            utterances.extend(update.utterances)

        self.assertEqual(len(utterances), 1)
        utterance = utterances[0]
        self.assertEqual(utterance.voiced_samples, 8192)
        self.assertEqual(len(utterance.speaker_pcm16le), 8192 * 2)
        self.assertEqual(len(utterance.amivoice_pcm16le), (8192 + 8000) * 2)


class AudioRuntimeControlTests(unittest.TestCase):
    def test_local_microphone_starts_without_display_delivery_target(self) -> None:
        # チャット配送先とは独立してローカル入力leaseを開始する。
        with TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            try:
                state = service.store.read_state()
                state["console_access_token"] = "token"
                state["microphone_settings"]["local_input_device"] = {
                    "host_api": "ALSA",
                    "name": "USB Audio Device",
                }
                state["microphone_settings"]["console"] = {
                    "client_id": "console-main",
                    "input_device": None,
                }
                service.store.write_state(state)
                service._audio_runtime.reload_settings()

                event_socket = FakeWebSocket()
                event_session_id = service.register_event_stream_connection(
                    event_socket
                )
                service.handle_event_stream_message(
                    event_session_id,
                    {
                        "type": "hello",
                        "client_id": "console-main",
                        "client_kind": "cocoro_console",
                        "caps": [],
                        "event_subscriptions": [
                            "conversation_input",
                            "assistant_message",
                            "audio_runtime_state",
                        ],
                    },
                )

                audio_socket = FakeWebSocket()
                audio_session_id = service.register_audio_stream_connection(
                    audio_socket,
                    endpoint_source="local_microphone",
                )
                service.handle_audio_stream_message(
                    audio_session_id,
                    "text",
                    json.dumps(
                        {
                            "type": "audio_start",
                            "protocol_version": "2",
                            "client_id": "microphone-connector-main",
                            "input_source": "local_microphone",
                            "input_session_id": None,
                            "format": AUDIO_FORMAT,
                            "device": {
                                "host_api": "ALSA",
                                "name": "USB Audio Device",
                            },
                            "capture_settings": {
                                "source_sample_rate": 48000,
                            },
                        }
                    ).encode("utf-8"),
                )

                self.assertEqual(audio_socket.sent[0]["type"], "audio_started")
                self.assertEqual(
                    audio_socket.sent[0]["paused_reason"],
                    "stt_disabled",
                )
                self.assertNotIn("response_client_id", service._audio_runtime.snapshot())
            finally:
                service.close_audio_runtime()

    def test_web_lease_can_enter_enrollment_when_stt_is_disabled(self) -> None:
        # 通常入力をpauseしたまま同じleaseを話者登録へ切り替える。
        with TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            try:
                state = service.store.read_state()
                state["console_access_token"] = "token"
                state["microphone_settings"]["input_source"] = "web_microphone"
                service.store.write_state(state)
                service._audio_runtime.reload_settings()

                display_name = service.create_conversation_display_name(
                    "token",
                    {"display_name": "テスト"},
                )

                event_socket = FakeWebSocket()
                event_session_id = service.register_event_stream_connection(
                    event_socket
                )
                service.handle_event_stream_message(
                    event_session_id,
                    {
                        "type": "hello",
                        "client_id": "web-audio-test",
                        "client_kind": "browser",
                        "caps": [],
                        "event_subscriptions": [
                            "conversation_input",
                            "assistant_message",
                            "audio_runtime_state",
                        ],
                    },
                )

                input_session = service.start_web_audio_input_session(
                    "token",
                    {
                        "owner_client_id": "web-audio-test",
                    },
                )
                self.assertEqual(input_session["input_source"], "web_microphone")

                audio_socket = FakeWebSocket()
                audio_session_id = service.register_audio_stream_connection(
                    audio_socket,
                    endpoint_source="web_microphone",
                )
                service.handle_audio_stream_message(
                    audio_session_id,
                    "text",
                    json.dumps(
                        {
                            "type": "audio_start",
                            "protocol_version": "2",
                            "client_id": "web-audio-test",
                            "input_source": "web_microphone",
                            "input_session_id": input_session[
                                "input_session_id"
                            ],
                            "format": AUDIO_FORMAT,
                            "device": None,
                            "capture_settings": {
                                "source_sample_rate": 48000,
                                "echo_cancellation": None,
                                "noise_suppression": None,
                                "auto_gain_control": False,
                                "device_id_present": True,
                            },
                        }
                    ).encode("utf-8"),
                )

                self.assertEqual(audio_socket.sent[0]["type"], "audio_started")
                self.assertEqual(
                    audio_socket.sent[0]["paused_reason"],
                    "stt_disabled",
                )

                enrollment = service.start_audio_speaker_enrollment(
                    "token",
                    {
                        "owner_client_id": "web-audio-test",
                        "conversation_display_name_id": display_name[
                            "conversation_display_name_id"
                        ],
                    },
                )

                self.assertEqual(enrollment["completed_samples"], 0)
                self.assertEqual(audio_socket.sent[-1]["type"], "audio_resumed")
                self.assertEqual(
                    service._audio_runtime.snapshot()["mode"],
                    "enrollment",
                )
            finally:
                service.close_audio_runtime()

    def test_web_input_session_uses_each_saved_source(self) -> None:
        # Web入力sessionはrequest値ではなく保存済み入力元からsourceを確定する。
        with TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            try:
                state = service.store.read_state()
                state["console_access_token"] = "token"
                service.store.write_state(state)

                event_socket = FakeWebSocket()
                event_session_id = service.register_event_stream_connection(
                    event_socket
                )
                service.handle_event_stream_message(
                    event_session_id,
                    {
                        "type": "hello",
                        "client_id": "web-audio-test",
                        "client_kind": "browser",
                        "caps": [],
                        "event_subscriptions": [
                            "conversation_input",
                            "assistant_message",
                            "audio_runtime_state",
                        ],
                    },
                )

                for input_source in (
                    "local_microphone",
                    "console_microphone",
                    "web_microphone",
                ):
                    with self.subTest(input_source=input_source):
                        state = service.store.read_state()
                        state["microphone_settings"]["input_source"] = input_source
                        service.store.write_state(state)
                        service._audio_runtime.reload_settings()

                        session = service.start_web_audio_input_session(
                            "token",
                            {"owner_client_id": "web-audio-test"},
                        )

                        self.assertEqual(session["input_source"], input_source)
                        self.assertNotIn("response_client_id", service._audio_runtime.snapshot())
                        service.stop_web_audio_input_session(
                            "token",
                            session["input_session_id"],
                        )

                state = service.store.read_state()
                state["microphone_settings"]["input_source"] = "local_microphone"
                service.store.write_state(state)
                service._audio_runtime.reload_settings()
                service.start_web_audio_input_session(
                    "token",
                    {"owner_client_id": "web-audio-test"},
                )
                state = service.store.read_state()
                state["microphone_settings"]["vad_probability_threshold"] = 0.55
                service.store.write_state(state)

                service._audio_runtime.reload_settings()

                self.assertIsNone(service._audio_runtime._web_input_session)
            finally:
                service.close_audio_runtime()

    def test_console_microphone_web_session_has_no_response_client_state(self) -> None:
        # Web入力session中もチャット配送先をruntime stateへ持たない。
        with TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            try:
                state = service.store.read_state()
                state["console_access_token"] = "token"
                state["microphone_settings"]["input_source"] = "console_microphone"
                state["microphone_settings"]["console"] = {
                    "client_id": "console-main",
                    "input_device": {
                        "device_id": "console-device",
                        "name": "Console Microphone",
                    },
                }
                service.store.write_state(state)
                service._audio_runtime.reload_settings()

                event_socket = FakeWebSocket()
                event_session_id = service.register_event_stream_connection(
                    event_socket
                )
                service.handle_event_stream_message(
                    event_session_id,
                    {
                        "type": "hello",
                        "client_id": "web-audio-test",
                        "client_kind": "browser",
                        "caps": [],
                        "event_subscriptions": [
                            "conversation_input",
                            "assistant_message",
                            "audio_runtime_state",
                        ],
                    },
                )
                service.start_web_audio_input_session(
                    "token",
                    {"owner_client_id": "web-audio-test"},
                )

                audio_socket = FakeWebSocket()
                audio_session_id = service.register_audio_stream_connection(
                    audio_socket,
                    endpoint_source="console_microphone",
                )
                service.handle_audio_stream_message(
                    audio_session_id,
                    "text",
                    json.dumps(
                        {
                            "type": "audio_start",
                            "protocol_version": "2",
                            "client_id": "console-main",
                            "input_source": "console_microphone",
                            "input_session_id": None,
                            "format": AUDIO_FORMAT,
                            "device": {
                                "device_id": "console-device",
                                "name": "Console Microphone",
                            },
                            "capture_settings": {
                                "source_sample_rate": 48000,
                            },
                        }
                    ).encode("utf-8"),
                )

                self.assertEqual(audio_socket.sent[0]["type"], "audio_started")
                self.assertNotIn("response_client_id", service._audio_runtime.snapshot())
            finally:
                service.close_audio_runtime()

    def test_last_utterance_result_exposes_threshold_met(self) -> None:
        # 識別 similarity としきい値超過を result_code に依存せず返す。
        with TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            try:
                state = service.store.read_state()
                state["microphone_settings"][
                    "speaker_recognition_threshold"
                ] = 0.6
                service.store.write_state(state)
                service._audio_runtime.reload_settings()

                item = QueuedUtterance(
                    utterance_seq=1,
                    source="local_microphone",
                    source_client_id="microphone-connector-main",
                    lease_generation=1,
                    settings_generation=1,
                    work_generation=1,
                    explicit_input=False,
                    utterance=SegmentedUtterance(
                        amivoice_pcm16le=b"\x00\x00" * 8000,
                        speaker_pcm16le=b"\x00\x00" * 8000,
                        voiced_samples=8000,
                        forced_split=False,
                    ),
                )
                accepted = service._audio_runtime._build_last_result(
                    item=item,
                    result_code="accepted",
                    stt_ms=10.0,
                    speaker_ms=5.0,
                    stt_error=None,
                    identification=SpeakerIdentification(
                        person_ref="person:voice:test",
                        accepted=True,
                        top1_person_ref="person:voice:test",
                        top1_similarity=0.72,
                        top2_person_ref=None,
                        top2_similarity=None,
                    ),
                )
                self.assertEqual(accepted["top1_similarity"], 0.72)
                self.assertIsNone(accepted["top2_similarity"])
                self.assertTrue(accepted["threshold_met"])
                self.assertNotIn("speaker_candidates", accepted)

                unidentified = service._audio_runtime._build_last_result(
                    item=item,
                    result_code="speaker_unidentified",
                    stt_ms=10.0,
                    speaker_ms=5.0,
                    stt_error=None,
                    identification=SpeakerIdentification(
                        person_ref=None,
                        accepted=False,
                        top1_person_ref="person:voice:a",
                        top1_similarity=0.55,
                        top2_person_ref="person:voice:b",
                        top2_similarity=0.5,
                    ),
                )
                self.assertEqual(unidentified["top1_similarity"], 0.55)
                self.assertEqual(unidentified["top2_similarity"], 0.5)
                self.assertFalse(unidentified["threshold_met"])
                self.assertEqual(
                    unidentified["speaker_candidates"],
                    [
                        {
                            "person_ref": "person:voice:a",
                            "similarity": 0.55,
                        },
                        {
                            "person_ref": "person:voice:b",
                            "similarity": 0.5,
                        },
                    ],
                )
            finally:
                service.close_audio_runtime()


if __name__ == "__main__":
    unittest.main()
