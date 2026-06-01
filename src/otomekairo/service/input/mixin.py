from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

from otomekairo.memory.utils import (
    llm_local_time_text,
    local_datetime,
    local_now,
    localize_timestamp_fields,
    now_iso,
    stable_json,
)
from otomekairo.service.common import ServiceError
from otomekairo.service.input.constants import (
    RECALL_HINT_RECENT_TURN_LIMIT,
    WORLD_STATE_FOREGROUND_LIMIT,
)
from otomekairo.service.input.capability_context import ServiceInputCapabilityContextMixin
from otomekairo.service.input.activity import ServiceInputActivityMixin
from otomekairo.service.input.cycle import ServiceInputCycleMixin
from otomekairo.service.input.initiative import ServiceInputInitiativeMixin
from otomekairo.service.input.logging import ServiceInputLoggingMixin
from otomekairo.service.input.pipeline import ServiceInputPipelineMixin
from otomekairo.service.input.trace import ServiceInputTraceMixin
from otomekairo.service.input.visual import ServiceInputVisualMixin
from otomekairo.service.input.wake_observation import ServiceInputWakeObservationMixin
from otomekairo.service.input.wake_pipeline import ServiceInputWakePipelineMixin
from otomekairo.service.input.world_state import ServiceInputWorldStateMixin
from otomekairo.world_state.models import WorldStateTrace


