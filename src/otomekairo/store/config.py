from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from otomekairo.defaults import DEFAULT_BACKGROUND_WAKE_SPEECH_FREQUENCY_LEVEL, build_default_state
from otomekairo.service.common import debug_log


CONFIG_DB_FILE_NAME = "config.db"
CURRENT_CONFIG_DB_VERSION = 2
SUPPORTED_CONFIG_DB_VERSIONS = {0, 1, CURRENT_CONFIG_DB_VERSION}


class ConfigStore:
    def __init__(self, root_dir: Path) -> None:
        # パス群
        self.root_dir = root_dir
        self.config_db_path = root_dir / CONFIG_DB_FILE_NAME

        # 初期化
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._initialize_config_db()

    def read_state(self) -> dict[str, Any]:
        # 設定DBから既存 service shape の state を組み立てる。
        with self._config_db() as conn:
            identity = conn.execute(
                """
                SELECT server_id, server_display_name, api_version, console_access_token
                FROM server_identity
                WHERE id = 1
                """
            ).fetchone()
            current = conn.execute(
                """
                SELECT
                    selected_persona_id,
                    selected_memory_set_id,
                    selected_model_preset_id,
                    background_wake_speech_frequency_level,
                    wake_policy_json
                FROM current_config
                WHERE id = 1
                """
            ).fetchone()
            if identity is None or current is None:
                raise RuntimeError("config.db is missing required singleton rows.")
            return {
                "server_id": identity["server_id"],
                "server_display_name": identity["server_display_name"],
                "api_version": identity["api_version"],
                "console_access_token": identity["console_access_token"],
                "selected_persona_id": current["selected_persona_id"],
                "selected_memory_set_id": current["selected_memory_set_id"],
                "selected_model_preset_id": current["selected_model_preset_id"],
                "background_wake_speech_frequency_level": current[
                    "background_wake_speech_frequency_level"
                ],
                "wake_policy": json.loads(current["wake_policy_json"]),
                "personas": self._read_payload_table(conn, "personas", "persona_id"),
                "memory_sets": self._read_payload_table(conn, "memory_sets", "memory_set_id"),
                "model_presets": self._read_payload_table(conn, "model_presets", "model_preset_id"),
                "camera_sources": self._read_payload_table(conn, "camera_sources", "vision_source_id"),
                "mcp_servers": self._read_payload_table(conn, "mcp_servers", "mcp_server_id"),
            }

    def write_state(self, state: dict[str, Any]) -> None:
        # 設定 state 全体を単一 transaction で置き換える。
        with self._config_db() as conn:
            self._write_state(conn, state)

    def _initialize_config_db(self) -> None:
        # 現行 schema 以外は受け付けない。
        with self._config_db() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            debug_log(
                "Store",
                f"config_db open path={self.config_db_path} user_version={version} expected={CURRENT_CONFIG_DB_VERSION}",
                level="DEBUG",
            )
            if version not in SUPPORTED_CONFIG_DB_VERSIONS:
                debug_log("Store", f"config_db unsupported_schema user_version={version}", level="ERROR")
                raise RuntimeError(
                    f"Unsupported config.db schema version: {version}. "
                    f"Expected {CURRENT_CONFIG_DB_VERSION}."
                )

            self._apply_current_schema(conn)
            if version == 0:
                self._write_state(conn, build_default_state())
                conn.execute(f"PRAGMA user_version = {CURRENT_CONFIG_DB_VERSION}")
                debug_log("Store", f"config_db initialized user_version={CURRENT_CONFIG_DB_VERSION}")
            elif version != CURRENT_CONFIG_DB_VERSION:
                conn.execute(f"PRAGMA user_version = {CURRENT_CONFIG_DB_VERSION}")
                debug_log(
                    "Store",
                    f"config_db schema updated user_version={CURRENT_CONFIG_DB_VERSION}",
                )
            else:
                debug_log("Store", f"config_db schema ready user_version={version}")

    def _open_config_db(self) -> sqlite3.Connection:
        # 接続
        conn = sqlite3.connect(self.config_db_path)
        conn.row_factory = sqlite3.Row

        # pragma群
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def _config_db(self) -> sqlite3.Connection:
        # 接続ライフサイクル
        conn = self._open_config_db()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _apply_current_schema(self, conn: sqlite3.Connection) -> None:
        # 設定DB schema 全体
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS server_identity (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                server_id TEXT NOT NULL,
                server_display_name TEXT NOT NULL,
                api_version TEXT NOT NULL,
                console_access_token TEXT
            );

            CREATE TABLE IF NOT EXISTS current_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                selected_persona_id TEXT NOT NULL,
                selected_memory_set_id TEXT NOT NULL,
                selected_model_preset_id TEXT NOT NULL,
                background_wake_speech_frequency_level INTEGER NOT NULL DEFAULT 5,
                wake_policy_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS personas (
                persona_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_sets (
                memory_set_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS model_presets (
                model_preset_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS camera_sources (
                vision_source_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mcp_servers (
                mcp_server_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            """
        )
        self._ensure_current_config_column(
            conn=conn,
            column_name="background_wake_speech_frequency_level",
            column_definition=(
                "background_wake_speech_frequency_level INTEGER NOT NULL "
                f"DEFAULT {DEFAULT_BACKGROUND_WAKE_SPEECH_FREQUENCY_LEVEL}"
            ),
        )

    def _ensure_current_config_column(
        self,
        *,
        conn: sqlite3.Connection,
        column_name: str,
        column_definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(current_config)").fetchall()
        }
        if column_name not in columns:
            conn.execute(f"ALTER TABLE current_config ADD COLUMN {column_definition}")

    def _write_state(self, conn: sqlite3.Connection, state: dict[str, Any]) -> None:
        conn.execute("DELETE FROM server_identity")
        conn.execute("DELETE FROM current_config")
        for table_name in ("personas", "memory_sets", "model_presets", "camera_sources", "mcp_servers"):
            conn.execute(f"DELETE FROM {table_name}")

        conn.execute(
            """
            INSERT INTO server_identity (
                id, server_id, server_display_name, api_version, console_access_token
            )
            VALUES (1, ?, ?, ?, ?)
            """,
            (
                state["server_id"],
                state["server_display_name"],
                state["api_version"],
                state.get("console_access_token"),
            ),
        )
        conn.execute(
            """
            INSERT INTO current_config (
                id,
                selected_persona_id,
                selected_memory_set_id,
                selected_model_preset_id,
                background_wake_speech_frequency_level,
                wake_policy_json
            )
            VALUES (1, ?, ?, ?, ?, ?)
            """,
            (
                state["selected_persona_id"],
                state["selected_memory_set_id"],
                state["selected_model_preset_id"],
                state["background_wake_speech_frequency_level"],
                self._to_json(state["wake_policy"]),
            ),
        )
        self._write_payload_table(conn, "personas", "persona_id", state["personas"])
        self._write_payload_table(conn, "memory_sets", "memory_set_id", state["memory_sets"])
        self._write_payload_table(conn, "model_presets", "model_preset_id", state["model_presets"])
        self._write_payload_table(conn, "camera_sources", "vision_source_id", state.get("camera_sources", {}))
        self._write_payload_table(conn, "mcp_servers", "mcp_server_id", state.get("mcp_servers", {}))

    def _read_payload_table(self, conn: sqlite3.Connection, table_name: str, id_column: str) -> dict[str, dict[str, Any]]:
        rows = conn.execute(
            f"""
            SELECT {id_column}, payload_json
            FROM {table_name}
            ORDER BY {id_column} ASC
            """
        ).fetchall()
        return {
            row[id_column]: json.loads(row["payload_json"])
            for row in rows
        }

    def _write_payload_table(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        id_column: str,
        entries: dict[str, dict[str, Any]],
    ) -> None:
        for entry_id, payload in entries.items():
            conn.execute(
                f"""
                INSERT INTO {table_name} ({id_column}, payload_json)
                VALUES (?, ?)
                """,
                (entry_id, self._to_json(payload)),
            )

    def _to_json(self, payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
