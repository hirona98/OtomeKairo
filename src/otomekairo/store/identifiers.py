from __future__ import annotations

import sqlite3


INTERNAL_ID_KINDS = frozenset({
    "event", "cycle", "episode", "episode_series", "memory_unit", "memory_link", "revision", "correction",
    "episode_affect", "mood_state", "affect_state", "world_state", "activity", "drive",
    "ongoing_action", "autonomous_run", "reflection_run", "visual_observation", "observation",
    "daily_visual_digest", "visual_duplicate_group", "relation_index",
})
MAX_INTERNAL_ID = 2**63 - 1


class InternalIdentityConflictError(ValueError):
    pass


class StoreIdentifierMixin:
    def resolve_observation_ids(self, identities: list[tuple[str, str]]) -> list[str]:
        # センサー報告の一つのbatchを一取引で確定する。
        with self._memory_db() as conn:
            return [self._resolve_internal_id(conn, "observation", scope="", key=key, fingerprint=fingerprint)
                    for key, fingerprint in identities]

    def allocate_id(self, kind: str) -> str:
        # 呼び出し元へ返す前に採番を確定する。後続の保存失敗による欠番は許容する。
        with self._memory_db() as conn:
            return self._allocate_id(conn, kind)

    def _allocate_id(self, conn: sqlite3.Connection, kind: str) -> str:
        if kind not in INTERNAL_ID_KINDS:
            raise ValueError(f"Unknown internal ID kind: {kind}")
        row = conn.execute(
            "UPDATE internal_id_sequence SET last_value = last_value + 1 "
            "WHERE id = 1 AND last_value < ? RETURNING last_value", (MAX_INTERNAL_ID,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Internal ID sequence is missing or exhausted.")
        return f"{kind}:{row[0]}"

    def resolve_internal_id(
        self, kind: str, *, scope: str, key: str, fingerprint: str | None = None,
    ) -> str:
        with self._memory_db() as conn:
            return self._resolve_internal_id(conn, kind, scope=scope, key=key, fingerprint=fingerprint)

    def _resolve_internal_id(
        self, conn: sqlite3.Connection, kind: str, *, scope: str, key: str,
        fingerprint: str | None = None,
    ) -> str:
        if kind not in INTERNAL_ID_KINDS or not isinstance(scope, str) or not isinstance(key, str) or not key:
            raise ValueError("Internal identity requires a known kind, scope and nonempty key.")
        row = conn.execute(
            "SELECT identifier, fingerprint FROM internal_identities WHERE kind = ? AND scope = ? AND identity_key = ?",
            (kind, scope, key),
        ).fetchone()
        if row is not None:
            if row["fingerprint"] != fingerprint:
                raise InternalIdentityConflictError("An internal identity refers to different source data.")
            return row["identifier"]
        identifier = self._allocate_id(conn, kind)
        conn.execute(
            "INSERT INTO internal_identities(kind, scope, identity_key, identifier, fingerprint) VALUES (?, ?, ?, ?, ?)",
            (kind, scope, key, identifier, fingerprint),
        )
        return identifier
