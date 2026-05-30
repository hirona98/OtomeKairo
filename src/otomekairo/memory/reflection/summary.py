from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from otomekairo.llm.client import LLMError
from otomekairo.memory.reflection.constants import (
    ACTIVE_MEMORY_STATUSES,
    REFLECTION_CONFIRMED_SUMMARY_EPISODES,
    REFLECTION_CONFIRMED_SUMMARY_EVIDENCE,
    REFLECTION_CONFIRMED_TOPIC_DORMANT_AFTER_DAYS,
    REFLECTION_HIGH_SALIENCE_COUNT,
    REFLECTION_HIGH_SALIENCE_THRESHOLD,
    REFLECTION_MIN_SUMMARY_EPISODES,
    REFLECTION_MIN_SUMMARY_EVIDENCE,
    REFLECTION_PERSONA_PROMPT_LIMIT,
    REFLECTION_SCOPE_AFFECT_LIMIT,
    REFLECTION_SCOPE_SIGNAL_SALIENCE,
    REFLECTION_SUMMARY_PACK_EPISODE_LIMIT,
    REFLECTION_SUMMARY_PACK_MEMORY_LIMIT,
    REFLECTION_TOPIC_DORMANT_AFTER_DAYS,
    REFLECTION_TRIGGER_CYCLE_INTERVAL,
    REFLECTION_TRIGGER_HOURS,
    REFLECTIVE_SCOPE_TYPES,
)
from otomekairo.memory.utils import (
    clamp_score,
    days_since,
    display_scope_key,
    hours_since,
    local_datetime,
    optional_text,
    stable_json,
    timestamp_sort_key,
)


