from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from otomekairo.llm.contexts import build_persona_context
from otomekairo.llm.contracts import validate_memory_reflection_summary_item
from otomekairo.memory.reflection.constants import (
    ACTIVE_MEMORY_STATUSES,
    REFLECTION_CONFIRMED_SUMMARY_EPISODES,
    REFLECTION_CONFIRMED_SUMMARY_EVIDENCE,
    REFLECTION_CONFIRMED_TOPIC_DORMANT_AFTER_DAYS,
    REFLECTION_HIGH_SALIENCE_COUNT,
    REFLECTION_HIGH_SALIENCE_THRESHOLD,
    REFLECTION_MIN_SUMMARY_EPISODES,
    REFLECTION_MIN_SUMMARY_EVIDENCE,
    REFLECTION_SCOPE_AFFECT_LIMIT,
    REFLECTION_SCOPE_SIGNAL_SALIENCE,
    REFLECTION_SUMMARY_BATCH_LIMIT,
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
            "dirty_scope_count": 0,
            "dirty_reasons": [],
            "llm_call_count": 0,
        }

    def _empty_memory_link_update(self, result_status: str = "not_started") -> dict[str, Any]:
        return {
            "result_status": result_status,
            "link_count": 0,
            "labels": {},
            "memory_link_ids": [],
        }

    def _reflection_summary_model_config(self, *, state: dict[str, Any]) -> dict[str, Any]:
        # 会話時点の state snapshot から生成モデル設定を読む。
        selected_model_preset_id = state["selected_model_preset_id"]
        selected_model_preset = state["model_presets"][selected_model_preset_id]
        if not isinstance(selected_model_preset, dict):
            raise LLMError("選択中の model preset snapshot が不正です。")
        return selected_model_preset

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
        reflection_summary_model_config: dict[str, Any],
        selected_persona: dict[str, Any],
        scope_support_index: dict[tuple[str, str], dict[str, Any]],
        memory_actions: list[dict[str, Any]] | None = None,
        affect_state_updates: list[dict[str, Any]] | None = None,
        previous_failed_scopes: list[dict[str, Any]] | None = None,
        trigger_reasons: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        persona_context = build_persona_context(
            selected_persona,
            role="memory_reflection_summary",
        )

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

        dirty_index = self._reflective_dirty_scope_index(
            episode_groups=episode_groups,
            memory_groups=memory_groups,
            summary_groups=summary_groups,
            memory_actions=memory_actions or [],
            affect_state_updates=affect_state_updates or [],
            previous_failed_scopes=previous_failed_scopes or [],
            trigger_reasons=trigger_reasons or [],
        )
        summary_generation = self._empty_summary_generation()
        summary_generation["dirty_scope_count"] = len(dirty_index)
        summary_generation["dirty_reasons"] = [
            {
                "scope_type": scope_type,
                "scope_key": scope_key,
                "reasons": reasons,
            }
            for (scope_type, scope_key), reasons in sorted(dirty_index.items())
        ]

        prepared_scopes: list[dict[str, Any]] = []
        for scope_type, scope_key in sorted(dirty_index):
            scope_episodes = episode_groups.get((scope_type, scope_key), [])
            scope_units = memory_groups.get((scope_type, scope_key), [])
            if not self._should_build_reflective_summary(
                scope_type=scope_type,
                scope_episodes=scope_episodes,
                scope_units=scope_units,
            ):
                continue
            summary_generation["requested_scope_count"] += 1
            scope_ref = f"scope:{len(prepared_scopes)}"
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
            prepared_scopes.append(
                {
                    "scope_ref": scope_ref,
                    "scope_type": scope_type,
                    "scope_key": scope_key,
                    "scope_episodes": scope_episodes,
                    "scope_units": scope_units,
                    "evidence_pack": {
                        "scope_ref": scope_ref,
                        **evidence_pack,
                    },
                }
            )

        generated_texts = self._generate_reflective_summary_texts(
            prepared_scopes=prepared_scopes,
            persona_context=persona_context,
            reflection_summary_model_config=reflection_summary_model_config,
            summary_generation=summary_generation,
        )

        actions: list[dict[str, Any]] = []
        failed_scope_keys = {
            (item.get("scope_type"), item.get("scope_key"))
            for item in summary_generation["failed_scopes"]
        }
        for prepared in prepared_scopes:
            summary_text = generated_texts.get(prepared["scope_ref"])
            scope_identity = (prepared["scope_type"], prepared["scope_key"])
            if not isinstance(summary_text, str) or not summary_text.strip():
                if scope_identity not in failed_scope_keys:
                    self._append_summary_generation_failure(
                        summary_generation=summary_generation,
                        scope_type=prepared["scope_type"],
                        scope_key=prepared["scope_key"],
                        failure_stage="generate_summary_text",
                        failure_reason="summary_text was not returned for dirty scope.",
                    )
                continue
            candidate = self._build_reflective_summary_candidate(
                scope_type=prepared["scope_type"],
                scope_key=prepared["scope_key"],
                summary_text=summary_text,
                evidence_pack=prepared["evidence_pack"],
            )
            evidence_event_ids = self._reflective_event_ids(
                scope_episodes=prepared["scope_episodes"],
                scope_units=prepared["scope_units"],
                limit=12,
            )
            summary_actions = self.action_resolver.resolve_memory_actions(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                event_ids=evidence_event_ids,
                cycle_ids=self._reflective_cycle_ids(scope_episodes=prepared["scope_episodes"], limit=12),
                candidate=candidate,
                embedding_definition=embedding_definition,
                allow_summary=True,
            )
            self._attach_reflective_summary_related_units(
                actions=summary_actions,
                scope_units=prepared["scope_units"],
            )
            actions.extend(summary_actions)
            summary_generation["succeeded_scope_count"] += 1

        return actions, summary_generation

    def _reflective_dirty_scope_index(
        self,
        *,
        episode_groups: dict[tuple[str, str], list[dict[str, Any]]],
        memory_groups: dict[tuple[str, str], list[dict[str, Any]]],
        summary_groups: dict[tuple[str, str], list[dict[str, Any]]],
        memory_actions: list[dict[str, Any]],
        affect_state_updates: list[dict[str, Any]],
        previous_failed_scopes: list[dict[str, Any]],
        trigger_reasons: list[str],
    ) -> dict[tuple[str, str], list[str]]:
        dirty: dict[tuple[str, str], list[str]] = {}

        def mark(scope_type: Any, scope_key: Any, reason: str) -> None:
            if scope_type not in REFLECTIVE_SCOPE_TYPES:
                return
            if not isinstance(scope_key, str) or not scope_key:
                return
            reasons = dirty.setdefault((scope_type, scope_key), [])
            if reason not in reasons:
                reasons.append(reason)

        for scope_type, scope_key in episode_groups:
            mark(scope_type, scope_key, "episode")
        for action in memory_actions:
            if not isinstance(action, dict):
                continue
            memory_unit = action.get("memory_unit")
            if not isinstance(memory_unit, dict):
                memory_unit = action.get("after_snapshot")
            if not isinstance(memory_unit, dict):
                continue
            mark(memory_unit.get("scope_type"), memory_unit.get("scope_key"), "memory_action")
        for update in affect_state_updates:
            if not isinstance(update, dict):
                continue
            if update.get("update_kind") not in {"created", "updated"}:
                continue
            mark(update.get("target_scope_type"), update.get("target_scope_key"), "affect_update")
        for failed in previous_failed_scopes:
            if not isinstance(failed, dict):
                continue
            mark(failed.get("scope_type"), failed.get("scope_key"), "previous_failure")
        if "self_change" in trigger_reasons:
            mark("self", "self", "self_change")

        eligible_scopes = set(episode_groups) | set(memory_groups)
        for scope_type, scope_key in eligible_scopes:
            if summary_groups.get((scope_type, scope_key)):
                continue
            if not self._should_build_reflective_summary(
                scope_type=scope_type,
                scope_episodes=episode_groups.get((scope_type, scope_key), []),
                scope_units=memory_groups.get((scope_type, scope_key), []),
            ):
                continue
            mark(scope_type, scope_key, "missing_summary")
        return dirty

    def _generate_reflective_summary_texts(
        self,
        *,
        prepared_scopes: list[dict[str, Any]],
        persona_context: Any,
        reflection_summary_model_config: dict[str, Any],
        summary_generation: dict[str, Any],
    ) -> dict[str, str]:
        generated: dict[str, str] = {}
        if not prepared_scopes:
            return generated
        for offset in range(0, len(prepared_scopes), REFLECTION_SUMMARY_BATCH_LIMIT):
            batch = prepared_scopes[offset : offset + REFLECTION_SUMMARY_BATCH_LIMIT]
            source_pack = {
                "scopes": [item["evidence_pack"] for item in batch],
            }
            try:
                payload = self.llm.generate_memory_reflection_summary(
                    model_config=reflection_summary_model_config,
                    persona_context=persona_context,
                    source_pack=source_pack,
                )
                summary_generation["llm_call_count"] += 1
            except Exception as exc:  # noqa: BLE001
                summary_generation["llm_call_count"] += 1
                for item in batch:
                    self._append_summary_generation_failure(
                        summary_generation=summary_generation,
                        scope_type=item["scope_type"],
                        scope_key=item["scope_key"],
                        failure_stage="generate_summary_text",
                        failure_reason=str(exc),
                    )
                continue
            summaries = payload.get("summaries")
            if not isinstance(summaries, list):
                for item in batch:
                    self._append_summary_generation_failure(
                        summary_generation=summary_generation,
                        scope_type=item["scope_type"],
                        scope_key=item["scope_key"],
                        failure_stage="generate_summary_text",
                        failure_reason="summaries is not an array.",
                    )
                continue
            allowed_refs = {item["scope_ref"] for item in batch}
            prepared_by_ref = {item["scope_ref"]: item for item in batch}
            seen_refs: set[str] = set()
            for item in summaries:
                scope_ref = item.get("scope_ref") if isinstance(item, dict) else None
                if scope_ref not in allowed_refs:
                    continue
                if scope_ref in seen_refs:
                    continue
                seen_refs.add(scope_ref)
                prepared = prepared_by_ref[scope_ref]
                try:
                    validate_memory_reflection_summary_item(item)
                    generated[scope_ref] = str(item["summary_text"]).strip()
                except Exception as exc:  # noqa: BLE001
                    self._append_summary_generation_failure(
                        summary_generation=summary_generation,
                        scope_type=prepared["scope_type"],
                        scope_key=prepared["scope_key"],
                        failure_stage="contract_validation",
                        failure_reason=str(exc),
                    )
        return generated

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
                support_kinds.append("persona_context")

        mood_context = None
        if scope_type == "self":
            mood_context = self._reflective_mood_context(mood_state)
            if mood_context is not None:
                support_kinds.append("mood_state")

        affect_context: list[dict[str, Any]] = []
        if scope_type in {"relationship", "entity"}:
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
            "persona_context": persona_context,
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
        persona_context = support.get("persona_context")
        if isinstance(persona_context, dict) and persona_context:
            payload["persona_context"] = persona_context
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
        if scope_type == "entity":
            return display_scope_key(scope_key)
        if scope_type == "topic":
            return display_scope_key(scope_key)
        if scope_type == "relationship":
            return f"{scope_key} の関係文脈"
        return display_scope_key(scope_key)

    def _reflective_persona_context(self, persona: dict[str, Any]) -> dict[str, Any] | None:
        return build_persona_context(
            persona,
            role="memory_reflection_summary",
        ).to_summary_payload()

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
            "entity": 0.5,
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
