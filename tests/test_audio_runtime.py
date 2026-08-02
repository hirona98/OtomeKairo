import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from otomekairo.audio.runtime import AUDIO_FORMAT
from otomekairo.audio.segmenter import AudioSegmenter, FRAME_BYTES
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
    def test_local_microphone_uses_console_without_windows_device(self) -> None:
        # Windows入力deviceがなくてもconsole clientを応答先としてleaseを開始する。
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
                self.assertEqual(
                    service._audio_runtime.snapshot()["response_client_id"],
                    "console-main",
                )
            finally:
                service.close_audio_runtime()

    def test_web_lease_can_enter_enrollment_when_stt_is_disabled(self) -> None:
        # 通常入力をpauseしたまま同じleaseを話者登録へ切り替える。
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
                        "input_source": "web_microphone",
                    },
                )

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
                        "display_name": "テスト",
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


if __name__ == "__main__":
    unittest.main()
