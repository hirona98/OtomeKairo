"""SQLite の cycle 別 event 生成処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite.event_record_insert_impl import (
    insert_action_history,
    insert_event,
)
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text
from otomekairo.infra.sqlite_store_runtime_view import (
    _pending_input_receipt_summary,
    _runtime_response_summary,
)
from otomekairo.schema.runtime_types import ActionHistoryRecord, PendingInputRecord
from otomekairo.usecase.observation_normalization import normalize_observation_source


# Block: settings override event 生成
def insert_settings_override_events(
    *,
    connection: sqlite3.Connection,
    override_id: str,
    cycle_id: str,
    key: str,
    apply_scope: str,
    final_status: str,
    reject_reason: str | None,
    resolved_at: int,
) -> list[str]:
    summary = f"settings {key} {final_status} ({apply_scope})"
    if reject_reason:
        summary = f"{summary}: {reject_reason}"
    return [
        insert_event(
            connection=connection,
            cycle_id=cycle_id,
            created_at=resolved_at,
            source="runtime",
            kind="internal_decision",
            searchable=True,
            result_summary=summary,
            payload_ref_json=_json_text(
                {
                    "payload_kind": "input_payload",
                    "payload_id": override_id,
                    "payload_version": 1,
                }
            ),
            input_journal_refs_json=_json_text([f"obs_{override_id}"]),
        )
    ]


# Block: pending input event 生成
def insert_pending_input_events(
    *,
    connection: sqlite3.Connection,
    pending_input: PendingInputRecord,
    cycle_id: str,
    action_results: list[ActionHistoryRecord],
    ui_events: list[dict[str, Any]],
    resolved_at: int,
) -> list[str]:
    input_journal_refs_json = _json_text([f"obs_{pending_input.input_id}"])
    event_ids = [
        insert_event(
            connection=connection,
            cycle_id=cycle_id,
            created_at=pending_input.created_at,
            source=normalize_observation_source(
                source=pending_input.source,
                payload=pending_input.payload,
            ),
            kind="observation",
            searchable=True,
            observation_summary=_pending_input_receipt_summary(pending_input),
            payload_ref_json=_json_text(
                {
                    "payload_kind": "input_payload",
                    "payload_id": pending_input.input_id,
                    "payload_version": 1,
                }
            ),
            input_journal_refs_json=input_journal_refs_json,
        )
    ]
    event_ids.extend(
        insert_action_history(
            connection=connection,
            cycle_id=cycle_id,
            action_results=action_results,
            input_journal_refs_json=input_journal_refs_json,
        )
    )
    response_summary = _runtime_response_summary(ui_events)
    if response_summary is None:
        return event_ids
    response_created_at = resolved_at
    if action_results:
        response_created_at = max(action_result.finished_at for action_result in action_results) + 1
    event_ids.append(
        insert_event(
            connection=connection,
            cycle_id=cycle_id,
            created_at=response_created_at,
            source="runtime",
            kind="external_response",
            searchable=True,
            result_summary=response_summary,
            input_journal_refs_json=input_journal_refs_json,
        )
    )
    return event_ids


# Block: task cycle event 生成
def insert_task_cycle_events(
    *,
    connection: sqlite3.Connection,
    cycle_id: str,
    action_results: list[ActionHistoryRecord],
    ui_events: list[dict[str, Any]],
    resolved_at: int,
) -> list[str]:
    event_ids = insert_action_history(
        connection=connection,
        cycle_id=cycle_id,
        action_results=action_results,
        input_journal_refs_json=None,
    )
    response_summary = _runtime_response_summary(ui_events)
    if response_summary is None:
        return event_ids
    response_created_at = resolved_at
    if action_results:
        response_created_at = max(action_result.finished_at for action_result in action_results) + 1
    event_ids.append(
        insert_event(
            connection=connection,
            cycle_id=cycle_id,
            created_at=response_created_at,
            source="runtime",
            kind="external_response",
            searchable=True,
            result_summary=response_summary,
        )
    )
    return event_ids