class MemoryReflectionSummaryMixin:
    def _empty_summary_generation(self) -> dict[str, Any]:
        return {
            "requested_scope_count": 0,
            "succeeded_scope_count": 0,
            "failed_scopes": [],
        }

    def _empty_memory_link_update(self, result_status: str = "not_started") -> dict[str, Any]:
        return {
            "result_status": result_status,
            "link_count": 0,
            "labels": {},
            "memory_link_ids": [],
        }

    def _reflection_summary_role_definition(self, *, state: dict[str, Any]) -> dict[str, Any]:
        # state snapshot から role を読む。current 設定は参照しない。
        selected_model_preset_id = state["selected_model_preset_id"]
        selected_model_preset = state["model_presets"][selected_model_preset_id]
        roles = selected_model_preset.get("roles")
        if not isinstance(roles, dict):
            raise LLMError("roles が不正なため、reflection summary role を取得できません。")
        role_definition = roles.get("memory_reflection_summary")
        if not isinstance(role_definition, dict):
            raise LLMError("選択中の model preset に reflection summary role がありません。")
        return role_definition

    def _selected_persona_definition(self, *, state: dict[str, Any]) -> dict[str, Any]:
        selected_persona_id = state.get("selected_persona_id")
        personas = state.get("personas")
        if not isinstance(selected_persona_id, str) or not selected_persona_id:
            raise ValueError("selected_persona_id snapshot が不正です。")
        if not isinstance(personas, dict):
            raise ValueError("personas snapshot が不正です。")
        persona = personas.get(selected_persona_id)
        if not isinstance(persona, dict):
            raise ValueError("選択中の persona snapshot がありません。")
        return persona

    def _reflective_trigger_reasons(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        latest_run: dict[str, Any] | None,
        episode: dict[str, Any],
        memory_actions: list[dict[str, Any]],
    ) -> list[str]:
        # 開始基準
        since_iso = latest_run["finished_at"] if isinstance(latest_run, dict) else None
        reasons: list[str] = []

        # サイクル間隔
        cycle_count = self.store.count_cycle_summaries_since(
            memory_set_id=memory_set_id,
            since_iso=since_iso,
        )
        if cycle_count >= REFLECTION_TRIGGER_CYCLE_INTERVAL:
            reasons.append("chat_turn_interval")

        # 経過時間
        if isinstance(since_iso, str) and hours_since(since_iso, finished_at) >= REFLECTION_TRIGGER_HOURS:
            reasons.append("elapsed_24h")

        # 高顕著度
        high_salience_count = self.store.count_high_salience_episodes_since(
            memory_set_id=memory_set_id,
            since_iso=since_iso,
            salience_threshold=REFLECTION_HIGH_SALIENCE_THRESHOLD,
        )
        if high_salience_count >= REFLECTION_HIGH_SALIENCE_COUNT:
            reasons.append("high_salience_cluster")

        # 補正シグナル
        if any(action["operation"] in {"supersede", "revoke", "correct"} for action in memory_actions):
            reasons.append("explicit_correction")

        # 関係シグナル
        if self._has_scope_trigger_signal(
            signal_scope_type="relationship",
            episode=episode,
            memory_actions=memory_actions,
        ):
            reasons.append("relationship_change")

        # 自己シグナル
        if self._has_scope_trigger_signal(
            signal_scope_type="self",
            episode=episode,
            memory_actions=memory_actions,
        ):
            reasons.append("self_change")

        # 結果
        deduped: list[str] = []
        for reason in reasons:
            if reason not in deduped:
                deduped.append(reason)
        return deduped

    def _build_reflective_summary_actions(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        episodes: list[dict[str, Any]],
        active_units: list[dict[str, Any]],
        embedding_definition: dict[str, Any],
        reflection_summary_role: dict[str, Any],
        scope_support_index: dict[tuple[str, str], dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # グループ化
        episode_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        memory_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        summary_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for episode in episodes:
            scope_type = episode.get("primary_scope_type")
            scope_key = episode.get("primary_scope_key")
            if scope_type not in REFLECTIVE_SCOPE_TYPES:
                continue
            if not isinstance(scope_key, str) or not scope_key:
                continue
            episode_groups[(scope_type, scope_key)].append(episode)
        for unit in active_units:
            scope_type = unit.get("scope_type")
            scope_key = unit.get("scope_key")
            if scope_type not in REFLECTIVE_SCOPE_TYPES:
                continue
            if not isinstance(scope_key, str) or not scope_key:
                continue
            if unit.get("memory_type") == "summary":
                summary_groups[(scope_type, scope_key)].append(unit)
                continue
            if unit.get("memory_type") == "commitment":
                continue
            memory_groups[(scope_type, scope_key)].append(unit)

        # スコープ走査
        actions: list[dict[str, Any]] = []
        summary_generation = self._empty_summary_generation()
        scope_keys = sorted(set(episode_groups.keys()) | set(memory_groups.keys()))
        for scope_type, scope_key in scope_keys:
            scope_episodes = episode_groups.get((scope_type, scope_key), [])
            scope_units = memory_groups.get((scope_type, scope_key), [])
            if not self._should_build_reflective_summary(
                scope_type=scope_type,
                scope_episodes=scope_episodes,
                scope_units=scope_units,
            ):
                continue

            summary_generation["requested_scope_count"] += 1
            try:
                evidence_pack = self._build_reflective_summary_evidence_pack(
                    scope_type=scope_type,
                    scope_key=scope_key,
                    scope_episodes=scope_episodes,
                    scope_units=scope_units,
                    existing_summary_units=summary_groups.get((scope_type, scope_key), []),
                    scope_support=scope_support_index.get((scope_type, scope_key)),
                )
            except Exception as exc:  # noqa: BLE001
                self._append_summary_generation_failure(
                    summary_generation=summary_generation,
                    scope_type=scope_type,
                    scope_key=scope_key,
                    failure_stage="build_evidence_pack",
                    failure_reason=str(exc),
                )
                continue

            try:
                summary_payload = self.llm.generate_memory_reflection_summary(
                    role_definition=reflection_summary_role,
                    evidence_pack=evidence_pack,
                )
            except Exception as exc:  # noqa: BLE001
                self._append_summary_generation_failure(
                    summary_generation=summary_generation,
                    scope_type=scope_type,
                    scope_key=scope_key,
                    failure_stage="generate_summary_text",
                    failure_reason=str(exc),
                )
                continue

            candidate = self._build_reflective_summary_candidate(
                scope_type=scope_type,
                scope_key=scope_key,
                summary_text=summary_payload["summary_text"],
                evidence_pack=evidence_pack,
            )
            evidence_event_ids = self._reflective_event_ids(
                scope_episodes=scope_episodes,
                scope_units=scope_units,
                limit=12,
            )
            summary_actions = self.action_resolver.resolve_memory_actions(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                event_ids=evidence_event_ids,
                cycle_ids=self._reflective_cycle_ids(scope_episodes=scope_episodes, limit=12),
                candidate=candidate,
                embedding_definition=embedding_definition,
                allow_summary=True,
            )
            self._attach_reflective_summary_related_units(
                actions=summary_actions,
                scope_units=scope_units,
            )
            actions.extend(summary_actions)
            summary_generation["succeeded_scope_count"] += 1

        # 結果
        return actions, summary_generation

    def _attach_reflective_summary_related_units(
        self,
        *,
        actions: list[dict[str, Any]],
        scope_units: list[dict[str, Any]],
    ) -> None:
        # 要約 memory_unit は根拠になった同一 scope の active units から派生する。
        related_memory_unit_ids: list[str] = []
        for unit in scope_units:
            memory_unit_id = unit.get("memory_unit_id")
            if not isinstance(memory_unit_id, str) or not memory_unit_id:
                continue
            if memory_unit_id in related_memory_unit_ids:
                continue
            related_memory_unit_ids.append(memory_unit_id)
            if len(related_memory_unit_ids) >= REFLECTION_SUMMARY_PACK_MEMORY_LIMIT:
                break

        if not related_memory_unit_ids:
            return

        for action in actions:
            memory_unit = action.get("memory_unit")
            if not isinstance(memory_unit, dict) or memory_unit.get("memory_type") != "summary":
                continue
            existing_related = [
                value
                for value in action.get("related_memory_unit_ids", [])
                if isinstance(value, str) and value
            ]
            action["related_memory_unit_ids"] = self._merge_memory_unit_ids(
                existing_related,
                related_memory_unit_ids,
            )

    def _merge_memory_unit_ids(self, existing_ids: list[str], new_ids: list[str]) -> list[str]:
        # 順序を保った重複排除
        merged: list[str] = []
        for memory_unit_id in existing_ids + new_ids:
            if memory_unit_id in merged:
                continue
            merged.append(memory_unit_id)
        return merged

    def _should_build_reflective_summary(
        self,
        *,
        scope_type: str,
        scope_episodes: list[dict[str, Any]],
        scope_units: list[dict[str, Any]],
    ) -> bool:
        # 根拠件数
        evidence_count = len(scope_episodes) + len(scope_units)
        support_cycle_count = self._reflective_support_cycle_count(
            scope_episodes=scope_episodes,
            scope_units=scope_units,
        )
        if evidence_count < REFLECTION_MIN_SUMMARY_EVIDENCE:
            return False
        if support_cycle_count < REFLECTION_MIN_SUMMARY_EPISODES:
            return False

        # トピック確認
        if scope_type == "topic":
            if len(scope_units) >= 2:
                return True
            return sum(1 for episode in scope_episodes if episode.get("open_loops")) >= 2

        # 結果
        return True

    def _build_reflective_summary_candidate(
        self,
        *,
        scope_type: str,
        scope_key: str,
        summary_text: str,
        evidence_pack: dict[str, Any],
    ) -> dict[str, Any]:
        # 根拠
        memory_types = evidence_pack["dominant_memory_types"]
        evidence_counts = evidence_pack["evidence_counts"]
        evidence_count = evidence_counts["episodes"] + evidence_counts["memory_units"]
        support_cycle_count = evidence_counts["support_cycles"]
        open_loop_count = evidence_counts["open_loops"]
        summary_status = evidence_pack["summary_status_candidate"]
        confidence_floor = 0.74 if summary_status == "confirmed" else 0.58

        # 候補
        return {
            "memory_type": "summary",
            "scope_type": scope_type,
            "scope_key": scope_key,
            "subject_ref": self._summary_subject_ref(scope_type, scope_key),
            "predicate": "long_term_pattern",
            "object_ref_or_value": f"{scope_type}:{scope_key}:summary",
            "summary_text": summary_text.strip(),
            "status": summary_status,
            "commitment_state": None,
            "confidence": min(
                0.86 if summary_status == "confirmed" else 0.72,
                confidence_floor + (0.03 * min(evidence_count, 4)) + (0.03 if open_loop_count > 0 else 0.0),
            ),
            "salience": self._reflective_summary_salience(
                scope_type=scope_type,
                evidence_count=evidence_count,
                open_loop_count=open_loop_count,
                status=summary_status,
            ),
            "valid_from": None,
            "valid_to": None,
            "qualifiers": {
                "summary_scope": scope_type,
                "source_memory_types": memory_types,
                "evidence_episode_count": evidence_counts["episodes"],
                "evidence_memory_count": evidence_counts["memory_units"],
                "support_cycle_count": support_cycle_count,
                "open_loop_count": open_loop_count,
            },
            "reason": "reflective consolidation で複数の記憶から長期傾向を要約したため。",
        }

    def _build_reflective_confirmation_actions(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        active_units: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # 選択
        actions: list[dict[str, Any]] = []
        for unit in active_units:
            if unit.get("status") != "inferred":
                continue
            if unit.get("memory_type") == "summary":
                continue

            matches = self.store.find_memory_units_for_compare(
                memory_set_id=memory_set_id,
                memory_type=unit["memory_type"],
                scope_type=unit["scope_type"],
                scope_key=unit["scope_key"],
                subject_ref=unit["subject_ref"],
                predicate=unit["predicate"],
                limit=5,
            )
            active_matches = [
                match
                for match in matches
                if match.get("status") in ACTIVE_MEMORY_STATUSES
            ]
            if self._has_conflicting_active_variants(active_matches):
                continue

            support_turn_count = self._support_turn_count(unit)
            if not (
                support_turn_count >= 3
                or (support_turn_count >= 2 and float(unit.get("confidence", 0.0)) >= 0.78 and len(active_matches) == 1)
            ):
                continue

            updated_unit = {
                **unit,
                "status": "confirmed",
                "confidence": max(clamp_score(unit["confidence"]), 0.78),
                "salience": max(clamp_score(unit["salience"]), 0.55),
                "last_confirmed_at": finished_at,
            }
            actions.append(
                self.action_resolver.build_memory_action(
                    operation="reinforce",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=updated_unit,
                    related_memory_unit_ids=[],
                    before_snapshot=unit,
                    after_snapshot=updated_unit,
                    reason="reflective consolidation で同一 memory_unit の反復根拠を確認し、inferred を confirmed へ引き上げたため。",
                    event_ids=unit.get("evidence_event_ids", []),
                )
            )

        # 結果
        return actions

    def _has_scope_trigger_signal(
        self,
        *,
        signal_scope_type: str,
        episode: dict[str, Any],
        memory_actions: list[dict[str, Any]],
    ) -> bool:
        # 要約シグナル
        if (
            episode.get("primary_scope_type") == signal_scope_type
            and float(episode.get("salience", 0.0)) >= REFLECTION_SCOPE_SIGNAL_SALIENCE
        ):
            return True

        # 記憶アクションシグナル
        return any(
            isinstance(action.get("memory_unit"), dict)
            and action["memory_unit"].get("scope_type") == signal_scope_type
            for action in memory_actions
        )

    def _build_reflective_scope_support_index(
        self,
        *,
        episodes: list[dict[str, Any]],
        active_units: list[dict[str, Any]],
        selected_persona: dict[str, Any],
        mood_state: dict[str, Any],
        affect_states: list[dict[str, Any]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        episode_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        memory_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for episode in episodes:
            scope_type = episode.get("primary_scope_type")
            scope_key = episode.get("primary_scope_key")
            if scope_type not in REFLECTIVE_SCOPE_TYPES:
                continue
            if not isinstance(scope_key, str) or not scope_key:
                continue
            episode_groups[(scope_type, scope_key)].append(episode)
        for unit in active_units:
            scope_type = unit.get("scope_type")
            scope_key = unit.get("scope_key")
            if scope_type not in REFLECTIVE_SCOPE_TYPES:
                continue
            if not isinstance(scope_key, str) or not scope_key:
                continue
            if unit.get("memory_type") in {"summary", "commitment"}:
                continue
            memory_groups[(scope_type, scope_key)].append(unit)

        scope_support_index: dict[tuple[str, str], dict[str, Any]] = {}
        for scope_type, scope_key in sorted(set(episode_groups.keys()) | set(memory_groups.keys())):
            scope_support_index[(scope_type, scope_key)] = self._build_reflective_scope_support(
                scope_type=scope_type,
                scope_key=scope_key,
                scope_episodes=episode_groups.get((scope_type, scope_key), []),
                scope_units=memory_groups.get((scope_type, scope_key), []),
                selected_persona=selected_persona,
                mood_state=mood_state,
                affect_states=affect_states,
            )
        return scope_support_index

    def _build_reflective_scope_support(
        self,
        *,
        scope_type: str,
        scope_key: str,
        scope_episodes: list[dict[str, Any]],
        scope_units: list[dict[str, Any]],
        selected_persona: dict[str, Any],
        mood_state: dict[str, Any],
        affect_states: list[dict[str, Any]],
    ) -> dict[str, Any]:
        support_kinds: list[str] = []
        if scope_episodes:
            support_kinds.append("episodes")
        if scope_units:
            support_kinds.append("memory_units")

        persona_context = None
        if scope_type in {"self", "relationship"}:
            persona_context = self._reflective_persona_context(selected_persona)
            if persona_context is not None:
                support_kinds.append("persona")

        mood_context = None
        if scope_type == "self":
            mood_context = self._reflective_mood_context(mood_state)
            if mood_context is not None:
                support_kinds.append("mood_state")

        affect_context: list[dict[str, Any]] = []
        if scope_type in {"relationship", "user"}:
            affect_context = self._reflective_affect_context(
                scope_type=scope_type,
                scope_key=scope_key,
                affect_states=affect_states,
            )
            if affect_context:
                support_kinds.append("affect_state")

        return {
            "scope_type": scope_type,
            "scope_key": scope_key,
            "scope_label": self._reflective_scope_label(scope_type=scope_type, scope_key=scope_key),
            "support_kinds": support_kinds,
            "persona": persona_context,
            "mood_state": mood_context,
            "affect_state": affect_context,
        }

    def _build_reflective_dormant_actions(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        episodes: list[dict[str, Any]],
        active_units: list[dict[str, Any]],
        excluded_memory_unit_ids: set[str],
    ) -> list[dict[str, Any]]:
        # 最近のトピックスコープ群
        recent_topic_scopes = {
            (episode.get("primary_scope_type"), episode.get("primary_scope_key"))
            for episode in episodes
            if episode.get("primary_scope_type") == "topic" and isinstance(episode.get("primary_scope_key"), str)
        }

        # 順序付きunit群
        ordered_units = sorted(
            active_units,
            key=lambda unit: (
                timestamp_sort_key(unit.get("last_confirmed_at") or unit.get("formed_at")),
                float(unit.get("salience", 0.0)),
            ),
        )

        # 選択
        actions: list[dict[str, Any]] = []
        for unit in ordered_units:
            if unit["memory_unit_id"] in excluded_memory_unit_ids:
                continue
            if unit.get("scope_type") != "topic":
                continue
            if unit.get("memory_type") == "commitment":
                continue
            if (unit.get("scope_type"), unit.get("scope_key")) in recent_topic_scopes:
                continue

            dormant_after_days = (
                REFLECTION_CONFIRMED_TOPIC_DORMANT_AFTER_DAYS
                if unit.get("status") == "confirmed"
                else REFLECTION_TOPIC_DORMANT_AFTER_DAYS
            )
            salience_threshold = 0.25 if unit.get("status") == "confirmed" else 0.4
            if float(unit.get("salience", 0.0)) > salience_threshold:
                continue
            if days_since(unit.get("last_confirmed_at") or unit.get("formed_at"), finished_at) < dormant_after_days:
                continue

            updated_unit = {
                **unit,
                "status": "dormant",
                "salience": min(clamp_score(unit["salience"]), 0.15),
            }
            actions.append(
                self.action_resolver.build_memory_action(
                    operation="dormant",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=updated_unit,
                    related_memory_unit_ids=[],
                    before_snapshot=unit,
                    after_snapshot=updated_unit,
                    reason="reflective consolidation で低重要かつ長期間未再確認の topic を dormant 化したため。",
                    event_ids=unit.get("evidence_event_ids", []),
                )
            )

        # 結果
        return actions

    def _summary_subject_ref(self, scope_type: str, scope_key: str) -> str:
        # 関係
        if scope_type == "relationship":
            return scope_key.split("|", 1)[0]

        # 結果
        return scope_key

    def _dominant_memory_types(self, scope_units: list[dict[str, Any]]) -> list[str]:
        # 件数
        counts = Counter(
            unit["memory_type"]
            for unit in scope_units
            if isinstance(unit.get("memory_type"), str)
        )

        # 結果
        return [memory_type for memory_type, _ in counts.most_common(2)]

    def _has_conflicting_active_variants(self, matches: list[dict[str, Any]]) -> bool:
        # バリアント署名群
        variant_signatures = {
            (
                match.get("object_ref_or_value"),
                stable_json(match.get("qualifiers", {})),
            )
            for match in matches
        }

        # 結果
        return len(variant_signatures) > 1

    def _reflective_summary_status(
        self,
        *,
        scope_type: str,
        evidence_count: int,
        support_cycle_count: int,
        open_loop_count: int,
    ) -> str:
        # トピック
        if scope_type == "topic":
            if support_cycle_count >= REFLECTION_CONFIRMED_SUMMARY_EPISODES and open_loop_count >= 2:
                return "confirmed"
            return "inferred"

        # 確認済み
        if (
            evidence_count >= REFLECTION_CONFIRMED_SUMMARY_EVIDENCE
            and support_cycle_count >= REFLECTION_CONFIRMED_SUMMARY_EPISODES
        ):
            return "confirmed"

        # 結果
        return "inferred"

    def _build_reflective_summary_evidence_pack(
        self,
        *,
        scope_type: str,
        scope_key: str,
        scope_units: list[dict[str, Any]],
        scope_episodes: list[dict[str, Any]],
        existing_summary_units: list[dict[str, Any]],
        scope_support: dict[str, Any] | None,
    ) -> dict[str, Any]:
        # counts
        memory_types = self._dominant_memory_types(scope_units)
        evidence_count = len(scope_episodes) + len(scope_units)
        support_cycle_count = self._reflective_support_cycle_count(
            scope_episodes=scope_episodes,
            scope_units=scope_units,
        )
        open_loop_count = sum(1 for episode in scope_episodes if episode.get("open_loops"))
        summary_status = self._reflective_summary_status(
            scope_type=scope_type,
            evidence_count=evidence_count,
            support_cycle_count=support_cycle_count,
            open_loop_count=open_loop_count,
        )

        payload = {
            "scope_type": scope_type,
            "scope_key": scope_key,
            "scope_label": self._reflective_scope_label(scope_type=scope_type, scope_key=scope_key),
            "summary_status_candidate": summary_status,
            "dominant_memory_types": memory_types,
            "evidence_counts": {
                "episodes": len(scope_episodes),
                "memory_units": len(scope_units),
                "support_cycles": support_cycle_count,
                "open_loops": open_loop_count,
            },
            "existing_summary_text": self._existing_summary_text(existing_summary_units),
            "episodes": [
                self._summary_pack_episode_item(item)
                for item in scope_episodes[:REFLECTION_SUMMARY_PACK_EPISODE_LIMIT]
            ],
            "memory_units": [
                self._summary_pack_memory_item(item)
                for item in self._summary_pack_memory_units(scope_units)
            ],
        }
        support = scope_support or {}
        support_kinds = support.get("support_kinds", [])
        if isinstance(support_kinds, list):
            payload["support_kinds"] = [
                value
                for value in support_kinds
                if isinstance(value, str) and value
            ]
        persona_context = support.get("persona")
        if isinstance(persona_context, dict) and persona_context:
            payload["persona"] = persona_context
        mood_context = support.get("mood_state")
        if isinstance(mood_context, dict) and mood_context:
            payload["mood_state"] = mood_context
        affect_context = support.get("affect_state")
        if isinstance(affect_context, list) and affect_context:
            payload["affect_state"] = affect_context
        return payload

    def _existing_summary_text(self, existing_summary_units: list[dict[str, Any]]) -> str | None:
        # 既存 summary の先頭だけを使う。
        for unit in existing_summary_units:
            summary_text = unit.get("summary_text")
            if isinstance(summary_text, str) and summary_text.strip():
                return summary_text.strip()
        return None

    def _summary_pack_memory_units(self, scope_units: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # salience / confidence 優先で上位を使う。
        ordered_units = sorted(
            scope_units,
            key=lambda unit: (
                -clamp_score(unit.get("salience")),
                -clamp_score(unit.get("confidence")),
                -self._safe_timestamp(unit.get("last_confirmed_at") or unit.get("formed_at")),
            ),
        )
        return ordered_units[:REFLECTION_SUMMARY_PACK_MEMORY_LIMIT]

    def _safe_timestamp(self, value: Any) -> float:
        timestamp = timestamp_sort_key(value)
        if timestamp == float("inf"):
            return 0.0
        return timestamp

    def _summary_pack_episode_item(self, episode: dict[str, Any]) -> dict[str, Any]:
        return {
            "formed_time_label": self._reflective_time_label(episode.get("formed_at")),
            "summary_text": episode.get("summary_text"),
            "outcome_text": episode.get("outcome_text"),
            "open_loops": episode.get("open_loops", []),
            "salience": clamp_score(episode.get("salience")),
        }

    def _summary_pack_memory_item(self, unit: dict[str, Any]) -> dict[str, Any]:
        return {
            "memory_type": unit.get("memory_type"),
            "predicate": unit.get("predicate"),
            "object_ref_or_value": unit.get("object_ref_or_value"),
            "summary_text": unit.get("summary_text"),
            "status": unit.get("status"),
            "confidence": clamp_score(unit.get("confidence")),
            "salience": clamp_score(unit.get("salience")),
        }

    def _summary_update_index(self, summary_actions: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        updates: dict[tuple[str, str], dict[str, Any]] = {}
        for action in summary_actions:
            if not isinstance(action, dict):
                continue
            memory_unit = action.get("after_snapshot")
            if not isinstance(memory_unit, dict):
                memory_unit = action.get("memory_unit")
            if not isinstance(memory_unit, dict):
                continue
            if memory_unit.get("memory_type") != "summary":
                continue
            scope_type = memory_unit.get("scope_type")
            scope_key = memory_unit.get("scope_key")
            if not isinstance(scope_type, str) or not scope_type:
                continue
            if not isinstance(scope_key, str) or not scope_key:
                continue
            update = updates.setdefault(
                (scope_type, scope_key),
                {
                    "summary_updated": True,
                    "operations": [],
                },
            )
            operation = action.get("operation")
            if isinstance(operation, str) and operation and operation not in update["operations"]:
                update["operations"].append(operation)
        return updates

    def _reflective_scope_label(self, *, scope_type: str, scope_key: str) -> str:
        if scope_type == "self":
            return "自分自身"
        if scope_type == "user":
            return "ユーザー"
        if scope_type == "topic":
            return display_scope_key(scope_key)
        if scope_type == "relationship":
            if scope_key == "self|user":
                return "あなたとの関係"
            return f"{scope_key} の関係文脈"
        return display_scope_key(scope_key)

    def _reflective_persona_context(self, persona: dict[str, Any]) -> dict[str, Any] | None:
        payload: dict[str, Any] = {}
        display_name = optional_text(persona.get("display_name"))
        if display_name is not None:
            payload["display_name"] = display_name
        initiative_baseline = optional_text(persona.get("initiative_baseline"))
        if initiative_baseline is not None:
            payload["initiative_baseline"] = initiative_baseline
        persona_prompt = optional_text(persona.get("persona_prompt"))
        if persona_prompt is not None:
            prompt_excerpt = " ".join(persona_prompt.split())
            payload["persona_prompt_excerpt"] = prompt_excerpt[:REFLECTION_PERSONA_PROMPT_LIMIT]
        return payload or None

    def _reflective_mood_context(self, mood_state: dict[str, Any]) -> dict[str, Any] | None:
        current_vad = mood_state.get("current_vad")
        if not isinstance(current_vad, dict):
            return None
        vad = {
            "v": round(float(current_vad.get("v", 0.0) or 0.0), 2),
            "a": round(float(current_vad.get("a", 0.0) or 0.0), 2),
            "d": round(float(current_vad.get("d", 0.0) or 0.0), 2),
        }
        signal = max(abs(vad["v"]), abs(vad["a"]), abs(vad["d"]))
        confidence = clamp_score(mood_state.get("confidence"))
        if signal < 0.12 and confidence <= 0.0:
            return None
        return {
            "summary_text": self._reflective_mood_summary_text(vad=vad),
            "current_vad": vad,
            "confidence": confidence,
        }

    def _reflective_mood_summary_text(self, *, vad: dict[str, float]) -> str:
        valence = vad["v"]
        arousal = vad["a"]
        dominance = vad["d"]
        if valence <= -0.25 and arousal >= 0.25:
            return "緊張や負荷に気を配りながら応答を整えたい状態が残っている。"
        if valence <= -0.2:
            return "慎重さや張りを抱えながら応答を整えている。"
        if valence >= 0.25 and dominance >= 0.1:
            return "落ち着いて前向きに応じやすい状態が続いている。"
        if arousal <= -0.2 and dominance <= -0.15:
            return "力を抜いて静かに整えたい状態が続いている。"
        return "感情の振れを見ながら応答を整えている。"

    def _reflective_affect_context(
        self,
        *,
        scope_type: str,
        scope_key: str,
        affect_states: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for record in affect_states:
            if not isinstance(record, dict):
                continue
            if record.get("target_scope_type") != scope_type:
                continue
            if record.get("target_scope_key") != scope_key:
                continue
            affect_label = optional_text(record.get("affect_label"))
            if affect_label is None:
                continue
            item: dict[str, Any] = {
                "affect_label": affect_label,
                "intensity": clamp_score(record.get("intensity")),
                "confidence": clamp_score(record.get("confidence")),
            }
            summary_text = optional_text(record.get("summary_text"))
            if summary_text is not None:
                item["summary_text"] = summary_text
            items.append(item)
            if len(items) >= REFLECTION_SCOPE_AFFECT_LIMIT:
                break
        return items

    def _reflective_time_label(self, value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        local_time = local_datetime(value)
        return f"{local_time.year}年{local_time.month}月{local_time.day}日 {local_time.hour}時{local_time.minute:02d}分"

    def _append_summary_generation_failure(
        self,
        *,
        summary_generation: dict[str, Any],
        scope_type: str,
        scope_key: str,
        failure_stage: str,
        failure_reason: str,
    ) -> None:
        failed_scopes = summary_generation["failed_scopes"]
        failed_scopes.append(
            {
                "scope_type": scope_type,
                "scope_key": scope_key,
                "failure_stage": failure_stage,
                "failure_reason": failure_reason,
            }
        )

    def _reflective_summary_salience(
        self,
        *,
        scope_type: str,
        evidence_count: int,
        open_loop_count: int,
        status: str,
    ) -> float:
        # 基底
        base = {
            "self": 0.46,
            "user": 0.5,
            "relationship": 0.56,
            "topic": 0.42,
        }.get(scope_type, 0.44)

        # 結果
        return min(
            0.78 if status == "confirmed" else 0.62,
            base
            + (0.03 * min(evidence_count, 4))
            + (0.03 if open_loop_count > 0 else 0.0)
            - (0.08 if status != "confirmed" else 0.0),
        )

    def _reflective_event_ids(
        self,
        *,
        scope_episodes: list[dict[str, Any]],
        scope_units: list[dict[str, Any]],
        limit: int,
    ) -> list[str]:
        # シード
        merged: list[str] = []
        for episode in scope_episodes:
            linked_event_ids = episode.get("linked_event_ids", [])
            for event_id in linked_event_ids:
                if not isinstance(event_id, str) or event_id in merged:
                    continue
                merged.append(event_id)
                if len(merged) >= limit:
                    return merged[:limit]
        for unit in scope_units:
            evidence_event_ids = unit.get("evidence_event_ids", [])
            for event_id in evidence_event_ids:
                if not isinstance(event_id, str) or event_id in merged:
                    continue
                merged.append(event_id)
                if len(merged) >= limit:
                    return merged[:limit]

        # 結果
        return merged[:limit]

    def _reflective_cycle_ids(
        self,
        *,
        scope_episodes: list[dict[str, Any]],
        limit: int,
    ) -> list[str]:
        # 収集
        cycle_ids: list[str] = []
        for episode in scope_episodes:
            cycle_id = episode.get("cycle_id")
            if not isinstance(cycle_id, str) or cycle_id in cycle_ids:
                continue
            cycle_ids.append(cycle_id)
            if len(cycle_ids) >= limit:
                break

        # 結果
        return cycle_ids

    def _reflective_support_cycle_count(
        self,
        *,
        scope_episodes: list[dict[str, Any]],
        scope_units: list[dict[str, Any]],
    ) -> int:
        # 収集
        cycle_ids: list[str] = self._reflective_cycle_ids(
            scope_episodes=scope_episodes,
            limit=24,
        )
        for unit in scope_units:
            for cycle_id in unit.get("evidence_cycle_ids", []):
                if not isinstance(cycle_id, str) or cycle_id in cycle_ids:
                    continue
                cycle_ids.append(cycle_id)

        # 結果
        return len(cycle_ids)

    def _support_turn_count(self, unit: dict[str, Any]) -> int:
        # サイクル補助
        cycle_ids = [
            cycle_id
            for cycle_id in unit.get("evidence_cycle_ids", [])
            if isinstance(cycle_id, str)
        ]
        if cycle_ids:
            return len(cycle_ids)

        # イベント代替
        if unit.get("evidence_event_ids"):
            return 1
        return 0
