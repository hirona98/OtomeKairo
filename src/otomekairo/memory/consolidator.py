from __future__ import annotations

from copy import deepcopy
import json
import uuid
from typing import Any

from otomekairo.llm.client import LLMClient
from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.reflection.consolidator import ReflectiveConsolidator
from otomekairo.memory.utils import clamp_score, normalized_text_list, optional_text
from otomekairo.memory.vector import MemoryVectorIndexer
from otomekairo.store.file_store import FileStore


# memory_interpretation に渡す events は補助文脈に留める。
MEMORY_CONTEXT_EVENT_LIMIT = 12
MEMORY_CONTEXT_EVENT_TOTAL_CHAR_LIMIT = 1600


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
        memory_context: dict[str, Any] | None = None,
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
            memory_context=self._build_memory_interpretation_context(
                memory_context=memory_context,
                events=events,
            ),
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
                "memory_link_update": affect_persist_result["memory_link_update"],
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
                    "drive_state_update": {
                        "result_status": "queued",
                        "active_drive_ids": [],
                        "removed_drive_ids": [],
                        "drive_summaries": [],
                        "scope_supports": [],
                    },
                    "affect_state_update": {
                        "result_status": "queued",
                        "created_affect_state_ids": [],
                        "updated_affect_state_ids": [],
                        "weakened_affect_state_ids": [],
                        "pruned_affect_state_ids": [],
                        "affect_state_summaries": [],
                    },
                    "memory_link_update": {
                        "result_status": "queued",
                        "link_count": 0,
                        "labels": {},
                        "memory_link_ids": [],
                    },
                    "failure_reason": None,
                },
                "drive_state_update": {
                    "result_status": "queued",
                    "active_drive_ids": [],
                    "removed_drive_ids": [],
                    "drive_summaries": [],
                    "scope_supports": [],
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
                    "drive_state_update": {
                        "result_status": "not_started",
                        "active_drive_ids": [],
                        "removed_drive_ids": [],
                        "drive_summaries": [],
                        "scope_supports": [],
                    },
                    "affect_state_update": {
                        "result_status": "not_started",
                        "created_affect_state_ids": [],
                        "updated_affect_state_ids": [],
                        "weakened_affect_state_ids": [],
                        "pruned_affect_state_ids": [],
                        "affect_state_summaries": [],
                    },
                    "memory_link_update": {
                        "result_status": "not_started",
                        "link_count": 0,
                        "labels": {},
                        "memory_link_ids": [],
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
        selected_persona_id = state["selected_persona_id"]
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
                "selected_persona_id": selected_persona_id,
                "selected_memory_set_id": selected_memory_set_id,
                "selected_model_preset_id": selected_model_preset_id,
                "personas": {
                    selected_persona_id: deepcopy(state["personas"][selected_persona_id]),
                },
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

    def _build_memory_interpretation_context(
        self,
        *,
        memory_context: dict[str, Any] | None,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = dict(memory_context) if isinstance(memory_context, dict) else {}
        compact_events = [self._compact_event_for_memory_context(event) for event in events]
        compact_events = [event for event in compact_events if event]
        if compact_events:
            limited_events = self._limit_memory_context_events(compact_events)
            payload["events"] = limited_events
            if len(limited_events) < len(compact_events):
                payload["events_truncated"] = {
                    "original_count": len(compact_events),
                    "included_count": len(limited_events),
                    "event_limit": MEMORY_CONTEXT_EVENT_LIMIT,
                    "total_char_limit": MEMORY_CONTEXT_EVENT_TOTAL_CHAR_LIMIT,
                }
        return payload

    def _limit_memory_context_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected = events
        if len(events) > MEMORY_CONTEXT_EVENT_LIMIT:
            selected = [events[0], *events[-(MEMORY_CONTEXT_EVENT_LIMIT - 1) :]]

        limited: list[dict[str, Any]] = []
        total_chars = 0
        for event in selected:
            event_chars = len(json.dumps(event, ensure_ascii=False, sort_keys=True))
            if limited and total_chars + event_chars > MEMORY_CONTEXT_EVENT_TOTAL_CHAR_LIMIT:
                break
            limited.append(event)
            total_chars += event_chars
        return limited

    def _compact_event_for_memory_context(self, event: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in (
            "kind",
            "role",
            "result_kind",
            "external_result_kind",
            "reason_code",
            "reason_summary",
            "pending_intent_summary",
        ):
            value = event.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                normalized = value.strip()
                if not normalized:
                    continue
                payload[key] = self._compact_text(normalized)
                continue
            if isinstance(value, (int, float, bool, list, dict)):
                payload[key] = value
        text = event.get("text")
        if isinstance(text, str) and text.strip():
            payload["text_summary"] = self._compact_text(text.strip())
        return payload

    def _compact_text(self, value: str, limit: int = 200) -> str:
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "…"

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
        if payload["episode_type"] in {"commitment_followup", "action_result", "task_progress", "follow_up"}:
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
