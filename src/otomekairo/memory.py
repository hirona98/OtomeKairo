from __future__ import annotations

from copy import deepcopy
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
        self.action_resolver = MemoryActionResolver(store=store, llm=llm)
        self.vector_indexer = MemoryVectorIndexer(store=store, llm=llm)
        self.reflective = ReflectiveConsolidator(
            store=store,
            llm=llm,
            action_resolver=self.action_resolver,
            vector_indexer=self.vector_indexer,
        )

    def consolidate_turn(
        self,
        *,
        state: dict[str, Any],
        cycle_id: str,
        finished_at: str,
        input_text: str,
        recall_hint: dict[str, Any],
        decision: dict[str, Any],
        reply_payload: dict[str, Any] | None,
        events: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        # モデル選択
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        memory_role = selected_preset["roles"]["memory_interpretation"]

        # 解釈
        interpretation = self.llm.generate_memory_interpretation(
            role_definition=memory_role,
            input_text=input_text,
            recall_hint=recall_hint,
            decision=decision,
            reply_text=reply_payload["reply_text"] if reply_payload else None,
            current_time=finished_at,
        )

        # Episode要約
        selected_memory_set_id = state["selected_memory_set_id"]
        embedding_definition = state["memory_sets"][selected_memory_set_id]["embedding"]
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
                    embedding_definition=embedding_definition,
                )
            )

        # episode affect群
        episode_affects = [
            self._build_episode_affect(
                memory_set_id=selected_memory_set_id,
                episode_id=episode["episode_id"],
                finished_at=finished_at,
                payload=episode_affect,
            )
            for episode_affect in interpretation["episode_affects"]
        ]

        # 永続化
        affect_persist_result = self.store.persist_turn_consolidation(
            episode=episode,
            memory_actions=memory_actions,
            episode_affects=episode_affects,
        )

        # 結果
        return (
            {
                "turn_consolidation_status": "succeeded",
                "episode_id": episode["episode_id"],
                "episode_summary": episode["summary_text"],
                "episode_series_id": episode.get("episode_series_id"),
                "open_loops": episode.get("open_loops", []),
                "memory_action_count": len(memory_actions),
                "episode_affect_count": len(episode_affects),
                "updated_memory_unit_ids": [
                    action["memory_unit_id"]
                    for action in memory_actions
                    if action.get("memory_unit_id")
                ],
                "episode_affects": [
                    {
                        "target_scope_type": affect["target_scope_type"],
                        "target_scope_key": affect["target_scope_key"],
                        "affect_label": affect["affect_label"],
                        "vad": affect["vad"],
                        "intensity": affect["intensity"],
                        "confidence": affect["confidence"],
                    }
                    for affect in episode_affects
                ],
                "mood_state_update": affect_persist_result["mood_state_update"],
                "affect_state_updates": affect_persist_result["affect_state_updates"],
                "failure_reason": None,
                "vector_index_sync": {
                    "result_status": "queued",
                    "failure_reason": None,
                },
                "reflective_consolidation": {
                    "started": False,
                    "result_status": "queued",
                    "trigger_reasons": [],
                    "affected_memory_unit_ids": [],
                    "summary_generation": {
                        "requested_scope_count": 0,
                        "succeeded_scope_count": 0,
                        "failed_scopes": [],
                    },
                    "failure_reason": None,
                },
            },
            self._build_postprocess_job(
                state=state,
                cycle_id=cycle_id,
                finished_at=finished_at,
                episode=episode,
                memory_actions=memory_actions,
            ),
        )

    def run_postprocess_job(self, *, job: dict[str, Any]) -> dict[str, Any]:
        # job状態
        state_snapshot = job["state_snapshot"]
        finished_at = job["turn_finished_at"]
        episode = job["episode"]
        memory_actions = job["memory_actions"]

        # ベクトル索引
        try:
            self.vector_indexer.sync(
                state=state_snapshot,
                finished_at=finished_at,
                episode=episode,
                memory_actions=memory_actions,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "vector_index_sync": {
                    "result_status": "failed",
                    "failure_reason": str(exc),
                },
                "reflective_consolidation": {
                    "started": False,
                    "result_status": "not_started",
                    "trigger_reasons": [],
                    "affected_memory_unit_ids": [],
                    "summary_generation": {
                        "requested_scope_count": 0,
                        "succeeded_scope_count": 0,
                        "failed_scopes": [],
                    },
                    "failure_reason": None,
                },
            }

        # 内省統合
        reflective_result = self.reflective.run(
            state=state_snapshot,
            finished_at=finished_at,
            episode=episode,
            memory_actions=memory_actions,
        )

        # 結果
        return {
            "vector_index_sync": {
                "result_status": "succeeded",
                "failure_reason": None,
            },
            "reflective_consolidation": reflective_result,
        }

    def _build_postprocess_job(
        self,
        *,
        state: dict[str, Any],
        cycle_id: str,
        finished_at: str,
        episode: dict[str, Any],
        memory_actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        selected_memory_set_id = state["selected_memory_set_id"]
        selected_model_preset_id = state["selected_model_preset_id"]
        selected_model_preset = state["model_presets"][selected_model_preset_id]
        reflection_summary_role = selected_model_preset["roles"]["memory_reflection_summary"]
        return {
            "cycle_id": cycle_id,
            "memory_set_id": selected_memory_set_id,
            "queued_at": finished_at,
            "started_at": None,
            "finished_at": None,
            "result_status": "queued",
            "turn_finished_at": finished_at,
            "state_snapshot": {
                "selected_memory_set_id": selected_memory_set_id,
                "selected_model_preset_id": selected_model_preset_id,
                "memory_sets": {
                    selected_memory_set_id: {
                        "embedding": deepcopy(
                            state["memory_sets"][selected_memory_set_id]["embedding"]
                        )
                    }
                },
                "model_presets": {
                    selected_model_preset_id: {
                        "roles": {
                            "memory_reflection_summary": deepcopy(reflection_summary_role),
                        }
                    }
                },
            },
            "episode": deepcopy(episode),
            "memory_actions": deepcopy(memory_actions),
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

    def _build_episode_affect(
        self,
        *,
        memory_set_id: str,
        episode_id: str,
        finished_at: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        # 記録
        return {
            "episode_affect_id": f"episode_affect:{uuid.uuid4().hex}",
            "memory_set_id": memory_set_id,
            "episode_id": episode_id,
            "target_scope_type": payload["target_scope_type"],
            "target_scope_key": payload["target_scope_key"],
            "affect_label": payload["affect_label"],
            "summary_text": optional_text(payload.get("summary_text")) or payload["affect_label"],
            "vad": self._build_vad(payload.get("vad")),
            "intensity": clamp_score(payload["intensity"]),
            "confidence": clamp_score(payload["confidence"]),
            "observed_at": finished_at,
            "created_at": finished_at,
        }

    def _build_vad(self, payload: Any) -> dict[str, float]:
        # 正規化
        if not isinstance(payload, dict):
            return {"v": 0.0, "a": 0.0, "d": 0.0}

        # 結果
        return {
            "v": self._clamp_vad_axis(payload.get("v")),
            "a": self._clamp_vad_axis(payload.get("a")),
            "d": self._clamp_vad_axis(payload.get("d")),
        }

    def _clamp_vad_axis(self, value: Any) -> float:
        # 正規化
        if not isinstance(value, (int, float)):
            return 0.0
        return max(-1.0, min(float(value), 1.0))
