"""SQLite pending-input and cycle commit implementations."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.event_writer_impl import (
    append_input_journal,
    insert_pending_input_events,
    insert_task_cycle_events,
)
from otomekairo.infra.sqlite.memory_job_impl import enqueue_write_memory_jobs
from otomekairo.infra.sqlite.runtime_lease_impl import sync_commit_log
from otomekairo.infra.sqlite.runtime_live_state_impl import (
    apply_task_state_mutations,
    insert_pending_input_mutations,
    replace_attention_state,
    sync_runtime_live_state,
)
from otomekairo.infra.sqlite.ui_event_impl import insert_ui_outbound_event_in_transaction
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _now_ms, _opaque_id
from otomekairo.infra.sqlite_store_runtime_view import (
    _pending_input_cycle_context,
    _pending_input_receipt_summary,
    _pending_input_user_message_payload,
    _task_cycle_context,
)
from otomekairo.schema.runtime_types import (
    ActionHistoryRecord,
    PendingInputMutationRecord,
    PendingInputRecord,
    TaskStateMutationRecord,
    TaskStateRecord,
)
from otomekairo.schema.store_errors import StoreConflictError, StoreValidationError
from otomekairo.usecase.camera_observation_payload import build_camera_observation_payload
from otomekairo.usecase.observation_normalization import normalize_observation_kind, normalize_observation_source


# Block: Chat input enqueue
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


# Block: Microphone message enqueue
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


# Block: Camera observation enqueue
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


# Block: Idle tick enqueue
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


# Block: Pending input enqueue
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


# Block: Cancel enqueue
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


# Block: Matching cancel claim
def claim_matching_cancel_input(
    backend: SqliteBackend,
    *,
    channel: str,
    target_message_id: str,
) -> PendingInputRecord | None:
    now_ms = _now_ms()
    with backend._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT input_id, source, channel, payload_json, created_at
            FROM pending_inputs
            WHERE status = 'queued'
              AND channel = ?
              AND json_extract(payload_json, '$.input_kind') = 'cancel'
              AND (
                    json_extract(payload_json, '$.target_message_id') IS NULL
                    OR json_extract(payload_json, '$.target_message_id') = ?
              )
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """,
            (channel, target_message_id),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            """
            UPDATE pending_inputs
            SET status = 'claimed',
                claimed_at = ?
            WHERE input_id = ?
              AND status = 'queued'
            """,
            (now_ms, row["input_id"]),
        )
    return PendingInputRecord(
        input_id=row["input_id"],
        source=row["source"],
        channel=row["channel"],
        created_at=int(row["created_at"]),
        payload=json.loads(row["payload_json"]),
    )


# Block: Pending input claim
def claim_next_pending_input(backend: SqliteBackend) -> PendingInputRecord | None:
    now_ms = _now_ms()
    with backend._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT input_id, source, channel, payload_json, created_at
            FROM pending_inputs
            WHERE status = 'queued'
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            """
            UPDATE pending_inputs
            SET status = 'claimed',
                claimed_at = ?
            WHERE input_id = ?
              AND status = 'queued'
            """,
            (now_ms, row["input_id"]),
        )
    return PendingInputRecord(
        input_id=row["input_id"],
        source=row["source"],
        channel=row["channel"],
        created_at=int(row["created_at"]),
        payload=json.loads(row["payload_json"]),
    )


# Block: Pending input discard
def discard_queued_pending_input(
    backend: SqliteBackend,
    *,
    input_id: str,
    discard_reason: str,
) -> bool:
    if not isinstance(input_id, str) or not input_id:
        raise StoreValidationError("input_id must be non-empty string")
    if not isinstance(discard_reason, str) or not discard_reason:
        raise StoreValidationError("discard_reason must be non-empty string")
    resolved_at = _now_ms()
    with backend._connect() as connection:
        updated_row_count = connection.execute(
            """
            UPDATE pending_inputs
            SET status = 'discarded',
                resolved_at = ?,
                discard_reason = ?
            WHERE input_id = ?
              AND status = 'queued'
            """,
            (resolved_at, discard_reason, input_id),
        ).rowcount
    if updated_row_count not in {0, 1}:
        raise StoreConflictError("pending input discard updated unexpected row count")
    return updated_row_count == 1


