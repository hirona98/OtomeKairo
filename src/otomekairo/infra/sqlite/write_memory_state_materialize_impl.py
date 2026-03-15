"""SQLite の write_memory 状態補助テーブル反映処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite_store_legacy_runtime import _opaque_id
from otomekairo.infra.sqlite_store_snapshots import (
    _state_about_time_from_row,
    _state_entity_entries_from_row,
)

from otomekairo.infra.sqlite.write_memory_state_update_impl import (
    fetch_memory_state_row_for_update,
)


# Block: 状態時制反映
def apply_state_about_time(
    *,
    connection: sqlite3.Connection,
    state_updates: list[dict[str, Any]],
    state_id_by_ref: dict[str, str],
    created_at: int,
) -> None:
    for state_id in _resolved_state_ids_for_materialization(
        state_updates=state_updates,
        state_id_by_ref=state_id_by_ref,
    ):
        state_row = fetch_memory_state_row_for_update(
            connection=connection,
            memory_state_id=state_id,
        )
        replace_state_about_time(
            connection=connection,
            state_row=state_row,
            created_at=created_at,
        )


# Block: 状態エンティティ反映
def apply_state_entities(
    *,
    connection: sqlite3.Connection,
    state_updates: list[dict[str, Any]],
    state_id_by_ref: dict[str, str],
    created_at: int,
) -> None:
    for state_id in _resolved_state_ids_for_materialization(
        state_updates=state_updates,
        state_id_by_ref=state_id_by_ref,
    ):
        state_row = fetch_memory_state_row_for_update(
            connection=connection,
            memory_state_id=state_id,
        )
        replace_state_entities(
            connection=connection,
            state_row=state_row,
            created_at=created_at,
        )


# Block: 状態時制置換
def replace_state_about_time(
    *,
    connection: sqlite3.Connection,
    state_row: sqlite3.Row,
    created_at: int,
) -> None:
    memory_state_id = str(state_row["memory_state_id"])
    connection.execute(
        """
        DELETE FROM state_about_time
        WHERE memory_state_id = ?
        """,
        (memory_state_id,),
    )
    about_time = _state_about_time_from_row(state_row)
    if about_time is None:
        return
    connection.execute(
        """
        INSERT INTO state_about_time (
            state_about_time_id,
            memory_state_id,
            about_start_ts,
            about_end_ts,
            about_year_start,
            about_year_end,
            life_stage,
            confidence,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _opaque_id("sat"),
            memory_state_id,
            about_time["about_start_ts"],
            about_time["about_end_ts"],
            about_time["about_year_start"],
            about_time["about_year_end"],
            about_time["life_stage"],
            about_time["confidence"],
            created_at,
            created_at,
        ),
    )


# Block: 状態エンティティ置換
def replace_state_entities(
    *,
    connection: sqlite3.Connection,
    state_row: sqlite3.Row,
    created_at: int,
) -> None:
    memory_state_id = str(state_row["memory_state_id"])
    connection.execute(
        """
        DELETE FROM state_entities
        WHERE memory_state_id = ?
        """,
        (memory_state_id,),
    )
    for entity_entry in _state_entity_entries_from_row(state_row):
        connection.execute(
            """
            INSERT INTO state_entities (
                state_entity_id,
                memory_state_id,
                entity_type_norm,
                entity_name_raw,
                entity_name_norm,
                confidence,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("sen"),
                memory_state_id,
                entity_entry["entity_type_norm"],
                entity_entry["entity_name_raw"],
                entity_entry["entity_name_norm"],
                entity_entry["confidence"],
                created_at,
            ),
        )


# Block: 反映対象状態解決
def _resolved_state_ids_for_materialization(
    *,
    state_updates: list[dict[str, Any]],
    state_id_by_ref: dict[str, str],
) -> list[str]:
    applied_state_ids: list[str] = []
    seen_state_ids: set[str] = set()
    for state_update in state_updates:
        operation = str(state_update["operation"])
        state_id = (
            state_id_by_ref.get(str(state_update["state_ref"]))
            if operation == "upsert"
            else str(state_update["target_state_id"])
        )
        if not isinstance(state_id, str) or not state_id or state_id in seen_state_ids:
            continue
        seen_state_ids.add(state_id)
        applied_state_ids.append(state_id)
    return applied_state_ids
