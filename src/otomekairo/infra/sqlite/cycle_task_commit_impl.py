"""SQLite の task cycle 確定処理。"""

from __future__ import annotations

import json
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.event_writer_impl import insert_task_cycle_events
from otomekairo.infra.sqlite.memory_job_impl import enqueue_write_memory_jobs
from otomekairo.infra.sqlite.runtime_lease_impl import sync_commit_log
from otomekairo.infra.sqlite.runtime_live_state_impl import (
    insert_pending_input_mutations,
    sync_runtime_live_state,
)
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _now_ms
from otomekairo.infra.sqlite_store_runtime_view import _task_cycle_context
from otomekairo.schema.runtime_types import (
    ActionHistoryRecord,
    PendingInputMutationRecord,
    TaskStateRecord,
)
from otomekairo.schema.store_errors import StoreConflictError, StoreValidationError


# Block: 待機 browse task claim
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


# Block: task cycle 確定
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
