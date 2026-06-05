from __future__ import annotations

from typing import Any

from otomekairo.service.input.constants import (
    INITIATIVE_AUTONOMOUS_PROBE_THRESHOLD,
    INITIATIVE_DRIVE_FRESHNESS_ADJUSTMENTS,
    INITIATIVE_DRIVE_KIND_SCORES,
)


class ServiceInputInitiativeScoringMixin:
    def _initiative_drive_summaries(
        self,
        drive_state_summary: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for drive_state in drive_state_summary or []:
            if not isinstance(drive_state, dict):
                continue
            item: dict[str, Any] = {
                "drive_id": drive_state.get("drive_id"),
                "summary_text": drive_state.get("summary_text"),
                "salience": drive_state.get("salience"),
            }
            for key in ("drive_kind", "focus_scope_type", "focus_scope_key", "freshness_hint", "source_updated_at", "stability_hint"):
                value = drive_state.get(key)
                if isinstance(value, str) and value.strip():
                    item[key] = value.strip()
            support_count = drive_state.get("support_count")
            if isinstance(support_count, int) and support_count > 0:
                item["support_count"] = support_count
            for key in ("support_strength", "scope_alignment", "signal_strength", "persona_alignment"):
                value = drive_state.get(key)
                if isinstance(value, (int, float)):
                    item[key] = round(max(0.0, min(float(value), 1.0)), 2)
            supporting_memory_types = drive_state.get("supporting_memory_types")
            if isinstance(supporting_memory_types, list):
                item["supporting_memory_types"] = [
                    value.strip()
                    for value in supporting_memory_types
                    if isinstance(value, str) and value.strip()
                ][:4]
            scope_support_kinds = drive_state.get("scope_support_kinds")
            if isinstance(scope_support_kinds, list):
                item["scope_support_kinds"] = [
                    value.strip()
                    for value in scope_support_kinds
                    if isinstance(value, str) and value.strip()
                ][:5]
            summaries.append(item)
        return summaries

    def _initiative_drive_priority_score(self, drive_summary: dict[str, Any]) -> float:
        drive_kind = self._client_context_text(drive_summary.get("drive_kind"), limit=48)
        salience = drive_summary.get("salience")
        support_count = drive_summary.get("support_count")
        support_strength = drive_summary.get("support_strength")
        scope_alignment = drive_summary.get("scope_alignment")
        freshness_hint = self._client_context_text(drive_summary.get("freshness_hint"), limit=16)
        signal_strength = drive_summary.get("signal_strength")
        persona_alignment = drive_summary.get("persona_alignment")
        stability_hint = self._client_context_text(drive_summary.get("stability_hint"), limit=16)
        score = INITIATIVE_DRIVE_KIND_SCORES.get(drive_kind or "", 0.08)
        if isinstance(salience, (int, float)):
            score += max(0.0, min(float(salience), 1.0)) * 0.18
        if isinstance(support_count, int) and support_count > 1:
            score += min(0.04, 0.01 * (support_count - 1))
        if isinstance(support_strength, (int, float)):
            score += min(0.08, max(0.0, min(float(support_strength), 1.0)) * 0.08)
        if isinstance(scope_alignment, (int, float)):
            score += max(0.0, min(float(scope_alignment), 1.0) - 0.5) * 0.08
        if freshness_hint is not None:
            score += INITIATIVE_DRIVE_FRESHNESS_ADJUSTMENTS.get(freshness_hint, 0.0)
        if isinstance(signal_strength, (int, float)):
            score += min(0.08, max(0.0, min(float(signal_strength), 1.0)) * 0.08)
        if isinstance(persona_alignment, (int, float)):
            score += (max(0.0, min(float(persona_alignment), 1.0)) - 0.5) * 0.06
        if stability_hint == "stable":
            score += 0.04
        elif stability_hint == "mixed":
            score -= 0.03
        elif stability_hint == "weak":
            score -= 0.07
        return max(0.0, score)

    def _initiative_strongest_drive_summary(
        self,
        drive_summaries: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        strongest: dict[str, Any] | None = None
        strongest_score = -1.0
        for drive_summary in drive_summaries:
            if not isinstance(drive_summary, dict):
                continue
            score = self._initiative_drive_priority_score(drive_summary)
            if score <= strongest_score:
                continue
            strongest = drive_summary
            strongest_score = score
        return strongest

    def _initiative_drive_signal_score(
        self,
        drive_summaries: list[dict[str, Any]],
    ) -> float:
        score = 0.0
        for drive_summary in drive_summaries[:3]:
            if not isinstance(drive_summary, dict):
                continue
            score += min(0.18, self._initiative_drive_priority_score(drive_summary))
        return min(score, 0.34)

    def _initiative_drive_world_alignment_bonus(
        self,
        *,
        strongest_drive: dict[str, Any] | None,
        world_state_summary: list[dict[str, Any]],
    ) -> float:
        if not isinstance(strongest_drive, dict) or not world_state_summary:
            return 0.0
        drive_kind = self._client_context_text(strongest_drive.get("drive_kind"), limit=48)
        state_types = {
            item.get("state_type")
            for item in world_state_summary
            if isinstance(item, dict) and isinstance(item.get("state_type"), str)
        }
        if drive_kind == "follow_through" and "schedule" in state_types:
            return 0.06
        if drive_kind in {"relationship_attunement", "user_attention"} and state_types.intersection(
            {"social_context", "visual_context", "external_service"}
        ):
            return 0.05
        if drive_kind == "self_regulation" and "body" in state_types:
            return 0.05
        if drive_kind == "topic_continuation" and state_types.intersection({"visual_context", "external_service"}):
            return 0.04
        return 0.0

    def _initiative_world_state_is_weak_foreground(self, world_state_summary: list[dict[str, Any]]) -> bool:
        state_types = {
            item.get("state_type")
            for item in world_state_summary
            if isinstance(item, dict) and isinstance(item.get("state_type"), str)
        }
        return bool(state_types) and state_types.issubset({"visual_context", "external_service", "device"})

    def _initiative_autonomous_probe_preference(
        self,
        *,
        trigger_kind: str,
        drive_summaries: list[dict[str, Any]],
        world_state_summary: list[dict[str, Any]],
        status_refresh_world_state_summary: list[dict[str, Any]],
        foreground_signal_summary: dict[str, Any],
        initiative_baseline: dict[str, Any],
        capability_summary: dict[str, Any],
    ) -> dict[str, Any] | None:
        if trigger_kind not in {"wake", "background_wake"}:
            return None
        strongest_drive = self._initiative_strongest_drive_summary(drive_summaries)
        if not isinstance(strongest_drive, dict):
            return None
        status_preference = self._initiative_autonomous_status_refresh_preference(
            strongest_drive=strongest_drive,
            world_state_summary=status_refresh_world_state_summary,
            capability_summary=capability_summary,
        )
        if isinstance(status_preference, dict):
            return status_preference
        status_target = self._initiative_status_refresh_target(strongest_drive)
        if isinstance(status_target, dict) and self._initiative_status_refresh_target_has_fresh_world_state(
            target=status_target,
            world_state_summary=status_refresh_world_state_summary,
        ):
            return None
        if self._initiative_foreground_thinness(foreground_signal_summary) != "thin":
            return None
        available_ids = capability_summary.get("available_ids", [])
        if not isinstance(available_ids, list) or "vision.capture" not in available_ids:
            return None
        vision_source_id = self._initiative_default_vision_source_id(capability_summary)
        if vision_source_id is None:
            return None
        if self._initiative_vision_source_has_fresh_world_state(
            vision_source_id=vision_source_id,
            world_state_summary=status_refresh_world_state_summary,
        ):
            return None
        if self._initiative_drive_priority_score(strongest_drive) < INITIATIVE_AUTONOMOUS_PROBE_THRESHOLD:
            return None
        level = self._client_context_text(initiative_baseline.get("level"), limit=16) or "medium"
        if trigger_kind == "background_wake" and level == "low":
            return None
        state_types = {
            item.get("state_type")
            for item in world_state_summary
            if isinstance(item, dict) and isinstance(item.get("state_type"), str)
        }
        if state_types.intersection({"body", "schedule", "social_context"}):
            return None
        drive_kind = self._client_context_text(strongest_drive.get("drive_kind"), limit=48)
        if drive_kind not in {"follow_through", "relationship_attunement", "user_attention", "topic_continuation"}:
            return None
        return {
            "capability_id": "vision.capture",
            "input": {
                "vision_source_id": vision_source_id,
                "mode": "still",
            },
            "reason_summary": "強い drive はあるが現在の前景観測が薄いため、先に画面観測を当てたい。",
        }

    def _initiative_vision_source_has_fresh_world_state(
        self,
        *,
        vision_source_id: str,
        world_state_summary: list[dict[str, Any]],
    ) -> bool:
        source_key = self._world_state_vision_source_key({"vision_source_id": vision_source_id})
        if source_key is None:
            return False
        target_integration_key = f"visual_context:{source_key}"
        return any(
            isinstance(item, dict)
            and item.get("state_type") == "visual_context"
            and item.get("integration_key") == target_integration_key
            and self._foreground_world_state_is_fresh(item)
            for item in world_state_summary
        )

    def _initiative_autonomous_status_refresh_preference(
        self,
        *,
        strongest_drive: dict[str, Any],
        world_state_summary: list[dict[str, Any]],
        capability_summary: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self._initiative_drive_priority_score(strongest_drive) < INITIATIVE_AUTONOMOUS_PROBE_THRESHOLD:
            return None
        target = self._initiative_status_refresh_target(strongest_drive)
        if target is None:
            return None
        available_ids = capability_summary.get("available_ids", [])
        capability_id = target["capability_id"]
        if not isinstance(available_ids, list) or capability_id not in available_ids:
            return None
        state_type = target["state_type"]
        matching_states = [
            item
            for item in world_state_summary
            if isinstance(item, dict) and item.get("state_type") == state_type
        ]
        if self._initiative_status_refresh_target_has_fresh_world_state(
            target=target,
            world_state_summary=world_state_summary,
        ):
            return None
        if matching_states:
            reason_summary = f"{target['label']}の前景 world_state はあるが新鮮ではないため、現在状態を確認する。"
        else:
            reason_summary = f"{target['label']}の前景 world_state が不足しているため、現在状態を確認する。"
        return {
            "capability_id": capability_id,
            "input": target["input"],
            "reason_summary": reason_summary,
        }

    def _initiative_status_refresh_target_has_fresh_world_state(
        self,
        *,
        target: dict[str, Any],
        world_state_summary: list[dict[str, Any]],
    ) -> bool:
        state_type = self._client_context_text(target.get("state_type"), limit=48)
        if state_type is None:
            return False
        return any(
            isinstance(item, dict)
            and item.get("state_type") == state_type
            and self._foreground_world_state_is_fresh(item)
            for item in world_state_summary
        )

    def _initiative_status_refresh_target(self, strongest_drive: dict[str, Any]) -> dict[str, Any] | None:
        drive_kind = self._client_context_text(strongest_drive.get("drive_kind"), limit=48)
        summary_text = self._client_context_text(strongest_drive.get("summary_text"), limit=240) or ""
        if drive_kind == "relationship_attunement":
            return {
                "capability_id": "social.status",
                "state_type": "social_context",
                "input": {"scope": "social_context"},
                "label": "対人文脈",
            }
        if drive_kind in {"topic_continuation", "follow_through", "user_attention"} and self._contains_any_text(
            summary_text,
            ("外部サービス", "GitHub", "github", "レビュー", "issue", "Issue", "PR", "pull request"),
        ):
            return {
                "capability_id": "external.status",
                "state_type": "external_service",
                "input": {"service": "github"},
                "label": "外部サービス",
            }
        if drive_kind in {"user_attention", "topic_continuation"} and self._contains_any_text(
            summary_text,
            ("端末", "デバイス", "接続", "電源", "バッテリー"),
        ):
            return {
                "capability_id": "device.status",
                "state_type": "device",
                "input": {"scope": "device"},
                "label": "端末状態",
            }
        if drive_kind in {"self_regulation", "user_attention", "topic_continuation"} and self._contains_any_text(
            summary_text,
            ("作業環境", "周囲", "部屋", "騒音", "明るさ", "環境"),
        ):
            return {
                "capability_id": "environment.status",
                "state_type": "environment",
                "input": {"scope": "environment"},
                "label": "周囲環境",
            }
        if drive_kind in {"follow_through", "user_attention", "topic_continuation"} and self._contains_any_text(
            summary_text,
            ("場所", "居場所", "移動", "作業場所", "出先"),
        ):
            return {
                "capability_id": "location.status",
                "state_type": "location",
                "input": {"scope": "location"},
                "label": "場所状態",
            }
        if drive_kind == "user_attention" and self._contains_any_text(
            summary_text,
            ("対人", "会話", "連絡", "通知", "会議", "やり取り"),
        ):
            return {
                "capability_id": "social.status",
                "state_type": "social_context",
                "input": {"scope": "social_context"},
                "label": "対人文脈",
            }
        if drive_kind == "follow_through" and self._contains_any_text(
            summary_text,
            ("予定", "スケジュール", "カレンダー", "このあと", "近日"),
        ):
            return {
                "capability_id": "schedule.status",
                "state_type": "schedule",
                "input": {"range": "near_term"},
                "label": "予定",
            }
        if drive_kind == "self_regulation":
            return {
                "capability_id": "body.status",
                "state_type": "body",
                "input": {"scope": "body"},
                "label": "身体状態",
            }
        return None

    def _initiative_world_state_signal_score(
        self,
        world_state_summary: list[dict[str, Any]],
    ) -> float:
        weights = {
            "schedule": 0.12,
            "social_context": 0.1,
            "body": 0.08,
            "external_service": 0.08,
            "visual_context": 0.06,
            "device": 0.05,
            "environment": 0.05,
            "location": 0.05,
        }
        score = 0.0
        for item in world_state_summary[:3]:
            if not isinstance(item, dict):
                continue
            state_type = item.get("state_type")
            if not isinstance(state_type, str):
                continue
            weight = weights.get(state_type, 0.04)
            salience = item.get("salience")
            if isinstance(salience, (int, float)):
                weight *= 0.7 + min(max(float(salience), 0.0), 1.0) * 0.5
            score += weight
        return min(score, 0.24)

    def _initiative_autonomous_blocking_reason(
        self,
        *,
        trigger_kind: str,
        drive_summaries: list[dict[str, Any]],
        strongest_drive: dict[str, Any] | None,
        world_state_summary: list[dict[str, Any]],
        foreground_signal_summary: dict[str, Any],
        suppression_summary: dict[str, Any],
        initiative_baseline: dict[str, Any],
        capability_summary: dict[str, Any],
    ) -> str | None:
        visual_signal = self._initiative_primary_visual_observation_signal(foreground_signal_summary)
        if isinstance(visual_signal, dict) and self._visual_observation_signal_is_judgable(visual_signal):
            return None
        reasons: list[str] = []
        level = self._client_context_text(initiative_baseline.get("level"), limit=16)
        if level == "low":
            reasons.append("initiative_baseline が low")
        if trigger_kind == "background_wake":
            reasons.append("定期起床")
        foreground_thinness = self._initiative_foreground_thinness(foreground_signal_summary)
        if foreground_thinness == "thin":
            reasons.append("前景文脈が thin")
        suppression_level = self._initiative_suppression_level(suppression_summary)
        if suppression_level == "high":
            reasons.append("suppression が high")
        elif suppression_level == "medium":
            reasons.append("抑制要因が残る")
        if not drive_summaries and world_state_summary:
            state_types = {
                item.get("state_type")
                for item in world_state_summary
                if isinstance(item, dict) and isinstance(item.get("state_type"), str)
            }
            if state_types and state_types.issubset({"visual_context", "external_service", "device"}):
                reasons.append("前景が視覚や外部状態中心")
        if int(capability_summary.get("available_count", 0)) == 0:
            reasons.append("使える capability が見当たらない")
        freshness_hint = self._client_context_text(
            strongest_drive.get("freshness_hint") if isinstance(strongest_drive, dict) else None,
            limit=16,
        )
        if freshness_hint == "stale":
            reasons.append("前景に出る drive が stale")
        if not reasons:
            return None
        return " / ".join(reasons) + " ため、押し出しは慎重にする。"