# Block: Waiting browse task claim
def claim_next_waiting_browse_task(backend: SqliteBackend) -> TaskStateRecord | None:
    now_ms = _now_ms()
    with backend._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT task_id,
                   task_kind,
                   task_status,
                   goal_hint,
                   completion_hint_json,
                   resume_condition_json,
                   interruptible,
                   priority,
                   title,
                   step_hints_json,
                   created_at,
                   updated_at
            FROM task_state
            WHERE task_kind = 'browse'
              AND task_status = 'waiting_external'
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        updated_row_count = connection.execute(
            """
            UPDATE task_state
            SET task_status = 'active',
                updated_at = ?
            WHERE task_id = ?
              AND task_status = 'waiting_external'
            """,
            (now_ms, row["task_id"]),
        ).rowcount
        if updated_row_count != 1:
            return None
    return TaskStateRecord(
        task_id=str(row["task_id"]),
        task_kind=str(row["task_kind"]),
        task_status="active",
        goal_hint=str(row["goal_hint"]),
        completion_hint=json.loads(row["completion_hint_json"]),
        resume_condition=json.loads(row["resume_condition_json"]),
        interruptible=bool(row["interruptible"]),
        priority=int(row["priority"]),
        title=(str(row["title"]) if row["title"] is not None else None),
        step_hints=json.loads(row["step_hints_json"]),
        created_at=int(row["created_at"]),
        updated_at=now_ms,
    )


# Block: Pending input journal append
def append_input_journal_for_pending_input(
    backend: SqliteBackend,
    *,
    pending_input: PendingInputRecord,
    cycle_id: str,
) -> None:
    append_input_journal(
        backend,
        observation_id=f"obs_{pending_input.input_id}",
        cycle_id=cycle_id,
        source=normalize_observation_source(
            source=pending_input.source,
            payload=pending_input.payload,
        ),
        kind=normalize_observation_kind(payload=pending_input.payload),
        captured_at=pending_input.created_at,
        receipt_summary=_pending_input_receipt_summary(pending_input),
        payload_id=pending_input.input_id,
    )


