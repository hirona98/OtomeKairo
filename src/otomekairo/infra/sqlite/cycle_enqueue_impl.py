"""SQLite の入力 enqueue 処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.ui_event_impl import insert_ui_outbound_event_in_transaction
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _now_ms, _opaque_id
from otomekairo.infra.sqlite_store_runtime_view import _pending_input_user_message_payload
from otomekairo.schema.store_errors import StoreConflictError, StoreValidationError
from otomekairo.usecase.camera_observation_payload import build_camera_observation_payload


# Block: チャット入力 enqueue
def enqueue_chat_message(
    backend: SqliteBackend,
    *,
    text: str | None,
    client_message_id: str | None,
    attachments: list[dict[str, Any]],
) -> dict[str, Any]:
    stripped_text = text.strip() if isinstance(text, str) else ""
    if len(stripped_text) > 4000:
        raise StoreValidationError("text is too long")
    if not stripped_text and not attachments:
        raise StoreValidationError("text or attachments must be provided")
    payload: dict[str, Any] = {
        "input_kind": "chat_message",
        "message_kind": "dialogue_turn",
        "trigger_reason": "external_input",
    }
    if stripped_text:
        payload["text"] = stripped_text
    if attachments:
        payload["attachments"] = attachments
    if client_message_id:
        payload["client_message_id"] = client_message_id
    return _enqueue_pending_input(
        backend,
        source="web_input",
        client_message_id=client_message_id,
        payload=payload,
        priority=100,
        emit_user_message_event=True,
    )


# Block: マイク入力 enqueue
def enqueue_microphone_message(
    backend: SqliteBackend,
    *,
    transcript_text: str,
    stt_provider: str,
    stt_language: str,
) -> dict[str, Any]:
    stripped_text = transcript_text.strip()
    stripped_provider = stt_provider.strip()
    stripped_language = stt_language.strip()
    if not stripped_text:
        raise StoreValidationError("transcript_text must be non-empty")
    if len(stripped_text) > 4000:
        raise StoreValidationError("transcript_text is too long")
    if not stripped_provider:
        raise StoreValidationError("stt_provider must be non-empty")
    if not stripped_language:
        raise StoreValidationError("stt_language must be non-empty")
    return _enqueue_pending_input(
        backend,
        source="microphone",
        client_message_id=None,
        payload={
            "input_kind": "microphone_message",
            "message_kind": "dialogue_turn",
            "trigger_reason": "external_input",
            "text": stripped_text,
            "stt_provider": stripped_provider,
            "stt_language": stripped_language,
        },
        priority=100,
        emit_user_message_event=True,
    )


# Block: カメラ観測 enqueue
def enqueue_camera_observation(
    backend: SqliteBackend,
    *,
    camera_connection_id: str,
    camera_display_name: str,
    capture_id: str,
    image_path: str,
    image_url: str,
    captured_at: int,
) -> dict[str, Any]:
    if not isinstance(camera_connection_id, str) or not camera_connection_id:
        raise StoreValidationError("camera_connection_id must be non-empty string")
    if not isinstance(camera_display_name, str) or not camera_display_name:
        raise StoreValidationError("camera_display_name must be non-empty string")
    if not isinstance(capture_id, str) or not capture_id:
        raise StoreValidationError("capture_id must be non-empty string")
    if not isinstance(image_path, str) or not image_path:
        raise StoreValidationError("image_path must be non-empty string")
    if not isinstance(image_url, str) or not image_url:
        raise StoreValidationError("image_url must be non-empty string")
    if isinstance(captured_at, bool) or not isinstance(captured_at, int):
        raise StoreValidationError("captured_at must be integer")
    payload = build_camera_observation_payload(
        camera_connection_id=camera_connection_id,
        camera_display_name=camera_display_name,
        capture_id=capture_id,
        image_path=image_path,
        image_url=image_url,
        captured_at=captured_at,
        trigger_reason="self_initiated",
    )
    enqueue_result = _enqueue_pending_input(
        backend,
        source="camera",
        client_message_id=None,
        payload=payload,
        priority=80,
    )
    return {
        **enqueue_result,
        "camera_connection_id": camera_connection_id,
        "camera_display_name": camera_display_name,
        "capture_id": capture_id,
        "image_path": image_path,
        "image_url": image_url,
        "captured_at": captured_at,
    }


# Block: idle tick enqueue
def enqueue_idle_tick(
    backend: SqliteBackend,
    *,
    idle_duration_ms: int,
) -> dict[str, Any]:
    if isinstance(idle_duration_ms, bool) or not isinstance(idle_duration_ms, int):
        raise StoreValidationError("idle_duration_ms must be integer")
    if idle_duration_ms <= 0:
        raise StoreValidationError("idle_duration_ms must be positive")
    return _enqueue_pending_input(
        backend,
        source="idle_tick",
        client_message_id=None,
        payload={
            "input_kind": "idle_tick",
            "trigger_reason": "idle_tick",
            "idle_duration_ms": idle_duration_ms,
        },
        priority=10,
    )


# Block: cancel enqueue
def enqueue_cancel(
    backend: SqliteBackend,
    *,
    target_message_id: str | None,
) -> dict[str, Any]:
    input_id = _opaque_id("inp")
    now_ms = _now_ms()
    payload: dict[str, Any] = {
        "input_kind": "cancel",
        "trigger_reason": "external_input",
    }
    if target_message_id:
        payload["target_message_id"] = target_message_id
    with backend._connect() as connection:
        connection.execute(
            """
            INSERT INTO pending_inputs (
                input_id,
                source,
                channel,
                client_message_id,
                payload_json,
                created_at,
                priority,
                status
            )
            VALUES (?, 'web_input', 'browser_chat', NULL, ?, ?, ?, 'queued')
            """,
            (
                input_id,
                _json_text(payload),
                now_ms,
                100,
            ),
        )
    return {"accepted": True, "status": "queued"}


# Block: pending input enqueue 共通
def _enqueue_pending_input(
    backend: SqliteBackend,
    *,
    source: str,
    client_message_id: str | None,
    payload: dict[str, Any],
    priority: int,
    emit_user_message_event: bool = False,
) -> dict[str, Any]:
    if not isinstance(source, str) or not source:
        raise StoreValidationError("source must be non-empty string")
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise StoreValidationError("priority must be integer")
    input_id = _opaque_id("inp")
    now_ms = _now_ms()
    try:
        with backend._connect() as connection:
            connection.execute(
                """
                INSERT INTO pending_inputs (
                    input_id,
                    source,
                    channel,
                    client_message_id,
                    payload_json,
                    created_at,
                    priority,
                    status
                )
                VALUES (?, ?, 'browser_chat', ?, ?, ?, ?, 'queued')
                """,
                (
                    input_id,
                    source,
                    client_message_id,
                    _json_text(payload),
                    now_ms,
                    priority,
                ),
            )
            if emit_user_message_event:
                insert_ui_outbound_event_in_transaction(
                    connection=connection,
                    channel="browser_chat",
                    event_type="message",
                    payload=_pending_input_user_message_payload(
                        input_id=input_id,
                        created_at=now_ms,
                        payload=payload,
                    ),
                    source_cycle_id=None,
                    created_at=now_ms,
                )
    except sqlite3.IntegrityError as error:
        raise StoreConflictError(
            "既に受け付けた入力です",
            error_code="duplicate_client_message_id",
        ) from error
    return {
        "accepted": True,
        "input_id": input_id,
        "status": "queued",
        "channel": "browser_chat",
    }
