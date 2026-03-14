"""Deterministic smoke check for fresh bootstrap and stale DB rejection."""

from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Callable

from otomekairo import __version__
from otomekairo.gateway.settings_editor_store import SettingsEditorStore
from otomekairo.schema.persistence import SCHEMA_NAME, SCHEMA_VERSION
from otomekairo.schema.settings import build_default_settings


# Block: Report constants
REPORT_SCHEMA_VERSION = 1


# Block: Smoke store bundle
@dataclass(frozen=True, slots=True)
class BootstrapInitSmokeStores:
    settings_editor_store: SettingsEditorStore


# Block: Public smoke runner
def run_bootstrap_init_smoke(
    *,
    keep_db: bool,
    build_stores: Callable[[Path], BootstrapInitSmokeStores],
) -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="otomekairo-bootstrap-init-"))
    db_path = temp_dir / "core.sqlite3"
    try:
        default_settings = build_default_settings()
        stores = build_stores(db_path)
        settings_editor = stores.settings_editor_store.read_settings_editor(default_settings)
        report = _build_report(
            db_path=db_path,
            keep_db=keep_db,
            settings_editor=settings_editor,
            build_stores=build_stores,
        )
        _validate_report(report)
        return report
    finally:
        if not keep_db:
            shutil.rmtree(temp_dir, ignore_errors=True)


# Block: Report build
def _build_report(
    *,
    db_path: Path,
    keep_db: bool,
    settings_editor: dict[str, Any],
    build_stores: Callable[[Path], BootstrapInitSmokeStores],
) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        meta_rows = connection.execute(
            """
            SELECT meta_key, meta_value_json
            FROM db_meta
            WHERE meta_key IN ('schema_version', 'schema_name', 'initialized_at', 'initializer_version')
            ORDER BY meta_key ASC
            """
        ).fetchall()
        meta = {
            str(row[0]): json.loads(str(row[1]))
            for row in meta_rows
        }
        sqlite_user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        singleton_rows = {
            "self_state": int(connection.execute("SELECT COUNT(*) FROM self_state").fetchone()[0]),
            "runtime_settings": int(connection.execute("SELECT COUNT(*) FROM runtime_settings").fetchone()[0]),
            "settings_editor_state": int(connection.execute("SELECT COUNT(*) FROM settings_editor_state").fetchone()[0]),
            "attention_state": int(connection.execute("SELECT COUNT(*) FROM attention_state").fetchone()[0]),
            "body_state": int(connection.execute("SELECT COUNT(*) FROM body_state").fetchone()[0]),
            "world_state": int(connection.execute("SELECT COUNT(*) FROM world_state").fetchone()[0]),
            "drive_state": int(connection.execute("SELECT COUNT(*) FROM drive_state").fetchone()[0]),
        }
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?
            WHERE meta_key = 'schema_version'
            """,
            (json.dumps(SCHEMA_VERSION - 1, ensure_ascii=True, separators=(",", ":")),),
        )
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
    stale_rejection_message: str | None = None
    try:
        build_stores(db_path)
    except RuntimeError as exc:
        stale_rejection_message = str(exc)
    editor_state = settings_editor.get("editor_state")
    checks = {
        "fresh_schema_meta_seeded": meta == {
            "initializer_version": __version__,
            "initialized_at": meta.get("initialized_at"),
            "schema_name": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
        },
        "fresh_sqlite_user_version_synced": sqlite_user_version == SCHEMA_VERSION,
        "singleton_rows_seeded": all(count == 1 for count in singleton_rows.values()),
        "settings_editor_defaults_seeded": (
            isinstance(editor_state, dict)
            and int(editor_state.get("revision", 0)) >= 1
            and bool(editor_state.get("active_character_preset_id"))
            and bool(editor_state.get("active_behavior_preset_id"))
            and bool(editor_state.get("active_conversation_preset_id"))
            and bool(editor_state.get("active_memory_preset_id"))
            and bool(editor_state.get("active_motion_preset_id"))
        ),
        "stale_db_rejected": (
            isinstance(stale_rejection_message, str)
            and "delete data/core.sqlite3 and restart" in stale_rejection_message
        ),
    }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "checks": checks,
        "meta": meta,
        "sqlite_user_version": sqlite_user_version,
        "singleton_rows": singleton_rows,
        "stale_rejection_message": stale_rejection_message,
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
            "bootstrap_init_smoke failed: " + ", ".join(sorted(failed_checks))
        )