# Block: Pending input finalize
def finalize_pending_input_cycle(
    backend: SqliteBackend,
    *,
    pending_input: PendingInputRecord,
    cycle_id: str,
    resolution_status: str,
    action_results: list[ActionHistoryRecord],
    task_mutations: list[TaskStateMutationRecord],
    pending_input_mutations: list[PendingInputMutationRecord],
    ui_events: list[dict[str, Any]],
    commit_payload: dict[str, Any],
    attention_snapshot: dict[str, Any] | None = None,
    discard_reason: str | None = None,
    camera_available: bool,
) -> int:
    if resolution_status not in {"consumed", "discarded"}:
        raise StoreValidationError("resolution_status is invalid")
    resolved_at = _now_ms()
    with backend._connect() as connection:
        apply_task_state_mutations(
            connection=connection,
            task_mutations=task_mutations,
        )
        if attention_snapshot is not None:
            replace_attention_state(
                connection=connection,
                attention_snapshot=attention_snapshot,
            )
        followup_input_ids = insert_pending_input_mutations(
            connection=connection,
            pending_input_mutations=pending_input_mutations,
        )
        event_ids = insert_pending_input_events(
            connection=connection,
            pending_input=pending_input,
            cycle_id=cycle_id,
            action_results=action_results,
            ui_events=ui_events,
            resolved_at=resolved_at,
        )
        enqueued_memory_job_ids = enqueue_write_memory_jobs(
            connection=connection,
            cycle_id=cycle_id,
            event_ids=event_ids,
            created_at=resolved_at,
        )
        updated_row_count = connection.execute(
            """
            UPDATE pending_inputs
            SET status = ?,
                resolved_at = ?,
                discard_reason = ?
            WHERE input_id = ?
              AND status = 'claimed'
            """,
            (resolution_status, resolved_at, discard_reason, pending_input.input_id),
        ).rowcount
        if updated_row_count != 1:
            raise StoreConflictError("pending input must be claimed before finalization")
        sync_runtime_live_state(
            connection=connection,
            camera_available=camera_available,
            updated_at=resolved_at,
            cycle_context=_pending_input_cycle_context(
                pending_input=pending_input,
                resolution_status=resolution_status,
                action_results=action_results,
                pending_input_mutations=pending_input_mutations,
            ),
        )
        connection.execute(
            """
            INSERT INTO commit_records (
                cycle_id,
                committed_at,
                log_sync_status,
                commit_payload_json
            )
            VALUES (?, ?, 'pending', ?)
            """,
            (
                cycle_id,
                resolved_at,
                _json_text(
                    {
                        **commit_payload,
                        "followup_input_ids": followup_input_ids,
                        "event_ids": event_ids,
                        "enqueued_memory_job_ids": enqueued_memory_job_ids,
                    }
                ),
            ),
        )
        commit_id = connection.execute(
            """
            SELECT commit_id
            FROM commit_records
            WHERE cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()
    if commit_id is None:
        raise RuntimeError("commit_records insert did not persist")
    finalized_commit_id = int(commit_id["commit_id"])
    sync_commit_log(backend, commit_id=finalized_commit_id)
    return finalized_commit_id


# Block: Task finalize
def finalize_task_cycle(
    backend: SqliteBackend,
    *,
    task: TaskStateRecord,
    cycle_id: str,
    final_status: str,
    action_results: list[ActionHistoryRecord],
    pending_input_mutations: list[PendingInputMutationRecord],
    ui_events: list[dict[str, Any]],
    commit_payload: dict[str, Any],
    camera_available: bool,
) -> int:
    if final_status not in {"completed", "abandoned"}:
        raise StoreValidationError("task final_status is invalid")
    resolved_at = _now_ms()
    with backend._connect() as connection:
        updated_row_count = connection.execute(
            """
            UPDATE task_state
            SET task_status = ?,
                updated_at = ?
            WHERE task_id = ?
              AND task_status = 'active'
            """,
            (final_status, resolved_at, task.task_id),
        ).rowcount
        if updated_row_count != 1:
            raise StoreConflictError("task must be active before finalization")
        followup_input_ids = insert_pending_input_mutations(
            connection=connection,
            pending_input_mutations=pending_input_mutations,
        )
        event_ids = insert_task_cycle_events(
            connection=connection,
            cycle_id=cycle_id,
            action_results=action_results,
            ui_events=ui_events,
            resolved_at=resolved_at,
        )
        enqueued_memory_job_ids = enqueue_write_memory_jobs(
            connection=connection,
            cycle_id=cycle_id,
            event_ids=event_ids,
            created_at=resolved_at,
        )
        sync_runtime_live_state(
            connection=connection,
            camera_available=camera_available,
            updated_at=resolved_at,
            cycle_context=_task_cycle_context(
                task=task,
                final_status=final_status,
                action_results=action_results,
                pending_input_mutations=pending_input_mutations,
            ),
        )
        connection.execute(
            """
            INSERT INTO commit_records (
                cycle_id,
                committed_at,
                log_sync_status,
                commit_payload_json
            )
            VALUES (?, ?, 'pending', ?)
            """,
            (
                cycle_id,
                resolved_at,
                _json_text(
                    {
                        **commit_payload,
                        "followup_input_ids": followup_input_ids,
                        "event_ids": event_ids,
                        "enqueued_memory_job_ids": enqueued_memory_job_ids,
                    }
                ),
            ),
        )
        commit_id = connection.execute(
            """
            SELECT commit_id
            FROM commit_records
            WHERE cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()
    if commit_id is None:
        raise RuntimeError("commit_records insert did not persist")
    finalized_commit_id = int(commit_id["commit_id"])
    sync_commit_log(backend, commit_id=finalized_commit_id)
    return finalized_commit_id
