"""SQLite の event / action_history 追記処理。"""

from __future__ import annotations

import sqlite3

from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _opaque_id
from otomekairo.infra.sqlite_store_runtime_view import (
    _action_command_summary,
    _action_result_summary,
)
from otomekairo.schema.runtime_types import ActionHistoryRecord
from otomekairo.schema.store_errors import StoreValidationError


# Block: event 追記
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


# Block: action_history と event 追記
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
