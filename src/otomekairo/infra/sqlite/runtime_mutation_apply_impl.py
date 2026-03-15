"""SQLite の runtime mutation 適用処理。"""

from __future__ import annotations

import sqlite3

from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _opaque_id
from otomekairo.schema.runtime_types import PendingInputMutationRecord, TaskStateMutationRecord
from otomekairo.schema.store_errors import StoreValidationError


# Block: pending input mutation 挿入
def insert_pending_input_mutations(
    *,
    connection: sqlite3.Connection,
    pending_input_mutations: list[PendingInputMutationRecord],
) -> list[str]:
    inserted_input_ids: list[str] = []
    for pending_input_mutation in pending_input_mutations:
        if pending_input_mutation.priority < 0:
            raise StoreValidationError("pending input mutation.priority must be non-negative")
        input_kind = pending_input_mutation.payload.get("input_kind")
        if not isinstance(input_kind, str) or not input_kind:
            raise StoreValidationError("pending input mutation.payload.input_kind must be non-empty string")
        input_id = _opaque_id("inp")
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
            VALUES (?, ?, ?, NULL, ?, ?, ?, 'queued')
            """,
            (
                input_id,
                pending_input_mutation.source,
                pending_input_mutation.channel,
                _json_text(pending_input_mutation.payload),
                pending_input_mutation.created_at,
                pending_input_mutation.priority,
            ),
        )
        inserted_input_ids.append(input_id)
    return inserted_input_ids


# Block: task mutation 適用
def apply_task_state_mutations(
    *,
    connection: sqlite3.Connection,
    task_mutations: list[TaskStateMutationRecord],
) -> None:
    for task_mutation in task_mutations:
        if task_mutation.task_status != "waiting_external":
            raise StoreValidationError("task mutation.task_status is invalid")
        if task_mutation.priority < 0:
            raise StoreValidationError("task mutation.priority must be non-negative")
        connection.execute(
            """
            INSERT INTO task_state (
                task_id,
                task_kind,
                task_status,
                goal_hint,
                completion_hint_json,
                resume_condition_json,
                interruptible,
                priority,
                created_at,
                updated_at,
                title,
                step_hints_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_mutation.task_id,
                task_mutation.task_kind,
                task_mutation.task_status,
                task_mutation.goal_hint,
                _json_text(task_mutation.completion_hint),
                _json_text(task_mutation.resume_condition),
                1 if task_mutation.interruptible else 0,
                task_mutation.priority,
                task_mutation.created_at,
                task_mutation.created_at,
                task_mutation.title,
                _json_text(task_mutation.step_hints),
            ),
        )
