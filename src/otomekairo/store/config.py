from __future__ import annotations

import json
import sqlite3
import struct
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from otomekairo.defaults import (
    build_default_desktop_capture,
    build_default_standing_concerns,
    build_default_state,
)
from otomekairo.memory.utils import now_iso
from otomekairo.service.common import debug_log


CONFIG_DB_FILE_NAME = "config.db"
CURRENT_CONFIG_DB_VERSION = 19
SUPPORTED_CONFIG_DB_VERSIONS = {0, CURRENT_CONFIG_DB_VERSION}


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
                    pre_send_check_model_preset_id,
                    selected_avatar_id,
                    thinking_speech_level,
                    selected_conversation_display_name_id,
                    wake_policy_json,
                    standing_concerns_json,
                    audio_output_settings_json,
                    microphone_settings_json
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
                "pre_send_check_model_preset_id": current[
                    "pre_send_check_model_preset_id"
                ],
                "selected_avatar_id": current["selected_avatar_id"],
                "thinking_speech_level": current[
                    "thinking_speech_level"
                ],
                "selected_conversation_display_name_id": current[
                    "selected_conversation_display_name_id"
                ],
                "conversation_display_names": self._read_conversation_display_names(conn),
                "wake_policy": json.loads(current["wake_policy_json"]),
                "standing_concerns": json.loads(current["standing_concerns_json"]),
                "audio_output_settings": json.loads(
                    current["audio_output_settings_json"]
                ),
                "microphone_settings": json.loads(current["microphone_settings_json"]),
                "personas": self._read_payload_table(conn, "personas", "persona_id"),
                "memory_sets": self._read_payload_table(conn, "memory_sets", "memory_set_id"),
                "model_presets": self._read_payload_table(conn, "model_presets", "model_preset_id"),
                "avatars": self._read_payload_table(conn, "avatars", "avatar_id"),
                "camera_sources": self._read_payload_table(conn, "camera_sources", "vision_source_id"),
                "mcp_servers": self._read_payload_table(conn, "mcp_servers", "mcp_server_id"),
                "agent_skill_sources": self._read_payload_table(
                    conn,
                    "agent_skill_sources",
                    "source_id",
                ),
                "desktop_capture_defaults": self._read_desktop_capture_defaults(conn),
                "console_client_settings": self._read_console_client_settings(conn),
            }

    def write_state(self, state: dict[str, Any]) -> None:
        # 設定 state 全体を単一 transaction で置き換える。
        with self._config_db() as conn:
            self._write_state(conn, state)

    def list_voice_speakers(
        self,
        *,
        registered_only: bool = False,
        include_embedding: bool = False,
    ) -> list[dict[str, Any]]:
        # 話者一覧の読み取りでは embedding を明示指定時だけ復元する。
        query = """
            SELECT
                person_ref,
                conversation_display_name_id,
                conversation_display_names.display_name AS display_name,
                registration_status,
                embedding,
                model_id,
                registered_at,
                voice_speakers.updated_at AS updated_at
            FROM voice_speakers
            JOIN conversation_display_names USING (conversation_display_name_id)
        """
        params: tuple[Any, ...] = ()
        if registered_only:
            query += "\nWHERE registration_status = ?"
            params = ("registered",)
        query += "\nORDER BY voice_speakers.created_at ASC, person_ref ASC"
        with self._config_db() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            self._voice_speaker_from_row(row, include_embedding=include_embedding)
            for row in rows
        ]

    def get_voice_speaker(
        self,
        person_ref: str,
        *,
        include_embedding: bool = False,
    ) -> dict[str, Any] | None:
        # 人物参照は音声人物の安定識別子として扱う。
        with self._config_db() as conn:
            row = conn.execute(
                """
                SELECT
                    person_ref,
                    conversation_display_name_id,
                    conversation_display_names.display_name AS display_name,
                    registration_status,
                    embedding,
                    model_id,
                    registered_at,
                    voice_speakers.updated_at AS updated_at
                FROM voice_speakers
                JOIN conversation_display_names USING (conversation_display_name_id)
                WHERE person_ref = ?
                """,
                (person_ref,),
            ).fetchone()
        if row is None:
            return None
        return self._voice_speaker_from_row(row, include_embedding=include_embedding)

    def replace_voice_speaker_registration(
        self,
        *,
        person_ref: str,
        conversation_display_name_id: str,
        embedding: list[float],
        model_id: str,
    ) -> dict[str, Any]:
        # 3サンプルから確定した embedding だけを原子的に保存する。
        timestamp = now_iso()
        embedding_blob = struct.pack(f"<{len(embedding)}f", *embedding)
        try:
            with self._config_db() as conn:
                existing = conn.execute(
                    """
                    SELECT created_at
                    FROM voice_speakers
                    WHERE person_ref = ?
                    """,
                    (person_ref,),
                ).fetchone()
                created_at = existing["created_at"] if existing is not None else timestamp
                conn.execute(
                    """
                    INSERT INTO voice_speakers (
                        person_ref,
                        conversation_display_name_id,
                        registration_status,
                        embedding,
                        model_id,
                        created_at,
                        registered_at,
                        updated_at
                    )
                    VALUES (?, ?, 'registered', ?, ?, ?, ?, ?)
                    ON CONFLICT(person_ref) DO UPDATE SET
                        conversation_display_name_id = excluded.conversation_display_name_id,
                        registration_status = 'registered',
                        embedding = excluded.embedding,
                        model_id = excluded.model_id,
                        registered_at = excluded.registered_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        person_ref,
                        conversation_display_name_id,
                        embedding_blob,
                        model_id,
                        created_at,
                        timestamp,
                        timestamp,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("conversation_display_name_assignment_conflict") from exc
        speaker = self.get_voice_speaker(person_ref)
        if speaker is None:
            raise RuntimeError("voice speaker registration was not persisted.")
        return speaker

    def assign_voice_speaker_conversation_display_name(
        self,
        *,
        person_ref: str,
        conversation_display_name_id: str,
    ) -> dict[str, Any] | None:
        # 呼び名参照だけを変更し、登録状態と embedding を維持する。
        try:
            with self._config_db() as conn:
                cursor = conn.execute(
                    """
                    UPDATE voice_speakers
                    SET conversation_display_name_id = ?, updated_at = ?
                    WHERE person_ref = ?
                    """,
                    (conversation_display_name_id, now_iso(), person_ref),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("conversation_display_name_assignment_conflict") from exc
        if cursor.rowcount == 0:
            return None
        return self.get_voice_speaker(person_ref)

    def list_conversation_display_names(self) -> list[dict[str, Any]]:
        with self._config_db() as conn:
            rows = conn.execute(
                """
                SELECT conversation_display_name_id, display_name, created_at, updated_at
                FROM conversation_display_names
                ORDER BY created_at ASC, conversation_display_name_id ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation_display_name(
        self,
        conversation_display_name_id: str,
    ) -> dict[str, Any] | None:
        with self._config_db() as conn:
            row = conn.execute(
                """
                SELECT conversation_display_name_id, display_name, created_at, updated_at
                FROM conversation_display_names
                WHERE conversation_display_name_id = ?
                """,
                (conversation_display_name_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def create_conversation_display_name(
        self,
        *,
        conversation_display_name_id: str,
        display_name: str,
    ) -> dict[str, Any]:
        timestamp = now_iso()
        try:
            with self._config_db() as conn:
                conn.execute(
                    """
                    INSERT INTO conversation_display_names (
                        conversation_display_name_id, display_name, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (conversation_display_name_id, display_name, timestamp, timestamp),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("duplicate_conversation_display_name") from exc
        created = self.get_conversation_display_name(conversation_display_name_id)
        if created is None:
            raise RuntimeError("conversation display name was not persisted.")
        return created

    def update_conversation_display_name(
        self,
        *,
        conversation_display_name_id: str,
        display_name: str,
    ) -> dict[str, Any] | None:
        try:
            with self._config_db() as conn:
                cursor = conn.execute(
                    """
                    UPDATE conversation_display_names
                    SET display_name = ?, updated_at = ?
                    WHERE conversation_display_name_id = ?
                    """,
                    (display_name, now_iso(), conversation_display_name_id),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("duplicate_conversation_display_name") from exc
        if cursor.rowcount == 0:
            return None
        return self.get_conversation_display_name(conversation_display_name_id)

    def delete_conversation_display_name(
        self,
        conversation_display_name_id: str,
    ) -> dict[str, Any] | None:
        existing = self.get_conversation_display_name(conversation_display_name_id)
        if existing is None:
            return None
        try:
            with self._config_db() as conn:
                conn.execute(
                    """
                    DELETE FROM conversation_display_names
                    WHERE conversation_display_name_id = ?
                    """,
                    (conversation_display_name_id,),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("conversation_display_name_in_use") from exc
        return existing

    def unregister_voice_speaker(self, person_ref: str) -> dict[str, Any] | None:
        # 人物rowを維持したまま音声登録情報だけを削除する。
        with self._config_db() as conn:
            cursor = conn.execute(
                """
                UPDATE voice_speakers
                SET
                    registration_status = 'unregistered',
                    embedding = NULL,
                    model_id = NULL,
                    registered_at = NULL,
                    updated_at = ?
                WHERE person_ref = ?
                """,
                (now_iso(), person_ref),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_voice_speaker(person_ref)

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
                # selected_conversation_display_name_id の FK を満たすため、
                # 呼ばれ方定義を current_config より先に書き込む。
                state = build_default_state()
                self._write_conversation_display_names(
                    conn,
                    state.get("conversation_display_names", {}),
                )
                self._write_state(conn, state)
                conn.execute(f"PRAGMA user_version = {CURRENT_CONFIG_DB_VERSION}")
                debug_log("Store", f"config_db initialized user_version={CURRENT_CONFIG_DB_VERSION}")
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
                pre_send_check_model_preset_id TEXT NOT NULL,
                selected_avatar_id TEXT NOT NULL,
                thinking_speech_level INTEGER NOT NULL DEFAULT 5,
                selected_conversation_display_name_id TEXT,
                wake_policy_json TEXT NOT NULL,
                standing_concerns_json TEXT NOT NULL,
                audio_output_settings_json TEXT NOT NULL,
                microphone_settings_json TEXT NOT NULL,
                FOREIGN KEY (selected_conversation_display_name_id)
                    REFERENCES conversation_display_names(conversation_display_name_id)
                    ON DELETE RESTRICT
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

            CREATE TABLE IF NOT EXISTS avatars (
                avatar_id TEXT PRIMARY KEY,
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

            CREATE TABLE IF NOT EXISTS agent_skill_sources (
                source_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS console_client_settings (
                client_id TEXT PRIMARY KEY,
                last_connected_at TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS desktop_capture_defaults (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversation_display_names (
                conversation_display_name_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS voice_speakers (
                person_ref TEXT PRIMARY KEY,
                conversation_display_name_id TEXT NOT NULL UNIQUE,
                registration_status TEXT NOT NULL
                    CHECK (registration_status IN ('registered', 'unregistered')),
                embedding BLOB,
                model_id TEXT,
                created_at TEXT NOT NULL,
                registered_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (conversation_display_name_id)
                    REFERENCES conversation_display_names(conversation_display_name_id)
                    ON DELETE RESTRICT,
                CHECK (
                    (
                        registration_status = 'registered'
                        AND embedding IS NOT NULL
                        AND model_id IS NOT NULL
                        AND registered_at IS NOT NULL
                    )
                    OR
                    (
                        registration_status = 'unregistered'
                        AND embedding IS NULL
                        AND model_id IS NULL
                        AND registered_at IS NULL
                    )
                )
            );
            """
        )

    def _write_state(self, conn: sqlite3.Connection, state: dict[str, Any]) -> None:
        conn.execute("DELETE FROM server_identity")
        conn.execute("DELETE FROM current_config")
        for table_name in (
            "personas",
            "memory_sets",
            "model_presets",
            "avatars",
            "camera_sources",
            "mcp_servers",
            "agent_skill_sources",
            "console_client_settings",
        ):
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
                pre_send_check_model_preset_id,
                selected_avatar_id,
                thinking_speech_level,
                selected_conversation_display_name_id,
                wake_policy_json,
                standing_concerns_json,
                audio_output_settings_json,
                microphone_settings_json
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state["selected_persona_id"],
                state["selected_memory_set_id"],
                state["selected_model_preset_id"],
                state["pre_send_check_model_preset_id"],
                state["selected_avatar_id"],
                state["thinking_speech_level"],
                state["selected_conversation_display_name_id"],
                self._to_json(state["wake_policy"]),
                self._to_json(state.get("standing_concerns") or build_default_standing_concerns()),
                self._to_json(state["audio_output_settings"]),
                self._to_json(state["microphone_settings"]),
            ),
        )
        self._write_payload_table(conn, "personas", "persona_id", state["personas"])
        self._write_payload_table(conn, "memory_sets", "memory_set_id", state["memory_sets"])
        self._write_payload_table(conn, "model_presets", "model_preset_id", state["model_presets"])
        self._write_payload_table(conn, "avatars", "avatar_id", state["avatars"])
        self._write_payload_table(conn, "camera_sources", "vision_source_id", state.get("camera_sources", {}))
        self._write_payload_table(conn, "mcp_servers", "mcp_server_id", state.get("mcp_servers", {}))
        self._write_payload_table(
            conn,
            "agent_skill_sources",
            "source_id",
            state.get("agent_skill_sources", {}),
        )
        self._write_desktop_capture_defaults(conn, state.get("desktop_capture_defaults"))
        self._write_console_client_settings(conn, state.get("console_client_settings", {}))

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

    def _read_conversation_display_names(
        self,
        conn: sqlite3.Connection,
    ) -> dict[str, dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT conversation_display_name_id, display_name, created_at, updated_at
            FROM conversation_display_names
            ORDER BY created_at ASC, conversation_display_name_id ASC
            """
        ).fetchall()
        return {
            row["conversation_display_name_id"]: dict(row)
            for row in rows
        }

    def _write_conversation_display_names(
        self,
        conn: sqlite3.Connection,
        entries: dict[str, dict[str, Any]],
    ) -> None:
        # 初期 state の呼ばれ方定義を seed する。既存 row は触らない。
        timestamp = now_iso()
        for entry_id, entry in entries.items():
            conn.execute(
                """
                INSERT INTO conversation_display_names (
                    conversation_display_name_id, display_name, created_at, updated_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    entry_id,
                    entry["display_name"],
                    entry.get("created_at") or timestamp,
                    entry.get("updated_at") or timestamp,
                ),
            )

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

    def _read_desktop_capture_defaults(self, conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT payload_json
            FROM desktop_capture_defaults
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            return build_default_desktop_capture()
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            return build_default_desktop_capture()
        return payload

    def _write_desktop_capture_defaults(
        self,
        conn: sqlite3.Connection,
        definition: dict[str, Any] | None,
    ) -> None:
        conn.execute("DELETE FROM desktop_capture_defaults")
        payload = definition if isinstance(definition, dict) else build_default_desktop_capture()
        conn.execute(
            """
            INSERT INTO desktop_capture_defaults (id, payload_json)
            VALUES (1, ?)
            """,
            (self._to_json(payload),),
        )

    def _read_console_client_settings(self, conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT client_id, last_connected_at, payload_json
            FROM console_client_settings
            ORDER BY client_id ASC
            """
        ).fetchall()
        return {
            row["client_id"]: {
                "last_connected_at": row["last_connected_at"],
                "settings": json.loads(row["payload_json"]),
            }
            for row in rows
        }

    def _write_console_client_settings(
        self,
        conn: sqlite3.Connection,
        entries: dict[str, dict[str, Any]],
    ) -> None:
        for client_id, entry in entries.items():
            conn.execute(
                """
                INSERT INTO console_client_settings (
                    client_id,
                    last_connected_at,
                    payload_json
                )
                VALUES (?, ?, ?)
                """,
                (
                    client_id,
                    entry.get("last_connected_at"),
                    self._to_json(entry["settings"]),
                ),
            )

    def _to_json(self, payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _voice_speaker_from_row(
        self,
        row: sqlite3.Row,
        *,
        include_embedding: bool,
    ) -> dict[str, Any]:
        speaker = {
            "person_ref": row["person_ref"],
            "conversation_display_name_id": row["conversation_display_name_id"],
            "display_name": row["display_name"],
            "registration_status": row["registration_status"],
            "model_id": row["model_id"],
            "registered_at": row["registered_at"],
            "updated_at": row["updated_at"],
        }
        if include_embedding:
            blob = row["embedding"]
            speaker["embedding"] = (
                list(struct.unpack(f"<{len(blob) // 4}f", blob))
                if blob is not None
                else None
            )
        return speaker
