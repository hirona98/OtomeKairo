"""SQLite bootstrap 接続処理。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec

from otomekairo.infra.sqlite_store_legacy_runtime import _quoted_identifier, _repo_root


# Block: SQLite 接続作成
def connect_sqlite(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    connection.enable_load_extension(False)
    return connection


# Block: スキーマ存在確認
def schema_exists(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'db_meta'
        """
    ).fetchone()
    return row is not None


# Block: スキーマ SQL 読み込み
def load_schema_sql() -> str:
    schema_path = _repo_root() / "sql" / "core_schema.sql"
    return schema_path.read_text(encoding="utf-8")


# Block: テーブル列名取得
def table_column_names(
    *,
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    quoted_table_name = _quoted_identifier(table_name)
    column_rows = connection.execute(
        f"""
        PRAGMA table_info({quoted_table_name})
        """
    ).fetchall()
    if not column_rows:
        raise RuntimeError(f"{table_name} table is missing from core_schema")
    return {str(row["name"]) for row in column_rows}
