"""SQLite bootstrap メタデータ処理。"""

from __future__ import annotations

import json
import sqlite3

from otomekairo.infra.sqlite_store_vectors import (
    EMBEDDING_VECTOR_DIMENSION,
    _delete_vec_index_row,
    _replace_vec_index_row,
)
from otomekairo.schema.persistence import SCHEMA_NAME, SCHEMA_VERSION


# Block: 既存スキーマ検証
def verify_existing_schema(
    *,
    connection: sqlite3.Connection,
    schema_created: bool,
) -> None:
    if schema_created:
        return
    current_version = _read_schema_version(connection)
    if current_version != SCHEMA_VERSION:
        raise RuntimeError(
            "existing database schema_version is unsupported; delete data/core.sqlite3 and restart"
        )
    schema_name_row = connection.execute(
        """
        SELECT meta_value_json
        FROM db_meta
        WHERE meta_key = 'schema_name'
        """
    ).fetchone()
    if schema_name_row is None:
        raise RuntimeError(
            "existing database schema metadata is incomplete; delete data/core.sqlite3 and restart"
        )
    current_schema_name = json.loads(schema_name_row["meta_value_json"])
    if current_schema_name != SCHEMA_NAME:
        raise RuntimeError(
            "existing database schema_name is unsupported; delete data/core.sqlite3 and restart"
        )
    current_user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current_user_version != SCHEMA_VERSION:
        raise RuntimeError(
            "existing database user_version is unsupported; delete data/core.sqlite3 and restart"
        )


# Block: sqlite-vec 索引整合
def ensure_vec_index_schema(
    *,
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_items_index USING vec0(
            embedding float[{EMBEDDING_VECTOR_DIMENSION}]
        )
        """
    )
    rows = connection.execute(
        """
        SELECT rowid, embedding, searchable
        FROM vec_items
        """
    ).fetchall()
    for row in rows:
        vec_row_id = int(row["rowid"])
        if int(row["searchable"]) == 1:
            _replace_vec_index_row(
                connection=connection,
                vec_row_id=vec_row_id,
                embedding_blob=bytes(row["embedding"]),
            )
            continue
        _delete_vec_index_row(
            connection=connection,
            vec_row_id=vec_row_id,
        )


# Block: DB メタデータ初期化
def ensure_db_meta(
    *,
    connection: sqlite3.Connection,
    now_ms: int,
    schema_created: bool,
    initializer_version: str,
) -> None:
    if schema_created is False:
        return
    for key, value in {
        "schema_version": SCHEMA_VERSION,
        "schema_name": SCHEMA_NAME,
        "initialized_at": now_ms,
        "initializer_version": initializer_version,
    }.items():
        connection.execute(
            """
            INSERT INTO db_meta (meta_key, meta_value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(meta_key) DO UPDATE SET
                meta_value_json = excluded.meta_value_json,
                updated_at = excluded.updated_at
            """,
            (
                key,
                json.dumps(value, ensure_ascii=True, separators=(",", ":")),
                now_ms,
            ),
        )
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


# Block: スキーマ版取得
def _read_schema_version(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        """
        SELECT meta_value_json
        FROM db_meta
        WHERE meta_key = 'schema_version'
        """
    ).fetchone()
    if row is None:
        return None
    return int(json.loads(row["meta_value_json"]))
