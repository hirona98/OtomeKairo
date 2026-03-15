"""SQLite event and input-journal implementations."""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _now_ms, _opaque_id
from otomekairo.infra.sqlite_store_runtime_view import (
    _action_command_summary,
    _action_result_summary,
    _pending_input_receipt_summary,
    _runtime_response_summary,
)
from otomekairo.schema.runtime_types import ActionHistoryRecord, PendingInputRecord
from otomekairo.schema.store_errors import StoreValidationError
from otomekairo.usecase.observation_normalization import normalize_observation_source


# Block: Input journal append
def append_input_journal(
    backend: SqliteBackend,
    *,
    observation_id: str,
    cycle_id: str,
    source: str,
    kind: str,
    captured_at: int,
    receipt_summary: str,
    payload_id: str,
) -> None:
    now_ms = _now_ms()
    with backend._connect() as connection:
        connection.execute(
            """
            INSERT INTO input_journal (
                journal_id,
                observation_id,
                cycle_id,
                source,
                kind,
                captured_at,
                receipt_summary,
                payload_ref_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("jrnl"),
                observation_id,
                cycle_id,
                source,
                kind,
                captured_at,
                receipt_summary,
                _json_text(
                    {
                        "payload_kind": "input_payload",
                        "payload_id": payload_id,
                        "payload_version": 1,
                    }
                ),
                now_ms,
            ),
        )


# Block: Settings override events insert
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


# Block: Pending input events insert
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


# Block: Task cycle events insert
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


# Block: Action history insert
def insert_action_history(
    *,
    connection: sqlite3.Connection,
    cycle_id: str,
    action_results: list[ActionHistoryRecord],
    input_journal_refs_json: str | None,
) -> list[str]:
    event_ids: list[str] = []
    for action_result in action_results:
        if action_result.status not in {"succeeded", "failed", "stopped"}:
            raise StoreValidationError("action status is invalid")
        connection.execute(
            """
            INSERT INTO action_history (
                result_id,
                cycle_id,
                command_id,
                action_type,
                command_json,
                started_at,
                finished_at,
                status,
                failure_mode,
                observed_effects_json,
                raw_result_ref_json,
                adapter_trace_ref_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_result.result_id,
                cycle_id,
                action_result.command_id,
                action_result.action_type,
                _json_text(action_result.command),
                action_result.started_at,
                action_result.finished_at,
                action_result.status,
                action_result.failure_mode,
                (
                    _json_text(action_result.observed_effects)
                    if action_result.observed_effects is not None
                    else None
                ),
                (
                    _json_text(action_result.raw_result_ref)
                    if action_result.raw_result_ref is not None
                    else None
                ),
                (
                    _json_text(action_result.adapter_trace_ref)
                    if action_result.adapter_trace_ref is not None
                    else None
                ),
            ),
        )
        event_ids.append(
            insert_event(
                connection=connection,
                cycle_id=cycle_id,
                created_at=action_result.started_at,
                source="runtime",
                kind="action",
                searchable=True,
                action_summary=_action_command_summary(action_result),
                input_journal_refs_json=input_journal_refs_json,
            )
        )
        event_ids.append(
            insert_event(
                connection=connection,
                cycle_id=cycle_id,
                created_at=action_result.finished_at,
                source="runtime",
                kind="action_result",
                searchable=True,
                result_summary=_action_result_summary(action_result),
                input_journal_refs_json=input_journal_refs_json,
            )
        )
    return event_ids


# Block: Event insert
def insert_event(
    *,
    connection: sqlite3.Connection,
    cycle_id: str,
    created_at: int,
    source: str,
    kind: str,
    searchable: bool,
    observation_summary: str | None = None,
    action_summary: str | None = None,
    result_summary: str | None = None,
    payload_ref_json: str | None = None,
    input_journal_refs_json: str | None = None,
) -> str:
    event_id = _opaque_id("evt")
    connection.execute(
        """
        INSERT INTO events (
            event_id,
            cycle_id,
            created_at,
            source,
            kind,
            searchable,
            observation_summary,
            action_summary,
            result_summary,
            payload_ref_json,
            input_journal_refs_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            cycle_id,
            created_at,
            source,
            kind,
            1 if searchable else 0,
            observation_summary,
            action_summary,
            result_summary,
            payload_ref_json,
            input_journal_refs_json,
        ),
    )
    return event_id
