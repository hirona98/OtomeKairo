from __future__ import annotations

import queue
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from otomekairo.service.common import debug_log
from otomekairo.tts.endpoint_health import VoicevoxEndpointHealth
from otomekairo.tts.provider import TtsProvider, TtsProviderError


TTS_QUEUE_CAPACITY = 32


@dataclass(slots=True)
class _TtsDelivery:
    delivery_id: str
    target_client_kind: str
    destination: str
    cycle_id: str
    source_kind: str
    interaction_ref: str
    recipient_person_refs: list[str]
    speech_text: str
    tts_definition: dict[str, Any]
    ready: threading.Event = field(default_factory=threading.Event)
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class TtsDeliveryReservation:
    summary: dict[str, Any]
    delivery: _TtsDelivery | None


class TtsRuntime:
    def __init__(
        self,
        service: Any,
        provider: TtsProvider | None = None,
        voicevox_endpoint_health: VoicevoxEndpointHealth | None = None,
    ) -> None:
        self._service = service
        # provider 未指定時はヘルス監視付き provider を用意する。
        self._owns_voicevox_endpoint_health = voicevox_endpoint_health is None and provider is None
        self._voicevox_endpoint_health = (
            voicevox_endpoint_health
            if voicevox_endpoint_health is not None
            else (VoicevoxEndpointHealth() if provider is None else None)
        )
        if provider is not None:
            self._provider = provider
        else:
            self._provider = TtsProvider(
                voicevox_endpoint_health=self._voicevox_endpoint_health,
            )
        self._queue: queue.Queue[_TtsDelivery | None] = queue.Queue(
            maxsize=TTS_QUEUE_CAPACITY
        )
        self._stop_event = threading.Event()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="otomekairo-tts-worker",
            daemon=True,
        )
        self._worker.start()

    def reserve(
        self,
        *,
        cycle_id: str,
        source_kind: str,
        interaction_ref: str,
        recipient_person_refs: list[str],
        speech_text: str,
    ) -> TtsDeliveryReservation:
        state = self._service.store.read_state()
        avatar = state["avatars"][state["selected_avatar_id"]]
        tts_definition = avatar["tts"]
        if tts_definition.get("enabled") is not True:
            return self._terminal_reservation("disabled", None)

        destination = state["audio_output_settings"]["destination"]
        # CocoroConsole が起動していない場合は、発話受付時点でローカル出力へ切り替える。
        if (
            destination == "cocoro_console"
            and self._service._event_stream_registry.subscriber_count(
                "assistant_audio",
                client_kind="cocoro_console",
            ) == 0
        ):
            destination = "otomekairo"
        target_client_kind = {
            "otomekairo": "otomekairo_audio",
            "cocoro_console": "cocoro_console",
            "browser": "browser",
        }[destination]
        if self._service._event_stream_registry.subscriber_count(
            "assistant_audio",
            client_kind=target_client_kind,
        ) == 0:
            return self._terminal_reservation(
                "failed",
                "tts_target_unavailable",
            )

        delivery_id = f"tts_delivery:{uuid.uuid4().hex}"
        delivery = _TtsDelivery(
            delivery_id=delivery_id,
            target_client_kind=target_client_kind,
            destination=destination,
            cycle_id=cycle_id,
            source_kind=source_kind,
            interaction_ref=interaction_ref,
            recipient_person_refs=list(recipient_person_refs),
            speech_text=speech_text,
            tts_definition=deepcopy(tts_definition),
        )
        try:
            self._queue.put_nowait(delivery)
        except queue.Full:
            return self._terminal_reservation(
                "failed",
                "tts_queue_full",
            )
        return TtsDeliveryReservation(
            summary={
                "delivery_id": delivery_id,
                "status": "queued",
                "error_code": None,
            },
            delivery=delivery,
        )

    def activate(self, reservation: TtsDeliveryReservation) -> None:
        if reservation.delivery is not None:
            reservation.delivery.ready.set()

    def cancel(self, reservation: TtsDeliveryReservation) -> None:
        if reservation.delivery is None:
            return
        reservation.delivery.cancelled = True
        reservation.delivery.ready.set()

    def close(self) -> None:
        self._stop_event.set()
        while True:
            try:
                pending = self._queue.get_nowait()
            except queue.Empty:
                break
            if pending is not None:
                pending.cancelled = True
                pending.ready.set()
            self._queue.task_done()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker.is_alive():
            self._worker.join(timeout=5.0)
        if self._owns_voicevox_endpoint_health and self._voicevox_endpoint_health is not None:
            self._voicevox_endpoint_health.close()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            delivery = self._queue.get()
            try:
                if delivery is None:
                    return
                while not delivery.ready.wait(timeout=0.1):
                    if self._stop_event.is_set():
                        return
                if delivery.cancelled or self._stop_event.is_set():
                    continue
                self._execute(delivery)
            finally:
                self._queue.task_done()

    def _execute(self, delivery: _TtsDelivery) -> None:
        if self._service._event_stream_registry.subscriber_count(
            "assistant_audio",
            client_kind=delivery.target_client_kind,
        ) == 0:
            debug_log(
                "TTS",
                (
                    f"delivery skipped target_unavailable "
                    f"delivery={delivery.delivery_id} destination={delivery.destination}"
                ),
                level="WARNING",
            )
            return
        try:
            audio = self._provider.synthesize(
                delivery.speech_text,
                delivery.tts_definition,
            )
        except TtsProviderError as exc:
            self._send_failure(delivery, exc.error_code)
            debug_log(
                "TTS",
                (
                    f"delivery failed delivery={delivery.delivery_id} "
                    f"destination={delivery.destination} code={exc.error_code}"
                ),
                level="WARNING",
            )
            return
        except Exception as exc:  # noqa: BLE001
            self._send_failure(delivery, "tts_request_failed")
            debug_log(
                "TTS",
                (
                    f"delivery failed delivery={delivery.delivery_id} "
                    f"destination={delivery.destination} error={type(exc).__name__}"
                ),
                level="ERROR",
            )
            return

        event = self._event(
            delivery,
            status="succeeded",
            media_type=audio.media_type,
            byte_count=len(audio.data),
            error_code=None,
        )
        sent = self._service._event_stream_registry.send_to_subscribers_with_binary(
            "assistant_audio",
            event,
            audio.data,
            client_kind=delivery.target_client_kind,
        )
        debug_log(
            "TTS",
            (
                f"delivery completed delivery={delivery.delivery_id} "
                f"destination={delivery.destination} sent={sent} bytes={len(audio.data)}"
            ),
            level="DEBUG",
        )

    def _send_failure(self, delivery: _TtsDelivery, error_code: str) -> None:
        self._service._event_stream_registry.send_to_subscribers(
            "assistant_audio",
            self._event(
                delivery,
                status="failed",
                media_type=None,
                byte_count=0,
                error_code=error_code,
            ),
            client_kind=delivery.target_client_kind,
        )

    def _event(
        self,
        delivery: _TtsDelivery,
        *,
        status: str,
        media_type: str | None,
        byte_count: int,
        error_code: str | None,
    ) -> dict[str, Any]:
        return {
            "event_id": self._service._next_stream_event_id(),
            "type": "assistant_audio",
            "data": {
                "delivery_id": delivery.delivery_id,
                "destination": delivery.destination,
                "cycle_id": delivery.cycle_id,
                "source_kind": delivery.source_kind,
                "interaction_ref": delivery.interaction_ref,
                "recipient_person_refs": list(delivery.recipient_person_refs),
                "status": status,
                "media_type": media_type,
                "byte_count": byte_count,
                "error_code": error_code,
            },
        }

    def _terminal_reservation(
        self,
        status: str,
        error_code: str | None,
    ) -> TtsDeliveryReservation:
        return TtsDeliveryReservation(
            summary={
                "delivery_id": None,
                "status": status,
                "error_code": error_code,
            },
            delivery=None,
        )
