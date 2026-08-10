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
from otomekairo.event_stream import EventStreamRegistry, ServerWebSocket
from otomekairo.tts.endpoint_health import VoicevoxEndpointHealth, normalize_endpoint_url
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
        super().__init__()
        self.audio = audio
        self.received_text: str | None = None

    def _synthesize_voicevox(
        self,
        text: str,
        config: dict,
        deadline: float,
    ) -> tuple[bytes, str]:
        self.received_text = text
        endpoint = config["endpoint_url"].strip().rstrip("/")
        return self.audio, endpoint


class _CapturingProvider(TtsProvider):
    def __init__(self, audio: bytes, voicevox_endpoint_health=None) -> None:
        super().__init__(voicevox_endpoint_health=voicevox_endpoint_health)
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
    def __init__(self, tts_definition: dict, *, destination: str) -> None:
        self._state = {
            "selected_avatar_id": "avatar:test",
            "audio_output_settings": {
                "destination": destination,
                "local_output_device": None,
            },
            "avatars": {
                "avatar:test": {
                    "tts": deepcopy(tts_definition),
                }
            },
        }

    def read_state(self) -> dict:
        return deepcopy(self._state)


class _Registry:
    def __init__(self, *, accepted_client_kinds: set[str]) -> None:
        self.accepted_client_kinds = set(accepted_client_kinds)
        self.events: list[dict] = []
        self.binary_deliveries: list[tuple[dict, bytes]] = []
        self.binary_client_kinds: list[str | None] = []
        self.delivered = threading.Event()

    def subscriber_count(self, event_type: str, *, client_kind: str | None = None) -> int:
        return int(
            event_type == "assistant_audio"
            and client_kind in self.accepted_client_kinds
        )

    def send_to_subscribers(self, event_type: str, event: dict, *, client_kind: str | None = None) -> int:
        self.events.append(event)
        self.delivered.set()
        return 1

    def send_to_subscribers_with_binary(
        self,
        event_type: str,
        event: dict,
        binary: bytes,
        *,
        client_kind: str | None = None,
    ) -> int:
        self.binary_deliveries.append((event, binary))
        self.binary_client_kinds.append(client_kind)
        self.delivered.set()
        return int(client_kind in self.accepted_client_kinds)


