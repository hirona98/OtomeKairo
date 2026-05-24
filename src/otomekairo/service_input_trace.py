from __future__ import annotations

import uuid
from typing import Any

from otomekairo.capabilities import capability_manifests
from otomekairo.llm_contexts import InitiativeContext
from otomekairo.service_capability import CapabilityDispatchError
from otomekairo.world_state_models import WorldStateTrace


class ServiceInputTraceMixin:
    def _complete_input_success(
        self,
        *,
        cycle_id: str,
        started_at: str,
        state: dict[str, Any],
        runtime_summary: dict[str, Any],
        input_text: str,
        client_context: dict[str, Any],
        pipeline: dict[str, Any],
        trigger_kind: str = "user_message",
        input_event_kind: str = "conversation_input",
        input_event_role: str = "user",
        consolidate_memory: bool = True,
        pending_intent_selection: dict[str, Any] | None = None,
        observation_summary: dict[str, Any] | None = None,
        capability_request_summary: dict[str, Any] | None = None,
        ongoing_action_transition_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # 結果選択
        decision = pipeline["decision"]
        reply_payload = pipeline["reply_payload"]
        if capability_request_summary is None:
            candidate_summary = pipeline.get("capability_request_summary")
            if isinstance(candidate_summary, dict):
                capability_request_summary = candidate_summary
        if ongoing_action_transition_summary is None:
            candidate_transition = pipeline.get("ongoing_action_transition_summary")
            if isinstance(candidate_transition, dict):
                ongoing_action_transition_summary = candidate_transition
        followup_capability_request_summary = pipeline.get("capability_request_summary")
        if not isinstance(followup_capability_request_summary, dict):
            followup_capability_request_summary = None
        internal_result_kind = decision["kind"]
        result_kind = self._external_result_kind(internal_result_kind)
        finished_at = self._now_iso()
        pending_intent_summary = self._apply_pending_intent_candidate(
            cycle_id=cycle_id,
            memory_set_id=state["selected_memory_set_id"],
            decision=decision,
            occurred_at=finished_at,
        )

        # 永続化
        events = self._persist_cycle_success(
            cycle_id=cycle_id,
            started_at=started_at,
            finished_at=finished_at,
            state=state,
            runtime_summary=runtime_summary,
            input_text=input_text,
            augmented_query_text=pipeline.get("augmented_query_text"),
            client_context=client_context,
            recall_hint=pipeline["recall_hint"],
            recall_pack=pipeline["recall_pack"],
            time_context=pipeline["time_context"],
            affect_context=pipeline["affect_context"],
            drive_state_summary=pipeline.get("drive_state_summary"),
            foreground_world_state=pipeline.get("foreground_world_state"),
            ongoing_action_summary=pipeline.get("ongoing_action_summary"),
            decision=decision,
            result_kind=result_kind,
            reply_payload=reply_payload,
            pending_intent_summary=pending_intent_summary,
            capability_decision_view=pipeline.get("capability_decision_view"),
            initiative_context=pipeline.get("initiative_context"),
            capability_result_context=pipeline.get("capability_result_context"),
            visual_observation_context=pipeline.get("visual_observation_context"),
            world_state_trace=pipeline.get("world_state_trace"),
            trigger_kind=trigger_kind,
            input_event_kind=input_event_kind,
            input_event_role=input_event_role,
            pending_intent_selection=pending_intent_selection,
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
            followup_capability_request_summary=followup_capability_request_summary,
            ongoing_action_transition_summary=ongoing_action_transition_summary,
        )

        # デバッグログ群
        self._emit_input_success_logs(
            cycle_id=cycle_id,
            trigger_kind=trigger_kind,
            input_text=input_text,
            pipeline=pipeline,
            result_kind=result_kind,
            reply_payload=reply_payload,
            pending_intent_selection=pending_intent_selection,
        )

        # memory trace更新
        if consolidate_memory:
            self._finalize_memory_trace(
                cycle_id=cycle_id,
                finished_at=finished_at,
                state=state,
                input_text=input_text,
                events=events,
                pipeline=pipeline,
                trigger_kind=trigger_kind,
                input_event_kind=input_event_kind,
                input_event_role=input_event_role,
                pending_intent_summary=pending_intent_summary,
                pending_intent_selection=pending_intent_selection,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                followup_capability_request_summary=followup_capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            )
        else:
            skipped_memory_trace = self._skipped_memory_trace(f"{trigger_kind}_cycle")
            self._update_cycle_trace_memory_trace(
                cycle_id=cycle_id,
                memory_trace=skipped_memory_trace,
            )
            self._emit_memory_trace_logs(
                cycle_id=cycle_id,
                memory_trace=skipped_memory_trace,
            )

        # 応答
        return {
            "cycle_id": cycle_id,
            "result_kind": result_kind,
            "reply": {"text": reply_payload["reply_text"]} if reply_payload else None,
            "capability_request": capability_request_summary if isinstance(capability_request_summary, dict) else None,
        }

    def _summarize_affect_context(self, affect_context: dict[str, Any]) -> dict[str, Any]:
        # mood
        mood_state = affect_context.get("mood_state") or {}
        affect_states = affect_context.get("affect_states", [])
        recent_episode_affects = affect_context.get("recent_episode_affects", [])

        # 結果
        return {
            "mood_current_vad": mood_state.get("current_vad"),
            "mood_confidence": mood_state.get("confidence"),
            "affect_state_count": len(affect_states),
            "affect_state_labels": [
                item["affect_label"]
                for item in affect_states
                if isinstance(item, dict) and isinstance(item.get("affect_label"), str)
            ],
            "recent_episode_affect_count": len(recent_episode_affects),
            "recent_episode_affect_labels": [
                item["affect_label"]
                for item in recent_episode_affects
                if isinstance(item, dict) and isinstance(item.get("affect_label"), str)
            ],
        }

    def _recall_adopted_reason_summary(self, recall_pack: dict[str, Any]) -> str:
        # 件数群
        memory_count = len(recall_pack["selected_memory_ids"])
        episode_count = len(recall_pack["selected_episode_ids"])
        association_memory_count = len(recall_pack["association_selected_memory_ids"])
        association_episode_count = len(recall_pack["association_selected_episode_ids"])
        recall_pack_selection = recall_pack.get("recall_pack_selection", {})
        selected_sections = recall_pack_selection.get("selected_section_order", [])
        selected_sections_summary = ",".join(selected_sections) if isinstance(selected_sections, list) else ""

        # 空
        if memory_count == 0 and episode_count == 0:
            return "構造レーンで採用候補は選ばれなかった。"

        # 関連のみ
        if memory_count == association_memory_count and episode_count == association_episode_count:
            return (
                "連想レーンで近傍候補を補助採用し、recall_pack_selection が意味的に最終選別した。"
                f" sections={selected_sections_summary or '-'}"
                f" association_memory_units={association_memory_count}, association_episodes={association_episode_count}"
            )

        # 混在
        if association_memory_count > 0 or association_episode_count > 0:
            return (
                "構造レーンを主軸にしつつ、連想レーンの近傍候補を補助採用し、recall_pack_selection が意味的に最終選別した。"
                f" sections={selected_sections_summary or '-'}"
                f" memory_units={memory_count}, episodes={episode_count},"
                f" association_memory_units={association_memory_count}, association_episodes={association_episode_count}"
            )

        # 要約
        return (
            "構造レーンで候補を集め、recall_pack_selection が意味的に最終選別した。"
            f" sections={selected_sections_summary or '-'}"
            f" memory_units={memory_count}, episodes={episode_count}"
        )

    def _recall_rejected_reason_summary(self, recall_pack: dict[str, Any]) -> str:
        # 空
        if recall_pack["candidate_count"] == 0:
            return "現時点では構造レーンにも連想レーンにも一致する長期記憶がなかった。"

        # selection
        recall_pack_selection = recall_pack.get("recall_pack_selection", {})
        dropped_candidate_refs = recall_pack_selection.get("dropped_candidate_refs", [])
        if isinstance(dropped_candidate_refs, list) and dropped_candidate_refs:
            return "候補収集後に recall_pack_selection と deterministic 制約で一部候補を落とした。"

        # 関連
        if recall_pack["association_selected_memory_ids"] or recall_pack["association_selected_episode_ids"]:
            return "候補収集後に recall_pack_selection で採否を絞り、vector-only 候補は補助扱いに留めた。"

        # 要約
        return "候補収集後に recall_pack_selection で採否を絞り、件数上限と dedupe を優先した。"

    def _external_result_kind(self, internal_result_kind: str) -> str:
        # マッピング
        if internal_result_kind == "pending_intent":
            return "noop"
        return internal_result_kind

    def _build_cycle_events(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        input_event_kind: str,
        input_event_role: str,
        input_text: str,
        started_at: str,
        finished_at: str,
        decision: dict[str, Any] | None = None,
        result_kind: str | None = None,
        reply_payload: dict[str, Any] | None = None,
        pending_intent_summary: dict[str, Any] | None = None,
        failure_reason: str | None = None,
        failure_event_kind: str = "recall_hint_failure",
        failure_event_payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        # 入力イベント
        events = [
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
                "memory_set_id": memory_set_id,
                "kind": input_event_kind,
                "role": input_event_role,
                "text": input_text,
                "created_at": started_at,
            }
        ]

        # 失敗イベント
        if failure_reason is not None:
            payload = {
                "failure_reason": failure_reason,
            }
            if isinstance(failure_event_payload, dict):
                payload.update(failure_event_payload)
            events.append(
                {
                    "event_id": f"event:{uuid.uuid4().hex}",
                    "cycle_id": cycle_id,
                    "memory_set_id": memory_set_id,
                    "kind": failure_event_kind,
                    "role": "system",
                    "created_at": finished_at,
                    **payload,
                }
            )
            return events

        # 決定イベント
        if decision is None or result_kind is None:
            raise ValueError("decision and result_kind are required for success events.")
        events.append(
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": cycle_id,
                "memory_set_id": memory_set_id,
                "kind": "decision",
                "role": "system",
                "result_kind": decision["kind"],
                "external_result_kind": result_kind,
                "reason_code": decision["reason_code"],
                "reason_summary": decision["reason_summary"],
                "pending_intent_summary": pending_intent_summary,
                "created_at": finished_at,
            }
        )

        # 応答イベント
        if reply_payload is not None:
            events.append(
                {
                    "event_id": f"event:{uuid.uuid4().hex}",
                    "cycle_id": cycle_id,
                    "memory_set_id": memory_set_id,
                    "kind": "reply",
                    "role": "assistant",
                    "text": reply_payload["reply_text"],
                    "created_at": finished_at,
                }
            )
        return events

    def _build_retrieval_run_success(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        started_at: str,
        finished_at: str,
        recall_hint: dict[str, Any],
        recall_pack: dict[str, Any],
    ) -> dict[str, Any]:
        event_evidence_generation = recall_pack.get("event_evidence_generation", {})
        recall_pack_selection = recall_pack.get("recall_pack_selection", {})
        return {
            "cycle_id": cycle_id,
            "selected_memory_set_id": memory_set_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "result_status": "succeeded",
            "recall_hint": recall_hint,
            "selected_episode_ids": recall_pack["selected_episode_ids"],
            "selected_event_ids": recall_pack["selected_event_ids"],
            "recall_pack_summary": self._summarize_recall_pack(recall_pack),
            "candidate_count": recall_pack["candidate_count"],
            "selected_memory_ids": recall_pack["selected_memory_ids"],
            "memory_link_context": self._summarize_memory_link_context(
                recall_pack.get("memory_link_context")
            ),
            "event_evidence_generation": {
                "requested_event_count": int(event_evidence_generation.get("requested_event_count", 0)),
                "loaded_event_count": int(event_evidence_generation.get("loaded_event_count", 0)),
                "succeeded_event_count": int(event_evidence_generation.get("succeeded_event_count", 0)),
                "failed_count": len(event_evidence_generation.get("failed_items", [])),
                "precise_evidence_used": bool(event_evidence_generation.get("precise_evidence_used", False)),
                "precise_selected_event_ids": event_evidence_generation.get("precise_selected_event_ids", []),
                "precise_requested_event_count": int(
                    event_evidence_generation.get("precise_requested_event_count", 0)
                ),
                "precise_loaded_event_count": int(
                    event_evidence_generation.get("precise_loaded_event_count", 0)
                ),
                "precise_reason_summary": event_evidence_generation.get("precise_reason_summary"),
            },
            "recall_pack_selection": {
                "result_status": str(recall_pack_selection.get("result_status", "succeeded")),
                "selected_section_order": recall_pack_selection.get("selected_section_order", []),
                "selected_candidate_count": len(recall_pack_selection.get("selected_candidate_refs", [])),
                "dropped_candidate_count": len(recall_pack_selection.get("dropped_candidate_refs", [])),
                "memory_link_count": int(recall_pack_selection.get("memory_link_count", 0) or 0),
                "memory_link_label_counts": recall_pack_selection.get("memory_link_label_counts", {}),
                "memory_link_representative_links": recall_pack_selection.get(
                    "memory_link_representative_links",
                    [],
                ),
            },
        }

    def _build_retrieval_run_failure(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        started_at: str,
        finished_at: str,
        failure_reason: str,
    ) -> dict[str, Any]:
        return {
            "cycle_id": cycle_id,
            "selected_memory_set_id": memory_set_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "result_status": "failed",
            "failure_reason": failure_reason,
            "selected_episode_ids": [],
            "selected_event_ids": [],
            "recall_pack_summary": None,
        }

    def _build_cycle_summary(
        self,
        *,
        cycle_id: str,
        started_at: str,
        finished_at: str,
        state: dict[str, Any],
        trigger_kind: str,
        result_kind: str,
        failed: bool,
    ) -> dict[str, Any]:
        return {
            "cycle_id": cycle_id,
            "server_id": state["server_id"],
            "trigger_kind": trigger_kind,
            "started_at": started_at,
            "finished_at": finished_at,
            "selected_persona_id": state["selected_persona_id"],
            "selected_memory_set_id": state["selected_memory_set_id"],
            "selected_model_preset_id": state["selected_model_preset_id"],
            "result_kind": result_kind,
            "failed": failed,
        }

    def _build_cycle_trace(
        self,
        *,
        cycle_id: str,
        cycle_summary: dict[str, Any],
        input_text: str,
        augmented_query_text: str | None,
        client_context: dict[str, Any],
        runtime_summary: dict[str, Any],
        foreground_world_state: list[dict[str, Any]] | None,
        recall_trace: dict[str, Any],
        decision_trace: dict[str, Any],
        world_state_trace: WorldStateTrace | None,
        result_trace: dict[str, Any],
        memory_trace: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None = None,
        observation_summary: dict[str, Any] | None = None,
        ongoing_action_summary: dict[str, Any] | None = None,
        initiative_context: InitiativeContext | None = None,
    ) -> dict[str, Any]:
        input_trace = {
            "trigger_kind": cycle_summary["trigger_kind"],
            "input_summary": self._clamp(input_text),
            "client_context_summary": self._clamp(str(client_context)),
            "normalized_input_summary": self._clamp(input_text.strip()),
            "runtime_state_summary": runtime_summary,
            "pending_intent_selection": pending_intent_selection or self._empty_pending_intent_selection_trace(),
        }
        input_context_addition_summary = self._input_context_addition_summary(
            input_text=input_text,
            augmented_query_text=augmented_query_text,
        )
        if input_context_addition_summary is not None:
            input_trace["input_context_addition_summary"] = input_context_addition_summary
            input_trace["augmented_query_summary"] = self._clamp(str(augmented_query_text or ""))
        if foreground_world_state:
            input_trace["foreground_world_state"] = foreground_world_state
        wake_observation_summary = self._client_context_text(
            client_context.get("wake_observation_summary"),
            limit=360,
        )
        if isinstance(wake_observation_summary, str):
            input_trace["wake_observation_summary"] = wake_observation_summary
        compact_wake_observations = self._compact_wake_observations(
            client_context.get("wake_observations")
        )
        if compact_wake_observations:
            input_trace["wake_observations"] = compact_wake_observations
        if isinstance(observation_summary, dict):
            input_trace["observation_summary"] = observation_summary
        if isinstance(ongoing_action_summary, dict):
            input_trace["ongoing_action_summary"] = ongoing_action_summary
        if initiative_context is not None:
            input_trace["initiative_context"] = self._compact_initiative_context_summary(
                initiative_context=initiative_context,
                pending_intent_selection=pending_intent_selection,
            )
        return {
            "cycle_id": cycle_id,
            "cycle_summary": cycle_summary,
            "input_trace": input_trace,
            "recall_trace": recall_trace,
            "decision_trace": decision_trace,
            "world_state_trace": world_state_trace.to_trace_payload() if world_state_trace is not None else {},
            "result_trace": result_trace,
            "memory_trace": memory_trace or {},
        }

    def _build_success_recall_trace(self, recall_hint: dict[str, Any], recall_pack: dict[str, Any]) -> dict[str, Any]:
        recall_pack_summary = self._summarize_recall_pack(recall_pack)
        trace = {
            "recall_hint_summary": recall_hint,
            "candidate_count": recall_pack["candidate_count"],
            "selected_memory_unit_ids": recall_pack["selected_memory_ids"],
            "selected_episode_ids": recall_pack["selected_episode_ids"],
            "selected_event_ids": recall_pack["selected_event_ids"],
            "event_evidence_generation": recall_pack.get(
                "event_evidence_generation",
                self._empty_event_evidence_generation_trace(),
            ),
            "memory_link_context": self._summarize_memory_link_context(
                recall_pack.get("memory_link_context")
            ),
            "recall_pack_selection": recall_pack.get(
                "recall_pack_selection",
                self._empty_recall_pack_selection_trace(),
            ),
            "recall_pack_summary": recall_pack_summary,
            "adopted_reason_summary": self._recall_adopted_reason_summary(recall_pack),
            "rejected_candidate_summary": self._recall_rejected_reason_summary(recall_pack),
        }
        if isinstance(recall_pack.get("answer_contract"), dict):
            trace["answer_contract"] = recall_pack["answer_contract"]
        if isinstance(recall_pack.get("evidence_pack"), dict):
            trace["evidence_pack"] = recall_pack["evidence_pack"]
        if isinstance(recall_pack.get("fact_resolution_trace"), dict):
            trace["fact_resolution_trace"] = recall_pack["fact_resolution_trace"]
        else:
            trace["fact_resolution_trace"] = self._empty_fact_resolution_trace()
        return trace

    def _build_failure_recall_trace(
        self,
        *,
        recall_hint: dict[str, Any] | None = None,
        recall_pack_selection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "recall_hint_summary": recall_hint,
            "candidate_count": 0,
            "selected_memory_unit_ids": [],
            "selected_episode_ids": [],
            "selected_event_ids": [],
            "event_evidence_generation": self._empty_event_evidence_generation_trace(),
            "memory_link_context": self._empty_memory_link_context_trace(),
            "recall_pack_selection": recall_pack_selection or self._empty_recall_pack_selection_trace(),
            "recall_pack_summary": None,
            "adopted_reason_summary": None,
            "rejected_candidate_summary": None,
            "fact_resolution_trace": self._empty_fact_resolution_trace(),
        }

    def _build_success_decision_trace(
        self,
        *,
        state: dict[str, Any],
        input_text: str,
        augmented_query_text: str | None,
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        initiative_context: InitiativeContext | None,
        capability_result_context: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        recall_pack: dict[str, Any],
        decision: dict[str, Any],
        pending_intent_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        trace = {
            "result_kind": decision["kind"],
            "reason_summary": decision["reason_summary"],
            "persona_summary": state["personas"][state["selected_persona_id"]]["display_name"],
            "memory_summary": state["memory_sets"][state["selected_memory_set_id"]]["display_name"],
            "current_context_summary": self._clamp(input_text),
            "internal_context_summary": {
                "time_context": time_context,
                "affect_context_summary": self._summarize_affect_context(affect_context),
                "drive_state_summary": drive_state_summary,
                "foreground_world_state": foreground_world_state,
                "ongoing_action_summary": ongoing_action_summary,
                "capability_decision_view": capability_decision_view,
                "initiative_context": initiative_context.to_prompt_payload() if initiative_context is not None else None,
                "capability_result_context": capability_result_context,
                "visual_observation_context": visual_observation_context,
                "recall_pack_summary": self._summarize_recall_pack(recall_pack),
                "memory_link_context": self._summarize_memory_link_context(
                    recall_pack.get("memory_link_context")
                ),
            },
            "primary_candidate_kind": decision["kind"],
            "pending_intent_candidate_summary": pending_intent_summary,
            "capability_request_candidate_summary": self._decision_capability_request_summary(decision),
        }
        input_context_addition_summary = self._input_context_addition_summary(
            input_text=input_text,
            augmented_query_text=augmented_query_text,
        )
        if input_context_addition_summary is not None:
            trace["input_context_addition_summary"] = input_context_addition_summary
            trace["augmented_query_summary"] = self._clamp(str(augmented_query_text or ""))
        if drive_state_summary:
            trace["drive_state_summary"] = drive_state_summary
        if isinstance(ongoing_action_summary, dict):
            trace["ongoing_action_summary"] = ongoing_action_summary
        if isinstance(capability_result_context, dict):
            trace["capability_result_context"] = capability_result_context
        return trace

    def _decision_capability_request_summary(self, decision: dict[str, Any]) -> dict[str, Any] | None:
        capability_request = decision.get("capability_request")
        if not isinstance(capability_request, dict):
            return None
        capability_id = capability_request.get("capability_id")
        input_payload = capability_request.get("input")
        if not isinstance(capability_id, str) or not isinstance(input_payload, dict):
            return None
        return {
            "capability_id": capability_id,
            "input": input_payload,
        }

    def _input_context_addition_summary(
        self,
        *,
        input_text: str,
        augmented_query_text: str | None,
    ) -> str | None:
        if not isinstance(augmented_query_text, str):
            return None
        original_text = input_text.strip()
        augmented_text = augmented_query_text.strip()
        if not augmented_text or augmented_text == original_text:
            return None
        addition_text = augmented_text
        if original_text and augmented_text.startswith(original_text):
            addition_text = augmented_text[len(original_text) :].strip()
        if not addition_text:
            return None
        return self._clamp(addition_text)

    def _build_failure_decision_trace(
        self,
        *,
        state: dict[str, Any],
        input_text: str,
        failure_reason: str,
        drive_state_summary: list[dict[str, Any]] | None = None,
        ongoing_action_summary: dict[str, Any] | None = None,
        capability_decision_view: list[dict[str, Any]] | None = None,
        initiative_context: InitiativeContext | None = None,
    ) -> dict[str, Any]:
        trace = {
            "result_kind": "internal_failure",
            "reason_summary": failure_reason,
            "persona_summary": state["personas"][state["selected_persona_id"]]["display_name"],
            "memory_summary": state["memory_sets"][state["selected_memory_set_id"]]["display_name"],
            "current_context_summary": self._clamp(input_text),
            "primary_candidate_kind": None,
        }
        if capability_decision_view or initiative_context:
            trace["internal_context_summary"] = {
                "capability_decision_view": capability_decision_view,
                "initiative_context": initiative_context.to_prompt_payload() if initiative_context is not None else None,
            }
        if drive_state_summary:
            trace["drive_state_summary"] = drive_state_summary
        if isinstance(ongoing_action_summary, dict):
            trace["ongoing_action_summary"] = ongoing_action_summary
        return trace

    def _build_success_result_trace(
        self,
        *,
        trigger_kind: str,
        input_text: str,
        started_at: str,
        finished_at: str,
        decision: dict[str, Any],
        result_kind: str,
        reply_payload: dict[str, Any] | None,
        pending_intent_summary: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None = None,
        initiative_context: InitiativeContext | None = None,
        observation_summary: dict[str, Any] | None = None,
        capability_request_summary: dict[str, Any] | None = None,
        followup_capability_request_summary: dict[str, Any] | None = None,
        ongoing_action_transition_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trace = {
            "result_kind": result_kind,
            "reply_summary": self._clamp(reply_payload["reply_text"]) if reply_payload else None,
            "noop_reason_summary": decision["reason_summary"] if decision["kind"] == "noop" else None,
            "pending_intent_summary": pending_intent_summary,
            "internal_failure_summary": None,
            "duration_ms": self._duration_ms(started_at, finished_at),
        }
        if isinstance(capability_request_summary, dict):
            trace["capability_request_summary"] = capability_request_summary
        if isinstance(ongoing_action_transition_summary, dict):
            trace["ongoing_action_transition_summary"] = ongoing_action_transition_summary
        trace["trigger_compact_summary"] = self._build_trigger_compact_summary(
            trigger_kind=trigger_kind,
            input_text=input_text,
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
            followup_capability_request_summary=followup_capability_request_summary,
            decision=decision,
            result_kind=result_kind,
            reply_payload=reply_payload,
            pending_intent_summary=pending_intent_summary,
            pending_intent_selection=pending_intent_selection,
            initiative_context=initiative_context,
            ongoing_action_transition_summary=ongoing_action_transition_summary,
        )
        capability_dispatch_summary = self._build_capability_dispatch_summary(
            trigger_kind=trigger_kind,
            capability_request_summary=capability_request_summary,
            followup_capability_request_summary=followup_capability_request_summary,
            decision=decision,
            ongoing_action_transition_summary=ongoing_action_transition_summary,
        )
        if isinstance(capability_dispatch_summary, dict):
            trace["capability_dispatch_summary"] = capability_dispatch_summary
        capability_result_followup_summary = self._build_capability_result_followup_summary(
            trigger_kind=trigger_kind,
            observation_summary=observation_summary,
            source_capability_request_summary=capability_request_summary,
            followup_capability_request_summary=followup_capability_request_summary,
            decision=decision,
            result_kind=result_kind,
            reply_payload=reply_payload,
            pending_intent_summary=pending_intent_summary,
            ongoing_action_transition_summary=ongoing_action_transition_summary,
        )
        if isinstance(capability_result_followup_summary, dict):
            trace["capability_result_followup_summary"] = capability_result_followup_summary
        return trace

    def _build_failure_result_trace(
        self,
        *,
        trigger_kind: str,
        input_text: str,
        started_at: str,
        finished_at: str,
        failure_reason: str,
        pending_intent_selection: dict[str, Any] | None = None,
        initiative_context: InitiativeContext | None = None,
        observation_summary: dict[str, Any] | None = None,
        capability_request_summary: dict[str, Any] | None = None,
        followup_capability_request_summary: dict[str, Any] | None = None,
        ongoing_action_transition_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trace = {
            "result_kind": "internal_failure",
            "reply_summary": None,
            "noop_reason_summary": None,
            "pending_intent_summary": None,
            "internal_failure_summary": failure_reason,
            "duration_ms": self._duration_ms(started_at, finished_at),
        }
        if isinstance(capability_request_summary, dict):
            trace["capability_request_summary"] = capability_request_summary
        if isinstance(ongoing_action_transition_summary, dict):
            trace["ongoing_action_transition_summary"] = ongoing_action_transition_summary
        trace["trigger_compact_summary"] = self._build_trigger_compact_summary(
            trigger_kind=trigger_kind,
            input_text=input_text,
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
            followup_capability_request_summary=followup_capability_request_summary,
            decision=None,
            result_kind="internal_failure",
            reply_payload=None,
            pending_intent_summary=None,
            pending_intent_selection=pending_intent_selection,
            initiative_context=initiative_context,
            ongoing_action_transition_summary=ongoing_action_transition_summary,
            failure_reason=failure_reason,
        )
        capability_dispatch_summary = self._build_capability_dispatch_summary(
            trigger_kind=trigger_kind,
            capability_request_summary=capability_request_summary,
            followup_capability_request_summary=followup_capability_request_summary,
            decision=None,
            ongoing_action_transition_summary=ongoing_action_transition_summary,
        )
        if isinstance(capability_dispatch_summary, dict):
            trace["capability_dispatch_summary"] = capability_dispatch_summary
        capability_result_followup_summary = self._build_capability_result_followup_summary(
            trigger_kind=trigger_kind,
            observation_summary=observation_summary,
            source_capability_request_summary=capability_request_summary,
            followup_capability_request_summary=followup_capability_request_summary,
            decision=None,
            result_kind="internal_failure",
            reply_payload=None,
            pending_intent_summary=None,
            ongoing_action_transition_summary=ongoing_action_transition_summary,
            failure_reason=failure_reason,
        )
        if isinstance(capability_result_followup_summary, dict):
            trace["capability_result_followup_summary"] = capability_result_followup_summary
        return trace

    def _build_trigger_compact_summary(
        self,
        *,
        trigger_kind: str,
        input_text: str,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
        followup_capability_request_summary: dict[str, Any] | None,
        decision: dict[str, Any] | None,
        result_kind: str,
        reply_payload: dict[str, Any] | None,
        pending_intent_summary: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None,
        initiative_context: InitiativeContext | None,
        ongoing_action_transition_summary: dict[str, Any] | None,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        dispatch_request_summary = (
            followup_capability_request_summary
            if trigger_kind == "capability_result"
            else capability_request_summary
        )
        return {
            "trigger_kind": trigger_kind,
            "trigger_family": self._trigger_compact_family(trigger_kind),
            "entry_summary": self._build_trigger_compact_entry_summary(
                trigger_kind=trigger_kind,
                input_text=input_text,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                pending_intent_selection=pending_intent_selection,
                initiative_context=initiative_context,
            ),
            "decision_summary": self._compact_capability_followup_decision_summary(decision),
            "result_summary": self._compact_trigger_result_summary(
                result_kind=result_kind,
                reply_payload=reply_payload,
                pending_intent_summary=pending_intent_summary,
                capability_request_summary=dispatch_request_summary,
                failure_reason=failure_reason,
            ),
            "transition_summary": self._compact_capability_followup_transition_summary(
                ongoing_action_transition_summary,
            ),
        }

    def _trigger_compact_family(self, trigger_kind: str) -> str:
        if trigger_kind == "capability_result":
            return "capability_result_followup"
        if trigger_kind in {"wake", "background_wake"}:
            return "initiative"
        if trigger_kind == "user_message":
            return "conversation"
        return "system"

    def _build_trigger_compact_entry_summary(
        self,
        *,
        trigger_kind: str,
        input_text: str,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None,
        initiative_context: InitiativeContext | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        normalized_input = self._clamp(input_text.strip(), limit=160)
        if normalized_input is not None:
            payload["input_summary"] = normalized_input
        compact_observation_summary = self._compact_capability_followup_observation_summary(observation_summary)
        if trigger_kind == "capability_result":
            payload["source_request_summary"] = self._compact_capability_request_summary(capability_request_summary)
            payload["observation_summary"] = compact_observation_summary
            return payload
        if trigger_kind in {"wake", "background_wake"}:
            payload.update(
                self._compact_initiative_entry_summary(
                    initiative_context=initiative_context,
                    pending_intent_selection=pending_intent_selection,
                )
            )
            if isinstance(compact_observation_summary, dict):
                payload["observation_summary"] = compact_observation_summary
            return payload
        if isinstance(compact_observation_summary, dict):
            payload["observation_summary"] = compact_observation_summary
        return payload

    def _compact_initiative_entry_summary(
        self,
        *,
        initiative_context: InitiativeContext | None,
        pending_intent_selection: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return self._compact_initiative_context_summary(
            initiative_context=initiative_context,
            pending_intent_selection=pending_intent_selection,
        )

    def _compact_initiative_context_summary(
        self,
        *,
        initiative_context: InitiativeContext | None,
        pending_intent_selection: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        initiative_payload = initiative_context.to_prompt_payload() if initiative_context is not None else None
        if isinstance(initiative_payload, dict):
            trigger_kind = initiative_payload.get("trigger_kind")
            if isinstance(trigger_kind, str) and trigger_kind.strip():
                payload["trigger_kind"] = trigger_kind.strip()
            opportunity_summary = initiative_payload.get("opportunity_summary")
            if isinstance(opportunity_summary, str) and opportunity_summary.strip():
                payload["opportunity_summary"] = self._clamp(opportunity_summary.strip(), limit=160)
            time_context_summary = initiative_payload.get("time_context_summary")
            if isinstance(time_context_summary, dict):
                compact_time_context: dict[str, Any] = {}
                for key in ("current_time_text", "part_of_day", "weekday", "time_band_summary"):
                    value = time_context_summary.get(key)
                    if isinstance(value, str) and value.strip():
                        compact_time_context[key] = self._clamp(value.strip(), limit=120)
                if compact_time_context:
                    payload["time_context_summary"] = compact_time_context
            foreground_signal_summary = initiative_payload.get("foreground_signal_summary")
            if isinstance(foreground_signal_summary, dict):
                compact_foreground_signal: dict[str, Any] = {}
                for key in ("foreground_thinness", "reason_summary"):
                    value = foreground_signal_summary.get(key)
                    if isinstance(value, str) and value.strip():
                        compact_foreground_signal[key] = self._clamp(value.strip(), limit=120)
                world_state_count = foreground_signal_summary.get("world_state_count")
                if isinstance(world_state_count, int):
                    compact_foreground_signal["world_state_count"] = world_state_count
                state_types = foreground_signal_summary.get("state_types")
                if isinstance(state_types, list):
                    compact_foreground_signal["state_types"] = [
                        value.strip()
                        for value in state_types
                        if isinstance(value, str) and value.strip()
                    ][:4]
                desktop_signal = self._compact_desktop_observation_signal(
                    foreground_signal_summary.get("desktop_observation_signal")
                )
                if desktop_signal:
                    compact_foreground_signal["desktop_observation_signal"] = desktop_signal
                if compact_foreground_signal:
                    payload["foreground_signal_summary"] = compact_foreground_signal
            selected_candidate_family = initiative_payload.get("selected_candidate_family")
            if isinstance(selected_candidate_family, str) and selected_candidate_family.strip():
                payload["selected_candidate_family"] = selected_candidate_family.strip()
            initiative_baseline = initiative_payload.get("initiative_baseline")
            if isinstance(initiative_baseline, dict):
                baseline_level = initiative_baseline.get("level")
                if isinstance(baseline_level, str) and baseline_level.strip():
                    payload["initiative_baseline"] = baseline_level.strip()
            compact_pending_intent_summaries = self._compact_initiative_pending_intent_summaries(
                initiative_payload.get("pending_intent_summaries")
            )
            if compact_pending_intent_summaries:
                payload["pending_intent_summaries"] = compact_pending_intent_summaries
            compact_candidate_families = self._compact_initiative_candidate_families(
                initiative_payload.get("candidate_families")
            )
            if compact_candidate_families:
                payload["candidate_families"] = compact_candidate_families
            runtime_state_summary = initiative_payload.get("runtime_state_summary")
            if isinstance(runtime_state_summary, dict):
                payload["runtime_state_summary"] = {
                    "wake_scheduler_active": runtime_state_summary.get("wake_scheduler_active"),
                    "ongoing_action_exists": runtime_state_summary.get("ongoing_action_exists"),
                    "pending_memory_job_count": runtime_state_summary.get("pending_memory_job_count"),
                }
            compact_drive_summaries = self._compact_initiative_drive_summaries(
                initiative_payload.get("drive_summaries")
            )
            if compact_drive_summaries:
                payload["drive_summaries"] = compact_drive_summaries
            compact_recent_turn_summary = self._compact_initiative_recent_turn_summary(
                initiative_payload.get("recent_turn_summary")
            )
            if compact_recent_turn_summary:
                payload["recent_turn_summary"] = compact_recent_turn_summary
            compact_world_state_summaries = self._compact_initiative_world_state_summaries(
                initiative_payload.get("world_state_summary")
            )
            if compact_world_state_summaries:
                payload["world_state_summaries"] = compact_world_state_summaries
            compact_intervention_state = self._compact_initiative_intervention_state(
                initiative_payload.get("intervention_state")
            )
            if compact_intervention_state:
                payload["intervention_state"] = compact_intervention_state
            suppression_summary = initiative_payload.get("suppression_summary")
            if isinstance(suppression_summary, dict):
                compact_suppression: dict[str, Any] = {}
                for key in ("suppression_level", "reason_summary"):
                    value = suppression_summary.get(key)
                    if isinstance(value, str) and value.strip():
                        compact_suppression[key] = self._clamp(value.strip(), limit=160)
                for key in ("background_trigger", "cooldown_active", "same_dedupe_recently_replied"):
                    value = suppression_summary.get(key)
                    if isinstance(value, bool):
                        compact_suppression[key] = value
                if compact_suppression:
                    payload["suppression_summary"] = compact_suppression
            intervention_risk_summary = initiative_payload.get("intervention_risk_summary")
            if isinstance(intervention_risk_summary, str) and intervention_risk_summary.strip():
                payload["intervention_risk_summary"] = self._clamp(intervention_risk_summary.strip(), limit=160)
        compact_pending_intent_selection = self._compact_pending_intent_selection_summary(
            pending_intent_selection
        )
        if isinstance(compact_pending_intent_selection, dict):
            payload["pending_intent_selection_summary"] = compact_pending_intent_selection
        return payload

    def _compact_initiative_drive_summaries(self, summaries: Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        if not isinstance(summaries, list):
            return payload
        for summary in summaries[:2]:
            if not isinstance(summary, dict):
                continue
            item: dict[str, Any] = {}
            for key in ("drive_kind", "summary_text", "freshness_hint", "stability_hint"):
                value = summary.get(key)
                if not isinstance(value, str) or not value.strip():
                    continue
                item[key] = self._clamp(value.strip(), limit=160)
            salience = summary.get("salience")
            if isinstance(salience, (int, float)):
                item["salience"] = round(float(salience), 2)
            support_count = summary.get("support_count")
            if isinstance(support_count, int) and support_count > 0:
                item["support_count"] = support_count
            for key in ("support_strength", "scope_alignment", "signal_strength", "persona_alignment"):
                value = summary.get(key)
                if isinstance(value, (int, float)):
                    item[key] = round(max(0.0, min(float(value), 1.0)), 2)
            if item:
                payload.append(item)
        return payload

    def _compact_initiative_pending_intent_summaries(self, summaries: Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        if not isinstance(summaries, list):
            return payload
        for summary in summaries[:3]:
            if not isinstance(summary, dict):
                continue
            item: dict[str, Any] = {}
            for key in ("intent_kind", "intent_summary", "reason_summary"):
                value = summary.get(key)
                if not isinstance(value, str) or not value.strip():
                    continue
                item[key] = self._clamp(value.strip(), limit=160)
            if item:
                payload.append(item)
        return payload

    def _compact_initiative_candidate_families(self, candidate_families: Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        if not isinstance(candidate_families, list):
            return payload
        for family in candidate_families[:3]:
            if not isinstance(family, dict):
                continue
            item: dict[str, Any] = {}
            family_name = family.get("family")
            if isinstance(family_name, str) and family_name.strip():
                item["family"] = family_name.strip()
            for key in ("available", "selected"):
                value = family.get(key)
                if isinstance(value, bool):
                    item[key] = value
            reason_summary = family.get("reason_summary")
            if isinstance(reason_summary, str) and reason_summary.strip():
                item["reason_summary"] = self._clamp(reason_summary.strip(), limit=160)
            priority_score = family.get("priority_score")
            if isinstance(priority_score, (int, float)):
                item["priority_score"] = round(float(priority_score), 2)
            preferred_result_kind = family.get("preferred_result_kind")
            if isinstance(preferred_result_kind, str) and preferred_result_kind.strip():
                item["preferred_result_kind"] = preferred_result_kind.strip()
            preferred_result_reason_summary = family.get("preferred_result_reason_summary")
            if isinstance(preferred_result_reason_summary, str) and preferred_result_reason_summary.strip():
                item["preferred_result_reason_summary"] = self._clamp(
                    preferred_result_reason_summary.strip(),
                    limit=160,
                )
            preferred_capability_id = family.get("preferred_capability_id")
            if isinstance(preferred_capability_id, str) and preferred_capability_id.strip():
                item["preferred_capability_id"] = preferred_capability_id.strip()
            blocking_reason_summary = family.get("blocking_reason_summary")
            if isinstance(blocking_reason_summary, str) and blocking_reason_summary.strip():
                item["blocking_reason_summary"] = self._clamp(blocking_reason_summary.strip(), limit=160)
            preferred_capability_input = family.get("preferred_capability_input")
            if isinstance(preferred_capability_input, dict) and preferred_capability_input:
                item["preferred_capability_input"] = preferred_capability_input
            if item:
                payload.append(item)
        return payload

    def _compact_initiative_recent_turn_summary(self, recent_turn_summary: Any) -> list[dict[str, str]]:
        payload: list[dict[str, str]] = []
        if not isinstance(recent_turn_summary, list):
            return payload
        for item in recent_turn_summary[:2]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            text = item.get("text")
            if not isinstance(role, str) or not role.strip():
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            payload.append(
                {
                    "role": role.strip(),
                    "text": self._clamp(text.strip(), limit=80) or "",
                }
            )
        return payload

    def _compact_initiative_intervention_state(self, intervention_state: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if not isinstance(intervention_state, dict):
            return payload
        for key in ("background_trigger", "cooldown_active", "same_dedupe_recently_replied"):
            value = intervention_state.get(key)
            if isinstance(value, bool):
                payload[key] = value
        for key in ("cooldown_reason", "last_spontaneous_reply_age_label"):
            value = intervention_state.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = self._clamp(value.strip(), limit=120)
        return payload

    def _compact_initiative_world_state_summaries(self, summaries: Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        if not isinstance(summaries, list):
            return payload
        for summary in summaries[:3]:
            if not isinstance(summary, dict):
                continue
            item: dict[str, Any] = {}
            state_type = summary.get("state_type")
            summary_text = summary.get("summary_text")
            if isinstance(state_type, str) and state_type.strip():
                item["state_type"] = state_type.strip()
            if isinstance(summary_text, str) and summary_text.strip():
                item["summary_text"] = self._clamp(summary_text.strip(), limit=160)
            if item:
                payload.append(item)
        return payload

    def _compact_wake_observations(self, observations: Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        if not isinstance(observations, list):
            return payload
        for observation in observations[:6]:
            if not isinstance(observation, dict):
                continue
            item: dict[str, Any] = {}
            for key in (
                "observation_id",
                "capability_id",
                "status",
                "vision_source_id",
                "source_kind",
                "source_label",
                "visual_summary_text",
                "reason_summary",
                "error",
                "request_id",
            ):
                value = observation.get(key)
                if isinstance(value, str) and value.strip():
                    item[key] = self._clamp(value.strip(), limit=160)
            image_count = observation.get("image_count")
            if isinstance(image_count, int):
                item["image_count"] = image_count
            capability_request_summary = observation.get("capability_request_summary")
            if isinstance(capability_request_summary, dict):
                item["capability_request_summary"] = self._compact_capability_request_summary(
                    capability_request_summary
                )
            desktop_signal = self._compact_desktop_observation_signal(
                observation.get("desktop_observation_signal")
            )
            if desktop_signal:
                item["desktop_observation_signal"] = desktop_signal
            if item:
                payload.append(item)
        return payload

    def _compact_pending_intent_selection_summary(
        self,
        pending_intent_selection: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(pending_intent_selection, dict):
            return None
        payload: dict[str, Any] = {}
        for key in (
            "candidate_pool_count",
            "eligible_candidate_count",
            "selected_candidate_ref",
            "selected_candidate_id",
            "result_status",
        ):
            value = pending_intent_selection.get(key)
            if value is None:
                continue
            payload[key] = value
        for key in ("selection_reason", "failure_reason"):
            value = pending_intent_selection.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            payload[key] = self._clamp(value.strip(), limit=160)
        if not payload:
            return None
        return payload

    def _build_capability_dispatch_summary(
        self,
        *,
        trigger_kind: str,
        capability_request_summary: dict[str, Any] | None,
        followup_capability_request_summary: dict[str, Any] | None,
        decision: dict[str, Any] | None,
        ongoing_action_transition_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        dispatch_request_summary = followup_capability_request_summary if trigger_kind == "capability_result" else capability_request_summary
        compact_request_summary = self._compact_capability_request_summary(dispatch_request_summary)
        if not isinstance(compact_request_summary, dict):
            return None
        capability_id = compact_request_summary.get("capability_id")
        if not isinstance(capability_id, str) or not capability_id.strip():
            return None
        payload: dict[str, Any] = {
            "capability_id": capability_id.strip(),
            "capability_kind": self._capability_followup_capability_kind(capability_id.strip()),
            "request_summary": compact_request_summary,
            "transition_summary": self._compact_capability_followup_transition_summary(
                ongoing_action_transition_summary,
            ),
        }
        decision_summary = self._compact_capability_dispatch_decision_summary(decision)
        if isinstance(decision_summary, dict):
            payload["decision_summary"] = decision_summary
        return payload

    def _build_capability_result_followup_summary(
        self,
        *,
        trigger_kind: str,
        observation_summary: dict[str, Any] | None,
        source_capability_request_summary: dict[str, Any] | None,
        followup_capability_request_summary: dict[str, Any] | None,
        decision: dict[str, Any] | None,
        result_kind: str,
        reply_payload: dict[str, Any] | None,
        pending_intent_summary: dict[str, Any] | None,
        ongoing_action_transition_summary: dict[str, Any] | None,
        failure_reason: str | None = None,
    ) -> dict[str, Any] | None:
        if trigger_kind != "capability_result":
            return None
        capability_id = self._capability_followup_capability_id(
            observation_summary=observation_summary,
            source_capability_request_summary=source_capability_request_summary,
            ongoing_action_transition_summary=ongoing_action_transition_summary,
        )
        if capability_id is None:
            return None
        payload: dict[str, Any] = {
            "capability_id": capability_id,
            "capability_kind": self._capability_followup_capability_kind(capability_id),
            "source_request_summary": self._compact_capability_request_summary(source_capability_request_summary),
            "observation_summary": self._compact_capability_followup_observation_summary(observation_summary),
            "decision_summary": self._compact_capability_followup_decision_summary(decision),
            "followup_result_summary": self._compact_capability_followup_result_summary(
                result_kind=result_kind,
                reply_payload=reply_payload,
                pending_intent_summary=pending_intent_summary,
                followup_capability_request_summary=followup_capability_request_summary,
                failure_reason=failure_reason,
            ),
            "transition_summary": self._compact_capability_followup_transition_summary(
                ongoing_action_transition_summary,
            ),
        }
        return payload

    def _capability_followup_capability_id(
        self,
        *,
        observation_summary: dict[str, Any] | None,
        source_capability_request_summary: dict[str, Any] | None,
        ongoing_action_transition_summary: dict[str, Any] | None,
    ) -> str | None:
        for value in (
            observation_summary.get("capability_id") if isinstance(observation_summary, dict) else None,
            source_capability_request_summary.get("capability_id")
            if isinstance(source_capability_request_summary, dict)
            else None,
            ongoing_action_transition_summary.get("last_capability_id")
            if isinstance(ongoing_action_transition_summary, dict)
            else None,
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _capability_followup_capability_kind(self, capability_id: str) -> str | None:
        manifest = capability_manifests().get(capability_id, {})
        capability_kind = manifest.get("kind")
        if isinstance(capability_kind, str) and capability_kind.strip():
            return capability_kind.strip()
        return None

    def _compact_capability_request_summary(self, summary: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(summary, dict):
            return None
        payload: dict[str, Any] = {}
        for key in ("request_id", "capability_id", "status", "timeout_ms"):
            value = summary.get(key)
            if value is None:
                continue
            payload[key] = value
        readiness_digest = summary.get("readiness_digest")
        if isinstance(readiness_digest, dict):
            payload["readiness_digest"] = readiness_digest
        if not payload:
            return None
        return payload

    def _compact_capability_dispatch_decision_summary(
        self,
        decision: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        compact = self._compact_capability_followup_decision_summary(decision)
        if not isinstance(compact, dict):
            return None
        if compact.get("kind") != "capability_request":
            return None
        return compact

    def _compact_capability_followup_observation_summary(
        self,
        observation_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(observation_summary, dict):
            return None
        payload: dict[str, Any] = {}
        for key, value in observation_summary.items():
            if value is None:
                continue
            if isinstance(value, str):
                normalized = value.strip()
                if not normalized:
                    continue
                payload[key] = self._clamp(normalized, limit=160)
                continue
            if isinstance(value, (int, float, bool)):
                payload[key] = value
                continue
            if key == "readiness_digest" and isinstance(value, dict):
                payload[key] = value
        if not payload:
            return None
        return payload

    def _compact_capability_followup_decision_summary(
        self,
        decision: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(decision, dict):
            return None
        payload: dict[str, Any] = {}
        for key in ("kind", "reason_code", "reason_summary"):
            value = decision.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            payload[key] = self._clamp(value.strip(), limit=160)
        if not payload:
            return None
        return payload

    def _compact_trigger_result_summary(
        self,
        *,
        result_kind: str,
        reply_payload: dict[str, Any] | None,
        pending_intent_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
        failure_reason: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "result_kind": result_kind,
        }
        if isinstance(reply_payload, dict) and isinstance(reply_payload.get("reply_text"), str):
            payload["reply_summary"] = self._clamp(reply_payload["reply_text"].strip(), limit=160)
        if isinstance(pending_intent_summary, dict):
            payload["pending_intent_summary"] = pending_intent_summary
        compact_capability_request = self._compact_capability_request_summary(capability_request_summary)
        if isinstance(compact_capability_request, dict):
            payload["capability_request_summary"] = compact_capability_request
        if isinstance(failure_reason, str) and failure_reason.strip():
            payload["internal_failure_summary"] = self._clamp(failure_reason.strip(), limit=160)
        return payload

    def _compact_capability_followup_result_summary(
        self,
        *,
        result_kind: str,
        reply_payload: dict[str, Any] | None,
        pending_intent_summary: dict[str, Any] | None,
        followup_capability_request_summary: dict[str, Any] | None,
        failure_reason: str | None,
    ) -> dict[str, Any]:
        payload = self._compact_trigger_result_summary(
            result_kind=result_kind,
            reply_payload=reply_payload,
            pending_intent_summary=pending_intent_summary,
            capability_request_summary=followup_capability_request_summary,
            failure_reason=failure_reason,
        )
        compact_followup_request = payload.pop("capability_request_summary", None)
        if isinstance(compact_followup_request, dict):
            payload["followup_capability_request_summary"] = compact_followup_request
        return payload

    def _compact_capability_followup_transition_summary(
        self,
        ongoing_action_transition_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(ongoing_action_transition_summary, dict):
            return None
        payload: dict[str, Any] = {}
        for key in (
            "transition_sequence",
            "transition_kind",
            "final_state",
            "reason_code",
            "reason_summary",
            "transition_source",
            "decision_kind",
            "result_error",
            "detail_summary",
        ):
            value = ongoing_action_transition_summary.get(key)
            if value is None:
                continue
            payload[key] = value
        if not payload:
            return None
        return payload

    def _persist_cycle_success(
        self,
        *,
        cycle_id: str,
        started_at: str,
        finished_at: str,
        state: dict[str, Any],
        runtime_summary: dict[str, Any],
        input_text: str,
        augmented_query_text: str | None,
        client_context: dict[str, Any],
        recall_hint: dict[str, Any],
        recall_pack: dict[str, Any],
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        decision: dict[str, Any],
        result_kind: str,
        reply_payload: dict[str, Any] | None,
        pending_intent_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        initiative_context: InitiativeContext | None,
        capability_result_context: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        world_state_trace: WorldStateTrace | None,
        trigger_kind: str,
        input_event_kind: str,
        input_event_role: str,
        pending_intent_selection: dict[str, Any] | None = None,
        observation_summary: dict[str, Any] | None = None,
        capability_request_summary: dict[str, Any] | None = None,
        followup_capability_request_summary: dict[str, Any] | None = None,
        ongoing_action_transition_summary: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        memory_set_id = state["selected_memory_set_id"]
        events = self._build_cycle_events(
            cycle_id=cycle_id,
            memory_set_id=memory_set_id,
            input_event_kind=input_event_kind,
            input_event_role=input_event_role,
            input_text=input_text,
            started_at=started_at,
            finished_at=finished_at,
            decision=decision,
            result_kind=result_kind,
            reply_payload=reply_payload,
            pending_intent_summary=pending_intent_summary,
        )
        events.extend(
            self._build_event_evidence_audit_events(
                cycle_id=cycle_id,
                memory_set_id=memory_set_id,
                created_at=finished_at,
                recall_pack=recall_pack,
            )
        )
        retrieval_run = self._build_retrieval_run_success(
            cycle_id=cycle_id,
            memory_set_id=memory_set_id,
            started_at=started_at,
            finished_at=finished_at,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
        )
        cycle_summary = self._build_cycle_summary(
            cycle_id=cycle_id,
            started_at=started_at,
            finished_at=finished_at,
            state=state,
            trigger_kind=trigger_kind,
            result_kind=result_kind,
            failed=False,
        )
        cycle_trace = self._build_cycle_trace(
            cycle_id=cycle_id,
            cycle_summary=cycle_summary,
            input_text=input_text,
            augmented_query_text=augmented_query_text,
            client_context=client_context,
            runtime_summary=runtime_summary,
            foreground_world_state=foreground_world_state,
            recall_trace=self._build_success_recall_trace(recall_hint, recall_pack),
            decision_trace=self._build_success_decision_trace(
                state=state,
                input_text=input_text,
                augmented_query_text=augmented_query_text,
                time_context=time_context,
                affect_context=affect_context,
                drive_state_summary=drive_state_summary,
                foreground_world_state=foreground_world_state,
                ongoing_action_summary=ongoing_action_summary,
                capability_decision_view=capability_decision_view,
                initiative_context=initiative_context,
                capability_result_context=capability_result_context,
                visual_observation_context=visual_observation_context,
                recall_pack=recall_pack,
                decision=decision,
                pending_intent_summary=pending_intent_summary,
            ),
            world_state_trace=world_state_trace,
            result_trace=self._build_success_result_trace(
                trigger_kind=trigger_kind,
                input_text=input_text,
                started_at=started_at,
                finished_at=finished_at,
                decision=decision,
                result_kind=result_kind,
                reply_payload=reply_payload,
                pending_intent_summary=pending_intent_summary,
                pending_intent_selection=pending_intent_selection,
                initiative_context=initiative_context,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                followup_capability_request_summary=followup_capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            ),
            memory_trace=self._pending_memory_trace(),
            pending_intent_selection=pending_intent_selection,
            observation_summary=observation_summary,
            ongoing_action_summary=ongoing_action_summary,
            initiative_context=initiative_context,
        )
        self.store.persist_cycle_records(
            events=events,
            retrieval_run=retrieval_run,
            cycle_summary=cycle_summary,
            cycle_trace=cycle_trace,
        )
        return events

    def _persist_cycle_failure(
        self,
        *,
        cycle_id: str,
        started_at: str,
        finished_at: str,
        state: dict[str, Any],
        runtime_summary: dict[str, Any],
        input_text: str,
        client_context: dict[str, Any],
        failure_reason: str,
        trigger_kind: str = "user_message",
        input_event_kind: str = "conversation_input",
        input_event_role: str = "user",
        recall_trace: dict[str, Any] | None = None,
        failure_event_kind: str = "recall_hint_failure",
        failure_event_payload: dict[str, Any] | None = None,
        pending_intent_selection: dict[str, Any] | None = None,
        drive_state_summary: list[dict[str, Any]] | None = None,
        ongoing_action_summary: dict[str, Any] | None = None,
        capability_decision_view: list[dict[str, Any]] | None = None,
        initiative_context: InitiativeContext | None = None,
        observation_summary: dict[str, Any] | None = None,
        capability_request_summary: dict[str, Any] | None = None,
        followup_capability_request_summary: dict[str, Any] | None = None,
        ongoing_action_transition_summary: dict[str, Any] | None = None,
    ) -> None:
        memory_set_id = state["selected_memory_set_id"]
        events = self._build_cycle_events(
            cycle_id=cycle_id,
            memory_set_id=memory_set_id,
            input_event_kind=input_event_kind,
            input_event_role=input_event_role,
            input_text=input_text,
            started_at=started_at,
            finished_at=finished_at,
            failure_reason=failure_reason,
            failure_event_kind=failure_event_kind,
            failure_event_payload=failure_event_payload,
        )
        retrieval_run = self._build_retrieval_run_failure(
            cycle_id=cycle_id,
            memory_set_id=memory_set_id,
            started_at=started_at,
            finished_at=finished_at,
            failure_reason=failure_reason,
        )
        cycle_summary = self._build_cycle_summary(
            cycle_id=cycle_id,
            started_at=started_at,
            finished_at=finished_at,
            state=state,
            trigger_kind=trigger_kind,
            result_kind="internal_failure",
            failed=True,
        )
        cycle_trace = self._build_cycle_trace(
            cycle_id=cycle_id,
            cycle_summary=cycle_summary,
            input_text=input_text,
            augmented_query_text=None,
            client_context=client_context,
            runtime_summary=runtime_summary,
            foreground_world_state=None,
            recall_trace=recall_trace or self._build_failure_recall_trace(),
            decision_trace=self._build_failure_decision_trace(
                state=state,
                input_text=input_text,
                failure_reason=failure_reason,
                drive_state_summary=drive_state_summary,
                ongoing_action_summary=ongoing_action_summary,
                capability_decision_view=capability_decision_view,
                initiative_context=initiative_context,
            ),
            world_state_trace=None,
            result_trace=self._build_failure_result_trace(
                trigger_kind=trigger_kind,
                input_text=input_text,
                started_at=started_at,
                finished_at=finished_at,
                failure_reason=failure_reason,
                pending_intent_selection=pending_intent_selection,
                initiative_context=initiative_context,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                followup_capability_request_summary=followup_capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            ),
            memory_trace={},
            pending_intent_selection=pending_intent_selection,
            observation_summary=observation_summary,
            ongoing_action_summary=ongoing_action_summary,
            initiative_context=initiative_context,
        )
        self.store.persist_cycle_records(
            events=events,
            retrieval_run=retrieval_run,
            cycle_summary=cycle_summary,
            cycle_trace=cycle_trace,
        )

    def _exception_capability_dispatch_trace(
        self,
        exc: Exception,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not isinstance(exc, CapabilityDispatchError):
            return None, None
        capability_request_summary = exc.capability_request_summary
        ongoing_action_transition_summary = exc.ongoing_action_transition_summary
        if not isinstance(capability_request_summary, dict):
            capability_request_summary = None
        if not isinstance(ongoing_action_transition_summary, dict):
            ongoing_action_transition_summary = None
        return capability_request_summary, ongoing_action_transition_summary
