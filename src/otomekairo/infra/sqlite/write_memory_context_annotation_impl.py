"""SQLite の write_memory 注釈反映処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite_store_legacy_runtime import _opaque_id
from otomekairo.infra.sqlite_store_snapshots import (
    _event_entity_entries_from_annotation,
    _normalized_entity_name,
)


# Block: イベント時制反映
def apply_event_about_time(
    *,
    connection: sqlite3.Connection,
    event_annotations: list[dict[str, Any]],
    created_at: int,
) -> None:
    for event_annotation in event_annotations:
        replace_event_about_time(
            connection=connection,
            event_annotation=event_annotation,
            created_at=created_at,
        )


# Block: イベント時制置換
def replace_event_about_time(
    *,
    connection: sqlite3.Connection,
    event_annotation: dict[str, Any],
    created_at: int,
) -> None:
    event_id = str(event_annotation["event_id"])
    connection.execute(
        """
        DELETE FROM event_about_time
        WHERE event_id = ?
        """,
        (event_id,),
    )
    about_time = event_annotation.get("about_time")
    if not isinstance(about_time, dict):
        return
    connection.execute(
        """
        INSERT INTO event_about_time (
            event_about_time_id,
            event_id,
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
            _opaque_id("eat"),
            event_id,
            about_time.get("about_start_ts"),
            about_time.get("about_end_ts"),
            about_time.get("about_year_start"),
            about_time.get("about_year_end"),
            about_time.get("life_stage"),
            float(about_time["about_time_confidence"]),
            created_at,
            created_at,
        ),
    )


# Block: イベントエンティティ反映
def apply_event_entities(
    *,
    connection: sqlite3.Connection,
    event_annotations: list[dict[str, Any]],
    created_at: int,
) -> None:
    for event_annotation in event_annotations:
        replace_event_entities(
            connection=connection,
            event_annotation=event_annotation,
            created_at=created_at,
        )


# Block: イベントエンティティ置換
def replace_event_entities(
    *,
    connection: sqlite3.Connection,
    event_annotation: dict[str, Any],
    created_at: int,
) -> None:
    event_id = str(event_annotation["event_id"])
    connection.execute(
        """
        DELETE FROM event_entities
        WHERE event_id = ?
        """,
        (event_id,),
    )
    for entity_entry in _event_entity_entries_from_annotation(event_annotation):
        connection.execute(
            """
            INSERT INTO event_entities (
                event_entity_id,
                event_id,
                entity_type_norm,
                entity_name_raw,
                entity_name_norm,
                confidence,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("een"),
                event_id,
                str(entity_entry["entity_type_norm"]),
                str(entity_entry["entity_name_raw"]),
                _normalized_entity_name(str(entity_entry["entity_name_raw"])),
                float(entity_entry["confidence"]),
                created_at,
            ),
        )
