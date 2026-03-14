"""Vector and ranked-search helper functions for the SQLite state store."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

import sqlite_vec

from otomekairo.infra.sqlite_store_legacy_runtime import _opaque_id


# Block: Embedding constants
EMBEDDING_VECTOR_DIMENSION = 32


# Block: vec_items upsert
def _upsert_vec_item_row(
    *,
    connection: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    embedding_model: str,
    embedding_scope: str,
    source_updated_at: int,
    embedding_blob: bytes,
) -> int:
    connection.execute(
        """
        INSERT INTO vec_items (
            vec_item_id,
            entity_type,
            entity_id,
            embedding_model,
            embedding_scope,
            searchable,
            source_updated_at,
            embedding
        )
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(entity_type, entity_id, embedding_model, embedding_scope)
        DO UPDATE SET
            searchable = 1,
            source_updated_at = excluded.source_updated_at,
            embedding = excluded.embedding
        """,
        (
            _opaque_id("vec"),
            entity_type,
            entity_id,
            embedding_model,
            embedding_scope,
            source_updated_at,
            embedding_blob,
        ),
    )
    row = connection.execute(
        """
        SELECT rowid
        FROM vec_items
        WHERE entity_type = ?
          AND entity_id = ?
          AND embedding_model = ?
          AND embedding_scope = ?
        """,
        (entity_type, entity_id, embedding_model, embedding_scope),
    ).fetchone()
    if row is None:
        raise RuntimeError("vec_item row is missing after upsert")
    return int(row["rowid"])


# Block: vec_items unsearchable mark
def _mark_vec_item_unsearchable(
    *,
    connection: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    embedding_model: str,
    embedding_scope: str,
    source_updated_at: int,
) -> int | None:
    row = connection.execute(
        """
        SELECT rowid
        FROM vec_items
        WHERE entity_type = ?
          AND entity_id = ?
          AND embedding_model = ?
          AND embedding_scope = ?
        """,
        (entity_type, entity_id, embedding_model, embedding_scope),
    ).fetchone()
    if row is None:
        return None
    vec_row_id = int(row["rowid"])
    connection.execute(
        """
        UPDATE vec_items
        SET searchable = 0,
            source_updated_at = ?
        WHERE rowid = ?
        """,
        (source_updated_at, vec_row_id),
    )
    return vec_row_id


# Block: vec index replace
def _replace_vec_index_row(
    *,
    connection: sqlite3.Connection,
    vec_row_id: int,
    embedding_blob: bytes,
) -> None:
    connection.execute(
        """
        DELETE FROM vec_items_index
        WHERE rowid = ?
        """,
        (vec_row_id,),
    )
    connection.execute(
        """
        INSERT INTO vec_items_index (rowid, embedding)
        VALUES (?, ?)
        """,
        (vec_row_id, embedding_blob),
    )


# Block: vec index delete
def _delete_vec_index_row(
    *,
    connection: sqlite3.Connection,
    vec_row_id: int,
) -> None:
    connection.execute(
        """
        DELETE FROM vec_items_index
        WHERE rowid = ?
        """,
        (vec_row_id,),
    )


# Block: vec similarity search
def _search_vec_similarity_hits(
    *,
    connection: sqlite3.Connection,
    query_text: str,
    embedding_model: str,
    limit: int,
) -> list[dict[str, Any]]:
    query_blob = _build_embedding_blob(
        source_text=query_text,
        embedding_model=embedding_model,
        embedding_scope="global",
    )
    raw_rows = connection.execute(
        """
        SELECT rowid, distance
        FROM vec_items_index
        WHERE embedding MATCH ?
          AND k = ?
        """,
        (query_blob, limit),
    ).fetchall()
    hits: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for raw_row in raw_rows:
        metadata_row = connection.execute(
            """
            SELECT entity_type, entity_id, searchable
            FROM vec_items
            WHERE rowid = ?
            """,
            (int(raw_row["rowid"]),),
        ).fetchone()
        if metadata_row is None or int(metadata_row["searchable"]) != 1:
            continue
        entity_type = str(metadata_row["entity_type"])
        entity_id = str(metadata_row["entity_id"])
        pair = (entity_type, entity_id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        hits.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "distance": float(raw_row["distance"]),
            }
        )
    return hits


# Block: Ranked row merge
def _merge_ranked_event_rows(
    *,
    connection: sqlite3.Connection,
    ranked_hits: list[dict[str, Any]],
    fallback_rows: list[sqlite3.Row],
) -> list[sqlite3.Row]:
    ranked_ids = [hit["entity_id"] for hit in ranked_hits if hit["entity_type"] == "event"]
    ranked_rows = _fetch_event_rows_by_ids(
        connection=connection,
        event_ids=ranked_ids,
    )
    merged_rows: list[sqlite3.Row] = []
    seen_ids: set[str] = set()
    for row in ranked_rows + fallback_rows:
        event_id = str(row["event_id"])
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        merged_rows.append(row)
        if len(merged_rows) >= 5:
            break
    return merged_rows


def _merge_ranked_memory_rows(
    *,
    connection: sqlite3.Connection,
    ranked_hits: list[dict[str, Any]],
    fallback_rows: list[sqlite3.Row],
) -> list[sqlite3.Row]:
    ranked_ids = [hit["entity_id"] for hit in ranked_hits if hit["entity_type"] == "memory_state"]
    ranked_rows = _fetch_memory_rows_by_ids(
        connection=connection,
        memory_state_ids=ranked_ids,
    )
    merged_rows: list[sqlite3.Row] = []
    seen_ids: set[str] = set()
    for row in ranked_rows + fallback_rows:
        memory_state_id = str(row["memory_state_id"])
        if memory_state_id in seen_ids:
            continue
        seen_ids.add(memory_state_id)
        merged_rows.append(row)
        if len(merged_rows) >= 8:
            break
    return merged_rows


# Block: Ranked row fetch
def _fetch_event_rows_by_ids(
    *,
    connection: sqlite3.Connection,
    event_ids: list[str],
) -> list[sqlite3.Row]:
    if not event_ids:
        return []
    placeholder_sql = ",".join("?" for _ in event_ids)
    rows = connection.execute(
        f"""
        SELECT
            events.event_id,
            events.source,
            events.kind,
            events.observation_summary,
            events.action_summary,
            events.result_summary,
            events.created_at
        FROM events
        WHERE events.searchable = 1
          AND events.event_id IN ({placeholder_sql})
        """,
        tuple(event_ids),
    ).fetchall()
    row_map = {str(row["event_id"]): row for row in rows}
    return [row_map[event_id] for event_id in event_ids if event_id in row_map]


def _fetch_memory_rows_by_ids(
    *,
    connection: sqlite3.Connection,
    memory_state_ids: list[str],
) -> list[sqlite3.Row]:
    if not memory_state_ids:
        return []
    placeholder_sql = ",".join("?" for _ in memory_state_ids)
    rows = connection.execute(
        f"""
        SELECT
            memory_state_id,
            memory_kind,
            body_text,
            payload_json,
            confidence,
            importance,
            memory_strength,
            created_at,
            updated_at,
            last_confirmed_at
        FROM memory_states
        WHERE searchable = 1
          AND memory_kind IN ('summary', 'fact', 'relation', 'long_mood_state', 'reflection_note')
          AND memory_state_id IN ({placeholder_sql})
        """,
        tuple(memory_state_ids),
    ).fetchall()
    row_map = {str(row["memory_state_id"]): row for row in rows}
    return [row_map[memory_state_id] for memory_state_id in memory_state_ids if memory_state_id in row_map]


# Block: Embedding blob build
def _build_embedding_blob(
    *,
    source_text: str,
    embedding_model: str,
    embedding_scope: str,
) -> bytes:
    return sqlite_vec.serialize_float32(
        _build_embedding_vector(
            source_text=source_text,
            embedding_model=embedding_model,
            embedding_scope=embedding_scope,
        )
    )


# Block: Embedding vector build
def _build_embedding_vector(
    *,
    source_text: str,
    embedding_model: str,
    embedding_scope: str,
) -> list[float]:
    del embedding_scope
    tokens = _embedding_source_tokens(source_text)
    vector = [0.0] * EMBEDDING_VECTOR_DIMENSION
    for token in tokens:
        digest = hashlib.sha256(f"{embedding_model}\n{token}".encode("utf-8")).digest()
        for index in range(EMBEDDING_VECTOR_DIMENSION):
            vector[index] += (digest[index] / 127.5) - 1.0
    magnitude = sum(component * component for component in vector) ** 0.5
    if magnitude == 0.0:
        raise RuntimeError("embedding vector magnitude must not be zero")
    return [component / magnitude for component in vector]


# Block: Embedding tokenization
def _embedding_source_tokens(source_text: str) -> list[str]:
    normalized_text = source_text.strip().lower()
    if not normalized_text:
        raise RuntimeError("embedding source text must be non-empty")
    raw_tokens = normalized_text.replace("\n", " ").split(" ")
    tokens = [token for token in raw_tokens if token]
    if not tokens:
        raise RuntimeError("embedding source tokens must not be empty")
    return tokens