class _Service:
    def __init__(
        self,
        tts_definition: dict,
        *,
        destination: str = "browser",
        accepted_client_kinds: set[str] | None = None,
    ) -> None:
        self.store = _Store(tts_definition, destination=destination)
        self._event_stream_registry = _Registry(
            accepted_client_kinds=(
                {"browser"}
                if accepted_client_kinds is None
                else accepted_client_kinds
            ),
        )
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
        definition = {
            "engine": "voicevox",
            "voicevox_config": {
                "endpoint_url": "http://127.0.0.1:50021",
                "secondary_endpoint_url": "",
            },
        }
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

    def test_voicevox_selects_primary_when_both_healthy(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["engine"] = "voicevox"
        definition["voicevox_config"]["endpoint_url"] = "http://primary.example:50021"
        definition["voicevox_config"]["secondary_endpoint_url"] = (
            "http://secondary.example:50021"
        )
        health = VoicevoxEndpointHealth(
            interval_seconds=3600,
            probe_fn=lambda _url: True,
        )
        self.addCleanup(health.close)
        health.watch(
            definition["voicevox_config"]["endpoint_url"],
            definition["voicevox_config"]["secondary_endpoint_url"],
        )
        provider = _CapturingProvider(_pcm16_wav(), voicevox_endpoint_health=health)

        provider.synthesize("テスト", definition)

        request_url = provider.requests[0]["url"]
        self.assertTrue(request_url.startswith("http://primary.example:50021/"))

    def test_voicevox_selects_secondary_when_primary_unhealthy(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["engine"] = "voicevox"
        primary = "http://primary.example:50021"
        secondary = "http://secondary.example:50021"
        definition["voicevox_config"]["endpoint_url"] = primary
        definition["voicevox_config"]["secondary_endpoint_url"] = secondary
        health = VoicevoxEndpointHealth(
            interval_seconds=3600,
            probe_fn=lambda _url: True,
        )
        self.addCleanup(health.close)
        health.watch(primary, secondary)
        health.mark_unhealthy(primary)
        provider = _CapturingProvider(_pcm16_wav(), voicevox_endpoint_health=health)

        provider.synthesize("テスト", definition)

        request_url = provider.requests[0]["url"]
        self.assertTrue(request_url.startswith(f"{normalize_endpoint_url(secondary)}/"))

    def test_voicevox_marks_endpoint_unhealthy_on_request_failure(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["engine"] = "voicevox"
        primary = "http://primary.example:50021"
        secondary = "http://secondary.example:50021"
        definition["voicevox_config"]["endpoint_url"] = primary
        definition["voicevox_config"]["secondary_endpoint_url"] = secondary
        health = VoicevoxEndpointHealth(
            interval_seconds=3600,
            probe_fn=lambda _url: True,
        )
        self.addCleanup(health.close)

        class _FailingProvider(TtsProvider):
            def _request(self, url: str, **kwargs) -> bytes:
                raise TimeoutError("synthetic timeout")

        provider = _FailingProvider(voicevox_endpoint_health=health)
        with self.assertRaises(TtsProviderError) as raised:
            provider.synthesize("テスト", definition)
        self.assertEqual(raised.exception.error_code, "tts_request_failed")
        self.assertEqual(
            health.select_endpoint(primary, secondary),
            normalize_endpoint_url(secondary),
        )

    def test_voicevox_rejects_missing_secondary_endpoint_url(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["engine"] = "voicevox"
        del definition["voicevox_config"]["secondary_endpoint_url"]
        provider = TtsProvider()

        with self.assertRaises(TtsProviderError) as raised:
            provider.synthesize("テスト", definition)
        self.assertEqual(raised.exception.error_code, "tts_request_failed")

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
        self.assertEqual(event["data"]["destination"], "browser")
        self.assertEqual(
            service._event_stream_registry.binary_client_kinds,
            ["browser"],
        )

    def test_runtime_uses_cocoro_console_when_connected(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["enabled"] = True
        service = _Service(
            definition,
            destination="cocoro_console",
            accepted_client_kinds={"cocoro_console", "otomekairo_audio"},
        )
        runtime = TtsRuntime(service, _StaticProvider(_pcm16_wav()))
        self.addCleanup(runtime.close)

        reservation = runtime.reserve(
            cycle_id="cycle:test",
            source_kind="conversation",
            interaction_ref="interaction:test",
            recipient_person_refs=["person:test"],
            speech_text="テスト",
        )
        runtime.activate(reservation)

        self.assertEqual(reservation.summary["status"], "queued")
        self.assertTrue(service._event_stream_registry.delivered.wait(1.0))
        event, _ = service._event_stream_registry.binary_deliveries[0]
        self.assertEqual(event["data"]["destination"], "cocoro_console")
        self.assertEqual(
            service._event_stream_registry.binary_client_kinds,
            ["cocoro_console"],
        )

    def test_runtime_uses_otomekairo_when_cocoro_console_is_not_connected(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["enabled"] = True
        service = _Service(
            definition,
            destination="cocoro_console",
            accepted_client_kinds={"otomekairo_audio"},
        )
        runtime = TtsRuntime(service, _StaticProvider(_pcm16_wav()))
        self.addCleanup(runtime.close)

        reservation = runtime.reserve(
            cycle_id="cycle:test",
            source_kind="conversation",
            interaction_ref="interaction:test",
            recipient_person_refs=["person:test"],
            speech_text="テスト",
        )
        runtime.activate(reservation)

        self.assertEqual(reservation.summary["status"], "queued")
        self.assertTrue(service._event_stream_registry.delivered.wait(1.0))
        event, _ = service._event_stream_registry.binary_deliveries[0]
        self.assertEqual(event["data"]["destination"], "otomekairo")
        self.assertEqual(
            service._event_stream_registry.binary_client_kinds,
            ["otomekairo_audio"],
        )

    def test_runtime_reports_unavailable_when_cocoro_console_and_otomekairo_are_absent(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["enabled"] = True
        service = _Service(
            definition,
            destination="cocoro_console",
            accepted_client_kinds=set(),
        )
        runtime = TtsRuntime(service, _StaticProvider(_pcm16_wav()))
        self.addCleanup(runtime.close)

        reservation = runtime.reserve(
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
                "status": "failed",
                "error_code": "tts_target_unavailable",
            },
        )

    def test_runtime_keeps_otomekairo_as_the_explicit_destination(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["enabled"] = True
        service = _Service(
            definition,
            destination="otomekairo",
            accepted_client_kinds={"otomekairo_audio"},
        )
        runtime = TtsRuntime(service, _StaticProvider(_pcm16_wav()))
        self.addCleanup(runtime.close)

        reservation = runtime.reserve(
            cycle_id="cycle:test",
            source_kind="conversation",
            interaction_ref="interaction:test",
            recipient_person_refs=["person:test"],
            speech_text="テスト",
        )

        self.assertEqual(reservation.summary["status"], "queued")
        self.assertIsNotNone(reservation.delivery)
        self.assertEqual(reservation.delivery.destination, "otomekairo")
        self.assertEqual(
            reservation.delivery.target_client_kind,
            "otomekairo_audio",
        )

    def test_runtime_does_not_switch_browser_to_otomekairo(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        definition["enabled"] = True
        service = _Service(
            definition,
            destination="browser",
            accepted_client_kinds={"otomekairo_audio"},
        )
        runtime = TtsRuntime(service, _StaticProvider(_pcm16_wav()))
        self.addCleanup(runtime.close)

        reservation = runtime.reserve(
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
                "status": "failed",
                "error_code": "tts_target_unavailable",
            },
        )

    def test_runtime_reports_disabled_without_enqueuing(self) -> None:
        definition = deepcopy(build_default_avatar()["tts"])
        service = _Service(definition)
        runtime = TtsRuntime(service, _StaticProvider(_pcm16_wav()))
        self.addCleanup(runtime.close)

        reservation = runtime.reserve(
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
            cycle_id="cycle:test",
            source_kind="conversation",
            interaction_ref="interaction:test",
            recipient_person_refs=["person:test"],
            speech_text="テスト",
        )

        runtime.close()

        self.assertFalse(runtime._worker.is_alive())


class EventStreamBinaryTests(unittest.TestCase):
    def test_registry_fans_out_by_selected_client_kind(self) -> None:
        class RecordingWebSocket:
            def __init__(self) -> None:
                self.json: list[dict] = []
                self.binary: list[tuple[dict, bytes]] = []

            def send_json(self, payload: dict) -> None:
                self.json.append(payload)

            def send_json_and_binary(self, payload: dict, binary: bytes) -> None:
                self.binary.append((payload, binary))

            def close(self) -> None:
                return

        registry = EventStreamRegistry()
        clients = []
        for index, kind in enumerate(("browser", "browser", "cocoro_console")):
            websocket = RecordingWebSocket()
            session_id = registry.add_connection(websocket)  # type: ignore[arg-type]
            registry.register_hello(
                session_id,
                client_id=f"client:{index}",
                client_kind=kind,
                capabilities={},
                rejected_bindings=[],
                event_subscriptions=["assistant_message", "assistant_audio"],
            )
            clients.append(websocket)

        self.assertEqual(
            registry.send_to_subscribers("assistant_message", {"type": "assistant_message"}),
            3,
        )
        self.assertEqual(
            registry.send_to_subscribers_with_binary(
                "assistant_audio",
                {"type": "assistant_audio"},
                b"wav",
                client_kind="browser",
            ),
            2,
        )
        self.assertEqual([len(client.json) for client in clients], [1, 1, 1])
        self.assertEqual([len(client.binary) for client in clients], [1, 1, 0])

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
