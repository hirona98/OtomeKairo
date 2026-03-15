"""SQLite の pending input cycle 処理。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.event_writer_impl import append_input_journal, insert_pending_input_events
from otomekairo.infra.sqlite.memory_job_impl import enqueue_write_memory_jobs
from otomekairo.infra.sqlite.runtime_lease_impl import sync_commit_log
from otomekairo.infra.sqlite.runtime_live_state_impl import (
    apply_task_state_mutations,
    insert_pending_input_mutations,
    replace_attention_state,
    sync_runtime_live_state,
)
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _now_ms
from otomekairo.infra.sqlite_store_runtime_view import (
    _pending_input_cycle_context,
    _pending_input_receipt_summary,
)
from otomekairo.schema.runtime_types import (
    ActionHistoryRecord,
    PendingInputMutationRecord,
    PendingInputRecord,
    TaskStateMutationRecord,
)
from otomekairo.schema.store_errors import StoreConflictError, StoreValidationError
from otomekairo.usecase.observation_normalization import normalize_observation_kind, normalize_observation_source


# Block: 対象 cancel claim
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


# Block: 次の pending input claim
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


# Block: pending input 破棄
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


# Block: pending input journal 追記
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


# Block: pending input cycle 確定
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
