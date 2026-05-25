from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any
import uuid

from otomekairo.memory.reflection.constants import (
    REFLECTION_AFFECT_STATE_CONFIDENCE_WEAKEN_FACTOR,
    REFLECTION_AFFECT_STATE_INITIAL_LOOKBACK_HOURS,
    REFLECTION_AFFECT_STATE_MIN_EPISODES,
    REFLECTION_AFFECT_STATE_MIN_INTENSITY,
    REFLECTION_AFFECT_STATE_WEAKEN_AFTER_DAYS,
    REFLECTION_AFFECT_STATE_WEAKEN_FACTOR,
)
from otomekairo.memory.utils import clamp_score, days_since, local_datetime, optional_text


class MemoryReflectionAffectMixin:
    def _empty_affect_state_update(self, result_status: str = "not_started") -> dict[str, Any]:
        return {
            "result_status": result_status,
            "created_affect_state_ids": [],
            "updated_affect_state_ids": [],
            "weakened_affect_state_ids": [],
            "pruned_affect_state_ids": [],
            "affect_state_summaries": [],
        }

    def _build_reflective_affect_state_updates(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        episode_affects: list[dict[str, Any]],
        existing_affect_states: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # 対象指向の感情だけを持続状態へ育てる。self の現在気分は mood_state が持つ。
        existing_index = {
            (
                record.get("target_scope_type"),
                record.get("target_scope_key"),
                record.get("affect_label"),
            ): record
            for record in existing_affect_states
            if isinstance(record, dict)
        }
        grouped_affects: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for affect in episode_affects:
            target_scope_type = optional_text(affect.get("target_scope_type"))
            target_scope_key = optional_text(affect.get("target_scope_key"))
            affect_label = optional_text(affect.get("affect_label"))
            if target_scope_type not in {"relationship", "user"}:
                continue
            if target_scope_key is None or affect_label is None:
                continue
            if clamp_score(affect.get("intensity")) * clamp_score(affect.get("confidence")) <= 0.0:
                continue
            grouped_affects[(target_scope_type, target_scope_key, affect_label)].append(affect)

        updates: list[dict[str, Any]] = []
        updated_keys: set[tuple[str, str, str]] = set()
        for key, affects in sorted(grouped_affects.items()):
            target_scope_type, target_scope_key, affect_label = key
            support_episode_ids = self._unique_affect_texts(affects, "episode_id")
            existing = existing_index.get(key)
            if existing is None and len(support_episode_ids) < REFLECTION_AFFECT_STATE_MIN_EPISODES:
                continue

            moment = self._reflective_affect_moment(affects)
            if moment["weight"] <= 0.0:
                continue

            if existing is None:
                affect_state_id = f"affect_state:{uuid.uuid4().hex}"
                intensity = moment["intensity"]
                confidence = moment["confidence"]
                update_kind = "created"
                created_at = finished_at
            else:
                affect_state_id = existing["affect_state_id"]
                intensity = clamp_score(clamp_score(existing.get("intensity")) * 0.65 + moment["intensity"] * 0.35)
                confidence = clamp_score(max(clamp_score(existing.get("confidence")) * 0.9, moment["confidence"]))
                update_kind = "updated"
                created_at = existing.get("created_at") or finished_at

            observed_at = max(
                [
                    value
                    for value in (self._unique_affect_texts(affects, "observed_at") or [finished_at])
                    if isinstance(value, str) and value
                ]
            )
            update_record = {
                "affect_state_id": affect_state_id,
                "memory_set_id": memory_set_id,
                "target_scope_type": target_scope_type,
                "target_scope_key": target_scope_key,
                "affect_label": affect_label,
                "summary_text": self._reflective_affect_state_summary_text(
                    target_scope_type=target_scope_type,
                    target_scope_key=target_scope_key,
                    affect_label=affect_label,
                    support_episode_count=len(support_episode_ids),
                ),
                "vad": moment["vad"],
                "intensity": intensity,
                "confidence": confidence,
                "observed_at": observed_at,
                "created_at": created_at,
                "updated_at": finished_at,
                "supporting_episode_affect_ids": self._unique_affect_texts(affects, "episode_affect_id"),
                "supporting_episode_ids": support_episode_ids,
                "support_episode_count": len(support_episode_ids),
                "update_kind": update_kind,
            }
            updates.append(update_record)
            updated_keys.add(key)

        for existing in existing_affect_states:
            if not isinstance(existing, dict):
                continue
            key = (
                existing.get("target_scope_type"),
                existing.get("target_scope_key"),
                existing.get("affect_label"),
            )
            if key in updated_keys:
                continue
            if existing.get("target_scope_type") not in {"relationship", "user"}:
                continue
            if days_since(existing.get("updated_at"), finished_at) < REFLECTION_AFFECT_STATE_WEAKEN_AFTER_DAYS:
                continue
            previous_intensity = clamp_score(existing.get("intensity"))
            weakened_intensity = clamp_score(previous_intensity * REFLECTION_AFFECT_STATE_WEAKEN_FACTOR)
            if previous_intensity <= REFLECTION_AFFECT_STATE_MIN_INTENSITY and weakened_intensity <= previous_intensity:
                continue
            updates.append(
                {
                    **existing,
                    "intensity": weakened_intensity,
                    "confidence": clamp_score(
                        clamp_score(existing.get("confidence")) * REFLECTION_AFFECT_STATE_CONFIDENCE_WEAKEN_FACTOR
                    ),
                    "observed_at": existing.get("observed_at") or existing.get("updated_at") or finished_at,
                    "created_at": existing.get("created_at") or finished_at,
                    "updated_at": finished_at,
                    "supporting_episode_affect_ids": [],
                    "supporting_episode_ids": [],
                    "support_episode_count": 0,
                    "update_kind": "weakened",
                    "update_reason": "support_not_observed_after_reflection_window",
                }
            )

        return updates

    def _initial_affect_state_since_iso(self, since_iso: str | None) -> str | None:
        # 初回作成では、直前 reflection で単発消費された episode_affect を短く拾い直す。
        if not isinstance(since_iso, str) or not since_iso:
            return None
        return (
            local_datetime(since_iso)
            - timedelta(hours=REFLECTION_AFFECT_STATE_INITIAL_LOOKBACK_HOURS)
        ).isoformat()

    def _affect_state_update_trace(
        self,
        *,
        affect_state_updates: list[dict[str, Any]],
        persist_result: dict[str, Any],
    ) -> dict[str, Any]:
        # trace向けに作成、更新、弱化を分けて見せる。
        created_ids: list[str] = []
        updated_ids: list[str] = []
        weakened_ids: list[str] = []
        summaries: list[dict[str, Any]] = []
        for record in affect_state_updates:
            affect_state_id = record.get("affect_state_id")
            if not isinstance(affect_state_id, str) or not affect_state_id:
                continue
            update_kind = record.get("update_kind")
            if update_kind == "created":
                created_ids.append(affect_state_id)
            elif update_kind == "weakened":
                weakened_ids.append(affect_state_id)
            else:
                updated_ids.append(affect_state_id)
            summaries.append(
                {
                    "affect_state_id": affect_state_id,
                    "target_scope_type": record.get("target_scope_type"),
                    "target_scope_key": record.get("target_scope_key"),
                    "affect_label": record.get("affect_label"),
                    "intensity": record.get("intensity"),
                    "confidence": record.get("confidence"),
                    "update_kind": update_kind,
                    "summary_text": record.get("summary_text"),
                }
            )

        pruned_ids = persist_result.get("pruned_affect_state_ids", []) if isinstance(persist_result, dict) else []
        if not isinstance(pruned_ids, list):
            pruned_ids = []
        return {
            "result_status": "updated" if created_ids or updated_ids or weakened_ids or pruned_ids else "no_change",
            "created_affect_state_ids": created_ids,
            "updated_affect_state_ids": updated_ids,
            "weakened_affect_state_ids": weakened_ids,
            "pruned_affect_state_ids": [
                value
                for value in pruned_ids
                if isinstance(value, str) and value
            ],
            "affect_state_summaries": summaries[:12],
        }

    def _reflective_affect_moment(self, affects: list[dict[str, Any]]) -> dict[str, Any]:
        # episode_affect 群を重み付きの一時点信号へ圧縮する。
        weighted_vad = {"v": 0.0, "a": 0.0, "d": 0.0}
        weighted_intensity = 0.0
        weighted_confidence = 0.0
        weight_total = 0.0
        support_episode_ids = self._unique_affect_texts(affects, "episode_id")
        for affect in affects:
            intensity = clamp_score(affect.get("intensity"))
            confidence = clamp_score(affect.get("confidence"))
            weight = intensity * confidence
            if weight <= 0.0:
                continue
            vad = self._clamped_vad(affect.get("vad"))
            weighted_vad["v"] += vad["v"] * weight
            weighted_vad["a"] += vad["a"] * weight
            weighted_vad["d"] += vad["d"] * weight
            weighted_intensity += intensity * weight
            weighted_confidence += confidence * weight
            weight_total += weight

        if weight_total <= 0.0:
            return {
                "weight": 0.0,
                "vad": {"v": 0.0, "a": 0.0, "d": 0.0},
                "intensity": 0.0,
                "confidence": 0.0,
            }

        return {
            "weight": weight_total,
            "vad": {
                "v": self._clamp_vad_axis(weighted_vad["v"] / weight_total),
                "a": self._clamp_vad_axis(weighted_vad["a"] / weight_total),
                "d": self._clamp_vad_axis(weighted_vad["d"] / weight_total),
            },
            "intensity": clamp_score(weighted_intensity / weight_total),
            "confidence": clamp_score((weighted_confidence / weight_total) + 0.04 * max(0, len(support_episode_ids) - 1)),
        }

    def _reflective_affect_state_summary_text(
        self,
        *,
        target_scope_type: str,
        target_scope_key: str,
        affect_label: str,
        support_episode_count: int,
    ) -> str:
        label_text = {
            "concern": "気がかり",
            "warmth": "親しみ",
            "trust": "信頼",
            "tension": "緊張",
        }.get(affect_label, affect_label)
        scope_label = self._reflective_scope_label(scope_type=target_scope_type, scope_key=target_scope_key)
        return f"{scope_label} に対する {label_text} が {support_episode_count} 件の出来事で続いている。"

    def _unique_affect_texts(self, affects: list[dict[str, Any]], key: str) -> list[str]:
        values: list[str] = []
        for affect in affects:
            value = affect.get(key)
            if isinstance(value, str) and value and value not in values:
                values.append(value)
        return values

    def _clamped_vad(self, value: Any) -> dict[str, float]:
        if not isinstance(value, dict):
            return {"v": 0.0, "a": 0.0, "d": 0.0}
        return {
            "v": self._clamp_vad_axis(value.get("v")),
            "a": self._clamp_vad_axis(value.get("a")),
            "d": self._clamp_vad_axis(value.get("d")),
        }

    def _clamp_vad_axis(self, value: Any) -> float:
        if not isinstance(value, (int, float)):
            return 0.0
        return max(-1.0, min(float(value), 1.0))
