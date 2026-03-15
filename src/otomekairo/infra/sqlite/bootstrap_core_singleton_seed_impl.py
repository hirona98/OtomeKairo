"""SQLite bootstrap の core singleton seed 処理。"""

from __future__ import annotations

import sqlite3

from otomekairo.infra.sqlite.bootstrap_settings_editor_impl import (
    ensure_runtime_settings_defaults,
    ensure_settings_editor_defaults,
)
from otomekairo.infra.sqlite_store_legacy_runtime import (
    _json_text,
    _runtime_settings_seed_timestamps,
)
from otomekairo.infra.sqlite_store_live_state_seeds import (
    _self_state_current_emotion_seed,
    _self_state_invariants_seed,
    _self_state_long_term_goals_seed,
    _self_state_personality_seed,
    _self_state_relationship_overview_seed,
)
from otomekairo.schema.settings import build_default_settings


# Block: core singleton seed
def seed_core_singletons(
    *,
    connection: sqlite3.Connection,
    now_ms: int,
) -> None:
    # Block: self_state seed
    connection.execute(
        """
        INSERT INTO self_state (
            row_id,
            personality_json,
            current_emotion_json,
            long_term_goals_json,
            relationship_overview_json,
            invariants_json,
            personality_updated_at,
            updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(row_id) DO NOTHING
        """,
        (
            _json_text(_self_state_personality_seed()),
            _json_text(_self_state_current_emotion_seed()),
            _json_text(_self_state_long_term_goals_seed()),
            _json_text(_self_state_relationship_overview_seed()),
            _json_text(_self_state_invariants_seed()),
            now_ms,
            now_ms,
        ),
    )
    # Block: runtime_settings seed
    connection.execute(
        """
        INSERT INTO runtime_settings (
            row_id,
            values_json,
            value_updated_at_json,
            updated_at
        )
        VALUES (1, ?, ?, ?)
        ON CONFLICT(row_id) DO NOTHING
        """,
        (
            _json_text(build_default_settings()),
            _json_text(_runtime_settings_seed_timestamps(now_ms)),
            now_ms,
        ),
    )
    ensure_runtime_settings_defaults(
        connection=connection,
        now_ms=now_ms,
    )
    ensure_settings_editor_defaults(
        connection=connection,
        now_ms=now_ms,
    )
