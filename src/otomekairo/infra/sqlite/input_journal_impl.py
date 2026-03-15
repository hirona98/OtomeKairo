"""SQLite の input_journal 追記処理。"""

from __future__ import annotations

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _now_ms, _opaque_id


# Block: input_journal 追記
def append_input_journal(
    backend: SqliteBackend,
    *,
    observation_id: str,
    cycle_id: str,
    source: str,
    kind: str,
    captured_at: int,
    receipt_summary: str,
    payload_id: str,
) -> None:
    now_ms = _now_ms()
    with backend._connect() as connection:
        connection.execute(
            """
            INSERT INTO input_journal (
                journal_id,
                observation_id,
                cycle_id,
                source,
                kind,
                captured_at,
                receipt_summary,
                payload_ref_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("jrnl"),
                observation_id,
                cycle_id,
                source,
                kind,
                captured_at,
                receipt_summary,
                _json_text(
                    {
                        "payload_kind": "input_payload",
                        "payload_id": payload_id,
                        "payload_version": 1,
                    }
                ),
                now_ms,
            ),
        )
