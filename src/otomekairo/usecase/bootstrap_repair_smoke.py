"""Deterministic smoke check for bootstrap repair of broken settings editor state."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import json
from pathlib import Path
from typing import Any

from otomekairo import __version__
from otomekairo.infra.sqlite_state_store import SCHEMA_VERSION, SqliteStateStore
from otomekairo.schema.settings import build_default_settings


# Block: Report constants
REPORT_SCHEMA_VERSION = 1


# Block: Public smoke runner
def run_bootstrap_repair_smoke(*, keep_db: bool) -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="otomekairo-bootstrap-repair-"))
    db_path = temp_dir / "core.sqlite3"
    try:
        default_settings = build_default_settings()
        store = SqliteStateStore(
            db_path=db_path,
            initializer_version=__version__,
        )
        store.initialize()
        expected_editor_state = _break_settings_editor_state_fixture(db_path=db_path)
        repaired_store = SqliteStateStore(
            db_path=db_path,
            initializer_version=__version__,
        )
        repaired_store.initialize()
        repaired_editor_state = repaired_store.read_settings_editor(default_settings)
        report = _build_report(
            db_path=db_path,
            keep_db=keep_db,
            expected_editor_state=expected_editor_state,
            repaired_editor_state=repaired_editor_state,
        )
        _validate_report(report)
        return report
    finally:
        if not keep_db:
            shutil.rmtree(temp_dir, ignore_errors=True)


# Block: Broken fixture builder
def _break_settings_editor_state_fixture(*, db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        editor_row = connection.execute(
            """
            SELECT
                active_character_preset_id,
                active_behavior_preset_id,
                active_conversation_preset_id,
                active_memory_preset_id,
                active_motion_preset_id,
                system_values_json,
                revision
            FROM settings_editor_state
            WHERE row_id = 1
            """
        ).fetchone()
        if editor_row is None:
            raise RuntimeError("bootstrap repair smoke requires seeded settings_editor_state")
        expected_editor_state = {
            "active_character_preset_id": str(editor_row[0]),
            "active_behavior_preset_id": str(editor_row[1]),
            "active_conversation_preset_id": str(editor_row[2]),
            "active_memory_preset_id": str(editor_row[3]),
            "active_motion_preset_id": str(editor_row[4]),
            "system_values_json": str(editor_row[5]),
            "revision": int(editor_row[6]),
        }
        connection.execute(
            "ALTER TABLE settings_editor_state RENAME TO settings_editor_state_v7_fixture"
        )
        connection.execute("PRAGMA user_version = 0")
    return expected_editor_state


# Block: Report builder
def _build_report(
    *,
    db_path: Path,
    keep_db: bool,
    expected_editor_state: dict[str, Any],
    repaired_editor_state: dict[str, Any],
) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        residue_rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name GLOB 'settings_editor_state_v*'
            ORDER BY name ASC
            """
        ).fetchall()
        sqlite_user_version = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
    current_editor_state = {
        "active_character_preset_id": repaired_editor_state["editor_state"]["active_character_preset_id"],
        "active_behavior_preset_id": repaired_editor_state["editor_state"]["active_behavior_preset_id"],
        "active_conversation_preset_id": repaired_editor_state["editor_state"]["active_conversation_preset_id"],
        "active_memory_preset_id": repaired_editor_state["editor_state"]["active_memory_preset_id"],
        "active_motion_preset_id": repaired_editor_state["editor_state"]["active_motion_preset_id"],
        "system_values_json": json.dumps(
            repaired_editor_state["editor_state"]["system_values"],
            ensure_ascii=True,
            separators=(",", ":"),
        ),
        "revision": int(repaired_editor_state["editor_state"]["revision"]),
    }
    checks = {
        "settings_editor_state_restored": current_editor_state == expected_editor_state,
        "settings_editor_residue_removed": residue_rows == [],
        "sqlite_user_version_synced": sqlite_user_version == SCHEMA_VERSION,
    }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "checks": checks,
        "expected_editor_state": expected_editor_state,
        "current_editor_state": current_editor_state,
        "sqlite_user_version": sqlite_user_version,
        "residue_table_names": [str(row[0]) for row in residue_rows],
    }
    if keep_db:
        report["db_path"] = str(db_path)
    return report


# Block: Report validation
def _validate_report(report: dict[str, Any]) -> None:
    failed_checks = [
        check_name
        for check_name, passed in report["checks"].items()
        if bool(passed) is False
    ]
    if failed_checks:
        raise RuntimeError(
            "bootstrap_repair_smoke failed: " + ", ".join(sorted(failed_checks))
        )
