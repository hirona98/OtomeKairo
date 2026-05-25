from __future__ import annotations

from typing import Any
import uuid

from otomekairo.llm.client import LLMClient
from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.reflection.affect import MemoryReflectionAffectMixin
from otomekairo.memory.reflection.constants import (
    ACTIVE_MEMORY_STATUSES,
    REFLECTION_AFFECT_STATE_EPISODE_LIMIT,
    REFLECTION_EPISODE_LIMIT,
    REFLECTION_MEMORY_LIMIT,
    REFLECTIVE_SCOPE_TYPES,
)
from otomekairo.memory.reflection.drive import MemoryReflectionDriveMixin
from otomekairo.memory.reflection.summary import MemoryReflectionSummaryMixin
from otomekairo.memory.utils import action_counts, now_iso, unique_memory_unit_ids
from otomekairo.memory.vector import MemoryVectorIndexer
from otomekairo.store.file_store import FileStore


# 内省
class ReflectiveConsolidator(
    MemoryReflectionSummaryMixin,
    MemoryReflectionDriveMixin,
    MemoryReflectionAffectMixin,
):
    def __init__(
        self,
        *,
        store: FileStore,
        llm: LLMClient,
        action_resolver: MemoryActionResolver,
        vector_indexer: MemoryVectorIndexer,
    ) -> None:
        # 依存関係
        self.store = store
        self.llm = llm
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
        summary_generation = self._empty_summary_generation()
        drive_state_update = self._empty_drive_state_update()
        affect_state_update = self._empty_affect_state_update("not_started")
        memory_link_update = self._empty_memory_link_update("not_started")
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
                "summary_generation": summary_generation,
                "drive_state_update": drive_state_update,
                "affect_state_update": self._empty_affect_state_update("not_triggered"),
                "memory_link_update": self._empty_memory_link_update("not_triggered"),
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
            embedding_definition = state["memory_sets"][memory_set_id]["embedding"]
            reflection_summary_role = self._reflection_summary_role_definition(state=state)
            selected_persona = self._selected_persona_definition(state=state)
            mood_state = self.store.get_mood_state(
                memory_set_id=memory_set_id,
                current_time=finished_at,
            )
            affect_states = self.store.list_affect_states_for_context(
                memory_set_id=memory_set_id,
                limit=64,
            )
            episode_affects = self.store.list_episode_affects_for_reflection(
                memory_set_id=memory_set_id,
                since_iso=(
                    self._initial_affect_state_since_iso(since_iso)
                    if not affect_states
                    else since_iso
                ),
                limit=REFLECTION_AFFECT_STATE_EPISODE_LIMIT,
            )
            affect_state_updates = self._build_reflective_affect_state_updates(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                episode_affects=episode_affects,
                existing_affect_states=affect_states,
            )
            affect_persist_result = self.store.persist_affect_state_updates(
                affect_state_updates=affect_state_updates,
            )
            affect_state_update = self._affect_state_update_trace(
                affect_state_updates=affect_state_updates,
                persist_result=affect_persist_result,
            )
            affect_states = self.store.list_affect_states_for_context(
                memory_set_id=memory_set_id,
                limit=12,
            )
            scope_support_index = self._build_reflective_scope_support_index(
                episodes=episodes,
                active_units=active_units,
                selected_persona=selected_persona,
                mood_state=mood_state,
                affect_states=affect_states,
            )

            # アクション構築
            summary_actions, summary_generation = self._build_reflective_summary_actions(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                episodes=episodes,
                active_units=active_units,
                embedding_definition=embedding_definition,
                reflection_summary_role=reflection_summary_role,
                scope_support_index=scope_support_index,
            )
            reflection_actions.extend(summary_actions)
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
            memory_link_update = self.store.persist_memory_actions(memory_actions=reflection_actions)

            # ベクトル索引
            finished_reflection_at = now_iso()
            failure_reason: str | None = None
            result_status = (
                "updated"
                if reflection_actions or affect_state_update["result_status"] == "updated"
                else "no_change"
            )
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

            # 派生状態
            summary_update_index = self._summary_update_index(summary_actions)
            drive_state_update = self._refresh_drive_states(
                memory_set_id=memory_set_id,
                finished_at=finished_reflection_at,
                selected_persona=selected_persona,
                mood_state=mood_state,
                affect_states=affect_states,
                scope_support_index=scope_support_index,
                summary_update_index=summary_update_index,
            )

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
                    "summary_generation": summary_generation,
                    "drive_state_update": drive_state_update,
                    "affect_state_update": affect_state_update,
                    "memory_link_update": memory_link_update,
                    "failure_reason": failure_reason,
                }
            )

            # 結果
            return {
                "started": True,
                "result_status": result_status,
                "trigger_reasons": trigger_reasons,
                "affected_memory_unit_ids": affected_memory_unit_ids,
                "summary_generation": summary_generation,
                "drive_state_update": drive_state_update,
                "affect_state_update": affect_state_update,
                "memory_link_update": memory_link_update,
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
                    "summary_generation": summary_generation,
                    "drive_state_update": drive_state_update,
                    "affect_state_update": affect_state_update,
                    "memory_link_update": memory_link_update,
                    "failure_reason": failure_reason,
                }
            )
            return {
                "started": True,
                "result_status": "failed",
                "trigger_reasons": trigger_reasons,
                "affected_memory_unit_ids": unique_memory_unit_ids(reflection_actions),
                "summary_generation": summary_generation,
                "drive_state_update": drive_state_update,
                "affect_state_update": affect_state_update,
                "memory_link_update": memory_link_update,
                "failure_reason": failure_reason,
            }