class ServiceInputMixin(
    ServiceInputCycleMixin,
    ServiceInputActivityMixin,
    ServiceInputPipelineMixin,
    ServiceInputVisualMixin,
    ServiceInputCapabilityContextMixin,
    ServiceInputWakePipelineMixin,
    ServiceInputInitiativeMixin,
    ServiceInputWakeObservationMixin,
    ServiceInputWorldStateMixin,
    ServiceInputLoggingMixin,
    ServiceInputTraceMixin,
):
    # 検査API群
    def list_cycle_summaries(self, token: str | None, limit: int) -> dict[str, Any]:
        # 認可
        self._require_token(token)

        # 一覧
        return {
            "cycle_summaries": localize_timestamp_fields(self.store.list_cycle_summaries(limit)),
        }

    def get_cycle_trace(self, token: str | None, cycle_id: str) -> dict[str, Any]:
        # 認可
        self._require_token(token)

        # レコード検索
        trace = self.store.get_cycle_trace(cycle_id)
        if trace is not None:
            return localize_timestamp_fields(trace)

        raise ServiceError(404, "cycle_not_found", "The requested cycle_id does not exist.")

    def register_log_stream_connection(self, websocket: Any) -> str:
        # 結果
        return self._log_stream_registry.add_connection(websocket)

    def remove_log_stream_connection(self, session_id: str) -> None:
        # 削除
        self._log_stream_registry.remove_connection(session_id)

    def _noop_pipeline(
        self,
        *,
        state: dict[str, Any] | None,
        started_at: str,
        reason_summary: str,
    ) -> dict[str, Any]:
        # world_state
        foreground_world_state: list[dict[str, Any]] = []
        if isinstance(state, dict):
            foreground_world_state = (
                self._summarize_foreground_world_states(
                    self._list_current_world_states(
                        state=state,
                        current_time=started_at,
                        limit=WORLD_STATE_FOREGROUND_LIMIT,
                    ),
                    current_time=started_at,
                )
                or []
            )

        # 結果
        return {
            "recall_hint": self._empty_recall_hint(),
            "recall_pack": self._empty_recall_pack(),
            "time_context": self._build_time_context(current_time=started_at),
            "affect_context": {
                "mood_state": {
                    "baseline_vad": {"v": 0.0, "a": 0.0, "d": 0.0},
                    "residual_vad": {"v": 0.0, "a": 0.0, "d": 0.0},
                    "current_vad": {"v": 0.0, "a": 0.0, "d": 0.0},
                    "confidence": 0.0,
                    "observed_at": None,
                    "created_at": None,
                    "updated_at": None,
                },
                "affect_states": [],
                "recent_episode_affects": [],
            },
            "foreground_world_state": foreground_world_state,
            "world_state_trace": self._empty_world_state_trace(
                source_kind=None,
                source_ref=None,
                foreground_world_state=foreground_world_state,
            ),
            "decision": {
                "kind": "noop",
                "reason_code": "wake_noop",
                "reason_summary": reason_summary,
                "requires_confirmation": False,
                "pending_intent": None,
                "capability_request": None,
            },
            "reply_payload": None,
        }

    def _empty_recall_hint(self) -> dict[str, Any]:
        # 結果
        return {
            "primary_recall_focus": "user",
            "secondary_recall_focuses": [],
            "confidence": 0.0,
            "time_reference": "none",
            "focus_scopes": [],
            "mentioned_entities": [],
            "mentioned_topics": [],
            "risk_flags": [],
        }

    def _empty_recall_pack(self) -> dict[str, Any]:
        # 結果
        return {
            "self_model": [],
            "user_model": [],
            "relationship_model": [],
            "active_topics": [],
            "active_commitments": [],
            "episodic_evidence": [],
            "event_evidence": [],
            "visual_observations": [],
            "visual_daily_digests": [],
            "event_evidence_generation": self._empty_event_evidence_generation_trace(),
            "recall_pack_selection": self._empty_recall_pack_selection_trace(),
            "conflicts": [],
            "selected_memory_ids": [],
            "selected_episode_ids": [],
            "association_selected_memory_ids": [],
            "association_selected_episode_ids": [],
            "selected_event_ids": [],
            "memory_link_context": self._empty_memory_link_context_trace(),
            "candidate_count": 0,
            "fact_resolution_trace": self._empty_fact_resolution_trace(),
        }

    def _empty_event_evidence_generation_trace(self) -> dict[str, Any]:
        return {
            "requested_event_count": 0,
            "loaded_event_count": 0,
            "succeeded_event_count": 0,
            "failed_items": [],
            "precise_evidence_used": False,
            "precise_reason_codes": [],
            "precise_reason_summary": None,
            "precise_selected_event_ids": [],
            "precise_requested_event_count": 0,
            "precise_loaded_event_count": 0,
        }

    def _empty_fact_resolution_trace(self) -> dict[str, Any]:
        return {
            "result_status": "summary",
            "resolver_path": "summary",
            "query": {
                "augmented_query_text": None,
                "current_time": None,
                "contract": "summary",
                "boundary": "none",
                "target_actor": "any",
                "reason_codes": [],
                "query_terms": [],
                "requires_direct_evidence": False,
            },
            "selected_recall_sections": {
                "self_model": [],
                "user_model": [],
                "relationship_model": [],
                "active_topics": [],
                "active_commitments": [],
                "episodic_evidence": [],
                "event_evidence": [],
                "conflicts": [],
            },
            "boundary_event_candidates": [],
            "cycle_event_candidates": [],
            "statement_event_candidates": [],
            "conflict_candidates": [],
            "adopted_evidence_items": [],
            "consistency_checks": [],
            "missing_reason": None,
            "reply_guidance": None,
        }

    def _empty_recall_pack_selection_trace(self) -> dict[str, Any]:
        return {
            "candidate_section_counts": {
                "self_model": 0,
                "user_model": 0,
                "relationship_model": 0,
                "active_topics": 0,
                "active_commitments": 0,
                "episodic_evidence": 0,
            },
            "selected_section_order": [],
            "selected_candidate_refs": [],
            "dropped_candidate_refs": [],
            "conflict_summary_count": 0,
            "memory_link_count": 0,
            "memory_link_label_counts": {},
            "memory_link_representative_links": [],
            "result_status": "succeeded",
            "failure_reason": None,
        }

    def _empty_pending_intent_selection_trace(self) -> dict[str, Any]:
        return {
            "candidate_pool_count": 0,
            "eligible_candidate_count": 0,
            "selected_candidate_ref": None,
            "selected_candidate_id": None,
            "selection_reason": None,
            "result_status": "not_requested",
            "failure_reason": None,
        }

    def _build_time_context(self, *, current_time: str) -> dict[str, Any]:
        # タイムスタンプ解析
        current_dt = local_datetime(current_time)

        # 結果
        return {
            "current_time_text": llm_local_time_text(current_time).replace("\n", " / "),
            "weekday": current_dt.strftime("%A").lower(),
            "part_of_day": self._part_of_day(current_dt.hour),
        }

    def _build_affect_context(
        self,
        *,
        state: dict[str, Any],
        recall_hint: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        # クエリ
        mood_state = self.store.get_mood_state(
            memory_set_id=state["selected_memory_set_id"],
            current_time=current_time,
        )
        affect_states = self.store.list_affect_states_for_context(
            memory_set_id=state["selected_memory_set_id"],
            scope_filters=self._build_context_scope_filters(recall_hint),
            limit=3,
        )
        recent_episode_affects = []
        residual_vad = mood_state.get("residual_vad") or {"v": 0.0, "a": 0.0, "d": 0.0}
        residual_strength = max(abs(residual_vad.get("v", 0.0)), abs(residual_vad.get("a", 0.0)), abs(residual_vad.get("d", 0.0)))
        if residual_strength >= 0.15:
            recent_episode_affects = self.store.list_recent_episode_affects_for_context(
                memory_set_id=state["selected_memory_set_id"],
                scope_filters=[("self", "self")],
                limit=2,
            )

        # 結果
        return {
            "mood_state": mood_state,
            "affect_states": [
                {
                    "target_scope_type": record["target_scope_type"],
                    "target_scope_key": record["target_scope_key"],
                    "affect_label": record["affect_label"],
                    "summary_text": record.get("summary_text"),
                    "vad": record.get("vad"),
                    "intensity": record.get("intensity"),
                    "confidence": record.get("confidence"),
                    "updated_at": record.get("updated_at"),
                }
                for record in affect_states
            ],
            "recent_episode_affects": [
                {
                    "target_scope_type": record["target_scope_type"],
                    "target_scope_key": record["target_scope_key"],
                    "affect_label": record["affect_label"],
                    "summary_text": record.get("summary_text"),
                    "vad": record.get("vad"),
                    "intensity": record.get("intensity"),
                    "confidence": record.get("confidence"),
                    "observed_at": record.get("observed_at"),
                }
                for record in recent_episode_affects
            ],
        }


    def _empty_world_state_trace(
        self,
        *,
        source_kind: str | None,
        source_ref: str | None,
        foreground_world_state: list[dict[str, Any]],
    ) -> WorldStateTrace:
        return WorldStateTrace.not_requested(
            source_kind=source_kind,
            source_ref=source_ref,
            foreground_world_state=foreground_world_state,
        )

    def _should_consolidate_spontaneous_cycle(
        self,
        *,
        trigger_kind: str,
        pipeline: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        client_context: dict[str, Any] | None = None,
    ) -> bool:
        if trigger_kind not in {"wake", "background_wake", "capability_result"}:
            return False
        if self._observation_summary_is_vision_capture(observation_summary):
            return False
        if self._client_context_has_visual_wake_observation(client_context):
            return False

        decision = pipeline.get("decision")
        if isinstance(decision, dict):
            decision_kind = decision.get("kind")
            if decision_kind in {"reply", "pending_intent", "capability_request"}:
                return True

        if self._observation_capability_failed(observation_summary):
            return True

        return self._foreground_world_state_changed(pipeline)

    def _observation_capability_failed(self, observation_summary: dict[str, Any] | None) -> bool:
        if not isinstance(observation_summary, dict):
            return False
        error = observation_summary.get("error")
        return isinstance(error, str) and bool(error.strip())

    def _client_context_has_visual_wake_observation(
        self,
        client_context: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(client_context, dict):
            return False
        wake_observations = client_context.get("wake_observations")
        if not isinstance(wake_observations, list):
            return False
        return any(
            isinstance(item, dict)
            and item.get("capability_id") == "vision.capture"
            for item in wake_observations
        )

    def _foreground_world_state_changed(self, pipeline: dict[str, Any]) -> bool:
        if not isinstance(pipeline, dict):
            return False
        world_state_trace = pipeline.get("world_state_trace")
        if not isinstance(world_state_trace, WorldStateTrace):
            return False
        previous = world_state_trace.previous_foreground_world_state or []
        current = pipeline.get("foreground_world_state") or world_state_trace.foreground_world_state or []
        if not previous and not current:
            return False
        return self._foreground_world_state_signature(previous) != self._foreground_world_state_signature(current)

    def _foreground_world_state_signature(self, foreground_world_state: Any) -> str:
        if not isinstance(foreground_world_state, list):
            return "[]"
        signature_items: list[dict[str, Any]] = []
        for summary in foreground_world_state:
            if not isinstance(summary, dict):
                continue
            signature_items.append(
                {
                    "state_type": summary.get("state_type"),
                    "scope": summary.get("scope"),
                    "summary_text": summary.get("summary_text"),
                }
            )
        signature_items.sort(
            key=lambda item: (
                str(item.get("state_type") or ""),
                str(item.get("scope") or ""),
                str(item.get("summary_text") or ""),
            )
        )
        return stable_json(signature_items)

    def _build_context_scope_filters(self, recall_hint: dict[str, Any]) -> list[tuple[str, str]]:
        # 既定値
        filters: list[tuple[str, str]] = [("user", "user"), ("relationship", "self|user")]
        primary_recall_focus = recall_hint["primary_recall_focus"]
        if primary_recall_focus in {"commitment", "user", "relationship"}:
            filters.append(("relationship", "self|user"))

        # focus scope群
        filters.extend(self._parse_focus_scopes(recall_hint.get("focus_scopes", [])))

        # 重複排除
        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for scope_filter in filters:
            if scope_filter in seen:
                continue
            deduped.append(scope_filter)
            seen.add(scope_filter)

        # 結果
        return deduped

    def _parse_focus_scopes(self, scopes: list[Any]) -> list[tuple[str, str]]:
        # 解析
        parsed: list[tuple[str, str]] = []
        for scope in scopes:
            if not isinstance(scope, str):
                continue
            normalized = scope.strip()
            if not normalized:
                continue
            if normalized in {"self", "user"}:
                parsed.append((normalized, normalized))
                continue
            scope_type, separator, scope_key = normalized.partition(":")
            if not separator or not scope_key:
                continue
            if scope_type not in {"relationship", "topic"}:
                continue
            if scope_type == "topic":
                parsed.append((scope_type, normalized))
                continue
            parsed.append((scope_type, scope_key.strip()))

        # 結果
        return parsed

    def _part_of_day(self, hour: int) -> str:
        # 範囲
        if 5 <= hour < 11:
            return "morning"
        if 11 <= hour < 17:
            return "daytime"
        if 17 <= hour < 22:
            return "evening"
        return "night"

    def _load_recent_turns(self, state: dict) -> list[dict]:
        # ウィンドウ設定
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        prompt_window = selected_preset["prompt_window"]
        threshold = local_now() - timedelta(minutes=prompt_window["recent_turn_minutes"])
        turn_limit = prompt_window["recent_turn_limit"]

        # 検索
        return self.store.load_recent_turns(
            memory_set_id=state["selected_memory_set_id"],
            since_iso=threshold.isoformat(),
            limit=turn_limit,
        )

    def _recall_hint_recent_turns(self, recent_turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # RecallHint は入口判断なので prompt_window 候補をさらに軽くする。
        return recent_turns[-RECALL_HINT_RECENT_TURN_LIMIT:]

    def _begin_user_response_cycle(self) -> None:
        # ユーザー向け応答中は background wake の外向き発話を止める。
        with self._runtime_state_lock:
            count = self._wake_runtime_state.get("active_user_response_cycle_count")
            if not isinstance(count, int) or count < 0:
                count = 0
            self._wake_runtime_state["active_user_response_cycle_count"] = count + 1

    def _end_user_response_cycle(self) -> None:
        # カウンタ
        with self._runtime_state_lock:
            count = self._wake_runtime_state.get("active_user_response_cycle_count")
            if not isinstance(count, int) or count <= 0:
                self._wake_runtime_state["active_user_response_cycle_count"] = 0
                return
            self._wake_runtime_state["active_user_response_cycle_count"] = count - 1

    def _user_response_cycle_active(self) -> bool:
        # 状態
        with self._runtime_state_lock:
            count = self._wake_runtime_state.get("active_user_response_cycle_count")
        return isinstance(count, int) and count > 0

    def _recent_turns_added_since(self, *, state: dict[str, Any], started_at: str) -> bool:
        # wake 開始後に会話 turn が追加された場合、開始時 snapshot は古い。
        for turn in self._load_recent_turns(state):
            created_at = turn.get("created_at") if isinstance(turn, dict) else None
            if isinstance(created_at, str) and created_at > started_at:
                return True
        return False

    def _new_console_token(self) -> str:
        # トークン
        return f"tok_{secrets.token_urlsafe(24)}"

    def _new_cycle_id(self) -> str:
        # 識別子
        return f"cycle:{uuid.uuid4().hex}"

    def _now_iso(self) -> str:
        # タイムスタンプ
        return now_iso()

    def _parse_iso(self, value: str) -> datetime:
        # タイムスタンプ
        return local_datetime(value)

    def _duration_ms(self, started_at: str, finished_at: str) -> int:
        # 期間
        started = self._parse_iso(started_at)
        finished = self._parse_iso(finished_at)
        return max(int((finished - started).total_seconds() * 1000), 0)

    def _clamp(self, value: str | None, limit: int = 160) -> str | None:
        # 範囲制限
        if value is None:
            return None
        stripped = value.strip()
        if len(stripped) <= limit:
            return stripped
        return stripped[: limit - 1] + "…"
