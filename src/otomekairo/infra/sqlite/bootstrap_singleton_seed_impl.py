"""SQLite bootstrap singleton seed 処理。"""

from __future__ import annotations

import json
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
    _attention_primary_focus_seed,
    _body_state_load_seed,
    _body_state_mobility_seed,
    _body_state_output_locks_seed,
    _body_state_posture_seed,
    _body_state_sensor_availability_seed,
    _drive_state_drive_levels_seed,
    _drive_state_priority_effects_seed,
    _self_state_current_emotion_seed,
    _self_state_invariants_seed,
    _self_state_long_term_goals_seed,
    _self_state_personality_seed,
    _self_state_relationship_overview_seed,
    _world_state_affordances_seed,
    _world_state_attention_targets_seed,
    _world_state_constraints_seed,
    _world_state_external_waits_seed,
    _world_state_location_seed,
    _world_state_situation_summary_seed,
    _world_state_surroundings_seed,
)
from otomekairo.infra.sqlite_store_runtime_view import (
    _body_state_has_current_shape,
    _drive_state_has_current_shape,
    _world_state_has_current_shape,
)
from otomekairo.schema.settings import build_default_settings


# Block: singleton seed
def seed_singletons(
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
    # Block: attention_state seed
    connection.execute(
        """
        INSERT INTO attention_state (
            row_id,
            primary_focus_json,
            secondary_focuses_json,
            suppressed_items_json,
            revisit_queue_json,
            updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(row_id) DO NOTHING
        """,
        (
            _json_text(_attention_primary_focus_seed()),
            _json_text([]),
            _json_text([]),
            _json_text([]),
            now_ms,
        ),
    )
    ensure_attention_state_defaults(
        connection=connection,
        now_ms=now_ms,
    )
    # Block: body_state seed
    connection.execute(
        """
        INSERT INTO body_state (
            row_id,
            posture_json,
            mobility_json,
            sensor_availability_json,
            output_locks_json,
            load_json,
            updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(row_id) DO NOTHING
        """,
        (
            _json_text(_body_state_posture_seed()),
            _json_text(_body_state_mobility_seed()),
            _json_text(_body_state_sensor_availability_seed()),
            _json_text(_body_state_output_locks_seed()),
            _json_text(_body_state_load_seed()),
            now_ms,
        ),
    )
    ensure_body_state_defaults(
        connection=connection,
        now_ms=now_ms,
    )
    # Block: world_state seed
    connection.execute(
        """
        INSERT INTO world_state (
            row_id,
            location_json,
            situation_summary,
            surroundings_json,
            affordances_json,
            constraints_json,
            attention_targets_json,
            external_waits_json,
            updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(row_id) DO NOTHING
        """,
        (
            _json_text(_world_state_location_seed()),
            _world_state_situation_summary_seed(),
            _json_text(_world_state_surroundings_seed()),
            _json_text(_world_state_affordances_seed()),
            _json_text(_world_state_constraints_seed()),
            _json_text(_world_state_attention_targets_seed()),
            _json_text(_world_state_external_waits_seed()),
            now_ms,
        ),
    )
    ensure_world_state_defaults(
        connection=connection,
        now_ms=now_ms,
    )
    # Block: drive_state seed
    connection.execute(
        """
        INSERT INTO drive_state (
            row_id,
            drive_levels_json,
            priority_effects_json,
            updated_at
        )
        VALUES (1, ?, ?, ?)
        ON CONFLICT(row_id) DO UPDATE SET
            priority_effects_json = excluded.priority_effects_json,
            updated_at = excluded.updated_at
        WHERE json_extract(drive_state.priority_effects_json, '$.task_progress_bias') IS NULL
           OR json_extract(drive_state.priority_effects_json, '$.exploration_bias') IS NULL
           OR json_extract(drive_state.priority_effects_json, '$.maintenance_bias') IS NULL
           OR json_extract(drive_state.priority_effects_json, '$.social_bias') IS NULL
        """,
        (
            _json_text(_drive_state_drive_levels_seed()),
            _json_text(_drive_state_priority_effects_seed()),
            now_ms,
        ),
    )
    ensure_drive_state_defaults(
        connection=connection,
        now_ms=now_ms,
    )


# Block: attention_state 既定値補完
def ensure_attention_state_defaults(
    *,
    connection: sqlite3.Connection,
    now_ms: int,
) -> None:
    row = connection.execute(
        """
        SELECT primary_focus_json
        FROM attention_state
        WHERE row_id = 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("attention_state row is missing")
    primary_focus = json.loads(row["primary_focus_json"])
    if not isinstance(primary_focus, dict):
        raise RuntimeError("attention_state.primary_focus_json must be object")
    required_keys = {"focus_ref", "focus_kind", "summary", "score_hint", "reason_codes"}
    if required_keys.issubset(primary_focus):
        return
    connection.execute(
        """
        UPDATE attention_state
        SET primary_focus_json = ?,
            secondary_focuses_json = ?,
            suppressed_items_json = ?,
            revisit_queue_json = ?,
            updated_at = ?
        WHERE row_id = 1
        """,
        (
            _json_text(_attention_primary_focus_seed()),
            _json_text([]),
            _json_text([]),
            _json_text([]),
            now_ms,
        ),
    )


# Block: body_state 既定値補完
def ensure_body_state_defaults(
    *,
    connection: sqlite3.Connection,
    now_ms: int,
) -> None:
    row = connection.execute(
        """
        SELECT posture_json, mobility_json, sensor_availability_json, output_locks_json, load_json
        FROM body_state
        WHERE row_id = 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("body_state row is missing")
    if _body_state_has_current_shape(row):
        return
    connection.execute(
        """
        UPDATE body_state
        SET posture_json = ?,
            mobility_json = ?,
            sensor_availability_json = ?,
            output_locks_json = ?,
            load_json = ?,
            updated_at = ?
        WHERE row_id = 1
        """,
        (
            _json_text(_body_state_posture_seed()),
            _json_text(_body_state_mobility_seed()),
            _json_text(_body_state_sensor_availability_seed()),
            _json_text(_body_state_output_locks_seed()),
            _json_text(_body_state_load_seed()),
            now_ms,
        ),
    )


# Block: world_state 既定値補完
def ensure_world_state_defaults(
    *,
    connection: sqlite3.Connection,
    now_ms: int,
) -> None:
    row = connection.execute(
        """
        SELECT
            location_json,
            situation_summary,
            surroundings_json,
            affordances_json,
            constraints_json,
            attention_targets_json,
            external_waits_json
        FROM world_state
        WHERE row_id = 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("world_state row is missing")
    if _world_state_has_current_shape(row):
        return
    connection.execute(
        """
        UPDATE world_state
        SET location_json = ?,
            situation_summary = ?,
            surroundings_json = ?,
            affordances_json = ?,
            constraints_json = ?,
            attention_targets_json = ?,
            external_waits_json = ?,
            updated_at = ?
        WHERE row_id = 1
        """,
        (
            _json_text(_world_state_location_seed()),
            _world_state_situation_summary_seed(),
            _json_text(_world_state_surroundings_seed()),
            _json_text(_world_state_affordances_seed()),
            _json_text(_world_state_constraints_seed()),
            _json_text(_world_state_attention_targets_seed()),
            _json_text(_world_state_external_waits_seed()),
            now_ms,
        ),
    )


# Block: drive_state 既定値補完
def ensure_drive_state_defaults(
    *,
    connection: sqlite3.Connection,
    now_ms: int,
) -> None:
    row = connection.execute(
        """
        SELECT drive_levels_json, priority_effects_json
        FROM drive_state
        WHERE row_id = 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("drive_state row is missing")
    if _drive_state_has_current_shape(row):
        return
    connection.execute(
        """
        UPDATE drive_state
        SET drive_levels_json = ?,
            priority_effects_json = ?,
            updated_at = ?
        WHERE row_id = 1
        """,
        (
            _json_text(_drive_state_drive_levels_seed()),
            _json_text(_drive_state_priority_effects_seed()),
            now_ms,
        ),
    )
