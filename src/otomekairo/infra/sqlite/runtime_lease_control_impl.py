"""SQLite の runtime lease 制御。"""

from __future__ import annotations

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_legacy_runtime import _now_ms
from otomekairo.schema.store_errors import StoreConflictError, StoreValidationError


# Block: runtime lease 取得
def acquire_runtime_lease(
    backend: SqliteBackend,
    *,
    owner_token: str,
    lease_ttl_ms: int,
) -> None:
    if lease_ttl_ms <= 0:
        raise StoreValidationError("lease_ttl_ms must be positive")
    now_ms = _now_ms()
    expires_at = now_ms + lease_ttl_ms
    with backend._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT owner_token, expires_at
            FROM runtime_leases
            WHERE lease_name = 'primary_runtime'
            """
        ).fetchone()
        if row is not None and row["owner_token"] != owner_token and row["expires_at"] >= now_ms:
            raise StoreConflictError("primary runtime lease is already held")
        connection.execute(
            """
            INSERT INTO runtime_leases (
                lease_name,
                owner_token,
                acquired_at,
                heartbeat_at,
                expires_at
            )
            VALUES ('primary_runtime', ?, ?, ?, ?)
            ON CONFLICT(lease_name) DO UPDATE SET
                owner_token = excluded.owner_token,
                acquired_at = CASE
                    WHEN runtime_leases.owner_token = excluded.owner_token
                        THEN runtime_leases.acquired_at
                    ELSE excluded.acquired_at
                END,
                heartbeat_at = excluded.heartbeat_at,
                expires_at = excluded.expires_at
            """,
            (owner_token, now_ms, now_ms, expires_at),
        )


# Block: runtime lease 解放
def release_runtime_lease(
    backend: SqliteBackend,
    *,
    owner_token: str,
) -> None:
    with backend._connect() as connection:
        connection.execute(
            """
            DELETE FROM runtime_leases
            WHERE lease_name = 'primary_runtime'
              AND owner_token = ?
            """,
            (owner_token,),
        )
