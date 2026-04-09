from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
import uuid

from otomekairo.memory_actions import MemoryActionResolver
from otomekairo.memory_utils import (
    action_counts,
    clamp_score,
    days_since,
    display_scope_key,
    hours_since,
    now_iso,
    stable_json,
    timestamp_sort_key,
    unique_memory_unit_ids,
)
from otomekairo.memory_vector import MemoryVectorIndexer
from otomekairo.store import FileStore


# 定数
ACTIVE_MEMORY_STATUSES = ("inferred", "confirmed")
REFLECTIVE_SCOPE_TYPES = ("self", "user", "relationship", "topic")
REFLECTION_TRIGGER_CYCLE_INTERVAL = 8
REFLECTION_TRIGGER_HOURS = 24
REFLECTION_HIGH_SALIENCE_THRESHOLD = 0.8
REFLECTION_HIGH_SALIENCE_COUNT = 3
REFLECTION_SCOPE_SIGNAL_SALIENCE = 0.65
REFLECTION_EPISODE_LIMIT = 24
REFLECTION_MEMORY_LIMIT = 96
REFLECTION_MIN_SUMMARY_EVIDENCE = 3
REFLECTION_MIN_SUMMARY_EPISODES = 2
REFLECTION_CONFIRMED_SUMMARY_EVIDENCE = 7
REFLECTION_CONFIRMED_SUMMARY_EPISODES = 4
REFLECTION_TOPIC_DORMANT_AFTER_DAYS = 14
REFLECTION_CONFIRMED_TOPIC_DORMANT_AFTER_DAYS = 30


