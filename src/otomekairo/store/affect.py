from __future__ import annotations

import json
import sqlite3
from typing import Any

from otomekairo.memory.utils import parse_iso


MOOD_BASELINE_HALFLIFE_SECONDS = 86400.0
MOOD_RESIDUAL_HALFLIFE_SECONDS = 21600.0
MOOD_RESIDUAL_ALPHA = 0.75


def _zero_vad() -> dict[str, float]:
    # 既定値
    return {"v": 0.0, "a": 0.0, "d": 0.0}


def _clamp01(value: Any) -> float:
    # 正規化
    if not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(float(value), 1.0))


def _clamp_vad_axis(value: Any) -> float:
    # 正規化
    if not isinstance(value, (int, float)):
        return 0.0
    return max(-1.0, min(float(value), 1.0))


def _clamp_vad(value: Any) -> dict[str, float]:
    # 形状
    if not isinstance(value, dict):
        return _zero_vad()

    # 結果
    return {
        "v": _clamp_vad_axis(value.get("v")),
        "a": _clamp_vad_axis(value.get("a")),
        "d": _clamp_vad_axis(value.get("d")),
    }


def _vad_add(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    # 軸加算
    return _clamp_vad(
        {
            "v": left["v"] + right["v"],
            "a": left["a"] + right["a"],
            "d": left["d"] + right["d"],
        }
    )


def _vad_sub(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    # 軸減算
    return _clamp_vad(
        {
            "v": left["v"] - right["v"],
            "a": left["a"] - right["a"],
            "d": left["d"] - right["d"],
        }
    )


def _vad_scale(vad: dict[str, float], scale: float) -> dict[str, float]:
    # 係数
    return _clamp_vad(
        {
            "v": vad["v"] * scale,
            "a": vad["a"] * scale,
            "d": vad["d"] * scale,
        }
    )


def _vad_lerp(cur: dict[str, float], tgt: dict[str, float], alpha: float) -> dict[str, float]:
    # 線形補間
    return _clamp_vad(
        {
            "v": cur["v"] + alpha * (tgt["v"] - cur["v"]),
            "a": cur["a"] + alpha * (tgt["a"] - cur["a"]),
            "d": cur["d"] + alpha * (tgt["d"] - cur["d"]),
        }
    )


def _vad_decay(vad: dict[str, float], dt_seconds: float, half_life_seconds: float) -> dict[str, float]:
    # 半減期減衰
    if half_life_seconds <= 0:
        return _zero_vad()
    scale = 0.5 ** (max(0.0, dt_seconds) / half_life_seconds)
    return _vad_scale(vad, scale)


class StoreAffectMixin:
    def persist_turn_consolidation(
        self,
        *,
        episode: dict[str, Any] | None,
        memory_actions: list[dict[str, Any]],
        episode_affects: list[dict[str, Any]],
    ) -> dict[str, Any]:
        memory_link_records: list[dict[str, Any]] = []

        # トランザクション
        with self._memory_db() as conn:
            # episode追加
            if episode is not None:
                self._insert_episode(conn, episode)

            # 記憶アクション群
            for action in memory_actions:
                memory_link_records.extend(self._apply_memory_action(conn, action))

            # episode affect群
            for episode_affect in episode_affects:
                self._insert_episode_affect(conn, episode_affect)

            # mood更新
            mood_state_update = self._update_mood_state_from_episode_affects(
                conn,
                episode_affects=episode_affects,
                write_time=episode["formed_at"] if episode is not None else None,
            )

        # 結果
        return {
            "mood_state_update": mood_state_update,
            "affect_state_updates": [],
            "memory_link_update": self._memory_link_update_summary(memory_link_records),
        }

    def persist_affect_state_updates(self, *, affect_state_updates: list[dict[str, Any]]) -> dict[str, Any]:
        # 空
        if not affect_state_updates:
            return self._affect_state_update_summary([], [])

        # トランザクション
        persisted_records: list[dict[str, Any]] = []
        touched_targets: set[tuple[str, str]] = set()
        with self._memory_db() as conn:
            for record in affect_state_updates:
                persisted = self._upsert_affect_state(conn, record)
                persisted_records.append(persisted)
                touched_targets.add((persisted["target_scope_type"], persisted["target_scope_key"]))
            pruned_affect_state_ids = self._prune_affect_states_for_targets(
                conn,
                memory_set_id=affect_state_updates[0]["memory_set_id"],
                target_refs=touched_targets,
            )

        # 結果
        return self._affect_state_update_summary(persisted_records, pruned_affect_state_ids)

    def get_mood_state(self, *, memory_set_id: str, current_time: str) -> dict[str, Any]:
        # クエリ
        with self._memory_db() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM mood_state
                WHERE memory_set_id = ?
                """,
                (memory_set_id,),
            ).fetchone()

        # 既定値
        if row is None:
            return {
                "baseline_vad": _zero_vad(),
                "residual_vad": _zero_vad(),
                "current_vad": _zero_vad(),
                "confidence": 0.0,
                "observed_at": None,
                "created_at": None,
                "updated_at": None,
            }

        # 現在値導出
        record = json.loads(row["payload_json"])
        return self._with_current_vad(record, current_time=current_time)

    def list_affect_states_for_context(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]] | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Query部品群
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # スコープFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(target_scope_type = ? AND target_scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        query = f"""
            SELECT payload_json
            FROM affect_state
            WHERE {" AND ".join(clauses)}
            ORDER BY intensity DESC, updated_at DESC, rowid DESC
            LIMIT ?
        """

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def list_recent_episode_affects_for_context(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]] | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Query部品群
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # スコープFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(target_scope_type = ? AND target_scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        query = f"""
            SELECT payload_json
            FROM episode_affects
            WHERE {" AND ".join(clauses)}
            ORDER BY observed_at DESC, intensity DESC, rowid DESC
            LIMIT ?
        """

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def list_episode_affects_for_reflection(
        self,
        *,
        memory_set_id: str,
        since_iso: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Query部品群
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]
        if isinstance(since_iso, str) and since_iso:
            clauses.append("observed_at > ?")
            params.append(since_iso)

        query = f"""
            SELECT payload_json
            FROM episode_affects
            WHERE {" AND ".join(clauses)}
            ORDER BY observed_at DESC, intensity DESC, rowid DESC
            LIMIT ?
        """

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def _insert_episode_affect(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 追加
        conn.execute(
            """
            INSERT OR REPLACE INTO episode_affects (
                episode_affect_id,
                memory_set_id,
                episode_id,
                target_scope_type,
                target_scope_key,
                affect_label,
                intensity,
                confidence,
                observed_at,
                created_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["episode_affect_id"],
                record["memory_set_id"],
                record["episode_id"],
                record["target_scope_type"],
                record["target_scope_key"],
                record["affect_label"],
                record["intensity"],
                record["confidence"],
                record["observed_at"],
                record["created_at"],
                self._to_json(record),
            ),
        )

    def _update_mood_state_from_episode_affects(
        self,
        conn: sqlite3.Connection,
        *,
        episode_affects: list[dict[str, Any]],
        write_time: str | None,
    ) -> dict[str, Any]:
        # self だけに絞る
        self_affects = [
            affect
            for affect in episode_affects
            if affect.get("target_scope_type") == "self" and affect.get("target_scope_key") == "self"
        ]
        if not self_affects:
            return {
                "updated": False,
                "reason": "no_self_episode_affect",
            }

        # 集約
        weighted_vad = _zero_vad()
        sum_weight = 0.0
        observed_times: list[str] = []
        for affect in self_affects:
            weight = _clamp01(affect.get("intensity")) * _clamp01(affect.get("confidence"))
            if weight <= 0.0:
                continue
            weighted_vad = _vad_add(weighted_vad, _vad_scale(_clamp_vad(affect.get("vad")), weight))
            sum_weight += weight
            if isinstance(affect.get("observed_at"), str) and affect["observed_at"]:
                observed_times.append(affect["observed_at"])

        if sum_weight <= 0.0 or not observed_times:
            return {
                "updated": False,
                "reason": "zero_weight_episode_affect",
            }

        # 現在 row
        existing_row = conn.execute(
            """
            SELECT payload_json
            FROM mood_state
            WHERE memory_set_id = ?
            """,
            (self_affects[0]["memory_set_id"],),
        ).fetchone()

        moment_vad = _vad_scale(weighted_vad, 1.0 / sum_weight)
        moment_strength = _clamp01(sum_weight)
        moment_observed_at = max(observed_times)
        write_timestamp = write_time or moment_observed_at

        if existing_row is None:
            previous = {
                "mood_state_id": f"mood_state:{self_affects[0]['memory_set_id']}",
                "memory_set_id": self_affects[0]["memory_set_id"],
                "baseline_vad": _zero_vad(),
                "residual_vad": _zero_vad(),
                "confidence": 0.0,
                "observed_at": moment_observed_at,
                "created_at": write_timestamp,
                "updated_at": write_timestamp,
            }
        else:
            previous = json.loads(existing_row["payload_json"])

        previous_observed_at = previous.get("observed_at") or moment_observed_at
        dt_seconds = max(0.0, (parse_iso(moment_observed_at) - parse_iso(previous_observed_at)).total_seconds())
        alpha_base = _clamp01((1 - 0.5 ** (dt_seconds / MOOD_BASELINE_HALFLIFE_SECONDS)) * moment_strength)
        baseline_vad_new = _vad_lerp(_clamp_vad(previous.get("baseline_vad")), moment_vad, alpha_base)
        residual_vad_decayed = _vad_decay(
            _clamp_vad(previous.get("residual_vad")),
            dt_seconds,
            MOOD_RESIDUAL_HALFLIFE_SECONDS,
        )
        residual_input = _vad_sub(moment_vad, baseline_vad_new)
        residual_alpha = _clamp01(MOOD_RESIDUAL_ALPHA * moment_strength)
        residual_vad_new = _vad_lerp(residual_vad_decayed, residual_input, residual_alpha)
        current_vad = _vad_add(baseline_vad_new, residual_vad_new)
        payload = {
            "mood_state_id": previous["mood_state_id"],
            "memory_set_id": previous["memory_set_id"],
            "baseline_vad": baseline_vad_new,
            "residual_vad": residual_vad_new,
            "confidence": moment_strength,
            "observed_at": moment_observed_at,
            "created_at": previous["created_at"],
            "updated_at": write_timestamp,
        }
        self._upsert_mood_state(conn, payload)

        return {
            "updated": True,
            "reason": None,
            "confidence": payload["confidence"],
            "baseline_vad": baseline_vad_new,
            "residual_vad": residual_vad_new,
            "current_vad": current_vad,
            "observed_at": payload["observed_at"],
            "updated_at": payload["updated_at"],
        }

    def _upsert_mood_state(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 保存
        conn.execute(
            """
            INSERT OR REPLACE INTO mood_state (
                mood_state_id,
                memory_set_id,
                confidence,
                observed_at,
                created_at,
                updated_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["mood_state_id"],
                record["memory_set_id"],
                record["confidence"],
                record["observed_at"],
                record["created_at"],
                record["updated_at"],
                self._to_json(record),
            ),
        )

    def _with_current_vad(self, record: dict[str, Any], *, current_time: str) -> dict[str, Any]:
        # 減衰後現在値
        observed_at = record.get("observed_at")
        elapsed_seconds = 0.0
        if isinstance(observed_at, str) and observed_at:
            elapsed_seconds = max(0.0, (parse_iso(current_time) - parse_iso(observed_at)).total_seconds())
        current_vad = _vad_add(
            _clamp_vad(record.get("baseline_vad")),
            _vad_decay(_clamp_vad(record.get("residual_vad")), elapsed_seconds, MOOD_RESIDUAL_HALFLIFE_SECONDS),
        )

        # 結果
        return {
            **record,
            "baseline_vad": _clamp_vad(record.get("baseline_vad")),
            "residual_vad": _clamp_vad(record.get("residual_vad")),
            "current_vad": current_vad,
        }

    def _upsert_affect_state(self, conn: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
        # 既存検索
        existing_row = conn.execute(
            """
            SELECT affect_state_id, observed_at, created_at
            FROM affect_state
            WHERE memory_set_id = ?
              AND target_scope_type = ?
              AND target_scope_key = ?
              AND affect_label = ?
            """,
            (
                record["memory_set_id"],
                record["target_scope_type"],
                record["target_scope_key"],
                record["affect_label"],
            ),
        ).fetchone()

        # 識別解決
        affect_state_id = record["affect_state_id"]
        created_at = record["created_at"]
        if existing_row is not None:
            affect_state_id = existing_row["affect_state_id"]
            created_at = existing_row["created_at"]

        payload = {
            **record,
            "affect_state_id": affect_state_id,
            "created_at": created_at,
        }

        # upsert実行
        conn.execute(
            """
            INSERT OR REPLACE INTO affect_state (
                affect_state_id,
                memory_set_id,
                target_scope_type,
                target_scope_key,
                affect_label,
                intensity,
                confidence,
                observed_at,
                created_at,
                updated_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["affect_state_id"],
                payload["memory_set_id"],
                payload["target_scope_type"],
                payload["target_scope_key"],
                payload["affect_label"],
                payload["intensity"],
                payload["confidence"],
                payload["observed_at"],
                payload["created_at"],
                payload["updated_at"],
                self._to_json(payload),
            ),
        )
        return payload

    def _prune_affect_states_for_targets(
        self,
        conn: sqlite3.Connection,
        *,
        memory_set_id: str,
        target_refs: set[tuple[str, str]],
        keep_limit: int = 2,
    ) -> list[str]:
        # 対象ごとに強い持続感情だけを残す。
        pruned_ids: list[str] = []
        for target_scope_type, target_scope_key in sorted(target_refs):
            rows = conn.execute(
                """
                SELECT affect_state_id
                FROM affect_state
                WHERE memory_set_id = ?
                  AND target_scope_type = ?
                  AND target_scope_key = ?
                ORDER BY intensity DESC, confidence DESC, updated_at DESC, rowid DESC
                """,
                (memory_set_id, target_scope_type, target_scope_key),
            ).fetchall()
            for row in rows[keep_limit:]:
                affect_state_id = row["affect_state_id"]
                conn.execute(
                    """
                    DELETE FROM affect_state
                    WHERE affect_state_id = ?
                    """,
                    (affect_state_id,),
                )
                pruned_ids.append(affect_state_id)
        return pruned_ids

    def _affect_state_update_summary(
        self,
        records: list[dict[str, Any]],
        pruned_affect_state_ids: list[str],
    ) -> dict[str, Any]:
        # trace向け要約
        return {
            "result_status": "updated" if records or pruned_affect_state_ids else "no_change",
            "updated_affect_state_ids": [
                record["affect_state_id"]
                for record in records
                if isinstance(record.get("affect_state_id"), str)
            ],
            "pruned_affect_state_ids": pruned_affect_state_ids,
            "affect_states": [
                {
                    "affect_state_id": record["affect_state_id"],
                    "target_scope_type": record["target_scope_type"],
                    "target_scope_key": record["target_scope_key"],
                    "affect_label": record["affect_label"],
                    "intensity": record["intensity"],
                    "confidence": record["confidence"],
                    "updated_at": record["updated_at"],
                }
                for record in records
            ],
        }
