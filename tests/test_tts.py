import io
import json
import threading
import time
import unittest
import urllib.parse
import wave
from copy import deepcopy
from types import MethodType

from otomekairo.defaults import build_default_avatar
from otomekairo.event_stream import ServerWebSocket
from otomekairo.tts.provider import (
    SynthesizedAudio,
    TtsProvider,
    TtsProviderError,
    speech_text_for_tts,
)
from otomekairo.tts.runtime import TtsRuntime


def _pcm16_wav(sample_count: int = 8) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * sample_count)
    return output.getvalue()


class _StaticProvider(TtsProvider):
    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.received_text: str | None = None

    def _synthesize_voicevox(
        self,
        text: str,
        config: dict,
        deadline: float,
    ) -> bytes:
        self.received_text = text
        return self.audio


class _CapturingProvider(TtsProvider):
    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.requests: list[dict] = []

    def _request(self, url: str, **kwargs) -> bytes:
        self.requests.append({"url": url, **kwargs})
        if "/audio_query?" in url:
            return json.dumps(
                {
                    "speedScale": 1.0,
                    "pitchScale": 0.0,
                    "intonationScale": 1.0,
                    "volumeScale": 1.0,
                    "prePhonemeLength": 0.1,
                    "postPhonemeLength": 0.1,
                    "outputSamplingRate": 24000,
                    "outputStereo": False,
                }
            ).encode("utf-8")
        return self.audio


class _Store:
    def __init__(self, tts_definition: dict) -> None:
        self._state = {
            "selected_avatar_id": "avatar:test",
            "avatars": {
                "avatar:test": {
                    "tts": deepcopy(tts_definition),
                }
            },
        }

    def read_state(self) -> dict:
        return deepcopy(self._state)


class _Registry:
    def __init__(self) -> None:
        self.accepted = True
        self.events: list[dict] = []
        self.binary_deliveries: list[tuple[dict, bytes]] = []
        self.delivered = threading.Event()

    def client_accepts_event(self, client_id: str, event_type: str) -> bool:
        return self.accepted and client_id == "client:test" and event_type == "assistant_audio"

    def send_to_client(self, client_id: str, event: dict) -> bool:
        self.events.append(event)
        self.delivered.set()
        return True

    def send_to_client_with_binary(
        self,
        client_id: str,
        event: dict,
        binary: bytes,
    ) -> bool:
        self.binary_deliveries.append((event, binary))
        self.delivered.set()
        return True


class _Service:
    def __init__(self, tts_definition: dict) -> None:
        self.store = _Store(tts_definition)
        self._event_stream_registry = _Registry()
        self._event_id = 0

    def _next_stream_event_id(self) -> int:
        self._event_id += 1
        return self._event_id


