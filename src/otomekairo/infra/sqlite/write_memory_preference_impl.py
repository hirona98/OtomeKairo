"""SQLite の write_memory 嗜好更新処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite_store_legacy_runtime import (
    _json_text,
    _merged_unique_strings,
    _normalized_target_entity_ref_json,
    _opaque_id,
    _preference_target_key,
)
from otomekairo.infra.sqlite_store_snapshots import (
    _decoded_object_json,
    _decoded_string_array_json,
)


# Block: 嗜好更新適用
def apply_preference_updates(
    *,
    connection: sqlite3.Connection,
    preference_updates: list[dict[str, Any]],
    created_at: int,
) -> None:
    for preference_update in preference_updates:
        upsert_preference_memory_with_revision(
            connection=connection,
            preference_update=preference_update,
            created_at=created_at,
        )


# Block: 嗜好記憶 upsert
def upsert_preference_memory_with_revision(
    *,
    connection: sqlite3.Connection,
    preference_update: dict[str, Any],
    created_at: int,
) -> None:
    target_entity_ref = dict(preference_update["target_entity_ref"])
    target_key = _preference_target_key(target_entity_ref=target_entity_ref)
    target_entity_ref_json = _normalized_target_entity_ref_json(target_entity_ref)
    existing_row = connection.execute(
        """
        SELECT preference_id,
               owner_scope,
               target_entity_ref_json,
               target_key,
               domain,
               polarity,
               status,
               confidence,
               evidence_event_ids_json,
               created_at,
               updated_at
        FROM preference_memory
        WHERE owner_scope = ?
          AND domain = ?
          AND target_key = ?
          AND polarity = ?
        ORDER BY updated_at DESC, created_at DESC, preference_id DESC
        LIMIT 1
        """,
        (
            str(preference_update["owner_scope"]),
            str(preference_update["domain"]),
            target_key,
            str(preference_update["polarity"]),
        ),
    ).fetchone()
    merged_evidence_event_ids = _merged_unique_strings(
        _decoded_string_array_json(
            existing_row["evidence_event_ids_json"] if existing_row is not None else None
        ),
        list(preference_update["evidence_event_ids"]),
    )
    if existing_row is None:
        preference_id = _opaque_id("pref")
        connection.execute(
            """
            INSERT INTO preference_memory (
                preference_id,
                owner_scope,
                target_entity_ref_json,
                target_key,
                domain,
                polarity,
                status,
                confidence,
                evidence_event_ids_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                preference_id,
                str(preference_update["owner_scope"]),
                target_entity_ref_json,
                target_key,
                str(preference_update["domain"]),
                str(preference_update["polarity"]),
                str(preference_update["status"]),
                float(preference_update["confidence"]),
                _json_text(merged_evidence_event_ids),
                created_at,
                created_at,
            ),
        )
        sync_stable_preference_projection(
            connection=connection,
            preference_row={
                "preference_id": preference_id,
                "owner_scope": str(preference_update["owner_scope"]),
                "target_entity_ref_json": target_entity_ref_json,
                "target_key": target_key,
                "domain": str(preference_update["domain"]),
                "polarity": str(preference_update["polarity"]),
                "status": str(preference_update["status"]),
                "confidence": float(preference_update["confidence"]),
                "evidence_event_ids_json": _json_text(merged_evidence_event_ids),
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
        return
    preference_id = str(existing_row["preference_id"])
    connection.execute(
        """
        UPDATE preference_memory
        SET target_entity_ref_json = ?,
            target_key = ?,
            status = ?,
            confidence = ?,
            evidence_event_ids_json = ?,
            updated_at = ?
        WHERE preference_id = ?
        """,
        (
            target_entity_ref_json,
            target_key,
            str(preference_update["status"]),
            float(preference_update["confidence"]),
            _json_text(merged_evidence_event_ids),
            created_at,
            preference_id,
        ),
    )
    sync_stable_preference_projection(
        connection=connection,
        preference_row={
            "preference_id": preference_id,
            "owner_scope": str(existing_row["owner_scope"]),
            "target_entity_ref_json": target_entity_ref_json,
            "target_key": target_key,
            "domain": str(existing_row["domain"]),
            "polarity": str(existing_row["polarity"]),
            "status": str(preference_update["status"]),
            "confidence": float(preference_update["confidence"]),
            "evidence_event_ids_json": _json_text(merged_evidence_event_ids),
            "created_at": int(existing_row["created_at"]),
            "updated_at": created_at,
        },
    )


# Block: 安定嗜好投影同期
def sync_stable_preference_projection(
    *,
    connection: sqlite3.Connection,
    preference_row: dict[str, Any] | sqlite3.Row,
) -> None:
    owner_scope = str(preference_row["owner_scope"])
    target_entity_ref_json = str(preference_row["target_entity_ref_json"])
    target_key = str(preference_row["target_key"])
    domain = str(preference_row["domain"])
    polarity = str(preference_row["polarity"])
    status = str(preference_row["status"])
    if owner_scope != "self" or status not in {"confirmed", "revoked"}:
        connection.execute(
            """
            DELETE FROM stable_preference_projection
            WHERE owner_scope = ?
              AND domain = ?
              AND target_key = ?
              AND polarity = ?
            """,
            (owner_scope, domain, target_key, polarity),
        )
        return
    connection.execute(
        """
        INSERT INTO stable_preference_projection (
            owner_scope,
            target_entity_ref_json,
            target_key,
            domain,
            polarity,
            preference_id,
            status,
            confidence,
            evidence_event_ids_json,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_scope, domain, target_key, polarity)
        DO UPDATE SET
            target_entity_ref_json = excluded.target_entity_ref_json,
            preference_id = excluded.preference_id,
            status = excluded.status,
            confidence = excluded.confidence,
            evidence_event_ids_json = excluded.evidence_event_ids_json,
            created_at = excluded.created_at,
            updated_at = excluded.updated_at
        """,
        (
            owner_scope,
            target_entity_ref_json,
            target_key,
            domain,
            polarity,
            str(preference_row["preference_id"]),
            status,
            float(preference_row["confidence"]),
            str(preference_row["evidence_event_ids_json"]),
            int(preference_row["created_at"]),
            int(preference_row["updated_at"]),
        ),
    )