# 内省
class ReflectiveConsolidator:
    def __init__(
        self,
        *,
        store: FileStore,
        action_resolver: MemoryActionResolver,
        vector_indexer: MemoryVectorIndexer,
    ) -> None:
        # 依存関係
        self.store = store
        self.action_resolver = action_resolver
        self.vector_indexer = vector_indexer

    def run(
        self,
        *,
        state: dict[str, Any],
        finished_at: str,
        episode: dict[str, Any],
        memory_actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # トリガー確認
        memory_set_id = state["selected_memory_set_id"]
        latest_run = self.store.get_latest_reflection_run(memory_set_id)
        latest_updated_run = self.store.get_latest_reflection_run(
            memory_set_id,
            result_status="updated",
        )
        trigger_reasons = self._reflective_trigger_reasons(
            memory_set_id=memory_set_id,
            finished_at=finished_at,
            latest_run=latest_run,
            episode=episode,
            memory_actions=memory_actions,
        )
        if not trigger_reasons:
            return {
                "started": False,
                "result_status": "not_triggered",
                "trigger_reasons": [],
                "affected_memory_unit_ids": [],
                "failure_reason": None,
            }

        # 実行状態
        reflection_run_id = f"reflection_run:{uuid.uuid4().hex}"
        started_at = now_iso()
        since_iso = latest_updated_run["finished_at"] if isinstance(latest_updated_run, dict) else None
        episodes: list[dict[str, Any]] = []
        reflection_actions: list[dict[str, Any]] = []

        try:
            # 入力収集
            episodes = self.store.list_episodes_for_reflection(
                memory_set_id=memory_set_id,
                since_iso=since_iso,
                limit=REFLECTION_EPISODE_LIMIT,
            )
            active_units = self.store.list_memory_units_for_reflection(
                memory_set_id=memory_set_id,
                statuses=list(ACTIVE_MEMORY_STATUSES),
                scope_types=list(REFLECTIVE_SCOPE_TYPES),
                limit=REFLECTION_MEMORY_LIMIT,
            )

            # アクション構築
            reflection_actions.extend(
                self._build_reflective_summary_actions(
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    episodes=episodes,
                    active_units=active_units,
                )
            )
            reflection_actions.extend(
                self._build_reflective_confirmation_actions(
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    active_units=active_units,
                )
            )
            reflection_actions.extend(
                self._build_reflective_dormant_actions(
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    episodes=episodes,
                    active_units=active_units,
                    excluded_memory_unit_ids={
                        action["memory_unit_id"]
                        for action in reflection_actions
                    },
                )
            )

            # 記憶永続化
            self.store.persist_memory_actions(memory_actions=reflection_actions)

            # ベクトル索引
            finished_reflection_at = now_iso()
            failure_reason: str | None = None
            result_status = "updated" if reflection_actions else "no_change"
            try:
                self.vector_indexer.sync(
                    state=state,
                    finished_at=finished_reflection_at,
                    episode=None,
                    memory_actions=reflection_actions,
                )
            except Exception as exc:  # noqa: BLE001
                result_status = "failed"
                failure_reason = str(exc)

            # 内省実行
            affected_memory_unit_ids = unique_memory_unit_ids(reflection_actions)
            self.store.upsert_reflection_run(
                reflection_run={
                    "reflection_run_id": reflection_run_id,
                    "memory_set_id": memory_set_id,
                    "started_at": started_at,
                    "finished_at": finished_reflection_at,
                    "result_status": result_status,
                    "trigger_reasons": trigger_reasons,
                    "source_episode_ids": [episode["episode_id"] for episode in episodes],
                    "affected_memory_unit_ids": affected_memory_unit_ids,
                    "action_counts": action_counts(reflection_actions),
                    "failure_reason": failure_reason,
                }
            )

            # 結果
            return {
                "started": True,
                "result_status": result_status,
                "trigger_reasons": trigger_reasons,
                "affected_memory_unit_ids": affected_memory_unit_ids,
                "failure_reason": failure_reason,
            }
        except Exception as exc:  # noqa: BLE001
            # 失敗処理
            finished_reflection_at = now_iso()
            failure_reason = str(exc)
            self.store.upsert_reflection_run(
                reflection_run={
                    "reflection_run_id": reflection_run_id,
                    "memory_set_id": memory_set_id,
                    "started_at": started_at,
                    "finished_at": finished_reflection_at,
                    "result_status": "failed",
                    "trigger_reasons": trigger_reasons,
                    "source_episode_ids": [episode["episode_id"] for episode in episodes],
                    "affected_memory_unit_ids": unique_memory_unit_ids(reflection_actions),
                    "action_counts": action_counts(reflection_actions),
                    "failure_reason": failure_reason,
                }
            )
            return {
                "started": True,
                "result_status": "failed",
                "trigger_reasons": trigger_reasons,
                "affected_memory_unit_ids": unique_memory_unit_ids(reflection_actions),
                "failure_reason": failure_reason,
            }

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
        if any(action["operation"] in {"supersede", "revoke"} for action in memory_actions):
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
    ) -> list[dict[str, Any]]:
        # グループ化
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

        # スコープ走査
        actions: list[dict[str, Any]] = []
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

            candidate = self._build_reflective_summary_candidate(
                scope_type=scope_type,
                scope_key=scope_key,
                scope_episodes=scope_episodes,
                scope_units=scope_units,
            )
            evidence_event_ids = self._reflective_event_ids(
                scope_episodes=scope_episodes,
                scope_units=scope_units,
                limit=12,
            )
            actions.extend(
                self.action_resolver.resolve_memory_actions(
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    event_ids=evidence_event_ids,
                    cycle_ids=self._reflective_cycle_ids(scope_episodes=scope_episodes, limit=12),
                    candidate=candidate,
                    allow_summary=True,
                )
            )

        # 結果
        return actions

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
        scope_episodes: list[dict[str, Any]],
        scope_units: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # 根拠
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
        confidence_floor = 0.74 if summary_status == "confirmed" else 0.58

        # 候補
        return {
            "memory_type": "summary",
            "scope_type": scope_type,
            "scope_key": scope_key,
            "subject_ref": self._summary_subject_ref(scope_type, scope_key),
            "predicate": "long_term_pattern",
            "object_ref_or_value": f"{scope_type}:{scope_key}:summary",
            "summary_text": self._reflective_summary_text(
                scope_type=scope_type,
                scope_key=scope_key,
                memory_types=memory_types,
                open_loop_count=open_loop_count,
            ),
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
                "evidence_episode_count": len(scope_episodes),
                "evidence_memory_count": len(scope_units),
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

    def _reflective_summary_text(
        self,
        *,
        scope_type: str,
        scope_key: str,
        memory_types: list[str],
        open_loop_count: int,
    ) -> str:
        # トピック
        if scope_type == "topic":
            topic_label = display_scope_key(scope_key)
            if open_loop_count > 0:
                return f"最近は {topic_label} に関する話題が未完了テーマとして続いている。"
            return f"最近は {topic_label} に関する話題が繰り返し現れている。"

        # 関係
        if scope_type == "relationship":
            if scope_key != "self|user":
                relationship_label = display_scope_key(scope_key)
                if open_loop_count > 0:
                    return f"最近は {relationship_label} の関係文脈で継続中の確認事項や流れが積み上がっている。"
                if "relation" in memory_types:
                    return f"最近は {relationship_label} の関係理解が少しずつ安定している。"
                return f"最近は {relationship_label} の関係文脈が継続して積み上がっている。"
            if open_loop_count > 0:
                return "最近のあなたとのやり取りでは、継続中の確認事項や会話の流れが積み上がっている。"
            if "relation" in memory_types:
                return "最近のあなたとのやり取りでは、距離感や支え方に関する理解が少しずつ安定している。"
            return "最近のあなたとのやり取りでは、関係文脈が継続して積み上がっている。"

        # 自己
        if scope_type == "self":
            return "最近の自分側の応答では、受け止め方や関わり方に一定の傾向が見えている。"

        # ユーザー
        theme_text = self._reflective_theme_text(memory_types)
        return f"最近のあなたに関するやり取りでは、{theme_text}の理解が少しずつ積み上がっている。"

    def _reflective_theme_text(self, memory_types: list[str]) -> str:
        # マッピング
        labels = {
            "fact": "事実や状況",
            "preference": "好み",
            "relation": "関係性",
            "interpretation": "状態や受け止め",
            "summary": "長期傾向",
        }
        parts = [labels[memory_type] for memory_type in memory_types if memory_type in labels]
        if not parts:
            return "状態"
        if len(parts) == 1:
            return parts[0]
        return "や".join(parts)

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
            limit=REFLECTION_EPISODE_LIMIT,
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
