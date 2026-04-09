from __future__ import annotations

import uuid
from typing import Any

from otomekairo.llm import LLMClient
from otomekairo.memory_actions import MemoryActionResolver
from otomekairo.memory_reflection import ReflectiveConsolidator
from otomekairo.memory_utils import clamp_score, normalized_text_list, optional_text
from otomekairo.memory_vector import MemoryVectorIndexer
from otomekairo.store import FileStore


# 統合器
class MemoryConsolidator:
    def __init__(self, *, store: FileStore, llm: LLMClient) -> None:
        # 依存関係
        self.store = store
        self.llm = llm
        self.action_resolver = MemoryActionResolver(store=store)
        self.vector_indexer = MemoryVectorIndexer(store=store, llm=llm)
        self.reflective = ReflectiveConsolidator(
            store=store,
            action_resolver=self.action_resolver,
            vector_indexer=self.vector_indexer,
        )

    def consolidate_turn(
        self,
        *,
        state: dict[str, Any],
        cycle_id: str,
        finished_at: str,
        observation_text: str,
        recall_hint: dict[str, Any],
        decision: dict[str, Any],
        reply_payload: dict[str, Any] | None,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # 記憶切り替え
        if not state.get("memory_enabled", True):
            return {
                "turn_consolidation_status": "disabled",
                "episode_id": None,
                "memory_action_count": 0,
                "affect_update_count": 0,
                "failure_reason": None,
                "reflective_consolidation": {
                    "started": False,
                    "result_status": "disabled",
                    "trigger_reasons": [],
                    "affected_memory_unit_ids": [],
                    "failure_reason": None,
                },
            }

        # モデル選択
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        memory_role = selected_preset["roles"]["memory_interpretation"]

        # 解釈
        interpretation = self.llm.generate_memory_interpretation(
            role_definition=memory_role,
            observation_text=observation_text,
            recall_hint=recall_hint,
            decision=decision,
            reply_text=reply_payload["reply_text"] if reply_payload else None,
            current_time=finished_at,
        )

        # Episode要約
        selected_memory_set_id = state["selected_memory_set_id"]
        event_ids = [event["event_id"] for event in events]
        episode = self._build_episode(
            cycle_id=cycle_id,
            memory_set_id=selected_memory_set_id,
            finished_at=finished_at,
            event_ids=event_ids,
            payload=interpretation["episode"],
        )

        # 記憶アクション群
        memory_actions: list[dict[str, Any]] = []
        for candidate in interpretation["candidate_memory_units"]:
            memory_actions.extend(
                self.action_resolver.resolve_memory_actions(
                    memory_set_id=selected_memory_set_id,
                    finished_at=finished_at,
                    event_ids=event_ids,
                    cycle_ids=[cycle_id],
                    candidate=candidate,
                )
            )

        # affectUpdates生成
        affect_updates = [
            self._build_affect_update(
                memory_set_id=selected_memory_set_id,
                finished_at=finished_at,
                payload=affect_update,
            )
            for affect_update in interpretation["affect_updates"]
        ]

        # 永続化
        self.store.persist_turn_consolidation(
            episode=episode,
            memory_actions=memory_actions,
            affect_updates=affect_updates,
        )

        # ベクトル索引
        self.vector_indexer.sync(
            state=state,
            finished_at=finished_at,
            episode=episode,
            memory_actions=memory_actions,
        )

        # 内省統合
        reflective_result = self.reflective.run(
            state=state,
            finished_at=finished_at,
            episode=episode,
            memory_actions=memory_actions,
        )

        # 結果
        return {
            "turn_consolidation_status": "succeeded",
            "episode_id": episode["episode_id"],
            "episode_summary": episode["summary_text"],
            "episode_series_id": episode.get("episode_series_id"),
            "open_loops": episode.get("open_loops", []),
            "memory_action_count": len(memory_actions),
            "affect_update_count": len(affect_updates),
            "updated_memory_unit_ids": [action["memory_unit_id"] for action in memory_actions if action.get("memory_unit_id")],
            "affect_updates": [
                {
                    "layer": update["layer"],
                    "target_scope_type": update["target_scope_type"],
                    "target_scope_key": update["target_scope_key"],
                    "affect_label": update["affect_label"],
                    "intensity": update["intensity"],
                }
                for update in affect_updates
            ],
            "failure_reason": None,
            "reflective_consolidation": reflective_result,
        }

    def _build_episode(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        finished_at: str,
        event_ids: list[str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        # 正規化
        open_loops = normalized_text_list(payload.get("open_loops", []), limit=4)
        episode_series_id = self._resolve_episode_series_id(
            memory_set_id=memory_set_id,
            payload=payload,
            open_loops=open_loops,
        )

        # 記録
        return {
            "episode_id": f"episode:{uuid.uuid4().hex}",
            "cycle_id": cycle_id,
            "memory_set_id": memory_set_id,
            "episode_type": payload["episode_type"],
            "episode_series_id": episode_series_id,
            "primary_scope_type": payload["primary_scope_type"],
            "primary_scope_key": payload["primary_scope_key"],
            "summary_text": payload["summary_text"].strip(),
            "outcome_text": optional_text(payload.get("outcome_text")),
            "open_loops": open_loops,
            "salience": clamp_score(payload["salience"]),
            "formed_at": finished_at,
            "linked_event_ids": event_ids,
        }

    def _resolve_episode_series_id(
        self,
        *,
        memory_set_id: str,
        payload: dict[str, Any],
        open_loops: list[str],
    ) -> str | None:
        # 明示指定
        explicit_series_id = optional_text(payload.get("episode_series_id"))
        if explicit_series_id is not None:
            return explicit_series_id

        # 候補検索
        recent_episodes = self.store.list_recent_episodes_for_series(
            memory_set_id=memory_set_id,
            primary_scope_type=payload["primary_scope_type"],
            primary_scope_key=payload["primary_scope_key"],
            limit=6,
        )
        if not recent_episodes:
            return None

        # open loop の継続を優先する。
        if open_loops:
            requested = {value for value in open_loops if isinstance(value, str)}
            for episode in recent_episodes:
                candidate_loops = {
                    value
                    for value in episode.get("open_loops", [])
                    if isinstance(value, str)
                }
                if requested & candidate_loops:
                    return episode.get("episode_series_id") or episode["episode_id"]

        # 明示 continuation 系だけ、最近の open loop episode を継続扱いにする。
        if payload["episode_type"] in {"commitment_check", "action_result", "task_progress", "follow_up"}:
            for episode in recent_episodes:
                if episode.get("open_loops"):
                    return episode.get("episode_series_id") or episode["episode_id"]

        return None

    def _build_affect_update(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        # 記録
        return {
            "affect_state_id": f"affect_state:{uuid.uuid4().hex}",
            "memory_set_id": memory_set_id,
            "layer": payload["layer"],
            "target_scope_type": payload["target_scope_type"],
            "target_scope_key": payload["target_scope_key"],
            "affect_label": payload["affect_label"],
            "intensity": clamp_score(payload["intensity"]),
            "observed_at": finished_at,
            "updated_at": finished_at,
        }
