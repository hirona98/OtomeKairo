"""SQLite backend の bootstrap 集約。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from otomekairo.infra.sqlite.bootstrap_connection_impl import (
    connect_sqlite,
    load_schema_sql,
    schema_exists,
)
from otomekairo.infra.sqlite.bootstrap_meta_impl import (
    ensure_db_meta,
    ensure_vec_index_schema,
    verify_existing_schema,
)
from otomekairo.infra.sqlite.bootstrap_settings_editor_impl import verify_settings_editor_schema
from otomekairo.infra.sqlite.bootstrap_singleton_seed_impl import seed_singletons
from otomekairo.infra.sqlite_store_legacy_runtime import _now_ms


# Block: bootstrap 結果
@dataclass(frozen=True, slots=True)
class BootstrapResult:
    db_path: Path
    initialized_at: int


# Block: SQLite backend
class SqliteBackend:
    def __init__(self, db_path: Path, initializer_version: str) -> None:
        self._db_path = db_path
        self._initializer_version = initializer_version

    # Block: 初期化
    def initialize(self) -> BootstrapResult:
        now_ms = _now_ms()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            schema_created = False
            if not schema_exists(connection):
                connection.executescript(load_schema_sql())
                schema_created = True
            verify_existing_schema(
                connection=connection,
                schema_created=schema_created,
            )
            ensure_vec_index_schema(connection=connection)
            ensure_db_meta(
                connection=connection,
                now_ms=now_ms,
                schema_created=schema_created,
                initializer_version=self._initializer_version,
            )
            verify_settings_editor_schema(connection=connection)
            seed_singletons(
                connection=connection,
                now_ms=now_ms,
            )
        return BootstrapResult(db_path=self._db_path, initialized_at=now_ms)

    # Block: SQLite 接続
    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._db_path)