class TtsProviderTests(unittest.TestCase):
    def test_expression_protocol_removes_only_known_leading_tag(self) -> None:
        self.assertEqual(speech_text_for_tts("[face:Joy]  こんにちは"), "  こんにちは")
        self.assertEqual(speech_text_for_tts(" [face:Joy]こんにちは"), " [face:Joy]こんにちは")
        self.assertEqual(speech_text_for_tts("[face:Unknown]こんにちは"), "[face:Unknown]こんにちは")
        self.assertEqual(speech_text_for_tts("途中[face:Joy]です"), "途中[face:Joy]です")

    def test_provider_accepts_only_supported_wav(self) -> None:
        definition = {"engine": "voicevox", "voicevox_config": {}}
        provider = _StaticProvider(_pcm16_wav())

        synthesized = provider.synthesize("[face:Fun]\nそのまま読む", definition)

        self.assertIsInstance(synthesized, SynthesizedAudio)
        self.assertEqual(provider.received_text, "\nそのまま読む")
        with self.assertRaises(TtsProviderError) as raised:
            _StaticProvider(b"not-wave").synthesize("音声", definition)
        self.assertEqual(raised.exception.error_code, "tts_response_invalid")

    def test_voicevox_uses_two_step_api_and_preserves_text(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["engine"] = "voicevox"
        provider = _CapturingProvider(_pcm16_wav())

        provider.synthesize("[face:Sorrow]  テスト", definition)

        self.assertEqual(len(provider.requests), 2)
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(provider.requests[0]["url"]).query
        )
        self.assertEqual(query["text"], ["  テスト"])
        synthesis_body = json.loads(provider.requests[1]["body"].decode("utf-8"))
        self.assertEqual(synthesis_body["outputSamplingRate"], 24000)

    def test_style_bert_vits2_uses_voice_endpoint(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["engine"] = "style-bert-vits2"
        provider = _CapturingProvider(_pcm16_wav())

        provider.synthesize("テスト", definition)

        self.assertEqual(len(provider.requests), 1)
        request_url = urllib.parse.urlsplit(provider.requests[0]["url"])
        self.assertEqual(request_url.path, "/voice")
        query = urllib.parse.parse_qs(request_url.query)
        self.assertEqual(query["text"], ["テスト"])
        self.assertEqual(query["auto_split"], ["true"])

    def test_aivis_cloud_forces_wav_and_zero_bitrate(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["engine"] = "aivis-cloud"
        definition["aivis_cloud_config"]["api_key"] = "test-secret"
        provider = _CapturingProvider(_pcm16_wav())

        provider.synthesize("テスト", definition)

        payload = json.loads(provider.requests[0]["body"].decode("utf-8"))
        self.assertEqual(payload["output_format"], "wav")
        self.assertEqual(payload["output_bitrate"], 0)
        self.assertEqual(
            provider.requests[0]["authorization"],
            "Bearer test-secret",
        )


class TtsRuntimeTests(unittest.TestCase):
    def test_runtime_delivers_metadata_and_binary_after_activation(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["enabled"] = True
        provider = _StaticProvider(_pcm16_wav())
        service = _Service(definition)
        runtime = TtsRuntime(service, provider)
        self.addCleanup(runtime.close)

        reservation = runtime.reserve(
            target_client_id="client:test",
            cycle_id="cycle:test",
            source_kind="conversation",
            interaction_ref="interaction:test",
            recipient_person_refs=["person:test"],
            speech_text="テスト",
        )
        self.assertEqual(reservation.summary["status"], "queued")
        self.assertFalse(service._event_stream_registry.delivered.wait(0.02))

        runtime.activate(reservation)

        self.assertTrue(service._event_stream_registry.delivered.wait(1.0))
        event, binary = service._event_stream_registry.binary_deliveries[0]
        self.assertEqual(event["type"], "assistant_audio")
        self.assertEqual(event["data"]["status"], "succeeded")
        self.assertEqual(event["data"]["byte_count"], len(binary))
        self.assertEqual(event["data"]["media_type"], "audio/wav")

    def test_runtime_reports_disabled_without_enqueuing(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        service = _Service(definition)
        runtime = TtsRuntime(service, _StaticProvider(_pcm16_wav()))
        self.addCleanup(runtime.close)

        reservation = runtime.reserve(
            target_client_id="client:test",
            cycle_id="cycle:test",
            source_kind="conversation",
            interaction_ref="interaction:test",
            recipient_person_refs=["person:test"],
            speech_text="テスト",
        )

        self.assertEqual(
            reservation.summary,
            {
                "delivery_id": None,
                "status": "disabled",
                "error_code": None,
            },
        )

    def test_runtime_close_releases_unactivated_delivery(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["enabled"] = True
        service = _Service(definition)
        runtime = TtsRuntime(service, _StaticProvider(_pcm16_wav()))
        runtime.reserve(
            target_client_id="client:test",
            cycle_id="cycle:test",
            source_kind="conversation",
            interaction_ref="interaction:test",
            recipient_person_refs=["person:test"],
            speech_text="テスト",
        )

        runtime.close()

        self.assertFalse(runtime._worker.is_alive())


class EventStreamBinaryTests(unittest.TestCase):
    def test_metadata_and_binary_are_an_atomic_send_unit(self) -> None:
        websocket = ServerWebSocket(None)  # type: ignore[arg-type]
        recorded: list[tuple[int, bytes]] = []

        def record_frame(self, *, opcode: int, payload: bytes) -> None:
            recorded.append((opcode, payload))
            if opcode == 0x1:
                # 並行送信が割り込める時間を作り、send lockの境界を検証する。
                time.sleep(0.01)

        websocket._send_frame_unlocked = MethodType(  # type: ignore[method-assign]
            record_frame,
            websocket,
        )
        start = threading.Barrier(3)

        def send(delivery_id: str) -> None:
            start.wait()
            websocket.send_json_and_binary(
                {"delivery_id": delivery_id},
                delivery_id.encode("ascii"),
            )

        threads = [
            threading.Thread(target=send, args=("first",)),
            threading.Thread(target=send, args=("second",)),
        ]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=1.0)

        self.assertEqual([opcode for opcode, _ in recorded], [0x1, 0x2, 0x1, 0x2])
        for index in (0, 2):
            metadata = json.loads(recorded[index][1].decode("utf-8"))
            self.assertEqual(
                recorded[index + 1][1],
                metadata["delivery_id"].encode("ascii"),
            )


if __name__ == "__main__":
    unittest.main()
